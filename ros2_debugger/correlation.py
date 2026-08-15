"""Diagnostic correlation engine (Phase 5).

Phase 4 produces per-subject diagnostics ("/robot2/scan is below its expected
frequency"). Phase 5 asks: WHICH diagnostics are related, and what might be
contributing? It consumes the Phase 4 engine's ACTIVE diagnostics and produces:

  * Incidents — groups of related diagnostics with shared evidence and a
    cautious hypothesis
  * Confidence  — qualitative (LOW / MEDIUM / HIGH)
  * Hypotheses  — "resource pressure MAY be contributing", never "X caused Y"

Like diagnostics.py this module has NO rclpy imports. It is a pure consumer of
observations, not another ROS collector.

The five-layer distinction is enforced here by construction:

    OBSERVATION  "/robot2/scan = 1.2 Hz"
    DIAGNOSTIC   below expected 8.0 Hz                (Phase 4)
    CORRELATION  CPU + scan degradation on Robot 2 in a related time window
    HYPOTHESIS   resource pressure MAY be contributing (Phase 5)
    ROOT CAUSE   the LiDAR driver is broken            (NEVER claimed)

Correlation is not causation. The engine never asserts a direction or a cause;
hypothesis strings are template-constrained and the tests enforce that they
contain no causal vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Tuple

from ros2_debugger.diagnostics import Diagnostic

RESOURCE_RULES = frozenset({"high_cpu", "high_memory"})


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class IncidentState(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class CorrelationConfig:
    """Tunables for the correlation pass.

    temporal_window_s — two diagnostics are temporally related when their
        activation timestamps differ by no more than this (onset proximity).
    min_members — an incident must contain at least this many diagnostics;
        a single diagnostic is not an incident.
    """

    temporal_window_s: float = 30.0
    min_members: int = 2

    @classmethod
    def from_dict(cls, data: dict) -> "CorrelationConfig":
        data = data or {}
        return cls(
            temporal_window_s=float(data.get("temporal_window_s", 30.0)),
            min_members=int(data.get("min_members", 2)),
        )


@dataclass(frozen=True)
class Incident:
    """A group of related diagnostics, with evidence and a hypothesis.

    An incident is NOT a diagnostic: a diagnostic is a single-subject verdict
    ("X is abnormal"); an incident is a multi-subject grouping ("these
    abnormalities may be related") that makes no causal claim.

    Identity is the sorted tuple of member diagnostic keys. Membership changes
    therefore form a new incident; the previous one RESOLVES.
    """

    members: Tuple[Diagnostic, ...]
    strategies: Tuple[str, ...]
    confidence: Confidence
    hypothesis: str
    evidence: Tuple[str, ...]
    system: Optional[str]
    robot: Optional[str]
    attribution_uncertain: bool
    state: IncidentState = IncidentState.ACTIVE
    created_at: float = 0.0
    updated_at: float = 0.0
    resolved_at: Optional[float] = None

    @property
    def key(self) -> Tuple[str, ...]:
        return tuple(sorted(d.key for d in self.members))

    @property
    def owner(self) -> str:
        if self.system and self.robot:
            return f"{self.system}/{self.robot}"
        if self.system:
            return self.system
        return "unattributed"


def _is_resource(diag: Diagnostic) -> bool:
    return diag.rule_id in RESOURCE_RULES


def _shared_subject(a: Diagnostic, b: Diagnostic) -> bool:
    """Two diagnostics address the same concrete subject (topic/node/TF frame/
    process). A cheap, field-only proxy for a graph dependency link."""
    for attr in ("topic", "node", "tf_frame", "process"):
        va = getattr(a, attr)
        if va is not None and va == getattr(b, attr):
            return True
    return False


class CorrelationEngine:
    """Recomputes incidents from the current ACTIVE diagnostics each cycle.

    Pairing gate (false-correlation safety):
      1. temporal: activation timestamps within temporal_window_s (required),
      2. then at least one link:
         - entity: identical non-None (system, robot) — the primary gate; two
           different robots are NEVER merged, even in the same window, because
           a shared cause (global slowdown) would need separate machinery;
         - ownerless: BOTH members have no owner AND they share a subject or
           form a resource relationship — a genuinely uncertain linkage, so the
           incident is flagged attribution_uncertain and capped at LOW.

    Strategies recorded per incident: entity, temporal, plus resource (one
    resource rule + one behavioral rule — enables the chain hypothesis) and
    shared_subject (same topic/node/frame/process — dependency proxy).

    Confidence:
      LOW    attribution uncertain (ownerless linkage)
      MEDIUM entity + temporal co-occurrence on one robot
      HIGH   entity + temporal + a mechanism signal (resource or shared subject)
    """

    def __init__(self, config: CorrelationConfig) -> None:
        self.config = config
        self._incidents: Dict[Tuple[str, ...], Incident] = {}
        self._active_last: List[Diagnostic] = []
        self.history: List[Incident] = []
        self.evaluation_count = 0

    # --- public -----------------------------------------------------------

    def update(
        self, active: List[Diagnostic], now: float
    ) -> List[Incident]:
        """Recompute incidents from the current ACTIVE diagnostics.

        Returns the events for this cycle: newly-ACTIVE incidents and incidents
        that RESOLVED (members dropped below min_members, or membership changed
        so the old grouping no longer holds). Recovery is therefore automatic:
        when diagnostics recover, their incident follows.
        """
        self.evaluation_count += 1
        self._active_last = list(active)

        links = self._links(active)
        new_incidents: Dict[Tuple[str, ...], Incident] = {}
        for cluster in self._clusters(active, links):
            if len(cluster) < self.config.min_members:
                continue
            inc = self._make_incident(cluster, links, now)
            new_incidents[inc.key] = inc

        events: List[Incident] = []
        for key in list(self._incidents):
            if key not in new_incidents:
                resolved = replace(
                    self._incidents[key],
                    state=IncidentState.RESOLVED,
                    resolved_at=now,
                )
                del self._incidents[key]
                self.history.append(resolved)
                events.append(resolved)
        for key, inc in new_incidents.items():
            if key not in self._incidents:
                self._incidents[key] = inc
                self.history.append(inc)
                events.append(inc)
            else:
                self._incidents[key] = inc  # refresh without re-emitting
        return events

    @property
    def active(self) -> List[Incident]:
        return sorted(self._incidents.values(), key=lambda i: i.created_at)

    @property
    def uncorrelated(self) -> List[Tuple[Diagnostic, str]]:
        """Active diagnostics in no incident, with the reason.

        This is how the debugger communicates uncertainty: an unattributed
        diagnostic is never silently grouped by guessing — it is reported as
        not correlated, with an explicit reason.
        """
        in_incidents = {
            key for inc in self._incidents.values() for key in inc.key
        }
        out: List[Tuple[Diagnostic, str]] = []
        for diag in self._active_last:
            if diag.key in in_incidents:
                continue
            if diag.system is None:
                reason = "owner unknown; not grouped to avoid guessing"
            else:
                reason = "no co-occurring diagnostic on the same entity"
            out.append((diag, reason))
        return out

    @property
    def resolved(self) -> List[Incident]:
        return [
            i for i in self.history if i.state is IncidentState.RESOLVED
        ]

    # --- pairing / clustering ---------------------------------------------

    def _links(self, active: List[Diagnostic]) -> Dict[Tuple[str, str], Tuple[Tuple[str, ...], bool]]:
        links: Dict[Tuple[str, str], Tuple[Tuple[str, ...], bool]] = {}
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                link = self._link(active[i], active[j])
                if link is not None:
                    links[(active[i].key, active[j].key)] = link
        return links

    def _link(
        self, a: Diagnostic, b: Diagnostic
    ) -> Optional[Tuple[Tuple[str, ...], bool]]:
        """Return (strategies, uncertain) if a and b are correlation candidates."""
        if abs(a.timestamp - b.timestamp) > self.config.temporal_window_s:
            return None
        entity = (
            a.system is not None
            and a.system == b.system
            and a.robot == b.robot
        )
        if entity:
            strategies = {"entity", "temporal"}
            if _is_resource(a) != _is_resource(b):
                strategies.add("resource")
            if _shared_subject(a, b):
                strategies.add("shared_subject")
            return tuple(sorted(strategies)), False
        if a.system is None and b.system is None:
            # Ownerless linkage: both unowned, connected only by a shared
            # subject or a resource relationship. Genuinely uncertain.
            strategies = {"temporal"}
            if _shared_subject(a, b):
                strategies.add("shared_subject")
            if _is_resource(a) != _is_resource(b):
                strategies.add("resource")
            if len(strategies) > 1:
                return tuple(sorted(strategies)), True
        return None

    @staticmethod
    def _clusters(
        active: List[Diagnostic], links: Dict[Tuple[str, str], Tuple[Tuple[str, ...], bool]]
    ) -> List[List[Diagnostic]]:
        """Connected components of the pair-link graph."""
        neighbors: Dict[str, set] = {d.key: set() for d in active}
        for (ka, kb), _ in links.items():
            neighbors.setdefault(ka, set()).add(kb)
            neighbors.setdefault(kb, set()).add(ka)
        by_key = {d.key: d for d in active}
        seen: set = set()
        clusters: List[List[Diagnostic]] = []
        for start in by_key:
            if start in seen:
                continue
            stack = [start]
            cluster_keys: List[str] = []
            while stack:
                key = stack.pop()
                if key in seen:
                    continue
                seen.add(key)
                cluster_keys.append(key)
                stack.extend(neighbors.get(key, ()) - seen)
            clusters.append([by_key[k] for k in cluster_keys])
        return clusters

    # --- incident construction --------------------------------------------

    def _make_incident(
        self,
        members: List[Diagnostic],
        links: Dict[Tuple[str, str], Tuple[Tuple[str, ...], bool]],
        now: float,
    ) -> Incident:
        members = sorted(members, key=lambda d: d.timestamp)
        strategies: set = set()
        keys = [d.key for d in members]
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                pair = links.get((keys[i], keys[j]))
                if pair is not None:
                    strategies.update(pair[0])
        uncertain = any(d.system is None for d in members)
        if uncertain:
            confidence = Confidence.LOW
        elif "resource" in strategies or "shared_subject" in strategies:
            confidence = Confidence.HIGH
        else:
            confidence = Confidence.MEDIUM
        system = next((d.system for d in members if d.system), None)
        robot = next((d.robot for d in members if d.system), None)
        return Incident(
            members=tuple(members),
            strategies=tuple(sorted(strategies)),
            confidence=confidence,
            hypothesis=self._hypothesis(members, strategies, system, robot, uncertain),
            evidence=self._evidence(members, strategies, uncertain),
            system=system,
            robot=robot,
            attribution_uncertain=uncertain,
            created_at=now,
            updated_at=now,
        )

    def _hypothesis(
        self,
        members: List[Diagnostic],
        strategies: set,
        system: Optional[str],
        robot: Optional[str],
        uncertain: bool,
    ) -> str:
        owner = (
            f"{system}/{robot}"
            if system and robot
            else (system or "unattributed")
        )
        if "resource" in strategies:
            resource = [m for m in members if _is_resource(m)]
            behavioral = [m for m in members if not _is_resource(m)]
            rdesc = ", ".join(sorted({m.subject for m in resource})) or "resource pressure"
            bdesc = ", ".join(sorted({m.subject for m in behavioral}))
            return (
                f"On {owner}, resource conditions ({rdesc}) and behavioral "
                f"degradation ({bdesc}) co-occurred in a related time window. "
                f"Resource pressure may be a contributing factor. "
                f"Correlation is not causation; root cause is not determined."
            )
        return (
            f"{len(members)} active diagnostics on {owner} co-occurred within "
            f"the correlation window (signals: {', '.join(sorted(strategies))}). "
            f"They may be related. Correlation is not causation; "
            f"root cause is not determined."
        )

    def _evidence(self, members: List[Diagnostic], strategies: set, uncertain: bool) -> Tuple[str, ...]:
        lines = [
            f"members={len(members)}",
            f"signals={','.join(sorted(strategies))}",
            f"temporal_window={self.config.temporal_window_s:.0f}s",
        ]
        lines.extend(f"{d.rule_id}: {d.subject}" for d in members)
        if uncertain:
            lines.append(
                "uncertain: one or more diagnostics lack owner attribution; "
                "this grouping is low-confidence"
            )
        return tuple(lines)
