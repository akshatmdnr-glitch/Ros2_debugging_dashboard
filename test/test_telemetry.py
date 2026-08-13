"""Phase 3 telemetry tests: decision logic, rates, staleness, /proc, TF."""

import os
import subprocess
import sys
import time

from ros2_debugger.attribution import (
    AttributionConfig,
    Attributor,
    SystemModel,
)
from ros2_debugger.model import (
    EndpointInfo,
    GraphModel,
    NodeInfo,
    TopicInfo,
)
from ros2_debugger.telemetry import (
    ProcessMonitor,
    TelemetryConfig,
    TfStats,
    TopicMonitor,
)

WAREHOUSE_CONFIG = {
    "systems": {
        "warehouse": {
            "robots": {"robot1": ["/robot1"], "robot2": ["/robot2"]},
        },
        "slam": {"namespaces": ["/slam"]},
    }
}


def _node(name, ns):
    return NodeInfo(name=name, namespace=ns)


def _ep(node, etype="PUBLISHER"):
    return EndpointInfo(
        node=node,
        endpoint_type=etype,
        topic_type="x",
        reliability="RELIABLE",
        durability="VOLATILE",
        depth=10,
        deadline=0.0,
        lifespan=0.0,
        gid="g",
    )


def _graph_with(topics):
    graph = GraphModel()
    graph.sync_topics(topics, time.monotonic())
    return graph


class FakeCollector:
    """Stands in for the ROS-facing collector in decision/reconcile tests."""

    def __init__(self):
        self.subs = {}
        self.calls = []

    def ensure_topic_subscription(self, name, type_name, callback):
        if name in self.subs:
            return True
        self.subs[name] = callback
        self.calls.append(("ensure", name))
        return True

    def drop_topic_subscription(self, name):
        self.subs.pop(name, None)
        self.calls.append(("drop", name))


def _make_monitor(config_dict=None):
    return TopicMonitor(TelemetryConfig.from_dict(config_dict or {}))


def _attributed_model():
    config = AttributionConfig.from_dict(WAREHOUSE_CONFIG)
    model = SystemModel(Attributor(config))
    model.sync_nodes([_node("talker", "/robot1"), _node("talker", "/robot2")])
    return model


def test_monitor_subscribes_only_to_attributed_topics():
    model = _attributed_model()
    graph = _graph_with(
        [
            TopicInfo(
                "/robot1/chatter",
                ["std_msgs/msg/String"],
                publishers=[_ep(_node("talker", "/robot1"))],
            ),
            TopicInfo(
                "/chatter",
                ["std_msgs/msg/String"],
                publishers=[_ep(_node("talker", "/"))],
            ),
            TopicInfo(
                "/rosout", ["rcl_interfaces/msg/Log"],
                publishers=[_ep(_node("talker", "/robot1"))],
            ),
            TopicInfo(
                "/robot2/scan",
                ["sensor_msgs/msg/LaserScan"],
                publishers=[_ep(_node("talker", "/robot2"))],
            ),
        ]
    )
    collector = FakeCollector()
    monitor = _make_monitor()
    monitor.reconcile(graph, model, collector, time.monotonic())

    assert collector.subs.keys() == {"/robot1/chatter", "/robot2/scan"}
    stats = {s.topic: s for s in monitor.stats()}
    assert stats["/chatter"].reason == "unattributed"
    assert stats["/rosout"].reason == "infrastructure"
    assert stats["/robot1/chatter"].reason == "subscribed"


def test_monitor_respects_system_scope():
    config = AttributionConfig.from_dict(WAREHOUSE_CONFIG)
    model = SystemModel(Attributor(config))
    model.sync_nodes(
        [_node("talker", "/robot1"), _node("mapper", "/slam")]
    )
    graph = _graph_with(
        [
            TopicInfo(
                "/robot1/chatter",
                ["std_msgs/msg/String"],
                publishers=[_ep(_node("talker", "/robot1"))],
            ),
            TopicInfo(
                "/slam/map", ["nav_msgs/msg/OccupancyGrid"],
                publishers=[_ep(_node("mapper", "/slam"))],
            ),
        ]
    )
    collector = FakeCollector()
    monitor = _make_monitor({"monitor_systems": ["warehouse"]})
    monitor.reconcile(graph, model, collector, time.monotonic())
    assert "/robot1/chatter" in collector.subs
    assert "/slam/map" not in collector.subs
    stats = {s.topic: s for s in monitor.stats()}
    assert "not in monitor scope" in stats["/slam/map"].reason


