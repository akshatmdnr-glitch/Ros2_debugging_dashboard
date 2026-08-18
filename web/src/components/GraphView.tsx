// ROS graph visualization: a deterministic bipartite SVG layout.
// ROS nodes on the left, topics on the right, edges for publishes (solid
// green) and subscribes (dashed gray). Entities involved in an active
// diagnostic are highlighted. The data comes straight from the API
// (/nodes + /topics with endpoint node associations) — no re-collection.

import { useMemo } from "react";

import type { Topic } from "../types";

const NODE_W = 250;
const TOPIC_W = 320;
const BOX_H = 38;
const ROW_H = 50;
const GAP = 70;
const PAD_TOP = 16;
const PAD_LEFT = 16;

function short(s: string, max = 30): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function GraphView({
  nodes,
  topics,
  problemTopics,
  problemNodes,
}: {
  nodes: { fqn: string }[];
  topics: Topic[];
  problemTopics: Set<string>;
  problemNodes: Set<string>;
}) {
  const layout = useMemo(() => {
    const nodeRows = [...nodes].sort((a, b) => a.fqn.localeCompare(b.fqn));
    const topicRows = [...topics].sort((a, b) => a.name.localeCompare(b.name));
    const nodeY = new Map<string, number>();
    const topicY = new Map<string, number>();
    const nodeColX = PAD_LEFT;
    const topicColX = PAD_LEFT + NODE_W + GAP;
    nodeRows.forEach((n, i) => nodeY.set(n.fqn, PAD_TOP + i * ROW_H));
    topicRows.forEach((t, i) => topicY.set(t.name, PAD_TOP + i * ROW_H));
    const height = PAD_TOP + Math.max(nodeRows.length, topicRows.length) * ROW_H;
    return { nodeRows, topicRows, nodeY, topicY, nodeColX, topicColX, height };
  }, [nodes, topics]);

  const { nodeRows, topicRows, nodeY, topicY, nodeColX, topicColX, height } = layout;
  const width = topicColX + TOPIC_W + PAD_LEFT;

  return (
    <svg
      className="graph"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="ROS graph of nodes and topics"
    >
      {/* edges: node -> topic (publish), topic -> node (subscribe) */}
      {topics.map((t) => {
        const ty = topicY.get(t.name)!;
        const tLeft = topicColX;
        const tRight = topicColX + TOPIC_W;
        return (
          <g key={`edges-${t.name}`}>
            {t.publisher_nodes.map((n) => {
              const ny = nodeY.get(n);
              if (ny === undefined) return null;
              return (
                <path
                  key={`p-${n}-${t.name}`}
                  className="edge edge-publish"
                  d={`M ${nodeColX + NODE_W} ${ny + BOX_H / 2} ` +
                     `C ${nodeColX + NODE_W + GAP / 2} ${ny + BOX_H / 2}, ` +
                     `${tLeft - GAP / 2} ${ty + BOX_H / 2}, ${tLeft} ${ty + BOX_H / 2}`}
                />
              );
            })}
            {t.subscriber_nodes.map((n) => {
              const ny = nodeY.get(n);
              if (ny === undefined) return null;
              return (
                <path
                  key={`s-${n}-${t.name}`}
                  className="edge edge-subscribe"
                  d={`M ${tRight} ${ty + BOX_H / 2} ` +
                     `C ${tRight + GAP / 2} ${ty + BOX_H / 2}, ` +
                     `${nodeColX + NODE_W + GAP / 2} ${ny + BOX_H / 2}, ` +
                     `${nodeColX + NODE_W} ${ny + BOX_H / 2}`}
                />
              );
            })}
          </g>
        );
      })}

      {/* topic boxes */}
      {topicRows.map((t) => {
        const y = topicY.get(t.name)!;
        const problem = problemTopics.has(t.name);
        return (
          <g key={t.name}>
            <rect
              x={topicColX} y={y} width={TOPIC_W} height={BOX_H} rx={6}
              className={`graph-box topic-box ${problem ? "problem" : ""}`}
            />
            <text x={topicColX + 8} y={y + 16} className="graph-text mono">
              {short(t.name)}
            </text>
            <text x={topicColX + 8} y={y + 31} className="graph-text sub">
              {t.type ?? "?"} · {t.publishers}P/{t.subscribers}S
            </text>
          </g>
        );
      })}

      {/* node boxes */}
      {nodeRows.map((n) => {
        const y = nodeY.get(n.fqn)!;
        const problem = problemNodes.has(n.fqn);
        return (
          <g key={n.fqn}>
            <rect
              x={nodeColX} y={y} width={NODE_W} height={BOX_H} rx={6}
              className={`graph-box node-box ${problem ? "problem" : ""}`}
            />
            <text x={nodeColX + 8} y={y + BOX_H / 2 + 5} className="graph-text mono">
              {short(n.fqn)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
