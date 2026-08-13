"""Phase 2 attribution tests: scenarios S1-S7 plus topic attribution."""

import time

from ros2_debugger.attribution import (
    Attribution,
    AttributionConfig,
    Attributor,
    MIXED,
    SystemModel,
    UNCLASSIFIED,
)
from ros2_debugger.model import (
    ChangeKind,
    EndpointInfo,
    GraphEvent,
    NodeInfo,
    TopicInfo,
)

WAREHOUSE_CONFIG = {
    "systems": {
        "warehouse": {
            "namespaces": ["/warehouse"],
            "robots": {
                "robot1": ["/robot1"],
                "robot2": ["/robot2"],
                "robot3": ["/robot3"],
            },
            "nodes": ["/fleet_manager"],
        },
        "slam": {
            "namespaces": ["/slam"],
            "nodes": ["/rviz"],
        },
    }
}


def _node(name: str, namespace: str = "/") -> NodeInfo:
    return NodeInfo(name=name, namespace=namespace)


def _make_model() -> SystemModel:
    config = AttributionConfig.from_dict(WAREHOUSE_CONFIG)
    return SystemModel(Attributor(config))


def _event(kind, node):
    return GraphEvent(time.monotonic(), kind, node=node)


# --- Scenario 1: clear namespace ------------------------------------------


def test_scenario1_clear_namespace():
    model = _make_model()
    model.sync_nodes([_node("lidar", "/robot1"), _node("navigation", "/robot1")])
    lidar = model.attributed_nodes()[0]
    assert lidar.fqn == "/robot1/lidar"
    assert lidar.attribution.system == "warehouse"
    assert lidar.attribution.robot == "robot1"
    assert lidar.attribution.source == "config"
    assert lidar.attribution.confident is True
    assert len(model.nodes_in_robot("warehouse", "robot1")) == 2


# --- Scenario 2: multiple robots -------------------------------------------


def test_scenario2_multiple_robots():
    model = _make_model()
    model.sync_nodes(
        [_node("lidar", "/robot1"), _node("lidar", "/robot2"), _node("lidar", "/robot3")]
    )
    assert len(model.nodes_in_system("warehouse")) == 3
    assert len(model.nodes_in_robot("warehouse", "robot1")) == 1
    assert len(model.nodes_in_robot("warehouse", "robot2")) == 1
    assert len(model.nodes_in_robot("warehouse", "robot3")) == 1
    robots = {a.attribution.robot for a in model.nodes_in_system("warehouse")}
    assert robots == {"robot1", "robot2", "robot3"}


# --- Scenario 3: same domain, two systems ----------------------------------


def test_scenario3_two_systems_same_domain():
    model = _make_model()
    model.sync_nodes(
        [_node("lidar", "/robot1"), _node("fleet_manager", "/"), _node("lidar", "/slam")]
    )
    assert set(model.system_names()) == {"warehouse", "slam"}
    assert len(model.nodes_in_system("warehouse")) == 2
    assert len(model.nodes_in_system("slam")) == 1
    fleet = [a for a in model.nodes_in_system("warehouse") if a.fqn == "/fleet_manager"][0]
    assert fleet.attribution.robot is None  # system-level, not a robot
    # SLAM lidar must not be swallowed by the warehouse
    assert len(model.nodes_in_robot("warehouse", "robot1")) == 1


# --- Scenario 4: ambiguous node stays UNCLASSIFIED --------------------------


def test_scenario4_ambiguous_node_unclassified():
    model = _make_model()
    model.sync_nodes([_node("lidar_node")])
    (only,) = model.attributed_nodes()
    assert only.attribution is UNCLASSIFIED
    assert only.attribution.is_unclassified
    assert only.attribution.confident is False
    assert model.unclassified_nodes() == [only]
    assert model.nodes_in_system("warehouse") == []
    assert model.nodes_in_system("slam") == []


# --- Scenario 5: new node appears after start -------------------------------


def test_scenario5_new_node_appears():
    model = _make_model()
    model.sync_nodes([_node("lidar", "/robot1")])
    model.handle_graph_event(_event(ChangeKind.NODE_ADDED, _node("navigation", "/robot1")))
    assert [a.fqn for a in model.nodes_in_robot("warehouse", "robot1")] == [
        "/robot1/lidar",
        "/robot1/navigation",
    ]