def test_rate_and_staleness():
    model = _attributed_model()
    topic = TopicInfo(
        "/robot1/chatter", ["std_msgs/msg/String"],
        publishers=[_ep(_node("talker", "/robot1"))],
    )
    graph = _graph_with([topic])
    collector = FakeCollector()
    monitor = _make_monitor()

    t0 = time.monotonic()
    monitor.reconcile(graph, model, collector, t0)
    callback = collector.subs["/robot1/chatter"]

    for _ in range(20):
        callback(object())
    t1 = t0 + 1.0
    monitor.reconcile(graph, model, collector, t1)
    stat = {s.topic: s for s in monitor.stats()}["/robot1/chatter"]
    assert stat.receiving is True
    assert stat.message_count == 20
    assert 15.0 <= stat.rate_hz <= 25.0  # ~20 msgs over ~1s window

    # Publisher stops: no new messages.
    t2 = t1 + 2.0
    monitor.reconcile(graph, model, collector, t2)
    stat = {s.topic: s for s in monitor.stats()}["/robot1/chatter"]
    assert stat.rate_hz == 0.0
    assert stat.idle_seconds is not None and stat.idle_seconds >= 1.5
    assert stat.message_count == 20  # unchanged


def test_unsubscribe_when_topic_leaves_graph():
    model = _attributed_model()
    topic = TopicInfo(
        "/robot1/chatter", ["std_msgs/msg/String"],
        publishers=[_ep(_node("talker", "/robot1"))],
    )
    graph = _graph_with([topic])
    collector = FakeCollector()
    monitor = _make_monitor()
    monitor.reconcile(graph, model, collector, time.monotonic())
    assert "/robot1/chatter" in collector.subs

    graph.sync_topics([], time.monotonic())
    monitor.reconcile(graph, model, collector, time.monotonic())
    assert "/robot1/chatter" not in collector.subs
    stat = {s.topic: s for s in monitor.stats()}["/robot1/chatter"]
    assert stat.monitored is False


def test_process_monitor_liveness_and_resource():
    marker = f"rd2_proc_probe_{os.getpid()}"
    proc = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(60)  # {marker}"]
    )
    try:
        monitor = ProcessMonitor((marker,))
        monitor.sample(time.monotonic())
        (stat,) = monitor.stats()
        assert stat.alive is True
        assert stat.pids == [proc.pid]
        assert stat.rss_mb > 0.0
        # second sample establishes CPU delta baseline (no crash)
        monitor.sample(time.monotonic() + 1.0)
        (stat,) = monitor.stats()
        assert stat.alive is True
        assert stat.cpu_percent >= 0.0
    finally:
        proc.kill()
        proc.wait()
        monitor.sample(time.monotonic())
        (stat,) = monitor.stats()
        assert stat.alive is False


def test_tf_stats_freshness():
    tf = TfStats()
    now = 100.0
    tf.record("map", 10.0, now)
    tf.record("map", 10.1, now + 0.5)
    tf.record("odom", 20.0, now + 1.0)
    frames = {f.frame_id: f for f in tf.frames}
    assert frames["map"].count == 2
    assert frames["map"].last_stamp_sec == 10.1
    assert frames["odom"].count == 1
    assert tf.total_transforms == 3


def test_telemetry_config_parsing():
    cfg = TelemetryConfig.from_dict(
        {"monitor_systems": ["warehouse"], "monitor_topics": ["/a", "/b"],
         "processes": ["x"]}
    )
    assert cfg.monitor_systems == ("warehouse",)
    assert cfg.monitor_topics == ("/a", "/b")
    assert cfg.processes == ("x",)


def _unknown_node(name="_NODE_NAME_UNKNOWN_", ns="_NODE_NAMESPACE_UNKNOWN_"):
    return NodeInfo(name=name, namespace=ns)


def test_pending_endpoint_falls_back_to_topic_name_convention():
    from ros2_debugger.attribution import PENDING, SOURCE_CONVENTION

    model = _attributed_model()
    topic = TopicInfo(
        "/robot1/best", ["std_msgs/msg/String"],
        publishers=[_ep(_unknown_node())],
    )
    assert model.attribute_topic(topic) is PENDING
    # fallback via topic-name convention: low confidence, never confident
    attr = model.attributor.attribute_topic_name(topic.name)
    assert attr.system == "warehouse" and attr.robot == "robot1"
    assert attr.source == SOURCE_CONVENTION
    assert attr.confident is False


def test_monitor_recovers_topic_with_unknown_endpoint():
    model = _attributed_model()
    graph = _graph_with(
        [
            TopicInfo(
                "/robot1/best", ["std_msgs/msg/String"],
                publishers=[_ep(_unknown_node())],
            ),
            TopicInfo(
                "/best", ["std_msgs/msg/String"],
                publishers=[_ep(_unknown_node())],
            ),
        ]
    )
    collector = FakeCollector()
    monitor = _make_monitor()
    monitor.reconcile(graph, model, collector, time.monotonic())
    # /robot1/best matches the topic-name convention -> subscribed
    assert "/robot1/best" in collector.subs
    # /best has no convention match and no node evidence -> pending, retried
    stats = {s.topic: s for s in monitor.stats()}
    assert stats["/best"].reason == "waiting for publisher node info"
    assert stats["/best"].monitored is False

