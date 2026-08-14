# ROS 2 Graph Discovery

*Phase 1 of the ROS 2 Debugging & Observability Platform.*

This document explains the concepts behind the graph-discovery layer. It is
about **our** implementation, not a generic ROS 2 tutorial. Everything is
connected back to the debugger and, where useful, the warehouse project.

---

## 1. What problem does graph discovery solve?

The debugger must inspect a *running* ROS 2 system (the Warehouse) without
having any knowledge of its source code. To do anything useful it first needs
to answer the most basic question: **what is actually running right now?**

What the debugger needed to know:

- which nodes exist, and in which namespaces
- which topics exist, with what message types
- who publishes/subscribes to each topic, and with what QoS
- which services/actions exist
- when any of the above appears, changes, or disappears

Why the debugger cannot depend on the warehouse source code:

- the warehouse is a separate project; the debugger is explicitly **not**
  allowed to require uploading or reading it
- source code describes what the software *could* do, not what is running
  *right now* (a node may not be launched, a topic may be remapped at launch
  time, QoS may differ from the source defaults)
- depending on source would couple the debugger to one project, breaking its
  purpose of observing *any* ROS 2 system

Why it must inspect the running ROS 2 environment: because the runtime
environment is the only truthful, project-independent source of "what is
here". ROS 2 itself exposes this information through discovery + introspection
APIs.

What would happen without discovery: the debugger would be blind. It could
not render a graph, could not attribute nodes to robots (Phase 2), and could
not measure topic activity (Phase 3). There would be nothing to debug against.

## 2. What is ROS 2 discovery?

Discovery is the runtime mechanism by which ROS 2 processes find each other on
a network. When a node starts, it announces itself; when it creates a
publisher or subscriber, it announces that endpoint too. Participants on the
same discovery domain learn about each other automatically, without any
central registry or prior knowledge.

Consequences for our debugger:

- nodes/entities become visible to one another purely by *being in the same
  domain on the same network* — no code changes are required in the warehouse
- the debugger relies on the running environment, not the source workspace,
  because discovery is a property of running processes, not of files
- the same ROS 2 APIs that power `ros2 node list` / `ros2 topic list` are the
  ones we use in-process

## 3. What is DDS?

DDS (Data Distribution Service) is the middleware that ROS 2 runs on. It
provides discovery (participants/endpoints finding each other) and the
data-distribution contract (topics, QoS). ROS 2 sits on top of it through the
RMW (ROS Middleware Interface) abstraction layer.

What our debugger actually depends on from DDS:

- participants join a **domain**; the domain is the isolation boundary
- discovery makes graph information available (who is there, what topics
  exist, what QoS they declare)
- transport delivers messages to subscribers that match QoS

Important distinction: **DDS is the underlying technology; our application
uses ROS 2 (rclpy) APIs, not direct DDS programming.** We call
`get_node_names_and_namespaces()`, `get_topic_names_and_types()`,
`get_publishers_info_by_topic()`, etc. We never implement DDS discovery
ourselves. The DDS layer is what makes the ROS 2 introspection APIs truthful
in real time.

## 4. What is a ROS 2 graph?

The "graph" is the debugger's representation of everything discovery exposes:
nodes, topics, services, and their relationships (publisher/subscriber). In
our code it is a flat inventory — `GraphModel` — holding `NodeInfo`,
`TopicInfo`, and `EndpointInfo` objects.

What the graph is useful for:

- knowing that `/robot2/lidar` exists, its type, its QoS, and who owns it
- knowing which nodes are present at any moment

What the graph does **not** tell us:

- whether `/robot2/lidar` is actually publishing right now
- whether it is publishing at a healthy rate
- whether the process behind it is healthy
- whether anything is *wrong*

> The graph can show that `/robot2/lidar` exists, but it does not by itself
> prove that the LiDAR is healthy.

This gap is exactly what Phase 3 (telemetry) fills.

## 5. Graph discovery vs reading the source code

The debugger's input is **not** the warehouse source. It is:

```
Running ROS 2 environment
        ↓
ROS 2 introspection (rclpy graph APIs)
        ↓
debugger
```

Why this makes the debugger universal:

- it observes whatever is running, not whatever was written
- it works against any ROS 2 project, not just the warehouse
- it reflects runtime reality (actual namespaces, remaps, QoS) which source
  cannot express
- the warehouse never needs to change to be observed

Tradeoff: the debugger sees structure but not intent. It cannot know *why* a
node was written, or which nodes "belong to a project" — that becomes the
Phase 2 attribution problem.

## 6. Graph discovery vs polling