# --- Scenario 6: node disappears without corrupting the rest -----------------


def test_scenario6_node_disappears():
    model = _make_model()
    model.sync_nodes(
        [
            _node("lidar", "/robot1"),
            _node("navigation", "/robot1"),
            _node("lidar", "/robot2"),
            _node("mystery"),
        ]
    )
    model.handle_graph_event(_event(ChangeKind.NODE_REMOVED, _node("lidar", "/robot1")))
    assert [a.fqn for a in model.nodes_in_robot("warehouse", "robot1")] == [
        "/robot1/navigation"
    ]
    assert [a.fqn for a in model.nodes_in_robot("warehouse", "robot2")] == [
        "/robot2/lidar"
    ]
    assert len(model.unclassified_nodes()) == 1  # /mystery untouched


# --- Scenario 7: unrelated environment is not mixed in -----------------------


def test_scenario7_unrelated_environment_not_mixed():
    model = _make_model()
    model.sync_nodes(
        [_node("lidar", "/robot1"), _node("odom", "/drone"), _node("scan", "/somewhere")]
    )
    assert len(model.nodes_in_system("warehouse")) == 1
    assert len(model.nodes_in_system("slam")) == 0
    assert len(model.unclassified_nodes()) == 2
    assert {a.fqn for a in model.unclassified_nodes()} == {"/drone/odom", "/somewhere/scan"}


# --- attribution of topics via endpoint nodes --------------------------------


def _endpoint(node: NodeInfo, topic: str, etype: str) -> EndpointInfo:
    return EndpointInfo(
        node=node,
        endpoint_type=etype,
        topic_type="x",
        reliability="RELIABLE",
        durability="VOLATILE",
        depth=10,
        deadline=0.0,
        lifespan=0.0,
        gid="gid",
    )


def test_topic_single_owner_goes_to_its_robot():
    model = _make_model()
    r1 = _node("lidar", "/robot1")
    model.sync_nodes([r1])
    topic = TopicInfo(
        "/robot1/scan",
        ["sensor_msgs/msg/LaserScan"],
        publishers=[_endpoint(r1, "/robot1/scan", "PUBLISHER")],
    )
    assert model.attribute_topic(topic) == Attribution(
        "warehouse", "robot1", "config", True
    )


def test_topic_shared_across_owners_is_mixed():
    model = _make_model()
    r1 = _node("lidar", "/robot1")
    r2 = _node("lidar", "/robot2")
    model.sync_nodes([r1, r2])
    topic = TopicInfo(
        "/tf",
        ["tf2_msgs/msg/TFMessage"],
        publishers=[
            _endpoint(r1, "/tf", "PUBLISHER"),
            _endpoint(r2, "/tf", "PUBLISHER"),
        ],
    )
    assert model.attribute_topic(topic) is MIXED


def test_topic_with_unknown_endpoints_unclassified():
    model = _make_model()
    unknown = _node("lidar_node")
    model.sync_nodes([unknown])
    topic = TopicInfo(
        "/scan",
        ["sensor_msgs/msg/LaserScan"],
        publishers=[_endpoint(unknown, "/scan", "PUBLISHER")],
    )
    assert model.attribute_topic(topic) is UNCLASSIFIED


# --- config handling ---------------------------------------------------------


def test_config_accepts_dict_robot_form():
    config = AttributionConfig.from_dict(
        {
            "systems": {
                "warehouse": {
                    "robots": {
                        "robot1": {"namespaces": ["/robot1"], "nodes": ["/r1_gui"]}
                    }
                }
            }
        }
    )
    attributor = Attributor(config)
    assert attributor.attribute(_node("r1_gui")).robot == "robot1"
    assert attributor.attribute(_node("lidar", "/robot1")).robot == "robot1"


def test_no_config_means_everything_unclassified():
    model = SystemModel(Attributor(AttributionConfig()))
    model.sync_nodes([_node("lidar", "/robot1")])
    (only,) = model.attributed_nodes()
    assert only.attribution.is_unclassified
    assert model.system_names() == []
