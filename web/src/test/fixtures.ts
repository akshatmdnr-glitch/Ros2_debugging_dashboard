// Shared test fixtures: minimal but contract-shaped dashboard data so hook and
// patch-logic tests read naturally. The shapes mirror web/src/types.ts.

import type { DashboardData } from "../services/api";
import type {
  CorrelationGroup,
  Diagnostic,
  Incident,
} from "../types";

export function makeDiagnostic(overrides: Partial<Diagnostic> = {}): Diagnostic {
  return {
    key: ["high_cpu", "warehouse", "robot2", null, null, null, "robot2_lidar_driver"],
    rule_id: "high_cpu",
    severity: "WARNING",
    message: "process 'robot2_lidar_driver' is using high CPU (95.0%)",
    evidence: ["cpu=95.0%"],
    timestamp: 100,
    state: "ACTIVE",
    subject: "robot2_lidar_driver",
    system: "warehouse",
    robot: "robot2",
    node: null,
    topic: null,
    tf_frame: null,
    process: "robot2_lidar_driver",
    ...overrides,
  };
}

export function makeIncident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: 1,
    state: "ACTIVE",
    owner: "warehouse/robot2",
    system: "warehouse",
    robot: "robot2",
    confidence: "HIGH",
    strategies: ["entity", "resource"],
    started_at: 100,
    ended_at: null,
    duration: null,
    members: ["robot2_lidar_driver", "/robot2/scan"],
    member_count: 2,
    active_count: 2,
    events: [
      { timestamp: 100, transition: "ACTIVATED", subject: "robot2_lidar_driver" },
      { timestamp: 101, transition: "ACTIVATED", subject: "/robot2/scan" },
    ],
    ...overrides,
  };
}

export function makeCorrelationGroup(
  overrides: Partial<CorrelationGroup> = {},
): CorrelationGroup {
  return {
    key: ["/robot2/scan", "robot2_lidar_driver"],
    owner: "warehouse/robot2",
    system: "warehouse",
    robot: "robot2",
    confidence: "HIGH",
    strategies: ["entity", "resource"],
    hypothesis: "Resource pressure may be a contributing factor.",
    evidence: ["members=2", "signals=entity,resource"],
    attribution_uncertain: false,
    members: [],
    ...overrides,
  };
}

export function makeDashboard(overrides: Partial<DashboardData> = {}): DashboardData {
  const base: DashboardData = {
    health: {
      status: "running",
      uptime: 10,
      systems: 1,
      nodes: 1,
      topics: 1,
      active_diagnostics: 1,
      active_incidents: 1,
    },
    systems: { systems: [], unclassified: [] },
    robots: { robots: [] },
    nodes: { nodes: [] },
    topics: { topics: [] },
    diagnostics: { active: [makeDiagnostic()], resolved: [] },
    incidents: { active: [makeIncident()], history: [] },
    telemetry: { topics: [], processes: [], tf: { frames: [], edges: [] } },
    correlation: { active: [makeCorrelationGroup()], resolved: [] },
  };
  return { ...base, ...overrides };
}
