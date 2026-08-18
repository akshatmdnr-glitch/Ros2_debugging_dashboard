import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { fetchDashboard, fetchIncident } from "./services/api";
import type { DashboardData } from "./services/api";
import { MockWebSocket } from "./test/mockWebSocket";

vi.mock("./services/api", () => ({
  fetchDashboard: vi.fn(),
  fetchIncident: vi.fn(),
  streamUrl: vi.fn(() => "ws://localhost:8000/ws/stream"),
}));

const mockedFetch = vi.mocked(fetchDashboard);
const mockedIncident = vi.mocked(fetchIncident);

function demoData(): DashboardData {
  return {
    health: {
      status: "running",
      uptime: 10,
      systems: 1,
      nodes: 2,
      topics: 1,
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
    nodes: {
      nodes: [
        { fqn: "/robot1/talker", system: "warehouse", robot: "robot1", source: "config", confident: true },
        { fqn: "/robot2/lidar", system: "warehouse", robot: "robot2", source: "config", confident: true },
      ],
    },
    topics: {
      topics: [
        {
          name: "/robot2/scan",
          type: "sensor_msgs/msg/LaserScan",
          publishers: 1,
          subscribers: 0,
          publisher_nodes: ["/robot2/lidar"],
          subscriber_nodes: [],
        },
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
        {
          key: ["frequency_degradation", "warehouse", "robot2", null, "/robot2/scan", null, null],
          rule_id: "frequency_degradation",
          severity: "WARNING",
          message: "/robot2/scan frequency 1.20 Hz is below the expected minimum 8.0 Hz",
          evidence: ["observed=1.20Hz"],
          timestamp: 0,
          state: "ACTIVE",
          subject: "/robot2/scan",
          system: "warehouse",
          robot: "robot2",
          node: null,
          topic: "/robot2/scan",
          tf_frame: null,
          process: null,
        },
        {
          key: ["tf_stale", "warehouse", "robot2", null, null, "base_link", null],
          rule_id: "tf_stale",
          severity: "WARNING",
          message: "required TF frame 'base_link' is stale",
          evidence: ["last_seen=4.0s ago"],
          timestamp: 0,
          state: "ACTIVE",
          subject: "base_link",
          system: "warehouse",
          robot: "robot2",
          node: null,
          topic: null,
          tf_frame: "base_link",
          process: null,
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
      history: [
        {
          id: 2,
          state: "RECOVERED",
          owner: "warehouse/robot1",
          system: "warehouse",
          robot: "robot1",
          confidence: "MEDIUM",
          strategies: ["entity", "temporal"],
          started_at: 500,
          ended_at: 530,
          duration: 30,
          members: ["/robot1/chatter"],
          member_count: 1,
          active_count: 0,
          events: [
            { timestamp: 500, transition: "ACTIVATED", subject: "/robot1/chatter" },
            { timestamp: 530, transition: "RECOVERED", subject: "/robot1/chatter" },
          ],
        },
      ],
    },
    telemetry: {
      topics: [
        {
          topic: "/robot2/scan",
          type: "sensor_msgs/msg/LaserScan",
          monitored: true,
          receiving: true,
          message_count: 50,
          rate_hz: 1.2,
          idle_seconds: 0.4,
          reason: "subscribed",
        },
      ],
      processes: [],
      tf: {
        frames: [
          { frame_id: "map", count: 10, last_seen: 100 },
          { frame_id: "odom", count: 9, last_seen: 100 },
          { frame_id: "base_link", count: 8, last_seen: 96 },
        ],
        edges: [
          { parent: "map", child: "odom" },
          { parent: "odom", child: "base_link" },
        ],
      },
    },
    correlation: { active: [], resolved: [] },
  };
}

function renderApp(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("WebSocket", MockWebSocket);
    MockWebSocket.reset();
    MockWebSocket.autoOpen = true;
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    MockWebSocket.reset();
  });

  it("shows a connecting state before the first response", async () => {
    mockedFetch.mockImplementation(() => new Promise(() => {}));
    renderApp();
    expect(screen.getByText(/Connecting to the debugger backend/)).toBeInTheDocument();
  });

  it("shows an error banner when the backend is unreachable", async () => {
    // autoOpen=false simulates a failed WebSocket handshake (backend down).
    MockWebSocket.reset();
    MockWebSocket.autoOpen = false;
    mockedFetch.mockRejectedValue(new Error("fetch failed"));
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/Backend unreachable/)).toBeInTheDocument();
    });
    expect(screen.getByText("DISCONNECTED")).toBeInTheDocument();
  });

  it("renders the overview: systems, robot statuses, diagnostics, incidents", async () => {
    mockedFetch.mockResolvedValue(demoData());
    renderApp();
    await waitFor(() => {
      expect(screen.getByText("warehouse")).toBeInTheDocument();
    });
    expect(screen.getByText("robot1")).toBeInTheDocument();
    expect(screen.getByText("HEALTHY")).toBeInTheDocument();
    expect(screen.getByText("DEGRADED")).toBeInTheDocument();
    expect(screen.getAllByText("high_cpu").length).toBeGreaterThan(0);
    expect(screen.getByText("#1 · warehouse/robot2")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("shows honest empty states when there is no data (no fake data)", async () => {
    mockedFetch.mockResolvedValue({
      health: { status: "running", uptime: 0, systems: 0, nodes: 0, topics: 0, active_diagnostics: 0, active_incidents: 0 },
      systems: { systems: [], unclassified: [] },
      robots: { robots: [] },
      nodes: { nodes: [] },
      topics: { topics: [] },
      diagnostics: { active: [], resolved: [] },
      incidents: { active: [], history: [] },
      telemetry: { topics: [], processes: [], tf: { frames: [], edges: [] } },
      correlation: { active: [], resolved: [] },
    });
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/No systems discovered/)).toBeInTheDocument();
    });
    expect(screen.getByText("No active diagnostics.")).toBeInTheDocument();
    expect(screen.getByText(/No active incidents/)).toBeInTheDocument();
  });

  it("navigates to the graph view and highlights a problem topic", async () => {
    mockedFetch.mockResolvedValue(demoData());
    renderApp();
    await waitFor(() => expect(screen.getByText("LIVE")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("link", { name: "Graph" }));
    await waitFor(() => expect(screen.getByText("ROS graph")).toBeInTheDocument());
    // Nodes (from /nodes + topic endpoint nodes) and topics are rendered.
    expect(screen.getByText("/robot2/lidar")).toBeInTheDocument();
    expect(screen.getByText("/robot2/scan")).toBeInTheDocument();
    // /robot2/scan is the subject of an active diagnostic -> highlighted.
    const problemBox = document.querySelector(".topic-box.problem");
    expect(problemBox).not.toBeNull();
  });

  it("navigates to the TF tree view and highlights a stale frame", async () => {
    mockedFetch.mockResolvedValue(demoData());
    renderApp();
    await waitFor(() => expect(screen.getByText("LIVE")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("link", { name: "TF" }));
    await waitFor(() => expect(screen.getByText("TF tree")).toBeInTheDocument());
    expect(screen.getByText("map")).toBeInTheDocument();
    expect(screen.getByText("base_link")).toBeInTheDocument();
    // base_link is the subject of an active tf diagnostic -> highlighted.
    expect(document.querySelector(".tf-box.problem")).not.toBeNull();
  });

  it("navigates to the incidents view showing history", async () => {
    mockedFetch.mockResolvedValue(demoData());
    renderApp();
    await waitFor(() => expect(screen.getByText("LIVE")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("link", { name: "Incidents" }));
    await waitFor(() => expect(screen.getByText("Incident history (1)")).toBeInTheDocument());
    expect(screen.getByText("RECOVERED")).toBeInTheDocument();
  });

  it("shows a single incident's timeline at /incidents/1", async () => {
    mockedFetch.mockResolvedValue(demoData());
    mockedIncident.mockResolvedValue(demoData().incidents.active[0]);
    renderApp("/incidents/1");
    await waitFor(() => {
      expect(screen.getByText(/Incident #1 · warehouse\/robot2/)).toBeInTheDocument();
    });
    expect(screen.getByText("Timeline")).toBeInTheDocument();
    expect(screen.getByText("t+0.0s")).toBeInTheDocument();
    expect(screen.getByText("t+1.0s")).toBeInTheDocument();
  });

  it("shows a not-found state for an unknown incident", async () => {
    mockedFetch.mockResolvedValue(demoData());
    mockedIncident.mockRejectedValue(new Error("GET /incidents/99 -> 404 Not Found"));
    renderApp("/incidents/99");
    await waitFor(() => {
      expect(screen.getByText(/Incident #99 not found/)).toBeInTheDocument();
    });
  });

  it("navigates to the telemetry view showing topic telemetry", async () => {
    mockedFetch.mockResolvedValue(demoData());
    renderApp();
    await waitFor(() => expect(screen.getByText("LIVE")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("link", { name: "Telemetry" }));
    await waitFor(() => expect(screen.getByText("/robot2/scan")).toBeInTheDocument());
    expect(screen.getByText("1.20")).toBeInTheDocument();
  });
});
