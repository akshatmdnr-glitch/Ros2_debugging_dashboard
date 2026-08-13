"""Phase 4 diagnostics tests: healthy, stale, degradation, missing publisher,
node disappearance, TF, resources, and recovery."""

import time

from ros2_debugger.attribution import (
    AttributionConfig,
    Attributor,
    SystemModel,
)
from ros2_debugger.diagnostics import (
    DiagnosticConfig,
    DiagnosticEngine,
    DiagnosticState,
    Severity,
)
from ros2_debugger.model import (
    EndpointInfo,
    GraphModel,
    NodeInfo,
    TopicInfo,
)
from ros2_debugger.telemetry import (
    FrameStats,
    ProcessStats,
    TelemetryConfig,
    TelemetryModel,
    TopicStats,
)

WAREHOUSE_CONFIG = {
    "systems": {
        "warehouse": {
            "robots": {"robot1": ["/robot1"]},
        }
    }
}

DIAG_CONFIG = {
    "stale_after_s_default": 5.0,
    "topic_expectations": {
        "/robot1/scan": {"min_hz": 8.0, "stale_after_s": 2.0},
    },
    "required_tf_frames": ["odom"],
    "tf_stale_after_s": 3.0,
    "absence_grace_cycles": 3,
    "process_thresholds": {"cpu_warn_percent": 80.0, "mem_warn_mb": 1024.0},
}


def _node(name, ns="/robot1"):
    return NodeInfo(name=name, namespace=ns)


def _ep(node):
    return EndpointInfo(
        node=node, endpoint_type="PUBLISHER", topic_type="x",
        reliability="RELIABLE", durability="VOLATILE", depth=10,
        deadline=0.0, lifespan=0.0, gid="g",
    )


def _graph(scan_pub=True, scan_sub=False):
    topic = TopicInfo(
        "/robot1/scan",
        ["sensor_msgs/msg/LaserScan"],
        publishers=[_ep(_node("lidar"))] if scan_pub else [],
        subscribers=[_ep(_node("mapper"))] if scan_sub else [],
    )
    graph = GraphModel()
    graph.sync_topics([topic], time.monotonic())
    return graph


def _attributed_model(with_lidar=True):
    config = AttributionConfig.from_dict(WAREHOUSE_CONFIG)
    model = SystemModel(Attributor(config))
    if with_lidar:
        model.sync_nodes([_node("lidar"), _node("mapper")])
    return model


def _telemetry(topic_stats=None, process_stats=None, frames=None):
    model = TelemetryModel(TelemetryConfig())
    if topic_stats:
        for stat in topic_stats:
            model.topics._stats[stat.topic] = stat
    if process_stats:
        for stat in process_stats:
            model.processes._stats[stat.pattern] = stat
    if frames:
        for stat in frames:
            model.tf._frames[stat.frame_id] = stat
    return model


def _healthy_topic_stat():
    return TopicStats(
        topic="/robot1/scan", type="sensor_msgs/msg/LaserScan",
        monitored=True, receiving=True, message_count=100,
        rate_hz=10.0, last_message_time=time.monotonic(), idle_seconds=0.1,
        publisher_reliability="RELIABLE", publisher_durability="VOLATILE",
    )


def _healthy_telemetry(stat=None):
    """Telemetry with a healthy topic AND a fresh required TF frame, so no
    rule has anything to complain about."""
    return _telemetry(
        [stat if stat is not None else _healthy_topic_stat()],
        frames=[FrameStats(frame_id="odom", count=5, last_seen=time.monotonic())],
    )


def _firing_rules(engine, graph, model, telemetry):
    """Evaluate one cycle; return the rule_ids of newly-ACTIVE diagnostics."""
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    return {d.rule_id for d in events if d.state is DiagnosticState.ACTIVE}


def test_healthy_system_produces_no_diagnostics():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model()
    telemetry = _healthy_telemetry()
    for _ in range(5):
        assert engine.evaluate(graph, model, telemetry, time.monotonic()) == []
    assert engine.active == []


def test_stale_topic_fires_and_resolves():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model()
    stat = _healthy_topic_stat()
    telemetry = _healthy_telemetry(stat)

    stat.idle_seconds = 5.0  # gone quiet well past stale_after_s=2
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    assert {d.rule_id for d in events} == {"stale_topic"}
    stale = [d for d in events if d.rule_id == "stale_topic"][0]
    assert stale.severity is Severity.WARNING
    assert stale.topic == "/robot1/scan"
    assert stale.system == "warehouse" and stale.robot == "robot1"
    assert stale.evidence

    # Recovery: messages resume -> the diagnostic resolves.
    stat.idle_seconds = 0.1
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    resolved = [d for d in events if d.state is DiagnosticState.RESOLVED]
    assert [d.rule_id for d in resolved] == ["stale_topic"]
    assert engine.active == []


