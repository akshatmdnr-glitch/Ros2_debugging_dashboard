import type { RobotStatus } from "../types";

const LABEL: Record<string, string> = {
  HEALTHY: "HEALTHY",
  WARNING: "DEGRADED",
  CRITICAL: "CRITICAL",
  UNKNOWN: "UNKNOWN",
  loading: "CONNECTING",
  connected: "CONNECTED",
  error: "OFFLINE",
};

export function StatusBadge({ status }: { status: RobotStatus }) {
  const cls = status === "WARNING" ? "warning" : status.toLowerCase();
  return <span className={`badge badge-${cls}`}>{LABEL[status] ?? status}</span>;
}
