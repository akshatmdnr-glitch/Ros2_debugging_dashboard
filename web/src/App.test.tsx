import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { fetchDashboard } from "./services/api";
import type { DashboardData } from "./services/api";

vi.mock("./services/api", () => ({
  fetchDashboard: vi.fn(),
}));

const mockedFetch = vi.mocked(fetchDashboard);

function demoData(): DashboardData {
  return {
    health: {
      status: "running",
      uptime: 10,
      systems: 1,
      nodes: 3,
      topics: 2,
      active_diagnostics: 3,
      active_incidents: 1,
    },
    systems: {
      systems: [
        {
          name: "warehouse",
          system_nodes: [],
          active_diagnostics: 3,
          robots: [
            { name: "robot1", nodes: ["/robot1/talker"], active_diagnostics: 0, active_incidents: 0 },
            { name: "robot2", nodes: ["/robot2/lidar"], active_diagnostics: 3, active_incidents: 1 },
          ],
        },
      ],
      unclassified: [],
    },
    robots: {
      robots: [
        { system: "warehouse", name: "robot1", nodes: ["/robot1/talker"], active_diagnostics: 0, active_incidents: 0 },
        { system: "warehouse", name: "robot2", nodes: ["/robot2/lidar"], active_diagnostics: 3, active_incidents: 1 },
      ],
    },
    diagnostics: {
      active: [
        {
          key: ["high_cpu", "warehouse", "robot2", null, null, null, "robot2_lidar_driver"],
          rule_id: "high_cpu",
          severity: "WARNING",
          message: "process 'robot2_lidar_driver' is using high CPU (95.0%)",
          evidence: ["cpu=95.0%"],
          timestamp: 0,
          state: "ACTIVE",
          subject: "robot2_lidar_driver",
          system: "warehouse",
          robot: "robot2",
          node: null,
          topic: null,
          tf_frame: null,
          process: "robot2_lidar_driver",
        },
      ],
      resolved: [],
    },
    incidents: {
      active: [
        {
          id: 1,
          state: "ACTIVE",
          owner: "warehouse/robot2",
          system: "warehouse",
          robot: "robot2",
          confidence: "HIGH",
          strategies: ["entity", "resource", "temporal"],
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
        },
      ],
      history: [],
    },
    telemetry: { topics: [], processes: [], tf: [] },
    correlation: { active: [], resolved: [] },
  };
}

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows a connecting state before the first response", async () => {
    mockedFetch.mockImplementation(() => new Promise(() => {}));
    render(<App />);
    expect(screen.getByText(/Connecting to the debugger backend/)).toBeInTheDocument();
  });

  it("shows an error banner when the backend is unreachable", async () => {
    mockedFetch.mockRejectedValue(new Error("fetch failed"));
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/Backend unreachable/)).toBeInTheDocument();
    });
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
  });

  it("renders systems, robot statuses, diagnostics, and incidents from API data", async () => {
    mockedFetch.mockResolvedValue(demoData());
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText("warehouse")).toBeInTheDocument();
    });
    expect(screen.getByText("robot1")).toBeInTheDocument();
    expect(screen.getByText("HEALTHY")).toBeInTheDocument();
    expect(screen.getByText("DEGRADED")).toBeInTheDocument();
    expect(screen.getAllByText("high_cpu").length).toBeGreaterThan(0);
    expect(screen.getByText("#1 · warehouse/robot2")).toBeInTheDocument();
    expect(screen.getByText("CONNECTED")).toBeInTheDocument();
  });

  it("shows honest empty states when there is no data (no fake data)", async () => {
    mockedFetch.mockResolvedValue({
      health: { status: "running", uptime: 0, systems: 0, nodes: 0, topics: 0, active_diagnostics: 0, active_incidents: 0 },
      systems: { systems: [], unclassified: [] },
      robots: { robots: [] },
      diagnostics: { active: [], resolved: [] },
      incidents: { active: [], history: [] },
      telemetry: { topics: [], processes: [], tf: [] },
      correlation: { active: [], resolved: [] },
    });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/No systems discovered/)).toBeInTheDocument();
    });
    expect(screen.getByText("No active diagnostics.")).toBeInTheDocument();
    expect(screen.getByText(/No active incidents/)).toBeInTheDocument();
  });
});
