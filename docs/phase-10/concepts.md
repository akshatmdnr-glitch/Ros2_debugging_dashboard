# ROS Graph & TF Visualization

*Phase 10 of the ROS 2 Debugging & Observability Platform.*

The dashboard can now answer *"what is wrong?"* (diagnostics), *"how is it
related?"* (incidents), and *"what happened over time?"* (timelines). Phase 10
adds the structural views: **the ROS graph** (who publishes/subscribes what)
and **the TF tree** (how frames relate), and links them to the health story by
highlighting the entities involved in active diagnostics.

This document is about **our** implementation — the two backend data gaps we
had to close, and the two SVG visualizations we built on top. It is not a
generic charting tutorial.

---

## What problem does this solve?

A developer debugging Robot 2 wants to see the *shape* of the system, not just
the verdicts:

- **ROS graph**: which node publishes `/robot2/scan`? Which nodes subscribe to
  it? Where does the degraded topic sit in the data flow?
- **TF tree**: `map → odom → base_link → …`. Which frame is stale, and where
  does it sit in the chain?

Before this phase the dashboard had tables of nodes and topics, but **no
structure** — no edges, no hierarchy. And the two natural cross-links were
missing too: *"the /scan topic is degraded"* and *"base_link is stale"* were not
visually connected to the graph/TF views.

## Missing data → we extended the backend (the honest path)

The engine already *knew* both structures; the API just never exposed them.
Per the project rule, we did not invent frontend-only data — we extended the
existing components:

1. **Graph edges.** `/topics` returned publisher/subscriber *counts* but not
   *which nodes*. `GraphModel` already stores every endpoint's node
   (`TopicInfo.publishers[].node.fully_qualified_name`). We extended
   `snapshot_topics()` to include `publisher_nodes` and `subscriber_nodes`.
   Zero new collection — the data was already there.

2. **TF tree.** `TfStats` recorded per-frame freshness but **discarded the
   parent/child relationship** — only the collector ever saw it. This was a
   genuine gap. We extended `TfStats.record(parent, child, stamp, now)` to keep
   a `child → parent` map (`edges`), and the collector now passes both frame ids
   of each transform (both `/tf` and `/tf_static`). The `/telemetry` `tf`
   payload became `{frames, edges}`.

Both changes are small, additive, and keep the "collector feeds models, API
projects them" boundary intact. The TF change touched the collector because the
edge information *only* exists on the wire; nothing downstream could reconstruct
it.

## The ROS graph (GraphView)

`GraphView` renders a **deterministic bipartite SVG**: ROS nodes on the left,
topics on the right, edges between them.

- a **solid green edge** = node publishes the topic;
- a **dashed gray edge** = node subscribes to the topic;
- nodes are the union of `/nodes` (attributed) and every endpoint node on a
  topic — an unattributed node that talks on a topic is still part of the graph.

Why bipartite instead of a force-directed scatter? It is deterministic (no
layout jitter between polls), dependency-free (no d3/cytoscape), and it is
exactly how rqt_graph's simplified mode reads. For a debugger whose graphs are
typically small (tens of entities), this is the simplest layout that satisfies
the requirement.

## The TF tree (TfTree)

`TfTree` builds the parent/child tree from `/telemetry` `edges`:

- roots are frames that are never a child (e.g. `map`);
- children are laid out recursively, one column per depth level;
- each box shows the frame id and transform count.

This is pure presentation: the tree shape comes from the API's edges; the view
never decides freshness.

## Linking health to structure

The two new views read the active diagnostics and **highlight** the entities
they touch:

- `/graph` highlights a topic that is the subject of an active diagnostic, and
  the nodes connected to it;
- `/tf` highlights frames named by active `tf_stale`/`tf_missing` diagnostics.

This is the *integration* the phase asks for: you can see that `/robot2/scan`
is degraded *in place*, connected to `/robot2/lidar`, and that `base_link` sits
stale in the middle of the `map → odom → base_link` chain. The highlighting is
read-only presentation; the backend (Phase 4) remains the only authority on
what is abnormal.

## Frontend/backend responsibility (unchanged)

```
Backend decides:  observations → diagnostics → correlation → incidents → API
Frontend shows:   graph/TF structure + which entities the backend says are wrong
```

The frontend colors a box red because a diagnostic says so; it never decides
staleness or severity itself.

## What we implemented

**Backend:**
- `telemetry.py` — `TfStats.record(parent, child, …)` now also records
  `child → parent`; new `TfStats.edges` (sorted `(parent, child)` pairs).
