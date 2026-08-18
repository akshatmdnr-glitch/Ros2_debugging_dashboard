import { Link } from "react-router-dom";

import type { Incident } from "../types";

export function IncidentPanel({
  active,
  historyCount,
}: {
  active: Incident[];
  historyCount: number;
}) {
  return (
    <section>
      <h2>Incidents ({active.length} active)</h2>
      {active.length === 0 ? (
        <p className="empty">
          No active incidents.{" "}
          <span className="muted">
            ({historyCount} completed incident{historyCount === 1 ? "" : "s"} in
            history)
          </span>
        </p>
      ) : (
        <div className="grid">
          {active.map((i) => (
            <article key={i.id} className="card incident-card">
              <div className="row">
                <h4>
                  #{i.id} · {i.owner}
                </h4>
                <span className="badge badge-critical">{i.state}</span>
              </div>
              <p className="muted mono small">
                confidence={i.confidence} members={i.member_count} signals=
                {i.strategies.join(",")}
              </p>
              {i.members.length > 0 && (
                <p className="mono small">members: {i.members.join(", ")}</p>
              )}
              {i.events.length > 0 && (
                <ol className="timeline">
                  {i.events.map((e, idx) => (
                    <li key={idx} className={`timeline-${e.transition.toLowerCase()}`}>
                      <span className="mono">{e.transition}</span> {e.subject}
                    </li>
                  ))}
                </ol>
              )}
              <p className="small">
                <Link to={`/incidents/${i.id}`} className="nav-link">
                  view details →
                </Link>
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