The ideal would be **event-driven discovery**: ROS 2 pushes a notification the
moment the graph changes, and we update the model reactively.

What we expected: rclcpp has a graph-cache with discovery callbacks. We
looked for the equivalent in rclpy on ROS 2 Jazzy.

What we discovered: **rclpy on Jazzy exposes no public graph-event callback**
(no `rclpy.graph`, no node-discovery event API). The graph APIs are
*query-based*, not push-based.

What we implemented: **polling + snapshot diffing**.

- a 1-second timer (`CollectorNode._refresh_graph`) queries the graph
- the new snapshot is compared against the previous one
- differences are converted into `GraphEvent`s (ADDED / REMOVED / UPDATED)

Tradeoff introduced: change detection is bounded by the polling interval
(~1 s). A node that appears and disappears within one second could be missed.
For a debugging tool observing human-timescale problems this is acceptable,
and the architecture hides the mechanism: consumers of the model receive
events whether the source is push or poll.

## 7. What does graph change detection mean?

Change detection is the difference between showing the same picture twice and
reporting what changed:

- **initial snapshot** — taken as soon as the collector starts (and buffered;
  see section 8)
- **later snapshot** — taken on each timer tick
- **comparison** — the model diffs the two and records only the delta
- **added entities** — nodes/topics present now but not before → `NODE_ADDED`
  / `TOPIC_ADDED` events
- **removed entities** — present before but gone now → `NODE_REMOVED` /
  `TOPIC_REMOVED` events
- **updated entities** — endpoint/QoS signature changed → `TOPIC_UPDATED`

