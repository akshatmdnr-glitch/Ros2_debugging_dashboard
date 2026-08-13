"""Deterministic diagnostic engine (Phase 4).

Phases 1-3 observe: graph structure, attribution, telemetry, TF, processes.
Observation answers "what is happening?". Diagnosis answers "is that abnormal,
given explicit, configured expectations?".

This module consumes the observations and evaluates deterministic rules. It
never invents certainty: every diagnostic is produced by a named rule, backed
by evidence, and compared against a declared expectation. No AI, no history,
no root-cause analysis. Observation != Diagnosis != Root cause.

Recovery is a first-class property: a diagnostic that stops firing is marked
RESOLVED rather than lingering, so the debugger does not report a fixed fault.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, Iterator, List, Optional, Tuple

from ros2_debugger.model import GraphModel
from ros2_debugger.attribution import SOURCE_MIXED
from ros2_debugger.telemetry import TelemetryModel


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class DiagnosticState(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class Diagnostic:
    """A rule's verdict about an observed condition, with evidence."""

    rule_id: str
    severity: Severity
    message: str
    evidence: Tuple[str, ...]
    timestamp: float
    system: Optional[str] = None
    robot: Optional[str] = None
    node: Optional[str] = None
    topic: Optional[str] = None
    tf_frame: Optional[str] = None
    process: Optional[str] = None
    state: DiagnosticState = DiagnosticState.ACTIVE

    @property
    def key(self) -> Tuple:
        """Stable identity for dedupe/recovery (subject-scoped per rule)."""
        return (
            self.rule_id, self.system, self.robot, self.node, self.topic,
            self.tf_frame, self.process,
        )

    @property
    def subject(self) -> str:
        return (
            self.topic or self.node or self.tf_frame or self.process
            or self.system or "-"
        )

    def resolved(self, at: float) -> "Diagnostic":
        return replace(self, state=DiagnosticState.RESOLVED, timestamp=at)

    def with_owner(self, system: Optional[str], robot: Optional[str]) -> "Diagnostic":
        if self.system is not None:
            return self
        return replace(self, system=system, robot=robot)


@dataclass(frozen=True)
class TopicExpectation:
    min_hz: Optional[float] = None
    stale_after_s: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> "TopicExpectation":
        return cls(min_hz=data.get("min_hz"), stale_after_s=data.get("stale_after_s"))


@dataclass(frozen=True)
class DiagnosticConfig:
    """Explicit expectations the rules judge observations against."""

    topic_expectations: Dict[str, TopicExpectation] = field(default_factory=dict)
    stale_after_s_default: float = 5.0
    min_hz_default: Optional[float] = None
    required_tf_frames: Tuple[str, ...] = ()
    tf_stale_after_s: float = 3.0
    absence_grace_cycles: int = 3
    cpu_warn_percent: Optional[float] = None
    mem_warn_mb: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> "DiagnosticConfig":
        data = data or {}
        expectations = {
            name: TopicExpectation.from_dict(spec)
            for name, spec in (data.get("topic_expectations") or {}).items()
        }
        process = data.get("process_thresholds") or {}
        return cls(
            topic_expectations=expectations,
            stale_after_s_default=data.get("stale_after_s_default", 5.0),
            min_hz_default=data.get("min_hz_default"),
            required_tf_frames=tuple(data.get("required_tf_frames") or ()),
            tf_stale_after_s=data.get("tf_stale_after_s", 3.0),
            absence_grace_cycles=data.get("absence_grace_cycles", 3),
            cpu_warn_percent=process.get("cpu_warn_percent"),
            mem_warn_mb=process.get("mem_warn_mb"),
        )

    def expectation_for(self, topic: str) -> TopicExpectation:
        return self.topic_expectations.get(topic, TopicExpectation())


def _topic_owner(
    system_model, topic_name: str, graph: GraphModel
) -> Tuple[Optional[str], Optional[str]]:
    topic = graph.get_topic(topic_name)
    if topic is None:
        return None, None
    attr = system_model.attribute_topic(topic)
    if attr.is_unclassified or attr.source == SOURCE_MIXED:
        return None, None
    return attr.system, attr.robot


# --- rules ---------------------------------------------------------------

