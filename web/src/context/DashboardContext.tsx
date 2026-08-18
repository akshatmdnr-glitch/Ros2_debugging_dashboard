// Shares ONE polled dashboard snapshot across all views via React context, so
// the router views read from a single source of truth instead of each running
// its own poller (and instead of prop-drilling through every page).

import { createContext, useContext, type ReactNode } from "react";

import { useDashboard, type DashboardState } from "../hooks/useDashboard";

const DashboardContext = createContext<DashboardState | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const state = useDashboard(2000);
  return (
    <DashboardContext.Provider value={state}>{children}</DashboardContext.Provider>
  );
}

export function useDashboardContext(): DashboardState {
  const state = useContext(DashboardContext);
  if (state === null) {
    throw new Error("useDashboardContext must be used inside <DashboardProvider>");
  }
  return state;
}