def test_frequency_degradation_fires():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model()
    stat = _healthy_topic_stat()
    stat.rate_hz = 1.2  # below min_hz 8.0 but still active (idle small)
    stat.idle_seconds = 0.4
    telemetry = _telemetry([stat])
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    assert {d.rule_id for d in events} == {"frequency_degradation"}
    diag = events[0]
    assert "1.20 Hz" in diag.message and "8.0 Hz" in diag.message
    # The message must NOT claim root cause.
    assert "broken" not in diag.message and "hardware" not in diag.message


def test_no_expectation_means_no_degradation():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model()
    # /robot1/other has no expectation configured -> never judged
    stat = TopicStats(
        topic="/robot1/other", monitored=True, receiving=True,
        message_count=10, rate_hz=0.1, last_message_time=time.monotonic(),
        idle_seconds=0.3,
    )
    telemetry = _telemetry([stat])
    assert _firing_rules(engine, graph, model, telemetry) == set()


def test_missing_publisher_fires_after_grace():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph(scan_pub=False)  # no publisher on the graph
    model = _attributed_model()
    stat = TopicStats(
        topic="/robot1/scan", monitored=True, receiving=False, message_count=0,
    )
    telemetry = _telemetry([stat])
    # Grace is per-topic: before enough monitored cycles, no diagnostic.
    assert _firing_rules(engine, graph, model, telemetry) == set()
    stat.monitored_cycles = DIAG_CONFIG["absence_grace_cycles"] - 1
    assert _firing_rules(engine, graph, model, telemetry) == set()
    stat.monitored_cycles = DIAG_CONFIG["absence_grace_cycles"]
    assert "missing_publisher" in _firing_rules(engine, graph, model, telemetry)


def test_not_receiving_with_publisher():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph(scan_pub=True)  # publisher exists
    model = _attributed_model()
    stat = TopicStats(
        topic="/robot1/scan", monitored=True, receiving=False, message_count=0,
        monitored_cycles=DIAG_CONFIG["absence_grace_cycles"],
    )
    telemetry = _telemetry([stat])
    assert "not_receiving" in _firing_rules(engine, graph, model, telemetry)


def test_node_disappearance_fires_and_recovers():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model(with_lidar=True)
    telemetry = _healthy_telemetry()

    engine.evaluate(graph, model, telemetry, time.monotonic())  # seed
    model.sync_nodes([_node("mapper")])  # lidar node leaves the graph
    graph.sync_nodes([_node("mapper")], time.monotonic())
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    assert {d.rule_id for d in events} == {"node_disappeared"}
    diag = events[0]
    assert diag.node == "/robot1/lidar"
    assert diag.system == "warehouse" and diag.robot == "robot1"

    # While the node stays gone, the diagnostic STAYS active (not re-emitted).
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    assert events == []
    assert [d.rule_id for d in engine.active] == ["node_disappeared"]

    # Recovery: the node comes back.
    model.sync_nodes([_node("lidar"), _node("mapper")])
    graph.sync_nodes([_node("lidar"), _node("mapper")], time.monotonic())
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    assert any(d.state is DiagnosticState.RESOLVED for d in events)
    assert engine.active == []


def test_tf_missing_then_stale_then_recovers():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model()
    telemetry = _telemetry([])

    # Never seen: fires after grace.
    for _ in range(DIAG_CONFIG["absence_grace_cycles"] - 1):
        engine.evaluate(graph, model, telemetry, time.monotonic())
    assert "tf_missing" in _firing_rules(engine, graph, model, telemetry)

    # Now the frame appears, recent.
    now = time.monotonic()
    telemetry = _telemetry(frames=[FrameStats(frame_id="odom", count=5, last_seen=now)])
    assert _firing_rules(engine, graph, model, telemetry) == set()

    # Then goes stale.
    telemetry = _telemetry(frames=[FrameStats(frame_id="odom", count=5, last_seen=now - 10)])
    assert "tf_stale" in _firing_rules(engine, graph, model, telemetry)


def test_high_cpu_fires_with_healthy_and_overload():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model()
    healthy = ProcessStats(pattern="lidar", pids=[1], alive=True, cpu_percent=2.0, rss_mb=50.0)
    assert _firing_rules(engine, graph, model, _telemetry(process_stats=[healthy])) == set()

    overloaded = ProcessStats(pattern="lidar", pids=[1], alive=True, cpu_percent=95.0, rss_mb=50.0)
    assert "high_cpu" in _firing_rules(engine, graph, model, _telemetry(process_stats=[overloaded]))

    fat = ProcessStats(pattern="lidar", pids=[1], alive=True, cpu_percent=2.0, rss_mb=2048.0)
    assert "high_memory" in _firing_rules(engine, graph, model, _telemetry(process_stats=[fat]))


def test_engine_deduplicates_repeated_fires():
    engine = DiagnosticEngine(DiagnosticConfig.from_dict(DIAG_CONFIG))
    graph = _graph()
    model = _attributed_model()
    stat = _healthy_topic_stat()
    stat.idle_seconds = 5.0
    telemetry = _healthy_telemetry(stat)

    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    assert len([d for d in events if d.state is DiagnosticState.ACTIVE]) == 1
    events = engine.evaluate(graph, model, telemetry, time.monotonic())
    assert events == []  # still active, not re-emitted
    assert len(engine.active) == 1