def rule_stale_topic(engine, graph, system_model, telemetry, now):
    """A monitored topic that once delivered messages has gone quiet."""
    for stat in telemetry.topics.stats():
        if not stat.monitored or not stat.receiving or stat.last_message_time is None:
            continue
        if stat.idle_seconds is None:
            continue
        expectation = engine.config.expectation_for(stat.topic)
        threshold = (
            expectation.stale_after_s
            if expectation.stale_after_s is not None
            else engine.config.stale_after_s_default
        )
        if not threshold or stat.idle_seconds < threshold:
            continue
        system, robot = _topic_owner(system_model, stat.topic, graph)
        yield Diagnostic(
            rule_id="stale_topic",
            severity=Severity.WARNING,
            topic=stat.topic,
            system=system,
            robot=robot,
            message=(
                f"{stat.topic} is stale: no message received for "
                f"{stat.idle_seconds:.1f}s (threshold {threshold:.1f}s)"
            ),
            evidence=(
                f"message_count={stat.message_count}",
                f"last_message={stat.idle_seconds:.1f}s ago",
                f"threshold={threshold:.1f}s",
            ),
            timestamp=now,
        )


def rule_frequency_degradation(engine, graph, system_model, telemetry, now):
    """A monitored topic is still receiving, but below its expected rate."""
    for stat in telemetry.topics.stats():
        if not stat.monitored or not stat.receiving or stat.rate_hz <= 0:
            continue
        if stat.idle_seconds is None:
            continue
        expectation = engine.config.expectation_for(stat.topic)
        min_hz = (
            expectation.min_hz
            if expectation.min_hz is not None
            else engine.config.min_hz_default
        )
        if min_hz is None:
            continue
        stale_threshold = (
            expectation.stale_after_s
            if expectation.stale_after_s is not None
            else engine.config.stale_after_s_default
        )
        if stale_threshold and stat.idle_seconds >= stale_threshold:
            continue  # a stopped topic is covered by stale_topic, not this
        if stat.rate_hz >= min_hz:
            continue
        system, robot = _topic_owner(system_model, stat.topic, graph)
        yield Diagnostic(
            rule_id="frequency_degradation",
            severity=Severity.WARNING,
            topic=stat.topic,
            system=system,
            robot=robot,
            message=(
                f"{stat.topic} frequency {stat.rate_hz:.2f} Hz is below the "
                f"expected minimum {min_hz:.1f} Hz"
            ),
            evidence=(
                f"observed={stat.rate_hz:.2f}Hz",
                f"expected_min={min_hz:.1f}Hz",
                f"idle={stat.idle_seconds:.1f}s",
            ),
            timestamp=now,
        )


def rule_topic_no_publisher(engine, graph, system_model, telemetry, now):
    """A monitored topic never delivered, judged against graph publisher state.

    zero publishers -> the expected publisher is absent;
    publisher(s) present -> something is publishing but we receive nothing
    (QoS mismatch is one possible explanation, not a verdict).

    Grace is PER-TOPIC (monitored_cycles): a topic must have been monitored for
    several cycles before we judge it, so a topic discovered mid-session still
    gets the same startup grace as one present from the start.
    """
    for stat in telemetry.topics.stats():
        if not stat.monitored or stat.receiving:
            continue
        if stat.monitored_cycles < engine.config.absence_grace_cycles:
            continue
        topic = graph.get_topic(stat.topic)
        if topic is None:
            continue  # no longer on the graph; handled by other rules
        pub_count = len(topic.publishers)
        system, robot = _topic_owner(system_model, stat.topic, graph)
        if pub_count == 0:
            yield Diagnostic(
                rule_id="missing_publisher",
                severity=Severity.WARNING,
                topic=stat.topic,
                system=system,
                robot=robot,
                message=f"{stat.topic} is expected but has no publisher on the graph",
                evidence=("subscribers present", "publishers=0", "no message received"),
                timestamp=now,
            )
        else:
            yield Diagnostic(
                rule_id="not_receiving",
                severity=Severity.WARNING,
                topic=stat.topic,
                system=system,
                robot=robot,
                message=(
                    f"{stat.topic} is monitored but no messages arrive despite "
                    f"{pub_count} publisher(s)"
                ),
                evidence=(
                    f"publishers={pub_count}",
                    "messages_received=0",
                    "possible causes: QoS mismatch, silent publisher, discovery lag",
                ),
                timestamp=now,
            )


def rule_node_disappeared(engine, graph, system_model, telemetry, now):
    """A previously-observed attributed node left the graph.

    Stronger evidence than a sampled telemetry number: a structural change.
    `_ever_seen` keeps every significant node we have observed; a node missing
    from the CURRENT set keeps firing until it returns, so the diagnostic stays
    ACTIVE while the node is gone and only RESOLVES on reappearance.
    """
    current: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for attributed in system_model.attributed_nodes():
        if not attributed.attribution.is_unclassified:
            current[attributed.fqn] = (
                attributed.attribution.system,
                attributed.attribution.robot,
            )
    for fqn, (system, robot) in engine._ever_seen.items():
        if fqn not in current:
            yield Diagnostic(
                rule_id="node_disappeared",
                severity=Severity.WARNING,
                node=fqn,
                system=system,
                robot=robot,
                message=f"{fqn} left the graph",
                evidence=(
                    f"previously attributed to {system}/{robot or 'system-level'}",
                    "node no longer discoverable",
                ),
                timestamp=now,
            )
    engine._ever_seen.update(current)


