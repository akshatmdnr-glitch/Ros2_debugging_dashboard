import type { RobotView, System } from "../types";
import { RobotCard } from "./RobotCard";

export function SystemOverview({
  systems,
  robots,
  unclassified,
}: {
  systems: System[];
  robots: RobotView[];
  unclassified: string[];
}) {
  const bySystem = new Map<string, RobotView[]>();
  for (const r of robots) {
    const list = bySystem.get(r.system) ?? [];
    list.push(r);
    bySystem.set(r.system, list);
  }

  return (
    <section>
      <h2>Systems</h2>
      {systems.length === 0 && robots.length === 0 && (
        <p className="empty">
          No systems discovered. Is the debugger running on the same ROS domain?
        </p>
      )}
      {systems.map((s) => (
        <article key={s.name} className="card system-card">
          <div className="row">
            <h3>{s.name}</h3>
            <span className="muted mono">
              {bySystem.get(s.name)?.length ?? 0} robot
              {(bySystem.get(s.name)?.length ?? 0) === 1 ? "" : "s"}
            </span>
          </div>
          <div className="grid">
            {(bySystem.get(s.name) ?? []).map((r) => (
              <RobotCard key={`${s.name}/${r.name}`} robot={r} />
            ))}
          </div>
          {s.system_nodes.length > 0 && (
            <p className="muted small">
              system nodes: <span className="mono">{s.system_nodes.join(", ")}</span>
            </p>
          )}
        </article>
      ))}
      {unclassified.length > 0 && (
        <p className="muted small">
          unclassified nodes: <span className="mono">{unclassified.join(", ")}</span>
        </p>
      )}
    </section>
  );
}
