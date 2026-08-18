"""ROS-facing collector node (Phase 0 Decisions D1, D4, D6).

The collector is a normal ROS 2 node, so it joins whatever DDS domain the
current shell environment selects via ROS_DOMAIN_ID. It maintains a
GraphModel (poll + diff internally, but emits GraphEvents outward), and it
observes /rosout and the TF topics as ordinary subscriptions.
"""

from __future__ import annotations

import importlib
import os
import time
from typing import Callable, Dict, List

import rclpy
from rcl_interfaces.msg import Log
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from rclpy.topic_endpoint_info import TopicEndpointInfo
from tf2_msgs.msg import TFMessage

from ros2_debugger.model import (
    EndpointInfo,
    GraphEvent,
    GraphModel,
    NodeInfo,
    TopicInfo,
)

GraphEventHandler = Callable[[GraphEvent], None]
LogHandler = Callable[[Log], None]
TfTransformHandler = Callable[[str, str, float, bool], None]  # parent, child, stamp_sec, is_static
PostRefreshHandler = Callable[[], None]


def _enum_name(value) -> str:
    return getattr(value, "name", str(value))


def _resolve_message_class(type_name: str):
    """'std_msgs/msg/String' -> std_msgs.msg.String (or None)."""
    parts = type_name.split("/")
    if len(parts) != 3:
        return None
    pkg, msg_part, cls = parts
    try:
        module = importlib.import_module(f"{pkg}.{msg_part}")
        return getattr(module, cls)
    except Exception:
        return None


