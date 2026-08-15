"""Incident history & temporal analysis (Phase 6).

Phase 5 correlates diagnostics into incident *snapshots* (what is related right
now). Phase 6 gives incidents a temporal lifecycle: it remembers what happened
over time -- when an incident started, which diagnostics appeared and in what
order, when each recovered, how long the incident lasted, and whether the same
type has happened before.

This module is a pure consumer (no rclpy), like diagnostics.py/correlation.py.
It consumes:
  * DiagnosticEngine events (ACTIVE/RESOLVED transitions) -- the ground-truth
    event stream with timestamps,
  * CorrelationEngine.active (current correlated groups) -- membership,
    strategies, confidence, owner.
It never touches ROS and never re-collects anything.

Object model (distinct objects on purpose):
  OBSERVATION  "/scan received at 14:30:02"           (Phase 3, a measured fact)
  DIAGNOSTIC   "/scan below expected rate"             (Phase 4, a verdict)
  EVENT        "diagnostic became ACTIVE at 14:30:02"  (MemberEvent, a transition)
  INCIDENT     "Robot 2 sensor degradation"            (IncidentSession, a session)
  HISTORY      "the complete ordered sequence of events during the incident"
                   (IncidentSession.events + closed occurrences)

Lifecycle: ACTIVE -> RECOVERING -> RECOVERED.
  ACTIVE      every member still active (or just formed)
  RECOVERING  some members recovered, at least one still active
  RECOVERED   every member recovered; the session closes. A later occurrence on
              the same entity is a NEW incident, not a continuation.

Incident identity is a stable monotonic id assigned when the session forms.
Sessions are scoped to an entity (system, robot) or an ownerless subject, so a
correlated group that grows or shrinks UPDATES the open session instead of
spawning a new one -- this fixes the Phase 5 snapshot churn (documented in
design.md as a known limitation).

Storage: in-memory only. No persistence -- the debugger is a live observation
tool and cross-restart history is CONSIDERED/FUTURE. On restart the history is
empty by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from ros2_debugger.correlation import Confidence, Incident as CorrelationIncident
from ros2_debugger.diagnostics import Diagnostic, DiagnosticState

CONFIDENCE_RANK = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


class LifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    RECOVERING = "RECOVERING"
    RECOVERED = "RECOVERED"


class MemberTransition(str, Enum):
    ACTIVATED = "ACTIVATED"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True)
class MemberEvent:
    """One transition of one diagnostic within an incident."""

    timestamp: float  # monotonic
    key: str  # diagnostic key (rule + subject), stable identity
    subject: str  # human-readable subject for display
    transition: MemberTransition


@dataclass
class IncidentSession:
    """A stable incident occurrence with its temporal history.

    An incident is NOT a diagnostic (a single-subject verdict) and NOT a
    correlation snapshot (a current grouping): it is the running record of a
    related group over time. `events` preserves the exact order in which each
    member activated and recovered.
    """

    incident_id: int
    system: Optional[str]
    robot: Optional[str]
    strategies: Tuple[str, ...] = ()
    confidence: Confidence = Confidence.LOW
    started_at: float = 0.0
    ended_at: Optional[float] = None
    _members: set = field(default_factory=set, repr=False)
    _active: set = field(default_factory=set, repr=False)
    _events: List[MemberEvent] = field(default_factory=list, repr=False)

    def record(
        self, timestamp: float, key: str, subject: str, transition: MemberTransition
    ) -> None:
        self._events.append(MemberEvent(timestamp, key, subject, transition))
        if transition is MemberTransition.ACTIVATED:
            self._members.add(key)
            self._active.add(key)
            if self.started_at == 0.0 or timestamp < self.started_at:
                self.started_at = timestamp
        else:
            self._active.discard(key)

    def has_member(self, key: str) -> bool:
        return key in self._members

    def is_active(self, key: str) -> bool:
        return key in self._active

    def update_meta(self, strategies: Tuple[str, ...], confidence: Confidence) -> None:
        merged = set(self.strategies) | set(strategies)
        self.strategies = tuple(sorted(merged))
        if CONFIDENCE_RANK[confidence] > CONFIDENCE_RANK[self.confidence]:
            self.confidence = confidence

    def close(self, now: float) -> None:
        # The incident ends when its last member recovered, not when we noticed
        # (a recovery event can arrive one cycle later). Fall back to `now`.
        recovered_ts = [
            e.timestamp
            for e in self._events
            if e.transition is MemberTransition.RECOVERED
        ]
        self.ended_at = max(recovered_ts) if recovered_ts else now

    # --- derived views ----------------------------------------------------

    @property
    def state(self) -> LifecycleState:
        if self.ended_at is not None:
            return LifecycleState.RECOVERED
        if self.member_count > 0 and self.active_count < self.member_count:
            return LifecycleState.RECOVERING
        return LifecycleState.ACTIVE

    @property
    def owner(self) -> str:
        if self.system and self.robot:
            return f"{self.system}/{self.robot}"
        if self.system:
            return self.system
        return "unattributed"

    @property
    def members(self) -> Tuple[str, ...]:
        return tuple(sorted(self._members))

    @property
    def member_count(self) -> int:
        return len(self._members)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def events(self) -> Tuple[MemberEvent, ...]:
        return tuple(
            sorted(self._events, key=lambda e: (e.timestamp, e.key, e.transition))
        )

    @property
    def duration(self) -> Optional[float]:
        if self.started_at and self.ended_at is not None:
            return self.ended_at - self.started_at
        return None


@dataclass(frozen=True)
class _Group:
    """A merged correlation group feeding the history layer."""

    members: Tuple[Diagnostic, ...]
    strategies: Tuple[str, ...]
    confidence: Confidence
    system: Optional[str]
    robot: Optional[str]


def _shared_subject_value(members: Tuple[Diagnostic, ...]) -> Optional[str]:
    """The single topic/node/TF-frame/process all (ownerless) members share."""
    for attr in ("topic", "node", "tf_frame", "process"):
        values = {getattr(d, attr) for d in members}
        values.discard(None)
        if len(values) == 1 and next(iter(values)):
            return next(iter(values))
    return None


class HistoryEngine:
    """Maintains stable incident sessions from diagnostics events + groups."""

    def __init__(self) -> None:
        self._next_id = 1
        self._open: Dict[tuple, IncidentSession] = {}
        self._history: List[IncidentSession] = []
        self.evaluation_count = 0

    def update(
        self,
        diagnostic_events: List[Diagnostic],
        correlation_groups: List[CorrelationIncident],
        now: float,
    ) -> List[IncidentSession]:
        """Reconcile sessions with the current cycle; return changed sessions.

        A session is returned when it is created, gained a member, or closed.
        """
        self.evaluation_count += 1
        events: List[IncidentSession] = []

        # 1. Member recoveries from the diagnostic event stream. Recovery
        #    timestamps exist only here (a resolved diagnostic's own timestamp
        #    overwrites its activation time), so this is the ground truth.
        recoveries = [
            e for e in diagnostic_events if e.state is DiagnosticState.RESOLVED
        ]
        for session in self._open.values():
            for ev in recoveries:
                if session.has_member(ev.key):
                    session.record(ev.timestamp, ev.key, ev.subject, MemberTransition.RECOVERED)

        # 2. Group synchronization: create or update open sessions.
        by_scope: Dict[tuple, List[CorrelationIncident]] = {}
        for inc in correlation_groups:
            by_scope.setdefault(self._scope(inc), []).append(inc)
        for scope, inces in by_scope.items():
            group = self._merge(inces)
            session = self._open.get(scope)
            if session is None:
                session = self._create(scope, group)
                self._open[scope] = session
                events.append(session)
            else:
                before = session.member_count
                session.update_meta(group.strategies, group.confidence)
                for diag in group.members:
                    # Record an activation only when the member is not already
                    # active: a member that recovered and then fired again must
                    # be re-activated, not skipped because it was once a member.
                    if not session.is_active(diag.key):
                        session.record(
                            diag.timestamp, diag.key, diag.subject, MemberTransition.ACTIVATED
                        )
                if session.member_count > before:
                    events.append(session)

        # 3. Close sessions whose members have all recovered.
        for scope, session in list(self._open.items()):
            if session.member_count > 0 and session.active_count == 0:
                session.close(now)
                del self._open[scope]
                events.append(session)

        return events

    @property
    def active(self) -> List[IncidentSession]:
        return sorted(self._open.values(), key=lambda s: s.started_at)

    @property
    def closed(self) -> List[IncidentSession]:
        return [s for s in self._history if s.state is LifecycleState.RECOVERED]

    @property
    def all(self) -> List[IncidentSession]:
        return list(self._history)

    # --- helpers ----------------------------------------------------------

    def _create(self, scope: tuple, group: _Group) -> IncidentSession:
        session = IncidentSession(
            incident_id=self._next_id,
            system=group.system,
            robot=group.robot,
            strategies=group.strategies,
            confidence=group.confidence,
        )
        self._next_id += 1
        for diag in group.members:
            session.record(diag.timestamp, diag.key, diag.subject, MemberTransition.ACTIVATED)
        self._history.append(session)
        return session

    @staticmethod
    def _merge(inces: List[CorrelationIncident]) -> _Group:
        members: Dict[str, Diagnostic] = {}
        strategies: set = set()
        confidence = Confidence.LOW
        system: Optional[str] = None
        robot: Optional[str] = None
        for inc in inces:
            for diag in inc.members:
                members[diag.key] = diag
            strategies.update(inc.strategies)
            if CONFIDENCE_RANK[inc.confidence] > CONFIDENCE_RANK[confidence]:
                confidence = inc.confidence
            if inc.system is not None:
                system = inc.system
                robot = inc.robot
        return _Group(
            members=tuple(members.values()),
            strategies=tuple(sorted(strategies)),
            confidence=confidence,
            system=system,
            robot=robot,
        )

    @staticmethod
    def _scope(inc: CorrelationIncident) -> tuple:
        if inc.system is not None:
            return ("entity", inc.system, inc.robot)
        subject = _shared_subject_value(inc.members)
        if subject is not None:
            return ("subject", subject)
        return ("members", inc.key)
