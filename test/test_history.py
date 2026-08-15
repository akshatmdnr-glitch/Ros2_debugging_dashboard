"""Phase 6 incident-history tests: creation, update, ordering, recovery,
partial recovery, repeated incidents, multiple robots, empty system, rapid
changes, restart behavior, and ownerless scoping."""

from dataclasses import replace

from ros2_debugger.correlation import Confidence, Incident as CorrelationIncident
from ros2_debugger.diagnostics import Diagnostic, DiagnosticState, Severity
from ros2_debugger.history import (
    HistoryEngine,
    IncidentSession,
    LifecycleState,
    MemberTransition,
)


def _diag(
    rule_id,
    ts,
    system="warehouse",
    robot="robot2",
    topic=None,
    node=None,
    tf_frame=None,
    process=None,
):
    return Diagnostic(
        rule_id=rule_id,
        severity=Severity.WARNING,
        message="m",
        evidence=("e",),
        timestamp=ts,
        system=system,
        robot=robot,
        topic=topic,
        node=node,
        tf_frame=tf_frame,
        process=process,
        state=DiagnosticState.ACTIVE,
    )


def _resolved(diag, ts):
    return replace(diag, state=DiagnosticState.RESOLVED, timestamp=ts)


def _group(
    *members,
    system="warehouse",
    robot="robot2",
    strategies=("entity", "temporal"),
    confidence=Confidence.MEDIUM,
):
    return CorrelationIncident(
        members=tuple(members),
        strategies=strategies,
        confidence=confidence,
        hypothesis="h",
        evidence=("e",),
        system=system,
        robot=robot,
        attribution_uncertain=False,
    )


# --- creation / update ---------------------------------------------------

def test_incident_creation():
    eng = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    events = eng.update([], [_group(cpu, scan)], 200.0)
    assert len(events) == 1
    s = events[0]
    assert isinstance(s, IncidentSession)
    assert s.state is LifecycleState.ACTIVE
    assert s.started_at == 100.0  # earliest member activation
    assert s.member_count == 2
    assert s.owner == "warehouse/robot2"
    assert s.incident_id == 1


def test_incident_update_new_member():
    eng = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    first = eng.update([], [_group(cpu, scan)], 200.0)[0]
    assert len(eng.active) == 1

    # A new related diagnostic (TF stale) joins the same group.
    tf = _diag("tf_stale", 104.0, tf_frame="base_link")
    events = eng.update([], [_group(cpu, scan, tf)], 201.0)
    session = eng.active[0]
    # SAME incident, not a new one -- membership evolved.
    assert session.incident_id == first.incident_id
    assert session.member_count == 3
    assert any(
        e.key == tf.key and e.transition is MemberTransition.ACTIVATED
        for e in session.events
    )
    assert session.state is LifecycleState.ACTIVE


# --- ordering / recovery -------------------------------------------------

def test_event_ordering_preserved():
    eng = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    tf = _diag("tf_stale", 104.0, tf_frame="base_link")
    eng.update([], [_group(cpu, scan, tf)], 200.0)
    eng.update([_resolved(tf, 120.0)], [], 201.0)
    eng.update([_resolved(scan, 130.0)], [], 202.0)
    eng.update([_resolved(cpu, 140.0)], [], 203.0)

    (s,) = eng.closed
    timeline = [(e.transition, e.key) for e in s.events]
    assert timeline == [
        (MemberTransition.ACTIVATED, cpu.key),
        (MemberTransition.ACTIVATED, scan.key),
        (MemberTransition.ACTIVATED, tf.key),
        (MemberTransition.RECOVERED, tf.key),
        (MemberTransition.RECOVERED, scan.key),
        (MemberTransition.RECOVERED, cpu.key),
    ]
    assert s.state is LifecycleState.RECOVERED
    assert s.duration == 40.0  # 140 - 100


def test_full_recovery():
    eng = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    eng.update([], [_group(cpu, scan)], 200.0)
    assert eng.active[0].state is LifecycleState.ACTIVE

    eng.update([_resolved(cpu, 120.0)], [], 201.0)
    assert eng.active[0].state is LifecycleState.RECOVERING
    eng.update([_resolved(scan, 130.0)], [], 202.0)

    assert eng.active == []
    assert len(eng.closed) == 1
    s = eng.closed[0]
    assert s.state is LifecycleState.RECOVERED
    assert s.duration == 30.0


def test_partial_recovery_not_falsely_recovered():
    eng = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    tf = _diag("tf_stale", 104.0, tf_frame="base_link")
    eng.update([], [_group(cpu, scan, tf)], 200.0)

    # One member recovers; two remain active -> RECOVERING, not RECOVERED.
    eng.update([_resolved(tf, 120.0)], [], 201.0)
    s = eng.active[0]
    assert s.state is LifecycleState.RECOVERING
    assert s.active_count == 2 and s.member_count == 3
    assert eng.closed == []

    # Another recovers; scan still active -> still not RECOVERED.
    eng.update([_resolved(cpu, 125.0)], [], 202.0)
    s = eng.active[0]
    assert s.state is LifecycleState.RECOVERING
    assert s.active_count == 1
    assert eng.closed == []

    # The last one recovers -> finally RECOVERED.
    eng.update([_resolved(scan, 130.0)], [], 203.0)
    assert eng.active == []
    assert len(eng.closed) == 1


