import type { ConnectionState } from "../hooks/useDashboard";

function ConnectionBadge({ connection }: { connection: ConnectionState }) {
  return (
    <span className={`badge badge-${connection}`}>
      {connection === "loading" ? "CONNECTING" : connection === "connected" ? "CONNECTED" : "OFFLINE"}
    </span>
  );
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
          <span className="muted mono" title="time of the last successful poll">
            updated {new Date(lastUpdated).toLocaleTimeString()}
          </span>
        )}
      </div>
    </header>
  );
}
