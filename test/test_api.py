"""Phase 7 API tests: the backend adapter exposes the DebuggerApp state
without ROS, without duplicating collection, and with predictable responses."""

import inspect
from dataclasses import replace

import yaml
from fastapi.testclient import TestClient

from ros2_debugger.api import create_app
from ros2_debugger.app import DebuggerApp
from ros2_debugger.diagnostics import Diagnostic, DiagnosticState, Severity
from ros2_debugger.model import EndpointInfo, NodeInfo, TopicInfo
from ros2_debugger.telemetry import TopicStats

CONFIG = {
    "systems": {
        "warehouse": {
            "robots": {"robot1": ["/robot1"], "robot2": ["/robot2"]},
        }
    },
    "telemetry": {"monitor_systems": ["warehouse"]},
    "diagnostics": {
        "stale_after_s_default": 5.0,
        "topic_expectations": {
            "/robot2/scan": {"min_hz": 8.0, "stale_after_s": 2.0},
        },
        "required_tf_frames": [
            {"frame": "base_link", "system": "warehouse", "robot": "robot2"}
        ],
        "tf_stale_after_s": 3.0,
        "absence_grace_cycles": 1,
    },
    "correlation": {"temporal_window_s": 30.0, "min_members": 2},
}


def _config(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(CONFIG))
    return str(p)


def _client(tmp_path):
    app = DebuggerApp(config_path=_config(tmp_path), ros=False)
    return TestClient(create_app(app)), app


def _node(name, ns):
    return NodeInfo(name=name, namespace=ns)


def _ep(node):
    return EndpointInfo(
        node=node, endpoint_type="PUBLISHER", topic_type="x",
        reliability="RELIABLE", durability="VOLATILE", depth=10,
        deadline=0.0, lifespan=0.0, gid="g",
    )


def _seed_graph(app, now=100.0):
    nodes = [_node("lidar", "/robot2"), _node("talker", "/robot1")]
    app.graph.sync_nodes(nodes, now)
    app.system_model.sync_nodes(nodes)
    topic = TopicInfo(
        "/robot2/scan", ["sensor_msgs/msg/LaserScan"],
        publishers=[_ep(_node("lidar", "/robot2"))],
    )
    app.graph.sync_topics([topic], now)


def _seed_degraded(app, now=100.0):
    _seed_graph(app, now)
    stat = TopicStats(
        topic="/robot2/scan", type="sensor_msgs/msg/LaserScan",
        monitored=True, receiving=True, message_count=50,
        rate_hz=1.2, last_message_time=now, idle_seconds=0.4,
    )
    app.telemetry.topics._stats["/robot2/scan"] = stat
    app.diagnostic_engine.evaluate(app.graph, app.system_model, app.telemetry, now)


def _diag(rule, ts, topic=None, tf=None, process=None):
    return Diagnostic(
        rule_id=rule, severity=Severity.WARNING, message="m", evidence=("e",),
        timestamp=ts, system="warehouse", robot="robot2",
        topic=topic, tf_frame=tf, process=process,
        state=DiagnosticState.ACTIVE,
    )


def _resolved(diag, ts):
    return replace(diag, state=DiagnosticState.RESOLVED, timestamp=ts)


def _seed_incident(app, now=200.0):
    cpu = _diag("high_cpu", now, process="robot2_lidar_driver")
    scan = _diag("frequency_degradation", now + 1.0, topic="/robot2/scan")
    app.correlation_engine.update([cpu, scan], now + 5.0)
    app.history_engine.update([], app.correlation_engine.active, now + 5.0)


# --- startup / empty state ----------------------------------------------

