import { DiagnosticPanel } from "./components/DiagnosticPanel";
import { Header } from "./components/Header";
import { IncidentPanel } from "./components/IncidentPanel";
import { SystemOverview } from "./components/SystemOverview";
import { useDashboard } from "./hooks/useDashboard";
import { buildRobotViews } from "./status";

export default function App() {
  const { data, connection, error, lastUpdated } = useDashboard(2000);

  return (
    <div className="app">
      <Header connection={connection} lastUpdated={lastUpdated} />
      <main className="content">
        {connection === "loading" && (
          <p className="empty">Connecting to the debugger backend…</p>
        )}

        {connection === "error" && (
          <div className="banner banner-error" role="alert">
            <strong>Backend unreachable.</strong> {error ?? "unknown error"} — is{" "}
            <code>debugger-api</code> running at{" "}
            <code>http://localhost:8000</code>?
          </div>
        )}

        {data && (
          <>
            <SystemOverview
              systems={data.systems.systems}
              robots={buildRobotViews(
                data.robots.robots,
                data.diagnostics.active,
                data.incidents.active,
              )}
              unclassified={data.systems.unclassified}
            />
            <DiagnosticPanel active={data.diagnostics.active} />
            <IncidentPanel
              active={data.incidents.active}
              historyCount={data.incidents.history.length}
            />
          </>
        )}
      </main>
    </div>
  );
}
