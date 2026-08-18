import { Link, useParams } from "react-router-dom";

import { useIncident } from "../hooks/useIncident";

export function IncidentDetailPage() {
  const { id } = useParams();
  const { incident, loading, error } = useIncident(id);

  if (loading) return <p className="empty">Loading incident #{id}…</p>;

  if (error !== null || incident === null) {
    const notFound = /404/.test(error ?? "");
    return (
      <div className="banner banner-error" role="alert">
        <strong>{notFound ? `Incident #${id} not found.` : "Failed to load incident."}</strong>{" "}
        {notFound ? (
          <>
            It may not exist yet —{" "}
            <Link to="/incidents">back to incidents</Link>.
          </>
        ) : (
          error
        )}
      </div>
    );
  }

  const t0 = incident.started_at;
  return (
    <section>
      <p className="small">
        <Link to="/incidents">← back to incidents</Link>
      </p>

      <article className="card incident-card">
        <div className="row">
          <h2>
            Incident #{incident.id} · {incident.owner}
          </h2>
          <span className="badge badge-critical">{incident.state}</span>
        </div>

        <p className="muted mono small">
          confidence={incident.confidence} · members={incident.member_count} ·
          active={incident.active_count} · signals={incident.strategies.join(",")}
        </p>

        <div className="stat-grid">
          <div className="stat">
            <span className="stat-label">started</span>
            <span className="mono">{t0.toFixed(1)}s</span>
          </div>
          <div className="stat">
            <span className="stat-label">duration</span>
            <span className="mono">
              {incident.duration !== null ? `${incident.duration.toFixed(1)}s` : "in progress"}
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">ended</span>
            <span className="mono">
              {incident.ended_at !== null ? `${incident.ended_at.toFixed(1)}s` : "—"}
            </span>
          </div>
        </div>

        {incident.members.length > 0 && (
          <p className="mono small">members: {incident.members.join(", ")}</p>
        )}

        <h3 className="subhead">Timeline</h3>
        {incident.events.length === 0 ? (
          <p className="empty">No events recorded.</p>
        ) : (
          <ol className="timeline">
            {incident.events.map((e, idx) => (
              <li key={idx} className={`timeline-${e.transition.toLowerCase()}`}>
                <span className="mono time-offset">t+{(e.timestamp - t0).toFixed(1)}s</span>{" "}
                <span className="mono">{e.transition}</span> {e.subject}
              </li>
            ))}
          </ol>
        )}
      </article>
    </section>
  );
}
