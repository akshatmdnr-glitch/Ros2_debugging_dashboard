import { describe, expect, it } from "vitest";

import { buildRobotViews, deriveStatus } from "./status";
import type { Diagnostic, Incident, Robot } from "./types";

function diag(
  rule: string,
  severity: string,
  subject: string,
  robot = "robot2",
): Diagnostic {
  return {
    key: [rule, "warehouse", robot, null, null, null, subject],
    rule_id: rule,
    severity,
    message: "m",
    evidence: ["e"],
    timestamp: 0,
    state: "ACTIVE",
    subject,
    system: "warehouse",
    robot,
    node: null,
    topic: null,
    tf_frame: null,
    process: null,
  };
}

const robot: Robot = {
  system: "warehouse",
  name: "robot2",
  nodes: ["/robot2/lidar"],
  active_diagnostics: 0,
  active_incidents: 0,
};

describe("status derivation", () => {
  it("healthy when nothing is active", () => {
    expect(deriveStatus([], 0)).toBe("HEALTHY");
  });

  it("warning when a warning diagnostic or an incident is active", () => {
    expect(deriveStatus([diag("high_cpu", "WARNING", "nav")], 0)).toBe("WARNING");
    expect(deriveStatus([], 1)).toBe("WARNING");
  });

  it("critical when an ERROR diagnostic is active", () => {
    expect(deriveStatus([diag("node_disappeared", "ERROR", "/x")], 0)).toBe("CRITICAL");
  });

  it("attaches only that robot's diagnostics in the views", () => {
    const robot1: Robot = {
      system: "warehouse",
      name: "robot1",
      nodes: ["/robot1/talker"],
      active_diagnostics: 1,
      active_incidents: 0,
    };
    const views = buildRobotViews(
      [robot, robot1],
      [
        diag("high_cpu", "WARNING", "nav", "robot2"),
        diag("stale_topic", "WARNING", "/robot1/other", "robot1"),
      ],
      [] as Incident[],
    );
    const r2 = views.find((v) => v.name === "robot2")!;
    expect(r2.status).toBe("WARNING");
    expect(r2.diagnostics).toHaveLength(1);
    expect(r2.diagnostics[0].subject).toBe("nav");
  });
});
