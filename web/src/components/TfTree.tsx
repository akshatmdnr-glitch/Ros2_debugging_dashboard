// TF tree visualization: renders the parent/child frame tree from the API's
// /telemetry tf edges as a recursive SVG layout. Frames involved in an active
// TF diagnostic are highlighted (this is presentation only — the debugger
// decides staleness, the view just colors it).

import { useMemo } from "react";

import type { TfEdge, TfTelemetry } from "../types";

const COL_W = 200;
const BOX_W = 170;
const BOX_H = 30;
const ROW_H = 40;

interface Placed {
  id: string;
  x: number;
  y: number;
}

export function TfTree({
  frames,
  edges,
  problemFrames,
}: {
  frames: TfTelemetry[];
  edges: TfEdge[];
  problemFrames: Set<string>;
}) {
  const tree = useMemo(() => {
    const frameById = new Map(frames.map((f) => [f.frame_id, f]));
    const children = new Map<string, string[]>();
    const hasParent = new Set<string>();
    for (const e of edges) {
      hasParent.add(e.child);
      const list = children.get(e.parent) ?? [];
      list.push(e.child);
      children.set(e.parent, list);
    }
    for (const list of children.values()) list.sort();
    const roots = frames
      .map((f) => f.frame_id)
      .filter((id) => !hasParent.has(id))
      .sort();

    const subtreeHeight = (id: string): number => {
      const kids = children.get(id) ?? [];
      if (kids.length === 0) return ROW_H;
      return kids.reduce((acc, k) => acc + subtreeHeight(k), 0);
    };

    const placed: Placed[] = [];
    const place = (id: string, x: number, top: number): void => {
      const h = subtreeHeight(id);
      placed.push({ id, x, y: top + h / 2 });
      let cursor = top;
      for (const k of children.get(id) ?? []) {
        place(k, x + COL_W, cursor);
        cursor += subtreeHeight(k);
      }
    };

    let offset = 0;
    for (const root of roots) {
      place(root, 0, offset);
      offset += subtreeHeight(root) + ROW_H;
    }
    const height = Math.max(offset - ROW_H, ROW_H);
    const width = placed.reduce((m, p) => Math.max(m, p.x), 0) + COL_W + 30;
    const byId = new Map(placed.map((p) => [p.id, p]));
    const parents = new Map(edges.map((e) => [e.child, e.parent]));
    return { placed, byId, parents, frameById, height, width };
  }, [frames, edges]);

  const { placed, byId, parents, frameById, height, width } = tree;

  if (placed.length === 0) return <p className="empty">No TF tree.</p>;

  return (
    <svg
      className="graph"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="TF frame tree"
    >
      {placed.map((p) => {
        const parent = parents.get(p.id);
        if (parent === undefined) return null;
        const pp = byId.get(parent);
        if (pp === undefined) return null;
        return (
          <line
            key={`edge-${p.id}`}
            x1={pp.x + BOX_W}
            y1={pp.y}
            x2={p.x}
            y2={p.y}
            className="edge edge-tf"
          />
        );
      })}
      {placed.map((p) => {
        const f = frameById.get(p.id);
        const problem = problemFrames.has(p.id);
        return (
          <g key={p.id}>
            <rect
              x={p.x} y={p.y - BOX_H / 2} width={BOX_W} height={BOX_H} rx={6}
              className={`graph-box tf-box ${problem ? "problem" : ""}`}
            />
            <text x={p.x + 8} y={p.y + 5} className="graph-text mono">
              {p.id}
            </text>
            <text x={p.x + BOX_W - 8} y={p.y + 5} textAnchor="end" className="graph-text sub">
              {f !== undefined ? `×${f.count}` : ""}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
