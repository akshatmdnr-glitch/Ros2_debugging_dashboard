import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { IncidentPanel } from "../components/IncidentPanel";
import { SystemOverview } from "../components/SystemOverview";
import { useDashboardContext } from "../context/DashboardContext";
import { buildRobotViews } from "../status";

export function OverviewPage() {
  const { data } = useDashboardContext();
  if (data === null) return null;

  return (
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
  );
}
