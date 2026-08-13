"""Attribution layer (Phase 2).

The collector produces a flat, honest snapshot of the running ROS 2 graph
(Phase 1). Attribution is the first ANALYSIS capability: it imposes the
debugger's OWN logical organization (System -> Robot -> Node) on top of that
flat graph. This module is deliberately free of ROS/rclpy imports -- it is a
pure decision procedure over NodeInfo values, driven by explicit configuration
plus the namespaces/names the collector reports (Phase 0, Decision D5).

Design rules:
  * We never invent ownership. A node is attributed ONLY when explicit
    configuration matches it (namespace prefix or exact node name). Anything
    else is UNCLASSIFIED.
  * Namespaces are the SIGNAL we match on; configuration is the TRUTH. The
    graph cannot say "/robot1 belongs to Warehouse"; only config can.
  * Every attribution records its source and whether it is confident, so
    later diagnostics can audit the reasoning instead of trusting it blindly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ros2_debugger.model import ChangeKind, GraphEvent, NodeInfo, TopicInfo

SOURCE_CONFIG = "config"
SOURCE_CONVENTION = "convention"
SOURCE_MIXED = "mixed"

# rclpy reports these for endpoints whose node association has not resolved
# (an rmw discovery race). They are NOT "unclassified" -- they are "unknown
# for now", so we must retry rather than permanently discard the topic.
UNKNOWN_NODE_MARKERS = ("_NODE_NAME_UNKNOWN_", "_NODE_NAMESPACE_UNKNOWN_")


@dataclass(frozen=True)
class Attribution:
    """The debugger's answer to "who owns this entity?"."""

    system: Optional[str]
    robot: Optional[str]
    source: str  # provenance: why we believe this ("" if unclassified)
    confident: bool  # strong evidence only (config matches); never guessed

    @property
    def is_unclassified(self) -> bool:
        return self.system is None

    @property
    def key(self) -> Tuple[Optional[str], Optional[str]]:
        return (self.system, self.robot)

    def __str__(self) -> str:
        if self.is_unclassified:
            return "UNCLASSIFIED"
        if self.robot:
            return f"{self.system}/{self.robot}"
        return self.system


UNCLASSIFIED = Attribution(None, None, "", False)
MIXED = Attribution(None, None, SOURCE_MIXED, False)
# Endpoint node info is temporarily unavailable (rmw discovery race). The
# topic's owner cannot be decided yet; retry next cycle. Not a verdict.
PENDING = Attribution(None, None, "pending", False)


def _parts(fqn: str) -> Tuple[str, ...]:
    """Normalize a fully-qualified name into non-empty components."""
    return tuple(p for p in fqn.split("/") if p)


def _as_str_list(value) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class RobotConfig:
    name: str
    namespaces: Tuple[str, ...] = ()
    nodes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SystemConfig:
    name: str
    namespaces: Tuple[str, ...] = ()
    nodes: Tuple[str, ...] = ()
    robots: Tuple[RobotConfig, ...] = ()


