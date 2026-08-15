// Frontend-only derivation of a robot's visual status from the API data.
// The backend deliberately does NOT judge "healthy/degraded"; the view does,
// purely from the counts/severities it is given.

import type { Diagnostic, Incident, Robot, RobotStatus, RobotView } from "./types";

export function deriveStatus(
  diagnostics: Diagnostic[],
  activeIncidents: number,
): RobotStatus {
  if (diagnostics.some((d) => d.severity === "ERROR")) return "CRITICAL";
  if (diagnostics.length > 0 || activeIncidents > 0) return "WARNING";
  return "HEALTHY";
}

export function buildRobotViews(
  robots: Robot[],
  activeDiagnostics: Diagnostic[],
  activeIncidents: Incident[],
): RobotView[] {
  const byOwner = new Map<string, Diagnostic[]>();
  for (const d of activeDiagnostics) {
    if (d.system && d.robot) {
      const key = `${d.system}/${d.robot}`;
      const list = byOwner.get(key) ?? [];
      list.push(d);
      byOwner.set(key, list);
    }
  }
  return robots.map((r) => ({
    ...r,
    diagnostics: byOwner.get(`${r.system}/${r.name}`) ?? [],
    status: deriveStatus(
      byOwner.get(`${r.system}/${r.name}`) ?? [],
      r.active_incidents +
        activeIncidents.filter(
          (i) => i.system === r.system && i.robot === r.name,
        ).length,
    ),
  }));
}
