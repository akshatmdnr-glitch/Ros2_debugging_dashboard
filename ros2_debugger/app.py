"""Application composition root (Phase 7).

Phases 1-6 built a live ROS 2 observability pipeline:

    collector -> graph/attribution -> telemetry -> diagnostics
        -> correlation -> incident history

Phase 7 extracts that composition into a shared `DebuggerApp` so TWO consumers
run against the SAME authoritative state without duplicating it:

  * the CLI          (ros2 run ros2_debugger debugger)      -- live stream,
  * the backend API  (ros2 run ros2_debugger debugger-api)  -- HTTP snapshots
    for a future dashboard.

`DebuggerApp` is the single source of truth. The API must read it, never
rebuild it, so there is no "State A / State B" drift. The refresh cycle runs in
the rclpy spin thread; snapshots are read in the API thread; a lock guards
both.

This module creates the CollectorNode (the only component that imports rclpy);
everything else stays ROS-free.
"""

from __future__ import annotations

import os
import threading
import time
from typing import List, Optional, Tuple

import yaml

from ros2_debugger.attribution import (
    AttributionConfig,
    Attributor,
    SystemModel,
)
from ros2_debugger.broadcast import EventBroadcaster
from ros2_debugger.collector import CollectorNode
from ros2_debugger.correlation import (
    CorrelationConfig,
    CorrelationEngine,
)
from ros2_debugger.diagnostics import DiagnosticConfig, DiagnosticEngine
from ros2_debugger.history import HistoryEngine
from ros2_debugger.model import GraphModel
from ros2_debugger.telemetry import TelemetryConfig, TelemetryModel


def default_config_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config", "attribution.yaml"
    )


def load_configs(
    config_path: Optional[str],
) -> "tuple[AttributionConfig, TelemetryConfig, DiagnosticConfig, CorrelationConfig]":
    """Load attribution + telemetry + diagnostics + correlation config from one
    YAML file.

    On any failure fall back to empty configs (everything UNCLASSIFIED, no
    telemetry scope, no expectations) rather than guessing.
    """
    path = config_path or default_config_path()
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