class CollectorNode(Node):
    def __init__(self, graph_period: float = 1.0) -> None:
        super().__init__("debugger_collector")
        self.model = GraphModel()
        self.graph_event_handlers: List[GraphEventHandler] = []
        self.log_handlers: List[LogHandler] = []
        self.tf_transform_handlers: List[TfTransformHandler] = []
        self.post_refresh_handlers: List[PostRefreshHandler] = []
        self._telemetry_subs: Dict[str, "rclpy.subscription.Subscription"] = {}

        self._graph_timer = self.create_timer(graph_period, self._refresh_graph)

        rosout_qos = QoSProfile(
            depth=1000,
            history=HistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._rosout_sub = self.create_subscription(
            Log, "/rosout", self._on_rosout, rosout_qos
        )
        # Caveat (verified on Jazzy): rclpy publishes Python logging levels on
        # /rosout (DEBUG=10, INFO=20, WARN=30), offset +10 from the ROS 2
        # standard (DEBUG=0, INFO=10, WARN=20) used by rclcpp. The graph gives
        # no sender-language field, so a raw level of 20 is ambiguous
        # (rclpy INFO vs rclcpp WARN). We pass raw levels through and let the
        # analysis layer decide how to normalize -- guessing here would
        # mislabel severity.

        # /tf is volatile; /tf_static is transient_local. We match each one's
        # real contract so the observation channel is honest (Decision D6).
        tf_qos = QoSProfile(
            depth=1000,
            history=HistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        tf_static_qos = QoSProfile(
            depth=1000,
            history=HistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._tf_sub = self.create_subscription(
            TFMessage, "/tf", lambda m: self._on_tf(m, False), tf_qos
        )
        self._tf_static_sub = self.create_subscription(
            TFMessage, "/tf_static", lambda m: self._on_tf(m, True), tf_static_qos
        )

        # The normal use case is joining an ALREADY-running system, so the
        # first snapshot is the "initial discovery burst". Handlers are attached
        # after construction, so we buffer witnessed events and flush them when
        # a handler subscribes -- never silently drop the baseline (a debugger
        # that loses the burst looks like "nothing was there when I arrived").
        self._pending_events: List[GraphEvent] = []
        self._refresh_graph()

    # --- graph ---------------------------------------------------------

    def _refresh_graph(self) -> None:
        now = time.monotonic()
        self.model.sync_nodes(self._collect_nodes(), now)
        self.model.sync_topics(self._collect_topics(), now)
        events = self.model.drain_events()
        if self.graph_event_handlers:
            for event in events:
                for handler in self.graph_event_handlers:
                    handler(event)
        else:
            self._pending_events.extend(events)
        for handler in self.post_refresh_handlers:
            handler()

    def flush_pending_events(self) -> None:
        """Deliver events witnessed before handlers were attached."""
        for event in self._pending_events:
            for handler in self.graph_event_handlers:
                handler(event)
        self._pending_events.clear()

    def _collect_nodes(self) -> List[NodeInfo]:
        return [
            NodeInfo(name=name, namespace=namespace)
            for name, namespace in self.get_node_names_and_namespaces()
        ]

    def _collect_topics(self) -> List[TopicInfo]:
        topics: List[TopicInfo] = []
        for name, types in self.get_topic_names_and_types():
            topics.append(
                TopicInfo(
                    name=name,
                    types=types,
                    publishers=[
                        self._endpoint(tei)
                        for tei in self.get_publishers_info_by_topic(name)
                    ],
                    subscribers=[
                        self._endpoint(tei)
                        for tei in self.get_subscriptions_info_by_topic(name)
                    ],
                )
            )
        return topics

    @staticmethod
    def _endpoint(tei: TopicEndpointInfo) -> EndpointInfo:
        qp = tei.qos_profile
        return EndpointInfo(
            node=NodeInfo(name=tei.node_name, namespace=tei.node_namespace),
            endpoint_type=_enum_name(tei.endpoint_type),
            topic_type=tei.topic_type,
            reliability=_enum_name(qp.reliability),
            durability=_enum_name(qp.durability),
            depth=qp.depth,
            deadline=qp.deadline.nanoseconds / 1e9 if qp.deadline else 0.0,
            lifespan=qp.lifespan.nanoseconds / 1e9 if qp.lifespan else 0.0,
            gid=tei.endpoint_gid,
        )

    # --- subscriptions ---------------------------------------------------

    # --- telemetry subscriptions (Phase 3) -------------------------------

    def ensure_topic_subscription(self, topic_name: str, type_name: str, callback) -> bool:
        """Create a passive monitoring subscription for a topic.

        QoS is BEST_EFFORT/VOLATILE: maximally compatible with every publisher
        (reliable and best-effort alike) and it never imposes retries or
        latched-history buffering on the observed system.
        """
        if topic_name in self._telemetry_subs:
            return True
        msg_class = _resolve_message_class(type_name)
        if msg_class is None:
            return False
        qos = QoSProfile(
            depth=100,
            history=HistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        sub = self.create_subscription(msg_class, topic_name, callback, qos)
        self._telemetry_subs[topic_name] = sub
        return True

    def drop_topic_subscription(self, topic_name: str) -> None:
        sub = self._telemetry_subs.pop(topic_name, None)
        if sub is not None:
            self.destroy_subscription(sub)

    def _on_rosout(self, msg: Log) -> None:
        for handler in self.log_handlers:
            handler(msg)

    def _on_tf(self, msg: TFMessage, is_static: bool) -> None:
        for transform in msg.transforms:
            stamp = transform.header.stamp
            stamp_sec = stamp.sec + stamp.nanosec / 1e9
            # Pass BOTH sides of the transform so the analysis layer can build
            # the frame tree (parent -> child) as well as per-frame freshness.
            for handler in self.tf_transform_handlers:
                handler(
                    transform.header.frame_id,
                    transform.child_frame_id,
                    stamp_sec,
                    is_static,
                )

    @property
    def domain_id(self) -> str:
        # rclpy exposes no get_domain_id() in Jazzy; the participant domain is
        # decided by this env var (default 0) at rclpy.init() time.
        return os.environ.get("ROS_DOMAIN_ID", "0")

    @property
    def rmw_identifier(self) -> str:
        return rclpy.get_rmw_implementation_identifier()
