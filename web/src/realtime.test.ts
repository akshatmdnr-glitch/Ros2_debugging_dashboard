// Pure-function tests for the real-time patch logic (web/src/realtime.ts).
// These prove the client applies events idempotently and recomputes the counts
// the backend would have produced -- with no fabricated data.

import { describe, expect, it } from "vitest";

import {
  applyCycle,
  applyDiagnosticEvents,
  applyIncidentEvents,
  recomputeCounts,
} from "./realtime";
import { makeCorrelationGroup, makeDashboard, makeDiagnostic, makeIncident } from "./test/fixtures";
import type { CycleMessage } from "./types";

function cycle(overrides: Partial<CycleMessage> = {}): CycleMessage {
  return {
    type: "cycle",
    seq: 2,
    server_time: 0,
    topology_changed: false,
    diagnostic_events: [],
    correlation_events: [],
    incident_events: [],
    ...overrides,
  };
}

describe("applyCycle", () => {
  it("returns null while there is no snapshot yet", () => {
    expect(applyCycle(null, cycle())).toBeNull();
  });

  it("leaves state unchanged for an empty cycle (no fake data)", () => {
    const data = makeDashboard();
    const next = applyCycle(data, cycle());
    expect(next).not.toBeNull();
    expect(next!.diagnostics).toEqual(data.diagnostics);
    expect(next!.incidents).toEqual(data.incidents);
    expect(next!.health.active_diagnostics).toBe(1);
    expect(next!.health.active_incidents).toBe(1);
  });

  it("activates a new diagnostic and adds it to the active set", () => {
    const data = makeDashboard();
    const tf = makeDiagnostic({
      rule_id: "tf_stale",
      tf_frame: "base_link",
      key: ["tf_stale", "warehouse", "robot2", null, null, "base_link", null],
      subject: "base_link",
    });
    const next = applyCycle(
      data,
      cycle({ diagnostic_events: [{ event: "ACTIVE", diagnostic: tf }] }),
    )!;
    expect(next.diagnostics.active).toHaveLength(2);
    expect(next.diagnostics.active.some((d) => d.rule_id === "tf_stale")).toBe(true);
    expect(next.health.active_diagnostics).toBe(2);
  });

  it("is idempotent: the same ACTIVE event twice does not duplicate", () => {
    const data = makeDashboard();
    const tf = makeDiagnostic({
      rule_id: "tf_stale",
      tf_frame: "base_link",
      key: ["tf_stale", "warehouse", "robot2", null, null, "base_link", null],
      subject: "base_link",
    });
    const message = cycle({ diagnostic_events: [{ event: "ACTIVE", diagnostic: tf }] });
    const once = applyCycle(data, message)!;
    const twice = applyCycle(once, message)!;
    expect(twice.diagnostics.active).toHaveLength(2);
  });

  it("resolves a diagnostic: moves it from active to resolved", () => {
    const data = makeDashboard();
    const resolved = makeDiagnostic({ state: "RESOLVED", timestamp: 200 });
    const next = applyCycle(
      data,
      cycle({ diagnostic_events: [{ event: "RESOLVED", diagnostic: resolved }] }),
    )!;
    expect(next.diagnostics.active).toHaveLength(0);
    expect(next.diagnostics.resolved.some((d) => d.rule_id === "high_cpu")).toBe(true);
    expect(next.health.active_diagnostics).toBe(0);
  });

  it("rapid updates compose in order (activate then resolve)", () => {
    const data = makeDashboard();
    const tf = makeDiagnostic({
      rule_id: "tf_stale",
      tf_frame: "base_link",
      key: ["tf_stale", "warehouse", "robot2", null, null, "base_link", null],
      subject: "base_link",
    });
    const next = applyCycle(
      applyCycle(
        data,
        cycle({ diagnostic_events: [{ event: "ACTIVE", diagnostic: tf }] }),
      )!,
      cycle({
        diagnostic_events: [
          { event: "RESOLVED", diagnostic: makeDiagnostic({ state: "RESOLVED", timestamp: 300 }) },
        ],
      }),
    )!;
    expect(next.diagnostics.active.map((d) => d.rule_id)).toEqual(["tf_stale"]);
    expect(next.health.active_diagnostics).toBe(1);
  });

  it("closes an incident: moves it from active to history", () => {
    const data = makeDashboard();
    const closed = makeIncident({
      state: "RECOVERED",
      ended_at: 200,
      duration: 100,
    });
    const next = applyCycle(
      data,
      cycle({ incident_events: [{ event: "CLOSED", incident: closed }] }),
    )!;
    expect(next.incidents.active).toHaveLength(0);
    expect(next.incidents.history.some((i) => i.id === 1)).toBe(true);
    expect(next.health.active_incidents).toBe(0);
  });

  it("is idempotent for incident updates (upsert by stable id)", () => {
    const data = makeDashboard();
    const message = cycle({
      incident_events: [
        { event: "UPDATED", incident: makeIncident({ active_count: 1 }) },
      ],
    });
    const once = applyCycle(data, message)!;
    const twice = applyCycle(once, message)!;
    expect(twice.incidents.active).toHaveLength(1);
  });

  it("resolves a correlation group: moves it from active to resolved", () => {
    const data = makeDashboard();
    const next = applyCycle(
      data,
      cycle({
        correlation_events: [
          { event: "RESOLVED", incident: makeCorrelationGroup({ key: ["/robot2/scan", "robot2_lidar_driver"] }) },
        ],
      }),
    )!;
    expect(next.correlation.active).toHaveLength(0);
    expect(next.correlation.resolved).toHaveLength(1);
  });
});

