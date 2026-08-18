import { Route, Routes } from "react-router-dom";

import { Header } from "./components/Header";
import { NavBar } from "./components/NavBar";
import { useDashboardContext } from "./context/DashboardContext";
import { GraphPage } from "./pages/GraphPage";
import { IncidentDetailPage } from "./pages/IncidentDetailPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { TelemetryPage } from "./pages/TelemetryPage";
import { TfPage } from "./pages/TfPage";

export function AppShell() {
  const { connection, error, lastUpdated } = useDashboardContext();

  return (
    <div className="app">
      <Header connection={connection} lastUpdated={lastUpdated} />
      <NavBar />
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
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/tf" element={<TfPage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="/incidents/:id" element={<IncidentDetailPage />} />
          <Route path="/telemetry" element={<TelemetryPage />} />
        </Routes>
      </main>
    </div>
  );
}
