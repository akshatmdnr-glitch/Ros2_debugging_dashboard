"""Backend API for the ROS 2 debugger (Phase 7).

This is a THIN ADAPTER between the debugger engine and a future dashboard. It
reads live snapshots from the shared `DebuggerApp` (the single source of
truth) and exposes them as stable, typed HTTP resources. It does NOT:

  * collect ROS data (no rclpy, no CollectorNode),
  * run diagnostics, correlation, or incident lifecycle logic,
  * keep a second copy of state.

The Pydantic models below are the API RESPONSE CONTRACT: they are decoupled
from the internal dataclasses so the engine can evolve without breaking the
dashboard.

Run:
    ros2 run ros2_debugger debugger-api --port 8000
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import List, Optional, Sequence

import rclpy
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ros2_debugger.app import DebuggerApp


# --- API response models (the stable external contract) ------------------

class Node(BaseModel):
    fqn: str
    system: Optional[str] = None
    robot: Optional[str] = None
    source: str
    confident: bool


class Topic(BaseModel):
    name: str
    type: Optional[str] = None
    publishers: int
    subscribers: int


class SystemRobot(BaseModel):
    name: str
    nodes: List[str]
    active_diagnostics: int
    active_incidents: int


class System(BaseModel):
    name: str
    system_nodes: List[str]
    active_diagnostics: int
    robots: List[SystemRobot]


class SystemsResponse(BaseModel):
    systems: List[System]
    unclassified: List[str]


class Robot(BaseModel):
    system: str
    name: str
    nodes: List[str]
    active_diagnostics: int
    active_incidents: int


class TopicTelemetry(BaseModel):
    topic: str
    type: Optional[str] = None
    monitored: bool
    receiving: bool
    message_count: int
    rate_hz: float
    idle_seconds: Optional[float] = None
    reason: str


class ProcessTelemetry(BaseModel):
    pattern: str
    alive: bool
    pids: List[int]
    cpu_percent: float
    rss_mb: float


class TfTelemetry(BaseModel):
    frame_id: str
    count: int
    last_seen: float


class TelemetryResponse(BaseModel):
    topics: List[TopicTelemetry]
    processes: List[ProcessTelemetry]
    tf: List[TfTelemetry]


class Diagnostic(BaseModel):
    key: List[Optional[str]]
    rule_id: str
    severity: str
    message: str
    evidence: List[str]
    timestamp: float
    state: str
    subject: str
    system: Optional[str] = None
    robot: Optional[str] = None
    node: Optional[str] = None
    topic: Optional[str] = None
    tf_frame: Optional[str] = None
    process: Optional[str] = None


class DiagnosticsResponse(BaseModel):
    active: List[Diagnostic]
    resolved: List[Diagnostic]


class CorrelationMember(BaseModel):
    key: List[Optional[str]]
    subject: str
    rule_id: str


class CorrelationGroup(BaseModel):
    key: List[str]
    owner: str
    system: Optional[str] = None
    robot: Optional[str] = None
    confidence: str
    strategies: List[str]
    hypothesis: str
    evidence: List[str]
    attribution_uncertain: bool
    members: List[CorrelationMember]


class CorrelationResponse(BaseModel):
    active: List[CorrelationGroup]
    resolved: List[CorrelationGroup]


class MemberEvent(BaseModel):
    timestamp: float
    transition: str
    subject: str


class Incident(BaseModel):
    id: int
    state: str
    owner: str
    system: Optional[str] = None
    robot: Optional[str] = None
    confidence: str
    strategies: List[str]
    started_at: float
    ended_at: Optional[float] = None
    duration: Optional[float] = None
    members: List[str]
    member_count: int
    active_count: int
    events: List[MemberEvent]


class IncidentsResponse(BaseModel):
    active: List[Incident]
    history: List[Incident]


class Health(BaseModel):
    status: str
    uptime: float
    systems: int
    nodes: int
    topics: int
    active_diagnostics: int
    active_incidents: int


class RobotsResponse(BaseModel):
    robots: List[Robot]


class NodesResponse(BaseModel):
    nodes: List[Node]


class TopicsResponse(BaseModel):
    topics: List[Topic]


# --- application ---------------------------------------------------------


def create_app(
    app: DebuggerApp,
    cors_origins: Sequence[str] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ),
) -> FastAPI:
    """Build the API as a thin adapter over one DebuggerApp instance.

    `cors_origins` lets the browser-based dashboard (served by the Vite dev
    server on a different origin) read the API. Without these headers the
    browser blocks the frontend's requests even though the API works in curl.
    """
    api = FastAPI(title="ROS 2 Debugger API", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @api.get("/health", response_model=Health)
    def health() -> Health:
        """Liveness + headline counts (overall picture)."""
        return Health(**app.snapshot_health())

    @api.get("/systems", response_model=SystemsResponse)
    def systems() -> SystemsResponse:
        """Configured systems with robots, their nodes, and active counts."""
        return SystemsResponse(**app.snapshot_systems())

    @api.get("/robots", response_model=RobotsResponse)
    def robots() -> RobotsResponse:
        """Flat list of robots with node lists and active counts."""
        return RobotsResponse(**app.snapshot_robots())

    @api.get("/nodes", response_model=NodesResponse)
    def nodes() -> NodesResponse:
        """Every attributed node with its owner and attribution source."""
        return NodesResponse(**app.snapshot_nodes())

    @api.get("/topics", response_model=TopicsResponse)
    def topics() -> TopicsResponse:
        """Topics on the graph with endpoint counts."""
        return TopicsResponse(**app.snapshot_topics())

    @api.get("/telemetry", response_model=TelemetryResponse)
    def telemetry() -> TelemetryResponse:
        """Topic activity, process resources, and TF freshness."""
        return TelemetryResponse(**app.snapshot_telemetry())

    @api.get("/diagnostics", response_model=DiagnosticsResponse)
    def diagnostics() -> DiagnosticsResponse:
        """Active (and resolved) diagnostic verdicts."""
        return DiagnosticsResponse(**app.snapshot_diagnostics())

    @api.get("/correlation", response_model=CorrelationResponse)
    def correlation() -> CorrelationResponse:
        """Current correlation groups and their cautious hypotheses."""
        return CorrelationResponse(**app.snapshot_correlation())

    @api.get("/incidents", response_model=IncidentsResponse)
    def incidents() -> IncidentsResponse:
        """All incidents: currently active and completed (history)."""
        return IncidentsResponse(**app.snapshot_incidents())

    @api.get("/incidents/active", response_model=List[Incident])
    def incidents_active() -> List[Incident]:
        """Only the incidents that are currently open."""
        return [Incident(**i) for i in app.snapshot_incidents()["active"]]

    @api.get("/incidents/history", response_model=List[Incident])
    def incidents_history() -> List[Incident]:
        """Completed incidents, each with its full ordered timeline."""
        return [Incident(**i) for i in app.snapshot_incidents()["history"]]

    @api.get("/incidents/{incident_id}", response_model=Incident)
    def incident(incident_id: int) -> Incident:
        """One incident by id, including its event timeline."""
        data = app.snapshot_incident(incident_id)
        if data is None:
            raise HTTPException(
                status_code=404,
                detail=f"incident {incident_id} not found",
            )
        return Incident(**data)

    return api


# --- entry point ---------------------------------------------------------


def seed_demo(app: DebuggerApp) -> None:
    """Populate a clearly-labelled synthetic warehouse state so the dashboard can
    be developed and demonstrated WITHOUT a live ROS system.

    This is UI-development tooling (invoked only by `--demo`); it is not real
    telemetry and the API never fakes data in normal operation. The synthetic
    state is produced by the REAL engines (telemetry -> diagnostics -> correlation
    -> history), just fed synthetic observations.
    """
    from ros2_debugger.diagnostics import DiagnosticConfig
    from ros2_debugger.model import EndpointInfo, NodeInfo, TopicInfo
    from ros2_debugger.telemetry import (
        FrameStats,
        ProcessStats,
        TelemetryConfig,
        TopicStats,
    )

    now = time.monotonic()
    nodes = [
        NodeInfo("talker", "/robot1"),
        NodeInfo("lidar", "/robot2"),
        NodeInfo("nav", "/robot2"),
    ]
    app.graph.sync_nodes(nodes, now)
    app.system_model.sync_nodes(nodes)
    app.graph.sync_topics(
        [
            TopicInfo(
                "/robot1/chatter", ["std_msgs/msg/String"],
                publishers=[EndpointInfo(
                    NodeInfo("talker", "/robot1"), "PUBLISHER",
                    "std_msgs/msg/String", "RELIABLE", "VOLATILE", 10, 0.0, 0.0, "g1",
                )],
            ),
            TopicInfo(
                "/robot2/scan", ["sensor_msgs/msg/LaserScan"],
                publishers=[EndpointInfo(
                    NodeInfo("lidar", "/robot2"), "PUBLISHER",
                    "sensor_msgs/msg/LaserScan", "RELIABLE", "VOLATILE", 10, 0.0, 0.0, "g2",
                )],
            ),
        ],
        now,
    )
    app.telemetry.topics._stats["/robot1/chatter"] = TopicStats(
        topic="/robot1/chatter", type="std_msgs/msg/String", monitored=True,
        receiving=True, message_count=100, rate_hz=1.0,
        last_message_time=now, idle_seconds=0.2,
    )
    app.telemetry.topics._stats["/robot2/scan"] = TopicStats(
        topic="/robot2/scan", type="sensor_msgs/msg/LaserScan", monitored=True,
        receiving=True, message_count=50, rate_hz=1.2,
        last_message_time=now, idle_seconds=0.4,
    )
    app.telemetry.processes._stats["robot2_lidar_driver"] = ProcessStats(
        pattern="robot2_lidar_driver", pids=[111], alive=True,
        cpu_percent=95.0, rss_mb=210.0,
    )
    # Attribute the synthetic process to Robot 2 so its high-CPU diagnostic is
    # entity-correlated (the demo config has no process owners of its own).
    app.telemetry.config = TelemetryConfig(
        monitor_systems=app.telemetry.config.monitor_systems,
        monitor_topics=app.telemetry.config.monitor_topics,
        processes=tuple(app.telemetry.config.processes) + ("robot2_lidar_driver",),
        process_owners={
            **app.telemetry.config.process_owners,
            "robot2_lidar_driver": ("warehouse", "robot2"),
        },
    )
    app.telemetry.tf._frames["base_link"] = FrameStats(
        frame_id="base_link", count=100, last_seen=now - 4.0,
    )

    # Give the engine expectations so the REAL rules produce the diagnostics.
    app.diagnostic_engine.config = DiagnosticConfig.from_dict(
        {
            "stale_after_s_default": 5.0,
            "topic_expectations": {
                "/robot2/scan": {"min_hz": 8.0, "stale_after_s": 2.0},
            },
            "required_tf_frames": [
                {"frame": "base_link", "system": "warehouse", "robot": "robot2"}
            ],
            "tf_stale_after_s": 3.0,
            "absence_grace_cycles": 1,
            "process_thresholds": {"cpu_warn_percent": 80.0, "mem_warn_mb": 1024.0},
        }
    )
    events = app.diagnostic_engine.evaluate(
        app.graph, app.system_model, app.telemetry, now
    )
    app.correlation_engine.update(app.diagnostic_engine.active, now + 1.0)
    app.history_engine.update(events, app.correlation_engine.active, now + 1.0)


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="debugger-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", default=None,
                        help="attribution config YAML (default: shipped)")
    parser.add_argument("--process", action="append", default=[],
                        help="additionally monitor an OS process by pattern")
    parser.add_argument("--timeout", type=float, default=None,
                        help="run for N seconds then exit (for testing)")
    parser.add_argument("--no-ros", action="store_true",
                        help="serve without joining a ROS domain (empty real "
                             "state; for frontend development)")
    parser.add_argument("--demo", action="store_true",
                        help="seed a clearly-labelled synthetic warehouse state "
                             "(UI development only; intended with --no-ros)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.no_ros:
        app_state = DebuggerApp(
            config_path=args.config, process_patterns=args.process, ros=False
        )
    else:
        rclpy.init()
        app_state = DebuggerApp(
            config_path=args.config, process_patterns=args.process
        )
        app_state.start_refresh()
        app_state.collector.flush_pending_events()

    if args.demo:
        seed_demo(app_state)
        print(
            "[demo] serving a SYNTHETIC warehouse state for UI development "
            "(not real telemetry)",
            flush=True,
        )

    api_app = create_app(app_state)
    server = uvicorn.Server(
        uvicorn.Config(api_app, host=args.host, port=args.port, log_level="warning")
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if args.no_ros:
        print(
            f"ROS 2 debugger API on http://{args.host}:{args.port} "
            f"(no-ROS, demo={bool(args.demo)})",
            flush=True,
        )
    else:
        print(
            f"ROS 2 debugger API on http://{args.host}:{args.port} "
            f"(ROS_DOMAIN_ID={app_state.collector.domain_id}) "
            f"rmw={app_state.collector.rmw_identifier}",
            flush=True,
        )

    try:
        if args.no_ros:
            if args.timeout is not None:
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    time.sleep(0.2)
            else:
                while True:
                    time.sleep(3600)
        elif args.timeout is not None:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                rclpy.spin_once(app_state.collector, timeout_sec=0.1)
        else:
            try:
                rclpy.spin(app_state.collector)
            except KeyboardInterrupt:
                print("\ninterrupted", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted", flush=True)
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)
        if not args.no_ros:
            app_state.collector.destroy_node()
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
