"""Phase 5 correlation tests: grouping, confidence, uncertainty, recovery,
and the optional owner evidence added to CPU/TF diagnostics."""

import time

from ros2_debugger.attribution import (
    AttributionConfig,
    Attributor,
    SystemModel,
)
from ros2_debugger.correlation import (
    Confidence,
    CorrelationConfig,
    CorrelationEngine,
    IncidentState,
)
from ros2_debugger.diagnostics import (
    Diagnostic,
    DiagnosticConfig,
    DiagnosticEngine,
    RequiredTfFrame,
    Severity,
)
from ros2_debugger.model import GraphModel
from ros2_debugger.telemetry import (
    FrameStats,
    ProcessStats,
    TelemetryConfig,
    TelemetryModel,
)

WAREHOUSE_CONFIG = {
    "systems": {
        "warehouse": {
            "robots": {"robot1": ["/robot1"], "robot2": ["/robot2"]},
        }
    }
}


def _diag(
    rule_id,
    system="warehouse",
    robot="robot2",
    topic=None,
    node=None,
    tf_frame=None,
    process=None,
    timestamp=100.0,
):
    return Diagnostic(
        rule_id=rule_id,
        severity=Severity.WARNING,
        message="m",
        evidence=("e",),
        timestamp=timestamp,
        system=system,
        robot=robot,
        topic=topic,
        node=node,
        tf_frame=tf_frame,
        process=process,
    )


def _engine(window=30.0, min_members=2):
    return CorrelationEngine(
        CorrelationConfig(temporal_window_s=window, min_members=min_members)
    )


def _attributed_model():
    config = AttributionConfig.from_dict(WAREHOUSE_CONFIG)
    return SystemModel(Attributor(config))


# --- grouping ------------------------------------------------------------

def test_unrelated_diagnostics_stay_separate():
    eng = _engine()
    cpu = _diag("high_cpu", system="warehouse", robot="robot1",
                process="nav1", timestamp=100.0)
    scan = _diag("frequency_degradation", system="warehouse", robot="robot2",
                 topic="/robot2/scan", timestamp=101.0)
    # Different robots, same time window -> never merged.
    assert eng.update([cpu, scan], 200.0) == []
    assert eng.active == []
    # Both reported as uncorrelated with a reason.
    assert len(eng.uncorrelated) == 2


def test_same_robot_correlates_medium():
    eng = _engine()
    a = _diag("stale_topic", topic="/robot2/scan", timestamp=100.0)
    b = _diag("frequency_degradation", topic="/robot2/cmd", timestamp=102.0)
    events = eng.update([a, b], 200.0)
    assert len(events) == 1
    inc = events[0]
    assert inc.state is IncidentState.ACTIVE
    assert inc.confidence is Confidence.MEDIUM
    assert "entity" in inc.strategies and "temporal" in inc.strategies
    assert inc.system == "warehouse" and inc.robot == "robot2"
    assert len(inc.members) == 2


def test_temporal_window_excludes_far_onsets():
    eng = _engine(window=30.0)
    a = _diag("stale_topic", topic="/robot2/scan", timestamp=100.0)
    b = _diag("frequency_degradation", topic="/robot2/cmd", timestamp=140.0)
    assert eng.update([a, b], 200.0) == []
    assert eng.active == []


def test_cpu_topic_relationship_resource_hypothesis():
    eng = _engine()
    cpu = _diag("high_cpu", process="robot2/nav", timestamp=100.0)
    scan = _diag("frequency_degradation", topic="/robot2/scan", timestamp=101.0)
    events = eng.update([cpu, scan], 200.0)
    inc = events[0]
    assert "resource" in inc.strategies
    assert inc.confidence is Confidence.HIGH
    assert "contributing factor" in inc.hypothesis
    assert "not causation" in inc.hypothesis


def test_multiple_robots_keep_separate_incidents():
    eng = _engine()
    r1a = _diag("high_cpu", system="warehouse", robot="robot1",
                process="nav1", timestamp=100.0)
    r1b = _diag("stale_topic", system="warehouse", robot="robot1",
                topic="/robot1/scan", timestamp=101.0)
    r2 = _diag("frequency_degradation", system="warehouse", robot="robot2",
               topic="/robot2/scan", timestamp=101.0)
    events = eng.update([r1a, r1b, r2], 200.0)
    assert len(events) == 1
    assert len(eng.active) == 1
    inc = eng.active[0]
    assert {d.robot for d in inc.members} == {"robot1"}
    assert r2.key not in inc.key


def test_shared_subject_bumps_confidence():
    # Two degraded topics on the same node -> dependency proxy, HIGH.
    eng = _engine()
    a = _diag("stale_topic", topic="/robot2/scan", node="/robot2/nav", timestamp=100.0)
    b = _diag("frequency_degradation", topic="/robot2/cmd", node="/robot2/nav", timestamp=102.0)
    inc = eng.update([a, b], 200.0)[0]
    assert "shared_subject" in inc.strategies
    assert inc.confidence is Confidence.HIGH


# --- uncertainty ---------------------------------------------------------

