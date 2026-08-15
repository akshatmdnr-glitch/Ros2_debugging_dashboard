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
from typing import List, Optional

import rclpy
import uvicorn
from fastapi import FastAPI, HTTPException
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


def create_app(app: DebuggerApp) -> FastAPI:
    """Build the API as a thin adapter over one DebuggerApp instance."""
    api = FastAPI(title="ROS 2 Debugger API", version="0.1.0")

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
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    rclpy.init()
    app_state = DebuggerApp(
        config_path=args.config, process_patterns=args.process
    )
    app_state.start_refresh()
    app_state.collector.flush_pending_events()

    api_app = create_app(app_state)
    server = uvicorn.Server(
        uvicorn.Config(api_app, host=args.host, port=args.port, log_level="warning")
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    print(
        f"ROS 2 debugger API on http://{args.host}:{args.port} "
        f"(ROS_DOMAIN_ID={app_state.collector.domain_id}) "
        f"rmw={app_state.collector.rmw_identifier}",
        flush=True,
    )

    try:
        if args.timeout is not None:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                rclpy.spin_once(app_state.collector, timeout_sec=0.1)
        else:
            try:
                rclpy.spin(app_state.collector)
            except KeyboardInterrupt:
                print("\ninterrupted", flush=True)
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)
        app_state.collector.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
