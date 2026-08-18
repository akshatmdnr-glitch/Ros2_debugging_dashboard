import { Link } from "react-router-dom";

import { useDashboardContext } from "../context/DashboardContext";
import type { Incident } from "../types";

function ActiveCard({ incident }: { incident: Incident }) {
  return (
    <article key={incident.id} className="card incident-card">
      <div className="row">
        <h4>
          <Link to={`/incidents/${incident.id}`}>#{incident.id}</Link> ·{" "}
          {incident.owner}
        </h4>
        <span className="badge badge-critical">{incident.state}</span>
      </div>
      <p className="muted mono small">
        confidence={incident.confidence} members={incident.member_count} signals=
        {incident.strategies.join(",")}
      </p>
      {incident.members.length > 0 && (
        <p className="mono small">members: {incident.members.join(", ")}</p>
      )}
    </article>
  );
}

export function IncidentsPage() {
  const { data } = useDashboardContext();
  if (data === null) return null;

  const { active, history } = data.incidents;

  return (
    <>
      <section>
        <h2>Active incidents ({active.length})</h2>
        {active.length === 0 ? (
          <p className="empty">No active incidents.</p>
        ) : (
          <div className="grid">
            {active.map((i) => (
              <ActiveCard key={i.id} incident={i} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2>Incident history ({history.length})</h2>
        {history.length === 0 ? (
          <p className="empty">No completed incidents in history.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>id</th>
                <th>owner</th>
                <th>state</th>
                <th>confidence</th>
                <th>members</th>
                <th>duration (s)</th>
              </tr>
            </thead>
            <tbody>
              {history.map((i) => (
                <tr key={i.id}>
                  <td>
                    <Link to={`/incidents/${i.id}`}>#{i.id}</Link>
                  </td>
                  <td>{i.owner}</td>
                  <td>{i.state}</td>
                  <td className="mono">{i.confidence}</td>
                  <td>{i.member_count}</td>
                  <td className="mono">
                    {i.duration !== null ? i.duration.toFixed(1) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
