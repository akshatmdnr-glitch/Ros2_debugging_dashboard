"""Unit tests for the DDS-agnostic GraphModel (no ROS needed)."""

import time

from ros2_debugger.model import (
    ChangeKind,
    GraphModel,
    NodeInfo,
    TopicInfo,
)


def _node(name, namespace="/"):
    return NodeInfo(name=name, namespace=namespace)


def _topic(name, types, publishers=(), subscribers=()):
    return TopicInfo(name=name, types=types, publishers=publishers, subscribers=subscribers)


def test_node_add_then_remove():
    model = GraphModel()
    now = time.monotonic()
    model.sync_nodes([_node("talker")], now)
    events = model.drain_events()
    assert [e.kind for e in events] == [ChangeKind.NODE_ADDED]
    assert events[0].node.fully_qualified_name == "/talker"

    model.sync_nodes([], now)
    events = model.drain_events()
    assert [e.kind for e in events] == [ChangeKind.NODE_REMOVED]


def test_node_no_change_no_event():
    model = GraphModel()
    now = time.monotonic()
    model.sync_nodes([_node("a"), _node("b")], now)
    assert len(model.drain_events()) == 2
    model.sync_nodes([_node("a"), _node("b")], now)
    assert model.drain_events() == []


def test_topic_add_update_remove():
    model = GraphModel()
    now = time.monotonic()
    model.sync_topics([_topic("/chatter", ["std_msgs/msg/String"])], now)
    assert [e.kind for e in model.drain_events()] == [ChangeKind.TOPIC_ADDED]

    # Different endpoint signature -> update
    model.sync_topics([_topic("/chatter", ["std_msgs/msg/String"])], now)
    assert model.drain_events() == []

    model.sync_topics([], now)
    assert [e.kind for e in model.drain_events()] == [ChangeKind.TOPIC_REMOVED]


def test_fully_qualified_name():
    assert _node("odom", "/").fully_qualified_name == "/odom"
    assert _node("odom", "/robot2").fully_qualified_name == "/robot2/odom"
    assert _node("odom", "/robot2/").fully_qualified_name == "/robot2/odom"
