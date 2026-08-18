import type { ConnectionState } from "../hooks/useRealtime";

const LABELS: Record<ConnectionState, string> = {
  connecting: "CONNECTING",
  live: "LIVE",
  stale: "STALE",
  reconnecting: "RECONNECTING",
  disconnected: "DISCONNECTED",
};

function ConnectionBadge({ connection }: { connection: ConnectionState }) {
  return <span className={`badge badge-${connection}`}>{LABELS[connection]}</span>;
}

export function Header({
  connection,
  lastUpdated,
}: {
  connection: ConnectionState;
  lastUpdated: number | null;
}) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-dot" aria-hidden="true" />
        <span>
          ROS 2 <strong>Debugging Dashboard</strong>
        </span>
      </div>
      <div className="topbar-meta">
        <ConnectionBadge connection={connection} />
        {lastUpdated !== null && (
          <span className="muted mono" title="time of the last event or snapshot">
            as of {new Date(lastUpdated).toLocaleTimeString()}
            {connection === "stale" && " (stale)"}
          </span>
        )}
      </div>
    </header>
  );
}
