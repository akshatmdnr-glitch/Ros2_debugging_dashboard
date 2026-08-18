import { useDashboardContext } from "../context/DashboardContext";

export function TelemetryPage() {
  const { data } = useDashboardContext();
  if (data === null) return null;

  const { topics, processes, tf } = data.telemetry;

  return (
    <>
      <section>
        <h2>Topics ({topics.length})</h2>
        {topics.length === 0 ? (
          <p className="empty">No monitored topics.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>topic</th>
                <th>type</th>
                <th>rate (Hz)</th>
                <th>count</th>
                <th>idle (s)</th>
                <th>receiving</th>
                <th>status</th>
              </tr>
            </thead>
            <tbody>
              {topics.map((t) => (
                <tr key={t.topic}>
                  <td className="mono">{t.topic}</td>
                  <td className="muted">{t.type ?? "—"}</td>
                  <td className="mono">{t.rate_hz.toFixed(2)}</td>
                  <td className="mono">{t.message_count}</td>
                  <td className="mono">
                    {t.idle_seconds !== null ? t.idle_seconds.toFixed(1) : "—"}
                  </td>
                  <td>{t.receiving ? "yes" : "no"}</td>
                  <td className="muted">{t.reason || (t.monitored ? "monitored" : "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Processes ({processes.length})</h2>
        {processes.length === 0 ? (
          <p className="empty">No monitored processes.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>pattern</th>
                <th>alive</th>
                <th>pids</th>
                <th>cpu (%)</th>
                <th>rss (MB)</th>
              </tr>
            </thead>
            <tbody>
              {processes.map((p) => (
                <tr key={p.pattern}>
                  <td className="mono">{p.pattern}</td>
                  <td>{p.alive ? "yes" : "no"}</td>
                  <td className="mono">{p.pids.join(", ") || "—"}</td>
                  <td className="mono">{p.cpu_percent.toFixed(1)}</td>
                  <td className="mono">{p.rss_mb.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>TF frames ({tf.length})</h2>
        {tf.length === 0 ? (
          <p className="empty">No TF frames observed.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>frame</th>
                <th>count</th>
                <th>last seen</th>
              </tr>
            </thead>
            <tbody>
              {tf.map((f) => (
                <tr key={f.frame_id}>
                  <td className="mono">{f.frame_id}</td>
                  <td className="mono">{f.count}</td>
                  <td className="mono">{f.last_seen.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