def rule_tf_required(engine, graph, system_model, telemetry, now):
    """Configured required TF frames must be fresh."""
    frames = {f.frame_id: f for f in telemetry.tf.frames}
    for frame in engine.config.required_tf_frames:
        entry = frames.get(frame)
        if entry is None:
            if engine.evaluation_count >= engine.config.absence_grace_cycles:
                yield Diagnostic(
                    rule_id="tf_missing",
                    severity=Severity.WARNING,
                    tf_frame=frame,
                    message=f"required TF frame '{frame}' has never been seen",
                    evidence=("frame listed in required_tf_frames", "no transform received"),
                    timestamp=now,
                )
        elif now - entry.last_seen > engine.config.tf_stale_after_s:
            yield Diagnostic(
                rule_id="tf_stale",
                severity=Severity.WARNING,
                tf_frame=frame,
                message=(
                    f"required TF frame '{frame}' is stale "
                    f"({now - entry.last_seen:.1f}s since last transform)"
                ),
                evidence=(
                    f"last_seen={now - entry.last_seen:.1f}s ago",
                    f"threshold={engine.config.tf_stale_after_s:.1f}s",
                ),
                timestamp=now,
            )


def rule_resource_overload(engine, graph, system_model, telemetry, now):
    """Process resource usage beyond configured thresholds.

    A relevant observation; explicitly NOT a root-cause claim about anything
    else.
    """
    cfg = engine.config
    for stat in telemetry.processes.stats():
        if not stat.alive:
            continue
        if cfg.cpu_warn_percent is not None and stat.cpu_percent > cfg.cpu_warn_percent:
            yield Diagnostic(
                rule_id="high_cpu",
                severity=Severity.WARNING,
                process=stat.pattern,
                message=(
                    f"process '{stat.pattern}' is using high CPU "
                    f"({stat.cpu_percent:.1f}%)"
                ),
                evidence=(
                    f"cpu={stat.cpu_percent:.1f}%",
                    f"threshold={cfg.cpu_warn_percent:.1f}%",
                    f"pids={stat.pids}",
                ),
                timestamp=now,
            )
        if cfg.mem_warn_mb is not None and stat.rss_mb > cfg.mem_warn_mb:
            yield Diagnostic(
                rule_id="high_memory",
                severity=Severity.WARNING,
                process=stat.pattern,
                message=(
                    f"process '{stat.pattern}' is using high memory "
                    f"({stat.rss_mb:.1f} MB)"
                ),
                evidence=(
                    f"rss={stat.rss_mb:.1f}MB",
                    f"threshold={cfg.mem_warn_mb:.1f}MB",
                ),
                timestamp=now,
            )


RULES = [
    rule_stale_topic,
    rule_frequency_degradation,
    rule_topic_no_publisher,
    rule_node_disappeared,
    rule_tf_required,
    rule_resource_overload,
]


class DiagnosticEngine:
    """Evaluates all rules each cycle and manages the ACTIVE/RESOLVED lifecycle."""

    def __init__(self, config: DiagnosticConfig) -> None:
        self.config = config
        self._active: Dict[Tuple, Diagnostic] = {}
        self._ever_seen: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        self.evaluation_count = 0
        self.history: List[Diagnostic] = []

    def evaluate(
        self, graph: GraphModel, system_model, telemetry: TelemetryModel, now: float
    ) -> List[Diagnostic]:
        """Run all rules; return new ACTIVE and RESOLVED events for this cycle."""
        self.evaluation_count += 1
        fires: Dict[Tuple, Diagnostic] = {}
        for rule in RULES:
            for diag in rule(self, graph, system_model, telemetry, now):
                fires[diag.key] = diag

        events: List[Diagnostic] = []
        for key in list(self._active):
            if key not in fires:
                resolved = self._active[key].resolved(now)
                del self._active[key]
                self.history.append(resolved)
                events.append(resolved)
        for key, diag in fires.items():
            if key not in self._active:
                self._active[key] = diag
                self.history.append(diag)
                events.append(diag)
        return events

    @property
    def active(self) -> List[Diagnostic]:
        return sorted(self._active.values(), key=lambda d: d.timestamp)

    @property
    def resolved(self) -> List[Diagnostic]:
        return [d for d in self.history if d.state is DiagnosticState.RESOLVED]
