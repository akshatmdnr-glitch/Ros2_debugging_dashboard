import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchDashboard, fetchIncident } from "./api";

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    statusText: ok ? "OK" : "Not Found",
    json: async () => body,
  } as Response;
}

describe("api service", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        if (path.endsWith("/health")) return jsonResponse({ status: "running" });
        if (path.endsWith("/systems")) return jsonResponse({ systems: [], unclassified: [] });
        if (path.endsWith("/robots")) return jsonResponse({ robots: [] });
        if (path.endsWith("/diagnostics")) return jsonResponse({ active: [], resolved: [] });
        if (path.endsWith("/incidents")) return jsonResponse({ active: [], history: [] });
        if (path.endsWith("/telemetry")) return jsonResponse({ topics: [], processes: [], tf: [] });
        if (path.endsWith("/correlation")) return jsonResponse({ active: [], resolved: [] });
        return jsonResponse({}, false, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches every resource into a dashboard snapshot", async () => {
    const data = await fetchDashboard();
    expect(data.health.status).toBe("running");
    expect(data.systems.systems).toEqual([]);
    expect(data.incidents.active).toEqual([]);
  });

  it("fetches a single incident by id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ id: 1, state: "ACTIVE", owner: "warehouse/robot2" }),
      ),
    );
    const inc = await fetchIncident("1");
    expect(inc.id).toBe(1);
    expect(inc.owner).toBe("warehouse/robot2");
  });

  it("rejects with the status when an incident does not exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "incident 99 not found" }, false, 404)),
    );
    await expect(fetchIncident("99")).rejects.toThrow(/404/);
  });

  it("rejects when the backend returns an error status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({}, false, 500)),
    );
    await expect(fetchDashboard()).rejects.toThrow(/500/);
  });

  it("rejects when the backend is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    await expect(fetchDashboard()).rejects.toThrow("fetch failed");
  });
});
