"""Debugger entry point: collector + minimal CLI.

Run:
    ros2 run ros2_debugger debugger            # live, Ctrl-C to stop
    ros2 run ros2_debugger debugger --timeout 8  # run 8s, print summary, exit
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import rclpy
import yaml
from rcl_interfaces.msg import Log

from ros2_debugger.attribution import (
    AttributionConfig,
    Attributor,
    SOURCE_MIXED,
    SystemModel,
)
from ros2_debugger.collector import CollectorNode
from ros2_debugger.correlation import (
    CorrelationConfig,
    CorrelationEngine,
)
from ros2_debugger.diagnostics import DiagnosticConfig, DiagnosticEngine
from ros2_debugger.history import HistoryEngine, LifecycleState
from ros2_debugger.model import ChangeKind, GraphEvent, TopicInfo
from ros2_debugger.telemetry import TelemetryConfig, TelemetryModel

_LEVEL_NAMES = {0: "DEBUG", 10: "INFO", 20: "WARN", 30: "ERROR", 40: "FATAL"}


def _level_name(level: int) -> str:
    return _LEVEL_NAMES.get(level, f"L{level}")


def _topic_line(topic: TopicInfo) -> str:
    pubs = f"PUB:{len(topic.publishers)}"
    subs = f"SUB:{len(topic.subscribers)}"
    qos = ""
    if topic.publishers:
        p = topic.publishers[0]
        qos = f" rel={p.reliability} dur={p.durability} d={p.depth}"
    return f"  {topic.name:<32} {topic.primary_type or '?':<28} {pubs} {subs}{qos}"


class _Printer:
    def __init__(self, show_topics: bool = True) -> None:
        self._show_topics = show_topics

    def on_graph_event(self, event: GraphEvent) -> None:
        now = time.monotonic()
        if event.kind == ChangeKind.NODE_ADDED:
            print(f"[+node]  {event.node.fully_qualified_name}", flush=True)
        elif event.kind == ChangeKind.NODE_REMOVED:
            print(f"[-node]  {event.node.fully_qualified_name}", flush=True)
        elif event.kind == ChangeKind.TOPIC_ADDED and self._show_topics:
            print(f"[+topic] {event.topic.name} ({event.topic.primary_type})", flush=True)
        elif event.kind == ChangeKind.TOPIC_REMOVED and self._show_topics:
            print(f"[-topic] {event.topic.name}", flush=True)
        elif event.kind == ChangeKind.TOPIC_UPDATED and self._show_topics:
            print(
                f"[~topic] {event.topic.name} endpoint/QoS changed", flush=True
            )

    def on_log(self, msg: Log) -> None:
        print(f"[log] {_level_name(msg.level):<5} {msg.name}: {msg.msg}", flush=True)

    def summary(self, node: CollectorNode) -> None:
        model = node.model
        print("\n=== graph summary ===")
        print(f"nodes ({len(model.nodes)}):")
        for n in model.nodes:
            print(f"  {n.fully_qualified_name}")
        print(f"topics ({len(model.topics)}):")
        for t in model.topics:
            print(_topic_line(t))
        print(
            f"\ndomain: ROS_DOMAIN_ID={node.domain_id} "
            f"rmw={node.rmw_identifier}"
        )


def _default_config_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config", "attribution.yaml"
    )


def _load_configs(
    config_path: str,
) -> "tuple[AttributionConfig, TelemetryConfig, DiagnosticConfig, CorrelationConfig]":
    """Load attribution + telemetry + diagnostics + correlation config from one
    YAML file.

    On any failure fall back to empty configs (everything UNCLASSIFIED, no
    telemetry scope, no expectations) rather than guessing.
    """
    path = config_path or _default_config_path()
    if not os.path.exists(path):
        print(f"[config] no config at {path}; defaults used", flush=True)
        return (
            AttributionConfig(),
            TelemetryConfig(),
            DiagnosticConfig(),
            CorrelationConfig(),
        )
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        attribution = AttributionConfig.from_dict(data)
        telemetry = TelemetryConfig.from_dict(data.get("telemetry", {}))
        diagnostics = DiagnosticConfig.from_dict(data.get("diagnostics", {}))
        correlation = CorrelationConfig.from_dict(data.get("correlation", {}))
    except Exception as exc:
        print(f"[config] failed to load {path}: {exc}; defaults used",
              flush=True)
        return (
            AttributionConfig(),
            TelemetryConfig(),
            DiagnosticConfig(),
            CorrelationConfig(),
        )
    print(
        f"[config] loaded {path}: systems={attribution.system_names} "
        f"processes={list(telemetry.processes)} "
        f"topic_expectations={list(diagnostics.topic_expectations)} "
        f"correlation_window={correlation.temporal_window_s:.0f}s",
        flush=True,
    )
    return attribution, telemetry, diagnostics, correlation


def _attributed_summary(system_model: SystemModel, graph) -> None:
    print("\n=== attributed systems (debugger's understanding) ===")
    for sys_name in system_model.system_names():
        nodes = system_model.nodes_in_system(sys_name)
        if not nodes:
            continue
        print(f"{sys_name} ({len(nodes)} nodes):")
        by_robot: dict = {}
        for a in nodes:
            by_robot.setdefault(a.attribution.robot, []).append(a)
        for robot in sorted(k for k in by_robot if k):
            bucket = by_robot[robot]
            print(f"  robot {robot} ({len(bucket)}):")
            for a in bucket:
                print(f"    {a.fqn}")
        system_level = by_robot.get(None, [])
        if system_level:
            print(f"  system-level ({len(system_level)}):")
            for a in system_level:
                print(f"    {a.fqn}")
    unclassified = system_model.unclassified_nodes()
    if unclassified:
        print(f"UNCLASSIFIED ({len(unclassified)}):")
        for a in unclassified:
            print(f"  {a.fqn}")
    shared = [
        t.name
        for t in graph.topics
        if system_model.attribute_topic(t).source == SOURCE_MIXED
    ]
    if shared:
        print(f"shared across owners: {', '.join(sorted(shared))}")


def _topic_telemetry_line(stat) -> str:
    last = "never"
    if stat.last_message_time is not None and stat.idle_seconds is not None:
        last = f"{stat.idle_seconds:.1f}s ago"
    qos = ""
    if stat.publisher_reliability:
        qos = f" pub={stat.publisher_reliability}/{stat.publisher_durability}"
    status = "monitored"
    if stat.reason:
        status = stat.reason
    return (
        f"  {stat.topic:<32} {stat.type or '?':<26} "
        f"rate={stat.rate_hz:>7.2f}Hz cnt={stat.message_count:<6} "
        f"last={last:<10} recv={'yes' if stat.receiving else 'no'}{qos}"
        f"  [{status}]"
    )

def _telemetry_summary(telemetry: TelemetryModel) -> None:
    print("\n=== runtime telemetry ===")
    topic_stats = telemetry.topics.stats()
    print(f"topics ({len(topic_stats)}):")
    for stat in topic_stats:
        print(_topic_telemetry_line(stat))
    process_stats = telemetry.processes.stats()
    if process_stats:
        print("processes:")
        for p in process_stats:
            state = "alive" if p.alive else "not running"
            print(
                f"  {p.pattern:<36} {state:<12} pids={p.pids} "
                f"cpu={p.cpu_percent:>6.1f}% rss={p.rss_mb:>7.1f}MB"
            )
    if telemetry.tf.frames:
        print("tf frames:")
        for f in telemetry.tf.frames:
            print(
                f"  {f.frame_id:<24} count={f.count:<6} "
                f"last_stamp={f.last_stamp_sec:.3f}"
            )
    print(f"\nsampled at {telemetry.sampled_at:.1f} (monotonic)")


def _print_telemetry_live(telemetry: TelemetryModel) -> None:
    for stat in telemetry.topics.stats():
        if stat.monitored:
            idle = f"{stat.idle_seconds:.1f}s ago" if stat.idle_seconds is not None else "never"
            print(
                f"[telemetry] {stat.topic} {stat.rate_hz:.2f}Hz "
                f"({stat.message_count} msgs, last {idle})",
                flush=True,
            )
    for p in telemetry.processes.stats():
        if p.alive:
            print(
                f"[telemetry] proc {p.pattern} cpu={p.cpu_percent:.1f}% "
                f"rss={p.rss_mb:.1f}MB",
                flush=True,
            )


def _print_diagnostic(diag) -> None:
    owner = ""
    if diag.system:
        owner = f"{diag.system}/{diag.robot or 'system-level'} "
    print(
        f"[diag] {diag.state.value:<7} {diag.severity.value:<7} "
        f"[{diag.rule_id}] {owner}{diag.subject}: {diag.message}",
        flush=True,
    )
    for evidence in diag.evidence:
        print(f"       evidence: {evidence}", flush=True)


def _diagnostics_summary(engine: DiagnosticEngine) -> None:
    print("\n=== diagnostics ===")
    active = engine.active
    if active:
        print(f"active ({len(active)}):")
        for diag in active:
            print(
                f"  {diag.severity.value:<7} [{diag.rule_id}] "
                f"{diag.subject}: {diag.message}"
            )
    else:
        print("active (0): no active diagnostics")
    resolved = engine.resolved
    if resolved:
        print(f"resolved ({len(resolved)}):")
        for diag in resolved:
            print(
                f"  {diag.severity.value:<7} [{diag.rule_id}] "
                f"{diag.subject}: {diag.message}"
            )


def _print_incident(inc) -> None:
    note = " (uncertain attribution)" if inc.attribution_uncertain else ""
    print(
        f"[incident] {inc.state.value:<7} {inc.confidence.value:<6} "
        f"{inc.owner}{note} members={len(inc.members)} "
        f"signals={','.join(inc.strategies)}",
        flush=True,
    )
    print(f"            {inc.hypothesis}", flush=True)
    for ev in inc.evidence:
        print(f"            evidence: {ev}", flush=True)


def _incident_summary(correlation: CorrelationEngine) -> None:
    print("\n=== correlation (incidents) ===")
    active = correlation.active
    if active:
        print(f"active ({len(active)}):")
        for inc in active:
            note = " (uncertain attribution)" if inc.attribution_uncertain else ""
            print(
                f"  {inc.confidence.value:<6} {inc.owner}{note} "
                f"members={len(inc.members)} signals={','.join(inc.strategies)}"
            )
            print(f"    {inc.hypothesis}")
    else:
        print("active (0): no correlated incidents")
    uncorrelated = correlation.uncorrelated
    if uncorrelated:
        print(f"not correlated ({len(uncorrelated)}):")
        for diag, reason in uncorrelated:
            print(
                f"  [{diag.rule_id}] {diag.subject}: {reason}"
            )
    resolved = correlation.resolved
    if resolved:
        print(f"resolved ({len(resolved)}):")
        for inc in resolved:
            print(
                f"  {inc.confidence.value:<6} {inc.owner} "
                f"members={len(inc.members)}: "
                f"{inc.hypothesis.split('.')[0]}."
            )


def _print_history_event(session) -> None:
    if session.state is LifecycleState.RECOVERED:
        dur = f" duration={session.duration:.1f}s" if session.duration is not None else ""
        print(
            f"[history] incident#{session.incident_id} RECOVERED {session.owner} "
            f"members={session.member_count}{dur}",
            flush=True,
        )
    else:
        print(
            f"[history] incident#{session.incident_id} {session.state.value} "
            f"{session.owner} members={session.member_count} "
            f"confidence={session.confidence.value}",
            flush=True,
        )


def _history_detail(session) -> None:
    dur = (
        f"duration={session.duration:.1f}s"
        if session.duration is not None
        else "in progress"
    )
    print(
        f"  incident#{session.incident_id} {session.state.value} {session.owner} "
        f"confidence={session.confidence.value} members={session.member_count} {dur}"
    )
    for e in session.events:
        print(f"    {e.timestamp:8.1f} {e.transition.value:<9} {e.subject}")


def _history_summary(history: HistoryEngine) -> None:
    print("\n=== incident history ===")
    active = history.active
    if active:
        print(f"active ({len(active)}):")
        for session in active:
            _history_detail(session)
    else:
        print("active (0): no open incidents")
    closed = history.closed
    if closed:
        print(f"closed ({len(closed)}):")
        for session in closed:
            _history_detail(session)
    else:
        print("closed (0): no completed incidents")


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="debugger")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="run for N seconds, print summary, then exit (for testing)",
    )
    parser.add_argument(
        "--no-topics",
        action="store_true",
        help="do not print topic add/remove/update events",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="attribution config YAML (default: shipped config/attribution.yaml)",
    )
    parser.add_argument(
        "--process",
        action="append",
        default=[],
        help="additionally monitor an OS process by command-line pattern "
             "(repeatable)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    rclpy.init()
    node = CollectorNode()
    printer = _Printer(show_topics=not args.no_topics)

    attribution_config, telemetry_config, diagnostic_config, correlation_config = (
        _load_configs(args.config)
    )
    if args.process:
        telemetry_config = TelemetryConfig(
            monitor_systems=telemetry_config.monitor_systems,
            monitor_topics=telemetry_config.monitor_topics,
            processes=tuple(telemetry_config.processes) + tuple(args.process),
            process_owners=dict(telemetry_config.process_owners),
        )
    system_model = SystemModel(Attributor(attribution_config))
    telemetry = TelemetryModel(telemetry_config)
    diagnostic_engine = DiagnosticEngine(diagnostic_config)
    correlation_engine = CorrelationEngine(correlation_config)
    history_engine = HistoryEngine()

    node.graph_event_handlers.append(system_model.handle_graph_event)
    node.graph_event_handlers.append(printer.on_graph_event)
    node.log_handlers.append(printer.on_log)
    node.tf_transform_handlers.append(
        lambda fid, stamp, is_static: telemetry.tf.record(
            fid, stamp, time.monotonic()
        )
    )

    tick = {"n": 0}

    def post_refresh() -> None:
        now = time.monotonic()
        telemetry.reconcile(node, system_model, node.model, now)
        diagnostic_events = diagnostic_engine.evaluate(
            node.model, system_model, telemetry, now
        )
        for diag in diagnostic_events:
            _print_diagnostic(diag)
        for incident in correlation_engine.update(
            diagnostic_engine.active, now
        ):
            _print_incident(incident)
        for session in history_engine.update(
            diagnostic_events, correlation_engine.active, now
        ):
            _print_history_event(session)
        tick["n"] += 1
        if tick["n"] % 5 == 0:
            _print_telemetry_live(telemetry)

    node.post_refresh_handlers.append(post_refresh)

    # Deliver the initial discovery burst (nodes/topics present before we
    # started) now that a handler is attached.
    node.flush_pending_events()

    print(
        f"ROS 2 debugger collector on domain ROS_DOMAIN_ID={node.domain_id} "
        f"(rmw={node.rmw_identifier})",
        flush=True,
    )
    print(
        "Observing graph, /rosout, /tf, and attributed-topic telemetry; "
        "evaluating diagnostics. Empty graph = not on the same domain/network.",
        flush=True,
    )

    try:
        if args.timeout is not None:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
        else:
            try:
                rclpy.spin(node)
            except KeyboardInterrupt:
                print("\ninterrupted", flush=True)
    finally:
        telemetry.reconcile(node, system_model, node.model, time.monotonic())
        diagnostic_events = diagnostic_engine.evaluate(
            node.model, system_model, telemetry, time.monotonic()
        )
        correlation_engine.update(diagnostic_engine.active, time.monotonic())
        history_engine.update(
            diagnostic_events, correlation_engine.active, time.monotonic()
        )
        printer.summary(node)
        _attributed_summary(system_model, node.model)
        _telemetry_summary(telemetry)
        _diagnostics_summary(diagnostic_engine)
        _incident_summary(correlation_engine)
        _history_summary(history_engine)
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