def test_api_starts_and_health(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["active_diagnostics"] == 0
    assert body["active_incidents"] == 0
    assert body["nodes"] == 0 and body["topics"] == 0


def test_empty_state_returns_valid_empty_responses(tmp_path):
    client, _ = _client(tmp_path)
    for path in [
        "/systems", "/robots", "/nodes", "/topics", "/telemetry",
        "/diagnostics", "/correlation", "/incidents",
        "/incidents/active", "/incidents/history",
    ]:
        assert client.get(path).status_code == 200, path
    assert client.get("/diagnostics").json() == {"active": [], "resolved": []}
    assert client.get("/incidents/active").json() == []
    assert client.get("/incidents/history").json() == []
    assert client.get("/robots").json() == {"robots": []}
    assert client.get("/nodes").json() == {"nodes": []}


# --- structure ----------------------------------------------------------

def test_systems(tmp_path):
    client, app = _client(tmp_path)
    _seed_graph(app)
    body = client.get("/systems").json()
    warehouse = next(s for s in body["systems"] if s["name"] == "warehouse")
    robots = {r["name"]: r for r in warehouse["robots"]}
    assert "/robot1/talker" in robots["robot1"]["nodes"]
    assert "/robot2/lidar" in robots["robot2"]["nodes"]


def test_robots(tmp_path):
    client, app = _client(tmp_path)
    _seed_graph(app)
    robots = client.get("/robots").json()["robots"]
    assert {r["name"] for r in robots} == {"robot1", "robot2"}
    assert {r["system"] for r in robots} == {"warehouse"}


def test_nodes_and_topics(tmp_path):
    client, app = _client(tmp_path)
    _seed_graph(app)
    nodes = {n["fqn"]: n for n in client.get("/nodes").json()["nodes"]}
    assert nodes["/robot2/lidar"]["robot"] == "robot2"
    assert nodes["/robot1/talker"]["system"] == "warehouse"
    topics = client.get("/topics").json()["topics"]
    scan = next(t for t in topics if t["name"] == "/robot2/scan")
    assert scan["publishers"] == 1 and scan["subscribers"] == 0


# --- telemetry / diagnostics --------------------------------------------

def test_telemetry(tmp_path):
    client, app = _client(tmp_path)
    _seed_degraded(app)
    body = client.get("/telemetry").json()
    topics = {t["topic"]: t for t in body["topics"]}
    assert topics["/robot2/scan"]["rate_hz"] == 1.2
    assert topics["/robot2/scan"]["receiving"] is True
    assert body["processes"] == [] and body["tf"] == []


def test_diagnostics(tmp_path):
    client, app = _client(tmp_path)
    _seed_degraded(app)
    body = client.get("/diagnostics").json()
    active = {d["rule_id"]: d for d in body["active"]}
    assert "frequency_degradation" in active
    scan = active["frequency_degradation"]
    assert scan["topic"] == "/robot2/scan"
    assert scan["system"] == "warehouse" and scan["robot"] == "robot2"
    assert scan["state"] == "ACTIVE"
    assert scan["evidence"]  # evidence travels with the verdict


# --- incidents ----------------------------------------------------------

def test_active_incidents(tmp_path):
    client, app = _client(tmp_path)
    _seed_incident(app)
    active = client.get("/incidents/active").json()
    assert len(active) == 1
    inc = active[0]
    assert inc["state"] == "ACTIVE"
    assert inc["owner"] == "warehouse/robot2"
    assert inc["member_count"] == 2
    assert len(inc["events"]) == 2


def test_incident_history_and_timeline(tmp_path):
    client, app = _client(tmp_path)
    _seed_incident(app)
    cpu = _diag("high_cpu", 200.0, process="robot2_lidar_driver")
    scan = _diag("frequency_degradation", 201.0, topic="/robot2/scan")
    app.history_engine.update([_resolved(cpu, 220.0), _resolved(scan, 221.0)], [], 220.0)

    assert client.get("/incidents/active").json() == []
    history = client.get("/incidents/history").json()
    assert len(history) == 1
    inc = history[0]
    assert inc["state"] == "RECOVERED"
    assert inc["duration"] is not None
    assert [e["transition"] for e in inc["events"]] == [
        "ACTIVATED", "ACTIVATED", "RECOVERED", "RECOVERED",
    ]


def test_incident_detail(tmp_path):
    client, app = _client(tmp_path)
    _seed_incident(app)
    r = client.get("/incidents/1")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 1
    assert body["events"][0]["transition"] == "ACTIVATED"


def test_incidents_combined(tmp_path):
    client, app = _client(tmp_path)
    _seed_incident(app)
    body = client.get("/incidents").json()
    assert len(body["active"]) == 1
    assert body["history"] == []


def test_correlation(tmp_path):
    client, app = _client(tmp_path)
    _seed_incident(app)
    body = client.get("/correlation").json()
    assert len(body["active"]) == 1
    group = body["active"][0]
    assert group["owner"] == "warehouse/robot2"
    assert "hypothesis" in group and "not causation" in group["hypothesis"]


# --- errors / validation ------------------------------------------------

def test_unknown_resource_404(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/incidents/9999").status_code == 404
    assert client.get("/definitely-not-an-endpoint").status_code == 404


def test_invalid_request(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/incidents/not-an-int").status_code == 422
    assert client.post("/incidents/active").status_code == 405


# --- single source of truth ---------------------------------------------

def test_state_updates_reflected(tmp_path):
    client, app = _client(tmp_path)
    assert client.get("/incidents/active").json() == []

    _seed_incident(app)
    assert len(client.get("/incidents/active").json()) == 1

    cpu = _diag("high_cpu", 200.0, process="robot2_lidar_driver")
    scan = _diag("frequency_degradation", 201.0, topic="/robot2/scan")
    app.history_engine.update([_resolved(cpu, 220.0), _resolved(scan, 221.0)], [], 220.0)

    assert client.get("/incidents/active").json() == []
    assert len(client.get("/incidents/history").json()) == 1
    # health counts follow the same app state
    health = client.get("/health").json()
    assert health["active_incidents"] == 0


# --- architecture boundary ----------------------------------------------

def test_api_does_not_collect_ros_data(tmp_path):
    import ros2_debugger.api as api_mod
    import ros2_debugger.app as app_mod
    # The HTTP adapter itself (create_app) must not collect ROS: no rclpy,
    # no collector, no subscriptions. It only reads the injected app.
    create_src = inspect.getsource(api_mod.create_app)
    assert "rclpy" not in create_src
    assert "CollectorNode" not in create_src
    assert "create_subscription" not in create_src
    assert "ensure_topic_subscription" not in create_src
    # Collection wiring lives in the app composition root.
    assert "CollectorNode()" in inspect.getsource(app_mod)


# --- CORS / demo (Phase 8 frontend support) -----------------------------

def test_cors_allows_frontend_origin(tmp_path):
    from ros2_debugger.api import create_app as _create_app

    app = DebuggerApp(config_path=_config(tmp_path), ros=False)
    client = TestClient(_create_app(app, cors_origins=("http://localhost:5173",)))
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_demo_seed_populates_state(tmp_path):
    from ros2_debugger.api import seed_demo

    app = DebuggerApp(config_path=_config(tmp_path), ros=False)
    seed_demo(app)
    client = TestClient(create_app(app))

    health = client.get("/health").json()
    assert health["active_diagnostics"] >= 3
    assert health["active_incidents"] == 1

    diags = {d["rule_id"] for d in client.get("/diagnostics").json()["active"]}
    assert {"high_cpu", "frequency_degradation", "tf_stale"} <= diags

    incidents = client.get("/incidents/active").json()
    assert len(incidents) == 1
    assert incidents[0]["owner"] == "warehouse/robot2"

    robots = client.get("/robots").json()["robots"]
    robot2 = next(r for r in robots if r["name"] == "robot2")
    assert robot2["active_diagnostics"] >= 3
    robot1 = next(r for r in robots if r["name"] == "robot1")
    assert robot1["active_diagnostics"] == 0
