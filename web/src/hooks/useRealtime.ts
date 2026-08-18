// Owns the dashboard's real-time data lifecycle (Phase 11).
//
// Pattern: initial full HTTP snapshot, then small WebSocket patches. The
// backend is the single source of truth; this hook only re-syncs (full
// snapshot on every connect) and applies the events it broadcasts.
//
// Connection states (the UI must never guess):
//   connecting    — waiting for the first snapshot / socket open,
//   live          — socket open AND snapshot current,
//   stale         — we have data but the socket dropped (data is old),
//   reconnecting  — actively retrying after a drop,
//   disconnected  — never reached the backend.
//
// On every (re)connect we refetch the full snapshot: events missed while
// disconnected are never replayed, so a fresh snapshot is the only safe
// convergence. If WebSocket is unavailable entirely, we fall back to polling.

import { useCallback, useEffect, useRef, useState } from "react";

import { applyCycle } from "../realtime";
import {
  fetchDashboard,
  streamUrl,
  type DashboardData,
} from "../services/api";
import type { RealtimeMessage } from "../types";

export type ConnectionState =
  | "connecting"
  | "live"
  | "stale"
  | "reconnecting"
  | "disconnected";

export interface RealtimeState {
  data: DashboardData | null;
  connection: ConnectionState;
  error: string | null;
  lastUpdated: number | null;
  reload: () => void;
}

const POLL_MS = 2000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

export function useRealtime(): RealtimeState {
  const [data, setData] = useState<DashboardData | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const reloadRef = useRef<() => void>(() => {});

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let retryTimer: number | null = null;
    let retryDelay = RECONNECT_BASE_MS;
    let everConnected = false;
    let currentData: DashboardData | null = null;
    let pollTimer: number | null = null;
    let polling = false;

    const loadSnapshot = async () => {
      try {
        const next = await fetchDashboard();
        if (disposed) return;
        currentData = next;
        setData(next);
        setLastUpdated(Date.now());
        setError(null);
        if (socket && socket.readyState === WebSocket.OPEN) {
          setConnection("live");
        } else if (polling) {
          setConnection("live");
        }
      } catch (err) {
        if (disposed) return;
        setError(err instanceof Error ? err.message : String(err));
        if (socket === null) setConnection("disconnected");
      }
    };

    const startPolling = () => {
      if (pollTimer !== null) return;
      polling = true;
      void loadSnapshot();
      pollTimer = window.setInterval(() => void loadSnapshot(), POLL_MS);
    };

    const scheduleReconnect = () => {
      if (retryTimer !== null || disposed) return;
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
        if (disposed) return;
        setConnection("reconnecting");
        openSocket();
      }, retryDelay);
    };

    const openSocket = () => {
      if (typeof WebSocket === "undefined") {
        startPolling();
        return;
      }
      let ws: WebSocket;
      try {
        ws = new WebSocket(streamUrl());
      } catch {
        // WebSocket exists but cannot be constructed (blocked/unsupported):
        // degrade to polling rather than reconnect-looping forever.
        startPolling();
        return;
      }
      socket = ws;
      ws.onopen = () => {
        if (disposed) return;
        everConnected = true;
        retryDelay = RECONNECT_BASE_MS;
        // Keep "connecting" until the re-sync snapshot arrives so the UI never
        // flashes LIVE over no/old data.
        if (currentData) setConnection("live");
        void loadSnapshot();
      };
      ws.onmessage = (event) => {
        if (disposed) return;
        let message: RealtimeMessage;
        try {
          message = JSON.parse(event.data as string) as RealtimeMessage;
        } catch {
          return;
        }
        if (message.type !== "cycle") return;
        if (message.topology_changed) {
          // Structure changed; only the backend can re-derive attribution, so
          // we re-sync the whole snapshot instead of guessing.
          void loadSnapshot();
        } else {
          const next = applyCycle(currentData, message);
          if (next === null) return;
          currentData = next;
          setData(next);
          setLastUpdated(Date.now());
        }
      };
      ws.onerror = () => {
        ws.close();
      };
      ws.onclose = () => {
        if (socket === ws) socket = null;
        if (disposed) return;
        setConnection(everConnected ? "stale" : "disconnected");
        scheduleReconnect();
      };
    };

    reloadRef.current = () => void loadSnapshot();

    openSocket();
    // Fetch the initial snapshot immediately rather than waiting for the
    // socket to open: faster first paint, and a reachable-but-WS-less backend
    // still surfaces a meaningful error instead of infinite connecting.
    void loadSnapshot();

    return () => {
      disposed = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      if (pollTimer !== null) window.clearInterval(pollTimer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, []);

  return {
    data,
    connection,
    error,
    lastUpdated,
    reload: useCallback(() => reloadRef.current(), []),
  };
}
