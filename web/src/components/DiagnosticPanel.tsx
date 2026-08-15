import type { Diagnostic } from "../types";

export function DiagnosticPanel({ active }: { active: Diagnostic[] }) {
  return (
    <section>
      <h2>Active diagnostics ({active.length})</h2>
      {active.length === 0 ? (
        <p className="empty">No active diagnostics.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>severity</th>
              <th>rule</th>
              <th>subject</th>
              <th>owner</th>
              <th>message</th>
            </tr>
          </thead>
          <tbody>
            {active.map((d) => (
              <tr key={d.key.join(".")}>
                <td>
                  <span className={`badge badge-${d.severity.toLowerCase()}`}>
                    {d.severity}
                  </span>
                </td>
                <td className="mono">{d.rule_id}</td>
                <td className="mono">{d.subject}</td>
                <td>{d.robot ? `${d.system}/${d.robot}` : d.system ?? "—"}</td>
                <td className="muted">{d.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