# --- separation ----------------------------------------------------------

def test_repeated_incident_separate_occurrences():
    eng = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    first = eng.update([], [_group(cpu, scan)], 200.0)[0]
    eng.update([_resolved(cpu, 120.0), _resolved(scan, 130.0)], [], 201.0)
    assert len(eng.closed) == 1

    # The same symptoms recur much later -> a NEW occurrence.
    cpu2 = _diag("high_cpu", 400.0, process="nav")
    scan2 = _diag("frequency_degradation", 402.0, topic="/robot2/scan")
    second = eng.update([], [_group(cpu2, scan2)], 500.0)[0]
    assert second.incident_id != first.incident_id
    assert second.started_at == 400.0
    assert len(eng.closed) == 1 and len(eng.active) == 1


def test_multiple_robots_separate_incidents():
    eng = HistoryEngine()
    r1a = _diag("high_cpu", 100.0, system="warehouse", robot="robot1", process="nav1")
    r1b = _diag("stale_topic", 102.0, system="warehouse", robot="robot1",
                topic="/robot1/scan")
    r2a = _diag("high_cpu", 100.0, system="warehouse", robot="robot2", process="nav2")
    r2b = _diag("frequency_degradation", 103.0, system="warehouse", robot="robot2",
                topic="/robot2/scan")
    eng.update(
        [],
        [
            _group(r1a, r1b, system="warehouse", robot="robot1"),
            _group(r2a, r2b, system="warehouse", robot="robot2"),
        ],
        200.0,
    )
    assert len(eng.active) == 2
    assert {s.owner for s in eng.active} == {"warehouse/robot1", "warehouse/robot2"}


def test_empty_system_no_incidents():
    eng = HistoryEngine()
    assert eng.update([], [], 100.0) == []
    assert eng.active == []
    assert eng.closed == []
    assert eng.all == []


def test_ownerless_incident_scoped_by_subject():
    eng = HistoryEngine()
    a = _diag("tf_stale", 100.0, system=None, robot=None, tf_frame="base_link")
    b = _diag("tf_missing", 101.0, system=None, robot=None, tf_frame="base_link")
    g = CorrelationIncident(
        members=(a, b),
        strategies=("temporal", "shared_subject"),
        confidence=Confidence.LOW,
        hypothesis="h",
        evidence=("e",),
        system=None,
        robot=None,
        attribution_uncertain=True,
    )
    eng.update([], [g], 200.0)
    assert len(eng.active) == 1
    assert eng.active[0].owner == "unattributed"

    # A different ownerless subject -> a separate incident, never merged.
    c = _diag("stale_topic", 200.0, system=None, robot=None, topic="/other/a")
    d = _diag("frequency_degradation", 201.0, system=None, robot=None, topic="/other/a")
    g2 = CorrelationIncident(
        members=(c, d),
        strategies=("temporal", "shared_subject"),
        confidence=Confidence.LOW,
        hypothesis="h",
        evidence=("e",),
        system=None,
        robot=None,
        attribution_uncertain=True,
    )
    eng.update([], [g2], 300.0)
    assert len(eng.active) == 2


# --- rapid change / restart ----------------------------------------------

def test_rapid_activation_and_recovery():
    eng = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    eng.update([], [_group(cpu, scan)], 200.0)

    # CPU recovers, then re-activates quickly.
    eng.update([_resolved(cpu, 110.0)], [], 201.0)
    assert eng.active[0].state is LifecycleState.RECOVERING
    eng.update([], [_group(_diag("high_cpu", 115.0, process="nav"), scan)], 202.0)
    s = eng.active[0]
    assert s.state is LifecycleState.ACTIVE  # re-activated, scan still active
    assert s.active_count == 2
    timeline = [e.transition for e in s.events]
    assert timeline.count(MemberTransition.ACTIVATED) == 3  # cpu, scan, cpu
    assert timeline.count(MemberTransition.RECOVERED) == 1

    # Both eventually recover -> closed, single occurrence.
    eng.update(
        [_resolved(_diag("high_cpu", 120.0, process="nav"), 120.0), _resolved(scan, 122.0)],
        [],
        203.0,
    )
    assert eng.active == []
    (s,) = eng.closed
    assert s.state is LifecycleState.RECOVERED
    assert s.duration == 22.0  # 122 - 100


def test_restart_resets_in_memory_history():
    eng1 = HistoryEngine()
    cpu = _diag("high_cpu", 100.0, process="nav")
    scan = _diag("frequency_degradation", 102.0, topic="/robot2/scan")
    eng1.update([], [_group(cpu, scan)], 200.0)
    assert len(eng1.active) == 1

    # A fresh engine is a restarted debugger: no memory of the past.
    eng2 = HistoryEngine()
    assert eng2.update([], [], 200.0) == []
    assert eng2.active == [] and eng2.closed == []
