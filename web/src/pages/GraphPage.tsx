import { useMemo } from "react";

import { GraphView } from "../components/GraphView";
import { useDashboardContext } from "../context/DashboardContext";

export function GraphPage() {
  const { data } = useDashboardContext();
  if (data === null) return null;

  // The ROS graph is the union of attributed nodes and every endpoint node on
  // a topic (an unattributed node that talks on a topic is still part of the
  // graph).
  const nodes = useMemo(() => {
    const seen = new Map<string, { fqn: string }>();
    for (const n of data.nodes.nodes) seen.set(n.fqn, { fqn: n.fqn });
    for (const t of data.topics.topics) {
      for (const n of t.publisher_nodes) seen.set(n, { fqn: n });
      for (const n of t.subscriber_nodes) seen.set(n, { fqn: n });
    }
    return [...seen.values()];
  }, [data]);

  const problemTopics = useMemo(
    () =>
      new Set(
        data.diagnostics.active
          .filter((d) => d.topic !== null)
          .map((d) => d.topic as string),
      ),
    [data],
  );
  const problemNodes = useMemo(() => {
    const set = new Set<string>();
    for (const t of data.topics.topics) {
      if (problemTopics.has(t.name)) {
        t.publisher_nodes.forEach((n) => set.add(n));
        t.subscriber_nodes.forEach((n) => set.add(n));
      }
    }
    return set;
  }, [data, problemTopics]);

  return (
    <section>
      <h2>ROS graph</h2>
      <p className="muted small">
        Nodes ↔ topics.{" "}
        <span className="problem-legend">highlighted</span> entities are involved
        in an active diagnostic.
      </p>
      {nodes.length === 0 && data.topics.topics.length === 0 ? (
        <p className="empty">No graph discovered.</p>
      ) : (
        <div className="card graph-wrap">
          <GraphView
            nodes={nodes}
            topics={data.topics.topics}
            problemTopics={problemTopics}
            problemNodes={problemNodes}
          />
        </div>
      )}
    </section>
  );
}