Why this matters: a debugger that must react to change ("Robot 2's lidar node
just disappeared") needs *events*, not just a fresh picture. Change history is
also the seed for future diagnostics (Phase 4 reasons about nodes leaving).

## 8. What happens when a node appears?

Trace through the actual implementation:

```
warehouse node starts
  → DDS discovery makes it visible
  → CollectorNode._refresh_graph() (1s timer) queries the graph
  → get_node_names_and_namespaces() returns the new node
  → GraphModel.sync_nodes() diffs and records a NODE_ADDED GraphEvent
  → collector dispatches the event to graph_event_handlers
  → SystemModel (Phase 2) attributes it; the CLI prints "[+node] /robot2/lidar"
```

Key classes: `CollectorNode` (`ros2_debugger/collector.py`) is the ROS-facing
boundary; `GraphModel` (`ros2_debugger/model.py`) holds the flat state and
emits `GraphEvent`s; `debugger.py` wires handlers.

A subtlety we solved: the first snapshot runs inside `CollectorNode.__init__`,
*before* any handlers are attached. Those initial events would be lost —
making the debugger look like "nothing was here when I arrived". We buffer
them and deliver them via `flush_pending_events()` once handlers subscribe.

## 9. What happens when a node disappears?

The equivalent removal flow:

```
warehouse node stops
  → discovery no longer reports it
  → next _refresh_graph() query omits it
  → GraphModel.sync_nodes() records a NODE_REMOVED GraphEvent
  → handlers react (CLI prints "[-node] /robot2/lidar")
```

Why node disappearance becomes useful evidence: a node leaving the graph is a
structural change, not a noisy measurement. Later phases treat it as stronger
evidence than, say, a CPU spike. This is why we built change events rather
than just snapshots.

## 10. ROS_DOMAIN_ID and discovery

`ROS_DOMAIN_ID` selects the DDS **domain**. Discovery only happens *within* a
domain; participants on different domains cannot see each other at all — the
domains are isolated networks for discovery and delivery.

Implications for the debugger:

- the collector inherits `ROS_DOMAIN_ID` from the shell environment at
  `rclpy.init()` time (default `0`)
- to observe the warehouse, the debugger must run in the *same* domain
- if the domains differ, the debugger sees an **empty graph — silently, with
  no error**
- we print the effective domain in the startup banner precisely because this
  failure is silent and confusing

So: `ROS_DOMAIN_ID=10` in the warehouse shell and `ROS_DOMAIN_ID=0` in the
debugger shell means the debugger sees nothing and there is no error to point
at. The banner ("ROS 2 debugger collector on domain ROS_DOMAIN_ID=…") is the
first thing to check.

## 11. Why does our debugger need graph discovery?

The engineering reason is universality plus honesty:

- the debugger must operate against an **unknown** ROS 2 system without being
  hard-coded for the warehouse
- discovery is the only mechanism that gives a truthful, live, project-neutral
  inventory of what is running
- without it, every later capability (attribution, telemetry, diagnostics)
  has no subject to work on

Graph discovery is the foundation: it answers "what exists?" so later phases
can answer "what belongs to what?" and "what is happening?".

## 12. What could go wrong?

Realistic failure cases relevant to what we built:

- **Wrong domain** — empty graph, no error. Mitigated by the banner and by
  inheriting `ROS_DOMAIN_ID` from the environment like every ROS 2 tool.
- **Incomplete discovery** — multicast/network issues (containers, firewall,
  `ROS_LOCALHOST_ONLY`) can delay or prevent discovery.
- **API limitations** — no rclpy graph-event callback on Jazzy → we poll.
- **Polling interval** — changes between ticks can be missed or batched.
- **Stale snapshots** — the graph APIs return the state as of discovery; there
  is inherent lag between reality and what we see.
- **Discovery timing** — endpoints may appear before/after their node, so a
  snapshot can be momentarily inconsistent.
- **Duplicate/same-named nodes** — two nodes with the same name/namespace in
  one domain collide in discovery; the graph represents one of them. We
  observed this directly with two talkers on the same domain.
- **Assumptions about graph information** — the graph says a topic exists; it
  does not say it is healthy. Over-reading the graph is a design trap we
  deliberately avoided.

## 13. What we actually implemented

- **`ros2_debugger/model.py`** — the DDS-agnostic flat model:
  - `NodeInfo`, `TopicInfo`, `EndpointInfo` (with QoS fields), `GraphEvent`
  - `GraphModel` with `sync_nodes`/`sync_topics` (diff + event emission) and
    `drain_events`
  - no rclpy imports — a clean contract between the ROS-facing collector and
    everything downstream
- **`ros2_debugger/collector.py`** — the ROS-facing boundary:
  - `CollectorNode` (an rclpy `Node` named `debugger_collector`)
  - a 1-second graph timer (`_refresh_graph`), node/topic collection
    (`_collect_nodes`, `_collect_topics`), endpoint extraction (`_endpoint`)
  - `flush_pending_events` for the initial discovery burst
  - `domain_id` read from `ROS_DOMAIN_ID`, `rmw_identifier` from rclpy
  - subscriptions to `/rosout`, `/tf`, `/tf_static` (Phase 1 baseline; richer
    handling in Phase 3)
- **`ros2_debugger/debugger.py`** — CLI composition root:
  - `_Printer` (live `[+node]`/`[-node]`/`[+topic]`… output, log lines)
  - `--timeout` (for non-interactive testing), `--no-topics`
  - startup banner with the effective domain
- **`test/test_model.py`** — unit tests for the diffing logic (add/remove,
  no-change-no-event, topic lifecycle, FQN normalization)

## 14. Important engineering decisions

- **The collector is the only ROS-facing boundary.** All rclpy/DDS knowledge
  lives in `collector.py`. Everything else consumes its output. This keeps
  analysis layers testable without ROS.
- **The graph model is separate and DDS-agnostic.** `GraphModel` has no rclpy
  imports. Downstream code depends on a plain model, not on ROS types.
- **Polling + diffing was chosen** because rclpy/Jazzy offers no graph-event
  API. The decision is hidden behind the event interface, so the mechanism
  could later be swapped for push without touching consumers.
- **Independence from the warehouse.** Nothing in Phase 1 references the
  warehouse; it observes any domain it is launched in.

## 15. What Phase 1 can and cannot tell us

**CAN:**

- discover runtime graph information (nodes, namespaces, topics, types, QoS,
  endpoints)
- observe graph changes over time
- detect nodes/entities appearing and disappearing

**CANNOT:**

- determine whether a node is healthy
- determine whether a topic is publishing correctly
- determine whether CPU is overloaded
- determine root cause

This is the boundary that motivates Phase 2 (organizing what exists) and
Phase 3 (measuring what is happening).

## 16. What I should be able to explain in an interview

1. Why doesn't the debugger read the warehouse source code?
2. Why does it need runtime graph discovery instead of a static inventory?
3. What role does DDS play, and why does the debugger use ROS 2 APIs rather
   than implementing DDS itself?
4. What does the graph actually tell us, and what does it deliberately not
   tell us?
5. Why did we use polling + snapshot diffing instead of event-driven updates?
6. What specific limitation in rclpy/Jazzy affected the design?
7. Why does the initial discovery burst need to be buffered and flushed?
8. What happens when the debugger runs on a different `ROS_DOMAIN_ID` than
   the warehouse, and why is that failure silent?
9. What does `GraphModel` know, and why is it deliberately free of rclpy
   imports?
10. How do node-added and node-removed events flow from discovery to the
    model to the CLI?
11. What did we observe when two nodes shared the same name on one domain?
12. Why is "the topic exists on the graph" different from "the topic is
    healthy"?