- `collector.py` — `TfTransformHandler` now passes `(parent, child, stamp,
  is_static)`; `_on_tf` forwards both frame ids of every transform.
- `app.py` — `_record_tf(parent, child, …)`; `snapshot_topics()` adds
  `publisher_nodes`/`subscriber_nodes`; `snapshot_telemetry()` `tf` is now
  `{frames, edges}`.
- `api.py` — `Topic` DTO + `publisher_nodes`/`subscriber_nodes`; new `TfEdge`
  and `TfResponse` DTOs; `TelemetryResponse.tf: TfResponse`.

**Frontend (`web/`):**
- `types.ts` — `Topic` (+endpoint node lists), `Node`, `TopicsResponse`,
  `TfEdge`, `TfResponse`; `TelemetryResponse.tf` is now `{frames, edges}`.
- `services/api.ts` — `fetchDashboard()` now also fetches `/nodes` and `/topics`.
- `components/GraphView.tsx` — the bipartite SVG graph.
- `components/TfTree.tsx` — the recursive SVG frame tree.
- `pages/GraphPage.tsx` / `pages/TfPage.tsx` — build the entity sets from the
  snapshot and highlight problem entities.
- `components/NavBar.tsx` + `AppShell.tsx` — `/graph` and `/tf` routes.
- `pages/TelemetryPage.tsx` — reads the new `tf` shape.
- `styles/global.css` — graph/TF box, edge, and problem-highlight styles
  (extends the Phase 8 design tokens: dollar green + cream, semantic warning
  highlight).

## Alternatives considered

- **A graph library (d3-force / cytoscape / react-force-graph)** — rejected for
  now: our graphs are small; the deterministic bipartite SVG is dependency-free,
  stable between polls, and testable. A force library is the option if graphs
  grow large or interactive dragging is wanted.
- **Infer TF parent/child from frame names** — rejected: guessing is exactly
  what this project refuses to do; the edges are captured from the wire.
- **Keep TF as flat frames only** — rejected: a tree is the information the
  robot actually provides, and the phase's goal is structure.
- **Backend computes a layout for the frontend** — rejected: layout is
  presentation, so it belongs in the view; the API stays structural.

## Tests

- Backend (79 pass, +2): `test_tf_stats_freshness_and_tree` (per-frame counts
  AND `edges` ordered by parent/child); `test_telemetry_tf_tree_exposed`
  (`/telemetry` returns `{frames, edges}` from `record()`); updated topics test
  asserts `publisher_nodes`/`subscriber_nodes`; updated `test_telemetry` for the
  new `tf` shape.
- Frontend (19 pass, +2): graph view renders nodes + topics and highlights a
  problem topic (`/robot2/scan`); TF tree renders frames and highlights a stale
  frame (`base_link`). Build (`tsc`) type-checks the new API contract.
- Live smoke: demo backend exposes `publisher_nodes` and `{frames, edges}`; the
  dev server SPA-falls back for `/graph` and `/tf`.

What the tests prove: the API now carries the graph edges and the TF tree, and
the views render them with problem highlighting. What they do not prove: the
real browser/backend path (verified by the manual smoke run) and layout quality
at very large graph sizes (acceptable for a debugging tool).

## What could go wrong?

- **Graph grows large** → the SVG becomes tall/wide; the `.graph-wrap` scrolls.
  Fine for a debugging tool; a force layout or collapse/expand is a future
  option.
- **TF cycle in the frame tree** (a frame is its own ancestor via a bad
  transform) → `TfTree` would recurse infinitely. Real TF graphs are DAGs; a
  cycle guard is a noted future hardening if it ever bites.
- **Topic with no endpoint node info** (`_NODE_NAME_UNKNOWN_`) → no edge is
  drawn for it; the topic box still shows (honest empty rather than a guessed
  edge).
- **TF edges only from a fresh session** → the tree builds from what was
  observed since startup (in-memory), matching Phase 6's history semantics.

## What I should be able to explain in an interview

1. Why did the API need changes for this phase, and what exactly was missing?
2. Where does the graph's edge data come from? (GraphModel endpoints → snapshot)
3. Where does the TF tree's parent/child data come from? (the collector wire →
   TfStats.edges → API)
4. Why is the graph a bipartite layout instead of force-directed?
5. How does the TF tree determine roots and lay out children?
6. How do the graph/TF views know what to highlight, and who decides what is
   abnormal?
7. Why is the TF handler signature change in the collector justified?
8. What stays purely presentational in the frontend?
9. What happens if a topic has no discoverable endpoint nodes?
10. Why did we not use a graph library yet, and when would we?