@dataclass(frozen=True)
class AttributionConfig:
    """Declarative mapping from namespace/node patterns to System/Robot.

    The config is the ground truth for structure. It may name systems that
    are not currently running; an empty system is simply not rendered.
    """

    systems: Dict[str, SystemConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "AttributionConfig":
        systems: Dict[str, SystemConfig] = {}
        for name, spec in (data.get("systems") or {}).items():
            robots: List[RobotConfig] = []
            for rname, rspec in (spec.get("robots") or {}).items():
                if isinstance(rspec, dict):
                    robots.append(
                        RobotConfig(
                            name=rname,
                            namespaces=_as_str_list(rspec.get("namespaces")),
                            nodes=_as_str_list(rspec.get("nodes")),
                        )
                    )
                else:
                    robots.append(
                        RobotConfig(name=rname, namespaces=_as_str_list(rspec))
                    )
            systems[name] = SystemConfig(
                name=name,
                namespaces=_as_str_list(spec.get("namespaces")),
                nodes=_as_str_list(spec.get("nodes")),
                robots=tuple(robots),
            )
        return cls(systems=systems)

    @property
    def system_names(self) -> List[str]:
        return sorted(self.systems)


@dataclass(frozen=True)
class _NamespaceRule:
    parts: Tuple[str, ...]
    system: str
    robot: Optional[str]

    @property
    def length(self) -> int:
        return len(self.parts)


@dataclass(frozen=True)
class _NodeNameRule:
    parts: Tuple[str, ...]
    system: str
    robot: Optional[str]


class Attributor:
    """Maps NodeInfo -> Attribution using explicit configuration.

    Matching precedence:
      1. exact node-name rules (a specific FQN beats a broad prefix), then
      2. namespace prefix rules, longest first (so a robot's own namespace
         wins over a wider system namespace).
    No match -> UNCLASSIFIED.
    """

    def __init__(self, config: AttributionConfig) -> None:
        self.config = config
        self._node_rules: Dict[Tuple[str, ...], _NodeNameRule] = {}
        self._namespace_rules: List[_NamespaceRule] = []
        for sys in config.systems.values():
            for fqn in sys.nodes:
                self._node_rules[_parts(fqn)] = _NodeNameRule(
                    _parts(fqn), sys.name, None
                )
            for ns in sys.namespaces:
                self._namespace_rules.append(
                    _NamespaceRule(_parts(ns), sys.name, None)
                )
            for robot in sys.robots:
                for fqn in robot.nodes:
                    self._node_rules[_parts(fqn)] = _NodeNameRule(
                        _parts(fqn), sys.name, robot.name
                    )
                for ns in robot.namespaces:
                    self._namespace_rules.append(
                        _NamespaceRule(_parts(ns), sys.name, robot.name)
                    )
        self._namespace_rules.sort(key=lambda r: r.length, reverse=True)

    def attribute(self, node: NodeInfo) -> Attribution:
        parts = _parts(node.fully_qualified_name)
        rule = self._node_rules.get(parts)
        if rule is not None:
            return Attribution(rule.system, rule.robot, SOURCE_CONFIG, True)
        for r in self._namespace_rules:
            if parts[: r.length] == r.parts:
                return Attribution(r.system, r.robot, SOURCE_CONFIG, True)
        return UNCLASSIFIED

    def attribute_topic_name(self, topic_name: str) -> Attribution:
        """LOW-CONFIDENCE fallback: match a topic's namespace against the same
        config namespace rules, ignoring the topic's leaf name.

        Used only when the topic's endpoint NODES are temporarily unknown
        (rmw reports _NODE_NAME_UNKNOWN_) so telemetry can still watch it.
        Never confident -- this is a hint, not a verdict.
        """
        parts = _parts(topic_name)[:-1]  # drop the topic leaf component
        for r in self._namespace_rules:
            if len(parts) >= r.length and parts[: r.length] == r.parts:
                return Attribution(r.system, r.robot, SOURCE_CONVENTION, False)
        return UNCLASSIFIED

    @property
    def system_names(self) -> List[str]:
        return self.config.system_names


@dataclass
class AttributedNode:
    node: NodeInfo
    attribution: Attribution

    @property
    def fqn(self) -> str:
        return self.node.fully_qualified_name


class SystemModel:
    """The debugger's attributed view of the graph.

    Maintained incrementally from GraphEvents emitted by the collector's
    GraphModel. Raw truth lives in GraphModel; this is interpretation. Topic
    ownership is derived lazily from endpoint-node attributions, so it is
    never duplicated here.
    """

    def __init__(self, attributor: Attributor) -> None:
        self._attributor = attributor
        self._nodes: Dict[str, AttributedNode] = {}

    @property
    def attributor(self) -> Attributor:
        return self._attributor

    # --- maintenance ----------------------------------------------------

    def handle_graph_event(self, event: GraphEvent) -> None:
        if event.kind == ChangeKind.NODE_ADDED and event.node is not None:
            self._add(event.node)
        elif event.kind == ChangeKind.NODE_REMOVED and event.node is not None:
            self._remove(event.node.fully_qualified_name)
        # TOPIC_* events do not change ownership.

    def sync_nodes(self, nodes: List[NodeInfo]) -> None:
        """Reconcile with a full node list (safety net if events are missed)."""
        current = {n.fully_qualified_name for n in nodes}
        for fqn in list(self._nodes):
            if fqn not in current:
                self._remove(fqn)
        for node in nodes:
            if node.fully_qualified_name not in self._nodes:
                self._add(node)

    def _add(self, node: NodeInfo) -> None:
        self._nodes[node.fully_qualified_name] = AttributedNode(
            node, self._attributor.attribute(node)
        )

    def _remove(self, fqn: str) -> None:
        self._nodes.pop(fqn, None)

    # --- queries --------------------------------------------------------

    def attributed_nodes(self) -> List[AttributedNode]:
        return sorted(self._nodes.values(), key=lambda a: a.fqn)

    def nodes_in_system(self, system: str) -> List[AttributedNode]:
        return sorted(
            (a for a in self._nodes.values() if a.attribution.system == system),
            key=lambda a: a.fqn,
        )

    def nodes_in_robot(self, system: str, robot: str) -> List[AttributedNode]:
        return sorted(
            (
                a
                for a in self._nodes.values()
                if a.attribution.system == system and a.attribution.robot == robot
            ),
            key=lambda a: a.fqn,
        )

    def unclassified_nodes(self) -> List[AttributedNode]:
        return sorted(
            (a for a in self._nodes.values() if a.attribution.is_unclassified),
            key=lambda a: a.fqn,
        )

    def system_names(self) -> List[str]:
        return self._attributor.system_names

    def attribute_topic(self, topic: TopicInfo) -> Attribution:
        """Attribute a topic via its endpoint nodes.

        A topic owned by nodes of exactly one (system, robot) inherits that
        owner. Endpoints owned by different owners -> MIXED (e.g. /tf, /rosout
        span robots). No known endpoints -> UNCLASSIFIED.

        If any endpoint's NODE is temporarily unknown to rmw
        (_NODE_NAME_UNKNOWN_), returns PENDING: the answer is not "unowned",
        it is "not known yet" -- callers should retry, not discard.
        """
        keys = set()
        any_endpoint = False
        pending = False
        for ep in topic.publishers + topic.subscribers:
            any_endpoint = True
            if any(m in ep.node.name for m in UNKNOWN_NODE_MARKERS) or any(
                m in ep.node.namespace for m in UNKNOWN_NODE_MARKERS
            ):
                pending = True
                continue
            attr = self._nodes.get(ep.node.fully_qualified_name)
            if attr is not None and not attr.attribution.is_unclassified:
                keys.add(attr.attribution.key)
        if pending:
            return PENDING
        if not any_endpoint:
            return UNCLASSIFIED
        if len(keys) == 1:
            system, robot = keys.pop()
            return Attribution(system, robot, SOURCE_CONFIG, True)
        if len(keys) > 1:
            return MIXED
        return UNCLASSIFIED
