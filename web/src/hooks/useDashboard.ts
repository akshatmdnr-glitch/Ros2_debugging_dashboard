// A small hook that owns the dashboard's data lifecycle: fetch once, then poll
// on a timer. It exposes loading / connected / error state so the UI can show
// what is happening instead of guessing.

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchDashboard, type DashboardData } from "../services/api";

export type ConnectionState = "loading" | "connected" | "error";

export interface DashboardState {
  data: DashboardData | null;
  connection: ConnectionState;
  error: string | null;
  lastUpdated: number | null;
  reload: () => void;
}

export function useDashboard(pollMs = 2000): DashboardState {
  const [data, setData] = useState<DashboardData | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const timerRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await fetchDashboard();
      setData(next);
      setConnection("connected");
      setError(null);
      setLastUpdated(Date.now());
    } catch (err) {
      setConnection("error");
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    timerRef.current = window.setInterval(() => void load(), pollMs);
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, [load, pollMs]);

  return { data, connection, error, lastUpdated, reload: () => void load() };
}
