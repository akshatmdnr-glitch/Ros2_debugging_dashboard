// Types that mirror the backend API contract (Phase 7 DTOs).
// The flow is: backend schema -> TypeScript type -> UI component.

export interface Health {
  status: string;
  uptime: number;
  systems: number;
  nodes: number;
  topics: number;
  active_diagnostics: number;
  active_incidents: number;
}

export interface SystemRobot {
  name: string;
  nodes: string[];
  active_diagnostics: number;
  active_incidents: number;
}

export interface System {
  name: string;
  system_nodes: string[];
  active_diagnostics: number;
  robots: SystemRobot[];
}

export interface SystemsResponse {
  systems: System[];
  unclassified: string[];
}

export interface Robot {
  system: string;
  name: string;
  nodes: string[];
  active_diagnostics: number;
  active_incidents: number;
}

export interface RobotsResponse {
  robots: Robot[];
}

export interface Topic {
  name: string;
  type: string | null;
  publishers: number;
  subscribers: number;
  publisher_nodes: string[];
  subscriber_nodes: string[];
}

export interface TopicsResponse {
  topics: Topic[];
}

export interface Node {
  fqn: string;
  system: string | null;
  robot: string | null;
  source: string;
  confident: boolean;
}

export interface NodesResponse {
  nodes: Node[];
}

export interface Diagnostic {
  key: (string | null)[];
  rule_id: string;
  severity: string;
  message: string;
  evidence: string[];
  timestamp: number;
  state: string;
  subject: string;
  system: string | null;
  robot: string | null;
  node: string | null;
  topic: string | null;
  tf_frame: string | null;
  process: string | null;
}

export interface DiagnosticsResponse {
  active: Diagnostic[];
  resolved: Diagnostic[];
}

export interface CorrelationGroup {
  key: string[];
  owner: string;
  system: string | null;
  robot: string | null;
  confidence: string;
  strategies: string[];
  hypothesis: string;
  evidence: string[];
  attribution_uncertain: boolean;
  members: { key: (string | null)[]; subject: string; rule_id: string }[];
}

export interface CorrelationResponse {
  active: CorrelationGroup[];
  resolved: CorrelationGroup[];
}

export interface MemberEvent {
  timestamp: number;
  transition: string;
  subject: string;
}

export interface Incident {
  id: number;
  state: string;
  owner: string;
  system: string | null;
  robot: string | null;
  confidence: string;
  strategies: string[];
  started_at: number;
  ended_at: number | null;
  duration: number | null;
  members: string[];
  member_count: number;
  active_count: number;
  events: MemberEvent[];
}

export interface IncidentsResponse {
  active: Incident[];
  history: Incident[];
}

export interface TopicTelemetry {
  topic: string;
  type: string | null;
  monitored: boolean;
  receiving: boolean;
  message_count: number;
  rate_hz: number;
  idle_seconds: number | null;
  reason: string;
}

export interface ProcessTelemetry {
  pattern: string;
  alive: boolean;
  pids: number[];
  cpu_percent: number;
  rss_mb: number;
}

export interface TfTelemetry {
  frame_id: string;
  count: number;
  last_seen: number;
}

export interface TfEdge {
  parent: string;
  child: string;
}

export interface TfResponse {
  frames: TfTelemetry[];
  edges: TfEdge[];
}

export interface TelemetryResponse {
  topics: TopicTelemetry[];
  processes: ProcessTelemetry[];
  tf: TfResponse;
}

// --- derived view model (frontend-only, not part of the API) ------------

export type RobotStatus = "HEALTHY" | "WARNING" | "CRITICAL" | "UNKNOWN";

export interface RobotView extends Robot {
  status: RobotStatus;
  diagnostics: Diagnostic[];
}

// --- real-time event channel (Phase 11) ----------------------------------
// Mirrors the WebSocket protocol of GET /ws/stream. The backend broadcasts one
// `cycle` per observation cycle carrying that cycle's transitions; `hello` and
// `heartbeat` are liveness messages.

export interface DiagnosticEvent {
  event: "ACTIVE" | "RESOLVED";
  diagnostic: Diagnostic;
}

export interface CorrelationEvent {
  event: "ACTIVE" | "RESOLVED";
  incident: CorrelationGroup;
}

export interface IncidentEvent {
  event: "UPDATED" | "CLOSED";
  incident: Incident;
}

export interface CycleMessage {
  type: "cycle";
  seq: number;
  server_time: number;
  topology_changed: boolean;
  diagnostic_events: DiagnosticEvent[];
  correlation_events: CorrelationEvent[];
  incident_events: IncidentEvent[];
}

export interface HelloMessage {
  type: "hello";
  server_time: number;
}

export interface HeartbeatMessage {
  type: "heartbeat";
  server_time: number;
}

export type RealtimeMessage = CycleMessage | HelloMessage | HeartbeatMessage;
