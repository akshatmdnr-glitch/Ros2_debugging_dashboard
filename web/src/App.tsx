import { AppShell } from "./AppShell";
import { DashboardProvider } from "./context/DashboardContext";

export default function App() {
  return (
    <DashboardProvider>
      <AppShell />
    </DashboardProvider>
  );
}
