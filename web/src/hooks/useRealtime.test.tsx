// Hook tests for the real-time connection state machine (useRealtime).
// WebSocket is stubbed with MockWebSocket so every transition
// (LIVE / STALE / RECONNECTING / DISCONNECTED) is driven deterministically.

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchDashboard } from "../services/api";
import { makeDiagnostic, makeDashboard } from "../test/fixtures";
import { MockWebSocket } from "../test/mockWebSocket";
import { useRealtime } from "./useRealtime";

vi.mock("../services/api", () => ({
  fetchDashboard: vi.fn(),
  streamUrl: vi.fn(() => "ws://localhost:8000/ws/stream"),
}));

const mockedFetch = vi.mocked(fetchDashboard);

beforeEach(() => {
  vi.stubGlobal("WebSocket", MockWebSocket);
  MockWebSocket.reset();
  mockedFetch.mockResolvedValue(makeDashboard());
});

afterEach(() => {
  vi.unstubAllGlobals();
  MockWebSocket.reset();
});

describe("useRealtime", () => {
  it("goes LIVE once the socket is open and the snapshot has arrived", async () => {
    MockWebSocket.autoOpen = true;
    const { result } = renderHook(() => useRealtime());
    await waitFor(() => expect(result.current.connection).toBe("live"));
    expect(result.current.data).not.toBeNull();
    expect(result.current.lastUpdated).not.toBeNull();
  });

  it("shows DISCONNECTED when the backend never connects", async () => {
    MockWebSocket.autoOpen = false;
    mockedFetch.mockRejectedValue(new Error("fetch failed"));
    const { result } = renderHook(() => useRealtime());
    await waitFor(() => expect(result.current.connection).toBe("disconnected"));
    expect(result.current.error).not.toBeNull();
  });

  it("applies a cycle message to the snapshot without a refetch", async () => {
    MockWebSocket.autoOpen = true;
    const { result } = renderHook(() => useRealtime());
    await waitFor(() => expect(result.current.connection).toBe("live"));
    const fetchCalls = mockedFetch.mock.calls.length;
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.serverSend({
        type: "cycle",
        seq: 2,
        server_time: 0,
        topology_changed: false,
        diagnostic_events: [
          {
            event: "ACTIVE",
            diagnostic: makeDiagnostic({
              rule_id: "tf_stale",
              tf_frame: "base_link",
              key: ["tf_stale", "warehouse", "robot2", null, null, "base_link", null],
              subject: "base_link",
            }),
          },
        ],
        correlation_events: [],
        incident_events: [],
      });
    });

    expect(result.current.data!.diagnostics.active.some((d) => d.rule_id === "tf_stale")).toBe(true);
    expect(mockedFetch.mock.calls.length).toBe(fetchCalls);
  });

  it("refetches the full snapshot when the topology changed", async () => {
    MockWebSocket.autoOpen = true;
    const { result } = renderHook(() => useRealtime());
    await waitFor(() => expect(result.current.connection).toBe("live"));
    const fetchCalls = mockedFetch.mock.calls.length;
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.serverSend({
        type: "cycle",
        seq: 2,
        server_time: 0,
        topology_changed: true,
        diagnostic_events: [],
        correlation_events: [],
        incident_events: [],
      });
    });

    await waitFor(() => expect(mockedFetch.mock.calls.length).toBeGreaterThan(fetchCalls));
  });

  it("ignores a cycle before the first snapshot exists", async () => {
    MockWebSocket.autoOpen = true;
    mockedFetch.mockImplementation(() => new Promise(() => {}));
    const { result } = renderHook(() => useRealtime());
    await act(async () => {});
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.serverSend({
        type: "cycle",
        seq: 1,
        server_time: 0,
        topology_changed: false,
        diagnostic_events: [
          { event: "ACTIVE", diagnostic: makeDiagnostic() },
        ],
        correlation_events: [],
        incident_events: [],
      });
    });

    expect(result.current.data).toBeNull();
  });

  it("marks the view STALE on a drop, then recovers to LIVE via reconnect", async () => {
    MockWebSocket.autoOpen = true;
    const { result } = renderHook(() => useRealtime());
    await waitFor(() => expect(result.current.connection).toBe("live"));
    const ws = MockWebSocket.instances[0];

    act(() => ws.serverClose());
    expect(result.current.connection).toBe("stale");

    // Backoff retry -> a fresh connection (autoOpen) -> LIVE again, re-synced.
    await waitFor(() => expect(result.current.connection).toBe("live"), {
      timeout: 4000,
    });
    expect(MockWebSocket.instances.length).toBeGreaterThanOrEqual(2);
  });
});
