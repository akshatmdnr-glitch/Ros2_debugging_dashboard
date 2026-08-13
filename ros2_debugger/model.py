"""DDS-agnostic model of the running ROS 2 graph.

The collector feeds this model; later analysis layers consume it. Nothing in
this module imports rclpy/rmw/DDS types, so the model stays a clean contract
between the ROS-facing collector and anything downstream (Phase 0, Decision D5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class ChangeKind(str, Enum):
    NODE_ADDED = "node_added"
    NODE_REMOVED = "node_removed"
    TOPIC_ADDED = "topic_added"
    TOPIC_REMOVED = "topic_removed"
    TOPIC_UPDATED = "topic_updated"


@dataclass(frozen=True)
class NodeInfo:
    name: str
    namespace: str

    @property
    def fully_qualified_name(self) -> str:
        if self.namespace == "/":
            return "/" + self.name
        return f"{self.namespace.rstrip('/')}/{self.name}"


@dataclass(frozen=True)
class EndpointInfo:
    node: NodeInfo
    endpoint_type: str  # "PUBLISHER" | "SUBSCRIBER"
    topic_type: str
    reliability: str
    durability: str
    depth: int
    deadline: float  # seconds; 0.0 = not set
    lifespan: float  # seconds; 0.0 = not set
    gid: str


@dataclass(frozen=True)
class TopicInfo:
    name: str
    types: List[str]
    publishers: List[EndpointInfo] = field(default_factory=list)
    subscribers: List[EndpointInfo] = field(default_factory=list)

    @property
    def primary_type(self) -> Optional[str]:
        return self.types[0] if self.types else None


@dataclass(frozen=True)
class GraphEvent:
    timestamp: float  # seconds, time.monotonic()
    kind: ChangeKind
    node: Optional[NodeInfo] = None
    topic: Optional[TopicInfo] = None


class GraphModel:
    """Flat snapshot of the graph plus a FIFO of change events.

    The collector calls sync_*() with freshly queried state; this class diffs
    against its previous snapshot, updates itself, and records GraphEvents.
    Consumers drain_events() between snapshots to get an event stream even
    though the underlying collector may poll.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, NodeInfo] = {}
        self._topics: Dict[str, TopicInfo] = {}
        self._events: List[GraphEvent] = []

    @property
    def nodes(self) -> List[NodeInfo]:
        return sorted(self._nodes.values(), key=lambda n: n.fully_qualified_name)

    @property
    def topics(self) -> List[TopicInfo]:
        return sorted(self._topics.values(), key=lambda t: t.name)

    def has_node(self, fully_qualified_name: str) -> bool:
        return fully_qualified_name in self._nodes

    def has_topic(self, name: str) -> bool:
        return name in self._topics

    def get_topic(self, name: str) -> Optional[TopicInfo]:
        return self._topics.get(name)

    def sync_nodes(self, nodes: List[NodeInfo], now: float) -> None:
        incoming = {n.fully_qualified_name: n for n in nodes}
        for fqn, existing in list(self._nodes.items()):
            if fqn not in incoming:
                del self._nodes[fqn]
                self._events.append(
                    GraphEvent(now, ChangeKind.NODE_REMOVED, node=existing)
                )
        for fqn, node in incoming.items():
            if fqn not in self._nodes:
                self._nodes[fqn] = node
                self._events.append(
                    GraphEvent(now, ChangeKind.NODE_ADDED, node=node)
                )

    def sync_topics(self, topics: List[TopicInfo], now: float) -> None:
        def signature(t: TopicInfo):
            pubs = tuple(sorted((e.gid, e.topic_type, e.reliability, e.durability) for e in t.publishers))
            subs = tuple(sorted((e.gid, e.topic_type, e.reliability, e.durability) for e in t.subscribers))
            return (t.name, tuple(sorted(t.types)), pubs, subs)

        incoming = {t.name: t for t in topics}
        for name in list(self._topics):
            if name not in incoming:
                removed = self._topics.pop(name)
                self._events.append(
                    GraphEvent(now, ChangeKind.TOPIC_REMOVED, topic=removed)
                )
        for name, topic in incoming.items():
            if name not in self._topics:
                self._topics[name] = topic
                self._events.append(
                    GraphEvent(now, ChangeKind.TOPIC_ADDED, topic=topic)
                )
            elif signature(self._topics[name]) != signature(topic):
                self._topics[name] = topic
                self._events.append(
                    GraphEvent(now, ChangeKind.TOPIC_UPDATED, topic=topic)
                )

    def drain_events(self) -> List[GraphEvent]:
        out, self._events = self._events, []
        return out
