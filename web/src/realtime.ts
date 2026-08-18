// Pure patch logic for the real-time event stream (Phase 11).
//
// The backend is the single source of truth; this module only APPLIES the
// authoritative events it broadcasts, it never judges anything. Each patch is
// idempotent (applying the same ACTIVE event twice = the same result), mirroring
// the set-based semantics of HistoryEngine/CorrelationEngine. After patching we
// recompute the aggregation counts the backend would have produced (active
// diagnostics/incidents per system/robot) so the patched state stays honest.

import type { DashboardData } from "./services/api";
import type {
  CorrelationEvent,
  CorrelationResponse,
  CycleMessage,
  DiagnosticEvent,
  DiagnosticsResponse,
  IncidentEvent,
  IncidentsResponse,
} from "./types";

const SEP = "\u0000";

function upsert<T>(list: T[], keyOf: (item: T) => string, item: T): T[] {
  const next = list.filter((x) => keyOf(x) !== keyOf(item));
  next.push(item);
  return next;
}

export function applyDiagnosticEvents(
  current: DiagnosticsResponse,
  events: DiagnosticEvent[],
): DiagnosticsResponse {
  let active = [...current.active];
  let resolved = [...current.resolved];
  for (const ev of events) {
    const d = ev.diagnostic;
    const key = d.key.join(SEP);
    if (ev.event === "RESOLVED") {
      active = active.filter((x) => x.key.join(SEP) !== key);
      resolved = upsert(resolved, (x) => x.key.join(SEP), d);
    } else {
      active = upsert(active, (x) => x.key.join(SEP), d);
      resolved = resolved.filter((x) => x.key.join(SEP) !== key);
    }
  }
  return { active, resolved };
}

export function applyIncidentEvents(
  current: IncidentsResponse,
  events: IncidentEvent[],
): IncidentsResponse {
  let active = [...current.active];
  let history = [...current.history];
  for (const ev of events) {
    const inc = ev.incident;
    if (ev.event === "CLOSED") {
      active = active.filter((x) => x.id !== inc.id);
      history = upsert(history, (x) => String(x.id), inc);
    } else {
      active = upsert(active, (x) => String(x.id), inc);
      history = history.filter((x) => x.id !== inc.id);
    }
  }
  return { active, history };
}

export function applyCorrelationEvents(
  current: CorrelationResponse,
  events: CorrelationEvent[],
): CorrelationResponse {
  let active = [...current.active];
  let resolved = [...current.resolved];
  for (const ev of events) {
    const group = ev.incident;
    const key = group.key.join(SEP);
    if (ev.event === "RESOLVED") {
      active = active.filter((x) => x.key.join(SEP) !== key);
      resolved = upsert(resolved, (x) => x.key.join(SEP), group);
    } else {
      active = upsert(active, (x) => x.key.join(SEP), group);
      resolved = resolved.filter((x) => x.key.join(SEP) !== key);
    }
  }
  return { active, resolved };
}

/** Recompose the counts the backend aggregates (active per system/robot). */
export function recomputeCounts(data: DashboardData): DashboardData {
  const diag = new Map<string, number>();
  const inc = new Map<string, number>();
  const ownerKey = (system: string | null, robot: string | null) =>
    `${system ?? ""}${SEP}${robot ?? ""}`;

  for (const d of data.diagnostics.active) {
    const k = ownerKey(d.system, d.robot);
    diag.set(k, (diag.get(k) ?? 0) + 1);
  }
  for (const i of data.incidents.active) {
    const k = ownerKey(i.system, i.robot);
    inc.set(k, (inc.get(k) ?? 0) + 1);
  }

  const systems = data.systems.systems.map((sys) => ({
    ...sys,
    active_diagnostics: diag.get(ownerKey(sys.name, null)) ?? 0,
    robots: sys.robots.map((r) => ({
      ...r,
      active_diagnostics: diag.get(ownerKey(sys.name, r.name)) ?? 0,
      active_incidents: inc.get(ownerKey(sys.name, r.name)) ?? 0,
    })),
  }));

  const robots = data.robots.robots.map((r) => ({
    ...r,
    active_diagnostics: diag.get(ownerKey(r.system, r.name)) ?? 0,
    active_incidents: inc.get(ownerKey(r.system, r.name)) ?? 0,
  }));

  return {
    ...data,
    health: {
      ...data.health,
      active_diagnostics: data.diagnostics.active.length,
      active_incidents: data.incidents.active.length,
    },
    systems: { ...data.systems, systems },
    robots: { robots },
  };
}

/** Apply one cycle message to the current snapshot. Returns null when there is
 * no snapshot yet (the initial HTTP fetch arrives separately). */
export function applyCycle(
  data: DashboardData | null,
  message: CycleMessage,
): DashboardData | null {
  if (data === null) return null;
  const patched: DashboardData = {
    ...data,
    diagnostics: applyDiagnosticEvents(data.diagnostics, message.diagnostic_events),
    incidents: applyIncidentEvents(data.incidents, message.incident_events),
    correlation: applyCorrelationEvents(
      data.correlation,
      message.correlation_events,
    ),
  };
  return recomputeCounts(patched);
}
