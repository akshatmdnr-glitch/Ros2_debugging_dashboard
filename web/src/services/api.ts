// The single place that talks HTTP to the backend. UI components never call
// fetch() themselves; they go through this service so the API contract lives
// in one file and is easy to test.

import type {
  CorrelationResponse,
  DiagnosticsResponse,
  Health,
  Incident,
  IncidentsResponse,
  NodesResponse,
  RobotsResponse,
  SystemsResponse,
  TelemetryResponse,
  TopicsResponse,
} from "../types";

const BASE_URL =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`GET ${path} -> ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export interface DashboardData {
  health: Health;
  systems: SystemsResponse;
  robots: RobotsResponse;
  nodes: NodesResponse;
  topics: TopicsResponse;
  diagnostics: DiagnosticsResponse;
  incidents: IncidentsResponse;
  telemetry: TelemetryResponse;
  correlation: CorrelationResponse;
}

export async function fetchDashboard(): Promise<DashboardData> {
  const [
    health,
    systems,
    robots,
    nodes,
    topics,
    diagnostics,
    incidents,
    telemetry,
    correlation,
  ] = await Promise.all([
    get<Health>("/health"),
    get<SystemsResponse>("/systems"),
    get<RobotsResponse>("/robots"),
    get<NodesResponse>("/nodes"),
    get<TopicsResponse>("/topics"),
    get<DiagnosticsResponse>("/diagnostics"),
    get<IncidentsResponse>("/incidents"),
    get<TelemetryResponse>("/telemetry"),
    get<CorrelationResponse>("/correlation"),
  ]);
  return { health, systems, robots, nodes, topics, diagnostics, incidents, telemetry, correlation };
}

/** Fetch a single incident with its full event timeline (GET /incidents/{id}).
 * Throws when the incident does not exist (404) or the backend is down. */
export async function fetchIncident(id: string): Promise<Incident> {
  return get<Incident>(`/incidents/${id}`);
}
