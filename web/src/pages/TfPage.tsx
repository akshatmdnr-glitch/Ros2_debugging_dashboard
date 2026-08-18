import { TfTree } from "../components/TfTree";
import { useDashboardContext } from "../context/DashboardContext";

export function TfPage() {
  const { data } = useDashboardContext();
  if (data === null) return null;

  const problemFrames = new Set(
    data.diagnostics.active
      .filter((d) => d.tf_frame !== null)
      .map((d) => d.tf_frame as string),
  );

  return (
    <section>
      <h2>TF tree</h2>
      <p className="muted small">
        Frames from <code>/tf</code> and <code>/tf_static</code>.{" "}
        <span className="problem-legend">highlighted</span> frames are involved
        in an active TF diagnostic.
      </p>
      {data.telemetry.tf.frames.length === 0 ? (
        <p className="empty">No TF frames observed.</p>
      ) : (
        <div className="card graph-wrap">
          <TfTree
            frames={data.telemetry.tf.frames}
            edges={data.telemetry.tf.edges}
            problemFrames={problemFrames}
          />
        </div>
      )}
    </section>
  );
}