describe("recomputeCounts", () => {
  it("mirrors the backend's aggregation on untouched data", () => {
    const next = recomputeCounts(makeDashboard());
    expect(next.health.active_diagnostics).toBe(1);
    expect(next.health.active_incidents).toBe(1);
  });

  it("recomputes health and per-robot counts from the patched verdicts", () => {
    const data = makeDashboard({
      systems: {
        systems: [
          {
            name: "warehouse",
            system_nodes: [],
            active_diagnostics: 1,
            robots: [
              {
                name: "robot2",
                nodes: ["/robot2/lidar"],
                active_diagnostics: 1,
                active_incidents: 1,
              },
            ],
          },
        ],
        unclassified: [],
      },
      robots: {
        robots: [
          {
            system: "warehouse",
            name: "robot2",
            nodes: ["/robot2/lidar"],
            active_diagnostics: 1,
            active_incidents: 1,
          },
        ],
      },
    });
    const next = applyCycle(
      data,
      cycle({
        diagnostic_events: [
          { event: "RESOLVED", diagnostic: makeDiagnostic({ state: "RESOLVED", timestamp: 200 }) },
        ],
        incident_events: [
          { event: "CLOSED", incident: makeIncident({ state: "RECOVERED", ended_at: 200, duration: 100 }) },
        ],
      }),
    )!;
    expect(next.health.active_diagnostics).toBe(0);
    expect(next.health.active_incidents).toBe(0);
    expect(next.systems.systems[0].robots[0].active_diagnostics).toBe(0);
    expect(next.systems.systems[0].robots[0].active_incidents).toBe(0);
    expect(next.robots.robots[0].active_diagnostics).toBe(0);
    expect(next.robots.robots[0].active_incidents).toBe(0);
  });
});

describe("applyDiagnosticEvents / applyIncidentEvents", () => {
  it("survives duplicate and out-of-order events without losing state", () => {
    const data = makeDashboard();
    // Duplicate RESOLVED then the same RESOLVED again: harmless.
    const resolved = makeDiagnostic({ state: "RESOLVED", timestamp: 200 });
    const twice = applyDiagnosticEvents(
      applyDiagnosticEvents(data.diagnostics, [{ event: "RESOLVED", diagnostic: resolved }]),
      [{ event: "RESOLVED", diagnostic: resolved }],
    );
    expect(twice.active).toHaveLength(0);
    expect(twice.resolved.filter((d) => d.rule_id === "high_cpu")).toHaveLength(1);
  });

  it("re-activates a previously resolved diagnostic (set semantics)", () => {
    const data = makeDashboard();
    const resolved = makeDiagnostic({ state: "RESOLVED", timestamp: 200 });
    const afterResolve = applyDiagnosticEvents(data.diagnostics, [
      { event: "RESOLVED", diagnostic: resolved },
    ]);
    const reActivated = makeDiagnostic({ timestamp: 300 });
    const afterActivate = applyDiagnosticEvents(afterResolve, [
      { event: "ACTIVE", diagnostic: reActivated },
    ]);
    expect(afterActivate.active).toHaveLength(1);
    expect(afterActivate.resolved).toHaveLength(0);
  });

  it("deduplicates incident events by stable id", () => {
    const data = makeDashboard();
    const message = { event: "UPDATED" as const, incident: makeIncident() };
    const once = applyIncidentEvents(data.incidents, [message, message]);
    expect(once.active).toHaveLength(1);
  });
});