class DebuggerApp:
    """Composition root and authoritative application state.

    `ros=True` joins ROS (creates a CollectorNode and wires it); `ros=False`
    builds the analysis stack only, which is how the API is unit-tested without
    a live ROS environment.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        process_patterns: Tuple[str, ...] = (),
        ros: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._started = time.monotonic()

        # Real-time channel (Phase 11): refresh() broadcasts each cycle's
        # event stream here; the API's WebSocket endpoint subscribes a sink.
        self.broadcaster = EventBroadcaster()
        self._cycle_seq = 0
        self._graph_events: List = []

        attribution_config, telemetry_config, diagnostic_config, correlation_config = (
            load_configs(config_path)
        )
        if process_patterns:
            telemetry_config = TelemetryConfig(
                monitor_systems=telemetry_config.monitor_systems,
                monitor_topics=telemetry_config.monitor_topics,
                processes=tuple(telemetry_config.processes) + tuple(process_patterns),
                process_owners=dict(telemetry_config.process_owners),
            )

        self.system_model = SystemModel(Attributor(attribution_config))
        self.telemetry = TelemetryModel(telemetry_config)
        self.diagnostic_engine = DiagnosticEngine(diagnostic_config)
        self.correlation_engine = CorrelationEngine(correlation_config)
        self.history_engine = HistoryEngine()

        self.graph: GraphModel = GraphModel()
        self.collector: Optional[CollectorNode] = None
        if ros:
            self.collector = CollectorNode()
            self.graph = self.collector.model
            self.collector.graph_event_handlers.append(
                self.system_model.handle_graph_event
            )
            self.collector.graph_event_handlers.append(self._capture_graph_event)
            self.collector.tf_transform_handlers.append(self._record_tf)

    def _record_tf(
        self, parent: str, child: str, stamp_sec: float, is_static: bool
    ) -> None:
        self.telemetry.tf.record(parent, child, stamp_sec, time.monotonic())

    def _capture_graph_event(self, event) -> None:
        """Remember structural changes (node/topic added/removed) so the next
        refresh cycle can tell clients "the topology changed, re-sync". The
        graph and attribution live only on the backend; the frontend must NOT
        re-derive them, so we signal a full snapshot refetch instead."""
        self._graph_events.append(event)

    # --- observation cycle ------------------------------------------------

    def refresh(self, now: float) -> "tuple[List, List, List]":
        """Run one full observation/evaluation cycle under the state lock.

        Returns (diagnostic_events, correlation_events, history_events) so a
        consumer (the CLI) can render live output; the API reads snapshots and
        ALSO broadcasts the same events to real-time subscribers (Phase 11), so
        the dashboard sees exactly what the CLI sees -- one authoritative stream.
        """
        with self._lock:
            self.telemetry.reconcile(
                self.collector, self.system_model, self.graph, now
            )
            diagnostic_events = self.diagnostic_engine.evaluate(
                self.graph, self.system_model, self.telemetry, now
            )
            correlation_events = self.correlation_engine.update(
                self.diagnostic_engine.active, now
            )
            history_events = self.history_engine.update(
                diagnostic_events, self.correlation_engine.active, now
            )
            message = self._cycle_message(
                diagnostic_events, correlation_events, history_events
            )
        self.broadcaster.publish(message)
        return diagnostic_events, correlation_events, history_events

    def _cycle_message(
        self,
        diagnostic_events: List,
        correlation_events: List,
        history_events: List,
    ) -> dict:
        """Serialize this cycle's transitions into one real-time message.

        The event streams are the SAME objects the CLI renders; only their
        serialization changes here (reusing the snapshot DTO builders). A cycle
        may carry zero events -- that is still a valid, honest heartbeat of
        "nothing changed this cycle".
        """
        self._cycle_seq += 1
        topology_changed = bool(self._graph_events)
        self._graph_events = []
        return {
            "type": "cycle",
            "seq": self._cycle_seq,
            "server_time": time.monotonic(),
            "topology_changed": topology_changed,
            "diagnostic_events": [
                {"event": d.state.value, "diagnostic": self._diagnostic_out(d)}
                for d in diagnostic_events
            ],
            "correlation_events": [
                {"event": i.state.value, "incident": self._correlation_out(i)}
                for i in correlation_events
            ],
            "incident_events": [
                {
                    "event": (
                        "CLOSED" if s.state.value == "RECOVERED" else "UPDATED"
                    ),
                    "incident": self._incident_out(s),
                }
                for s in history_events
            ],
        }

    def start_refresh(self) -> None:
        """Wire the refresh cycle into the collector's post-refresh hook."""
        if self.collector is not None:
            self.collector.post_refresh_handlers.append(
                lambda: self.refresh(time.monotonic())
            )

    # --- snapshots (plain dicts, built under the lock) --------------------

    def _counts_locked(self) -> "tuple[dict, dict]":
        """Active diagnostics and active incidents per (system, robot)."""
        diag: dict = {}
        for d in self.diagnostic_engine.active:
            key = (d.system, d.robot)
            diag[key] = diag.get(key, 0) + 1
        inc: dict = {}
        for s in self.history_engine.active:
            key = (s.system, s.robot)
            inc[key] = inc.get(key, 0) + 1
        return diag, inc

    def snapshot_health(self) -> dict:
        with self._lock:
            return {
                "status": "running",
                "uptime": time.monotonic() - self._started,
                "systems": len(self.system_model.system_names()),
                "nodes": len(self.graph.nodes),
                "topics": len(self.graph.topics),
                "active_diagnostics": len(self.diagnostic_engine.active),
                "active_incidents": len(self.history_engine.active),
            }

    def snapshot_systems(self) -> dict:
        with self._lock:
            diag_counts, inc_counts = self._counts_locked()
            systems = []
            for sys_name in self.system_model.system_names():
                buckets: dict = {}
                for a in self.system_model.nodes_in_system(sys_name):
                    buckets.setdefault(a.attribution.robot, []).append(a.fqn)
                robots = []
                for robot in sorted(k for k in buckets if k):
                    robots.append(
                        {
                            "name": robot,
                            "nodes": sorted(buckets[robot]),
                            "active_diagnostics": diag_counts.get((sys_name, robot), 0),
                            "active_incidents": inc_counts.get((sys_name, robot), 0),
                        }
                    )
                systems.append(
                    {
                        "name": sys_name,
                        "system_nodes": sorted(buckets.get(None, [])),
                        "active_diagnostics": diag_counts.get((sys_name, None), 0),
                        "robots": robots,
                    }
                )
            return {
                "systems": systems,
                "unclassified": sorted(
                    a.fqn for a in self.system_model.unclassified_nodes()
                ),
            }

    def snapshot_robots(self) -> dict:
        with self._lock:
            diag_counts, inc_counts = self._counts_locked()
            robots = []
            for sys_name in self.system_model.system_names():
                for robot in sorted(
                    {
                        a.attribution.robot
                        for a in self.system_model.nodes_in_system(sys_name)
                        if a.attribution.robot
                    }
                ):
                    robots.append(
                        {
                            "system": sys_name,
                            "name": robot,
                            "nodes": sorted(
                                a.fqn
                                for a in self.system_model.nodes_in_robot(
                                    sys_name, robot
                                )
                            ),
                            "active_diagnostics": diag_counts.get((sys_name, robot), 0),
                            "active_incidents": inc_counts.get((sys_name, robot), 0),
                        }
                    )
            return {"robots": robots}

    def snapshot_nodes(self) -> dict:
        with self._lock:
            return {
                "nodes": [
                    {
                        "fqn": a.fqn,
                        "system": a.attribution.system,
                        "robot": a.attribution.robot,
                        "source": a.attribution.source,
                        "confident": a.attribution.confident,
                    }
                    for a in self.system_model.attributed_nodes()
                ]
            }

    def snapshot_topics(self) -> dict:
        with self._lock:
            return {
                "topics": [
                    {
                        "name": t.name,
                        "type": t.primary_type,
                        "publishers": len(t.publishers),
                        "subscribers": len(t.subscribers),
                        "publisher_nodes": sorted(
                            {e.node.fully_qualified_name for e in t.publishers}
                        ),
                        "subscriber_nodes": sorted(
                            {e.node.fully_qualified_name for e in t.subscribers}
                        ),
                    }
                    for t in self.graph.topics
                ]
            }

    def snapshot_telemetry(self) -> dict:
        with self._lock:
            return {
                "topics": [
                    {
                        "topic": s.topic,
                        "type": s.type,
                        "monitored": s.monitored,
                        "receiving": s.receiving,
                        "message_count": s.message_count,
                        "rate_hz": s.rate_hz,
                        "idle_seconds": s.idle_seconds,
                        "reason": s.reason,
                    }
                    for s in self.telemetry.topics.stats()
                ],
                "processes": [
                    {
                        "pattern": p.pattern,
                        "alive": p.alive,
                        "pids": p.pids,
                        "cpu_percent": p.cpu_percent,
                        "rss_mb": p.rss_mb,
                    }
                    for p in self.telemetry.processes.stats()
                ],
                "tf": {
                    "frames": [
                        {
                            "frame_id": f.frame_id,
                            "count": f.count,
                            "last_seen": f.last_seen,
                        }
                        for f in self.telemetry.tf.frames
                    ],
                    "edges": [
                        {"parent": parent, "child": child}
                        for parent, child in self.telemetry.tf.edges
                    ],
                },
            }

    @staticmethod
    def _diagnostic_out(d) -> dict:
        return {
            "key": list(d.key),
            "rule_id": d.rule_id,
            "severity": d.severity.value,
            "message": d.message,
            "evidence": list(d.evidence),
            "timestamp": d.timestamp,
            "state": d.state.value,
            "subject": d.subject,
            "system": d.system,
            "robot": d.robot,
            "node": d.node,
            "topic": d.topic,
            "tf_frame": d.tf_frame,
            "process": d.process,
        }

    def snapshot_diagnostics(self) -> dict:
        with self._lock:
            return {
                "active": [
                    self._diagnostic_out(d) for d in self.diagnostic_engine.active
                ],
                "resolved": [
                    self._diagnostic_out(d) for d in self.diagnostic_engine.resolved
                ],
            }

    @staticmethod
    def _correlation_out(inc) -> dict:
        return {
            "key": sorted({m.subject for m in inc.members}),
            "owner": inc.owner,
            "system": inc.system,
            "robot": inc.robot,
            "confidence": inc.confidence.value,
            "strategies": list(inc.strategies),
            "hypothesis": inc.hypothesis,
            "evidence": list(inc.evidence),
            "attribution_uncertain": inc.attribution_uncertain,
            "members": [
                {"key": list(m.key), "subject": m.subject, "rule_id": m.rule_id}
                for m in inc.members
            ],
        }

    def snapshot_correlation(self) -> dict:
        with self._lock:
            return {
                "active": [
                    self._correlation_out(i) for i in self.correlation_engine.active
                ],
                "resolved": [
                    self._correlation_out(i) for i in self.correlation_engine.resolved
                ],
            }

    @staticmethod
    def _incident_out(s) -> dict:
        # `members` is exposed as readable subjects (from the activated events)
        # rather than the raw diagnostic-key tuples kept internally.
        members = sorted(
            {
                e.subject
                for e in s.events
                if e.transition.value == "ACTIVATED"
            }
        )
        return {
            "id": s.incident_id,
            "state": s.state.value,
            "owner": s.owner,
            "system": s.system,
            "robot": s.robot,
            "confidence": s.confidence.value,
            "strategies": list(s.strategies),
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "duration": s.duration,
            "members": members,
            "member_count": s.member_count,
            "active_count": s.active_count,
            "events": [
                {
                    "timestamp": e.timestamp,
                    "transition": e.transition.value,
                    "subject": e.subject,
                }
                for e in s.events
            ],
        }

    def snapshot_incidents(self) -> dict:
        with self._lock:
            return {
                "active": [
                    self._incident_out(s) for s in self.history_engine.active
                ],
                "history": [
                    self._incident_out(s) for s in self.history_engine.closed
                ],
            }

    def snapshot_incident(self, incident_id: int) -> Optional[dict]:
        with self._lock:
            for s in self.history_engine.all:
                if s.incident_id == incident_id:
                    return self._incident_out(s)
            return None
