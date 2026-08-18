// Shares ONE real-time dashboard state across all views via React context, so
// the router views read from a single source of truth instead of each running
// its own poller (and instead of prop-drilling through every page).

import { createContext, useContext, type ReactNode } from "react";

import { useRealtime, type RealtimeState } from "../hooks/useRealtime";

const DashboardContext = createContext<RealtimeState | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const state = useRealtime();
  return (
    <DashboardContext.Provider value={state}>{children}</DashboardContext.Provider>
  );
}

export function useDashboardContext(): RealtimeState {
  const state = useContext(DashboardContext);
  if (state === null) {
    throw new Error("useDashboardContext must be used inside <DashboardProvider>");
  }
  return state;
}