def test_ambiguous_ownerless_evidence_low_confidence():
    eng = _engine()
    a = _diag("tf_stale", system=None, robot=None, tf_frame="base_link", timestamp=100.0)
    b = _diag("tf_missing", system=None, robot=None, tf_frame="base_link", timestamp=101.0)
    events = eng.update([a, b], 200.0)
    assert len(events) == 1
    inc = events[0]
    assert inc.attribution_uncertain is True
    assert inc.confidence is Confidence.LOW
    # Unlinked ownerless diagnostics are NOT grouped (no guessing).
    c = _diag("stale_topic", system=None, robot=None, topic="/other", timestamp=100.0)
    d = _diag("stale_topic", system=None, robot=None, topic="/other2", timestamp=101.0)
    eng2 = _engine()
    assert eng2.update([c, d], 200.0) == []
    assert len(eng2.uncorrelated) == 2


def test_owned_and_unowned_never_pair():
    eng = _engine()
    a = _diag("high_cpu", process="nav", timestamp=100.0)
    b = _diag("frequency_degradation", system=None, robot=None,
              topic="/robot2/scan", timestamp=101.0)
    assert eng.update([a, b], 200.0) == []
    assert eng.active == []


# --- recovery ------------------------------------------------------------

def test_incident_recovers_when_members_resolve():
    eng = _engine()
    a = _diag("high_cpu", process="nav", timestamp=100.0)
    b = _diag("frequency_degradation", topic="/robot2/scan", timestamp=101.0)
    assert len(eng.update([a, b], 200.0)) == 1
    assert len(eng.active) == 1

    # CPU recovers: only the scan diagnostic remains -> incident resolves.
    events = eng.update([b], 201.0)
    resolved = [e for e in events if e.state is IncidentState.RESOLVED]
    assert len(resolved) == 1
    assert resolved[0].resolved_at is not None
    assert eng.active == []
    # The remaining diagnostic is reported, not silently grouped.
    assert len(eng.uncorrelated) == 1


# --- no false root cause -------------------------------------------------

def test_hypotheses_never_claim_root_cause():
    forbidden_claims = ("caused", "causes", "broken", "definitely", "proves",
                        "is the root cause", "is responsible for")
    scenarios = [
        [
            _diag("high_cpu", process="nav", timestamp=100.0),
            _diag("frequency_degradation", topic="/robot2/scan", timestamp=101.0),
        ],
        [
            _diag("stale_topic", topic="/robot1/chatter", timestamp=100.0),
            _diag("node_disappeared", node="/robot1/lidar", timestamp=102.0),
        ],
    ]
    for active in scenarios:
        eng = _engine()
        for inc in eng.update(active, 200.0):
            low = inc.hypothesis.lower()
            # Must explicitly deny causation, never claim it.
            assert "not causation" in low, inc.hypothesis
            assert all(w not in low for w in forbidden_claims), inc.hypothesis


# --- optional owner evidence (diagnostics extension) ---------------------

def test_process_config_optional_owners():
    cfg = TelemetryConfig.from_dict(
        {
            "processes": [
                {"pattern": "nav", "system": "warehouse", "robot": "robot2"},
                "plain",
            ]
        }
    )
    assert cfg.processes == ("nav", "plain")
    assert cfg.process_owners["nav"] == ("warehouse", "robot2")
    assert "plain" not in cfg.process_owners


def test_required_tf_frame_optional_owners():
    cfg = DiagnosticConfig.from_dict(
        {
            "required_tf_frames": [
                {"frame": "odom", "system": "warehouse", "robot": "robot2"},
                "base_link",
            ],
            "process_thresholds": {"cpu_warn_percent": 80.0},
        }
    )
    frames = {f.frame: f for f in cfg.required_tf_frames}
    assert isinstance(frames["odom"], RequiredTfFrame)
    assert frames["odom"].system == "warehouse" and frames["odom"].robot == "robot2"
    assert frames["base_link"].system is None and frames["base_link"].robot is None


def test_high_cpu_diagnostic_carries_owner():
    tele_cfg = TelemetryConfig.from_dict(
        {"processes": [{"pattern": "nav", "system": "warehouse", "robot": "robot2"}]}
    )
    tele = TelemetryModel(tele_cfg)
    tele.processes._stats["nav"] = ProcessStats(
        pattern="nav", pids=[1], alive=True, cpu_percent=95.0, rss_mb=10.0
    )
    engine = DiagnosticEngine(
        DiagnosticConfig.from_dict({"process_thresholds": {"cpu_warn_percent": 80.0}})
    )
    events = engine.evaluate(GraphModel(), _attributed_model(), tele, time.monotonic())
    diags = [d for d in events if d.rule_id == "high_cpu"]
    assert diags
    assert diags[0].system == "warehouse" and diags[0].robot == "robot2"


def test_tf_stale_diagnostic_carries_owner():
    cfg = DiagnosticConfig.from_dict(
        {
            "required_tf_frames": [
                {"frame": "base_link", "system": "warehouse", "robot": "robot2"}
            ],
            "tf_stale_after_s": 3.0,
        }
    )
    engine = DiagnosticEngine(cfg)
    tele = TelemetryModel(TelemetryConfig())
    tele.tf._frames["base_link"] = FrameStats(
        frame_id="base_link", count=1, last_seen=time.monotonic() - 10.0
    )
    events = engine.evaluate(GraphModel(), _attributed_model(), tele, time.monotonic())
    diags = [d for d in events if d.rule_id == "tf_stale"]
    assert diags
    assert diags[0].system == "warehouse" and diags[0].robot == "robot2"
