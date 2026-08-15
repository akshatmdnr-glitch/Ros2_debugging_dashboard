import type { RobotView } from "../types";
import { StatusBadge } from "./StatusBadge";

export function RobotCard({ robot }: { robot: RobotView }) {
  return (
    <div className="card robot-card">
      <div className="row">
        <h4>{robot.name}</h4>
        <StatusBadge status={robot.status} />
      </div>
      <p className="muted">
        nodes: {robot.nodes.length} · active diagnostics: {robot.active_diagnostics} ·
        active incidents: {robot.active_incidents}
      </p>
      {robot.diagnostics.length > 0 && (
        <ul className="diag-list">
          {robot.diagnostics.map((d) => (
            <li key={d.key.join(".")} className={`diag diag-${d.severity.toLowerCase()}`}>
              <span className="mono">{d.rule_id}</span> {d.subject}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
