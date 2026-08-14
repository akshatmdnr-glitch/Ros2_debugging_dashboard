# ROS 2 Attribution

*Phase 2 of the ROS 2 Debugging & Observability Platform.*

Phase 1 taught the debugger **what exists** (the raw graph). Phase 2 teaches
it **what belongs to what** — the logical organization of the graph that ROS 2
itself does not provide. This document explains the concepts of our
attribution layer. It is not a generic ROS 2 namespace tutorial.

---

## 1. What problem does attribution solve?

Consider the running environment:

```
Warehouse
├── Robot 1        (/robot1/lidar, /robot1/navigation, …)
├── Robot 2        (/robot2/lidar, …)
└── Fleet Manager  (/fleet_manager)
```

and, possibly, a second ROS 2 system sharing the same environment:

```
SLAM Experiment
├── /slam/lidar
├── /slam/map
└── /rviz
```

ROS 2 discovery presents all of these as **one flat graph**. `ros2 node list`
shows a long list of node names with no grouping. The graph cannot answer:

- "Which nodes belong to the Warehouse?"
- "Which nodes belong to Robot 2?"
- "Is `/slam/lidar` part of the warehouse? No."

A raw graph is not enough to understand **logical ownership**. Without
ownership, later diagnostics have no subject: "this node is behaving
abnormally" is useless unless we know *whose* node it is. Attribution is the
debugger's answer to that.

## 2. What is attribution?

Attribution is a **debugger interpretation** layered on top of **ROS 2
facts**. The distinction is fundamental:

```
ROS 2 fact:              "/robot2/lidar exists."
Debugger interpretation: "This node is associated with Robot 2."
```

The fact comes from discovery. The interpretation comes from our attribution
model, which combines two things:

- runtime evidence (namespaces, node names) — the *signal*
- explicit configuration (which namespaces belong to which system/robot) — the
  *truth*

An interpretation can be wrong in principle (stale configuration), but it is
never wrong *invisibly*: every attribution records its source and whether the
evidence is strong.

## 3. Namespaces

Namespaces give ROS 2 names hierarchy: `/robot1/lidar` is node `lidar` inside
namespace `/robot1`. Namespaces are:

- **useful as attribution evidence** — a consistent `/robot1/...` prefix is a
  genuine signal that these nodes are organized together; it is the only
  structural hint the running graph carries
- **NOT guaranteed proof of ownership** — a namespace alone cannot say that
  `/robot1` belongs to "Warehouse" (that is an organizational fact), and a
  namespace may be absent, inconsistent, or shared

Examples:

| Node | Namespace evidence | Can we attribute? |
|---|---|---|
| `/robot1/lidar` | clear `/robot1` prefix | yes, if config says `/robot1` → warehouse/robot1 |
| `/lidar_node` | none (root) | no → UNCLASSIFIED |
| `/slam/lidar` | prefix could mean a robot *or* a system | only the config decides |

Rule we follow: **namespaces are the key we match on; configuration is the
truth.** We never treat a namespace pattern as proof by itself.

## 4. System vs Robot vs Node

The graph gives us `Environment` (a domain) and `Node` (a discovered entity).
The middle layers are our debugger's model:

```
Environment        (ROS concept: one domain / what the collector sees)
    ↓
System             (debugger model: a logical application, e.g. Warehouse)
    ↓
Robot              (debugger model: a physical unit within a system)
    ↓
Node               (ROS concept: a discovered node)
    ↓
Topic / TF / ...   (ROS concepts: runtime entities attached to the node)
```

Which parts are ROS and which are ours:

- ROS 2 provides the **Environment** (domain) and the **leaves** (nodes,
  topics, namespaces).
- We introduce **System**, **Robot**, and the containment between them. These
  are not discoverable; they are declared in configuration and applied by the
  attribution engine.

`robot=None` is meaningful: a *system-level* node (e.g. `/fleet_manager`)
belongs to a system but to no robot.

## 5. Attribution strategies

Approaches we considered, and their status:

- **Namespace-based attribution** — *implemented*: the primary signal; the
  engine matches a node's namespace components against configured prefix
  rules (longest-prefix wins).
- **Naming conventions** — *implemented in a limited, low-confidence form*:
  when a topic's endpoint nodes are temporarily unknown, we can fall back to
  matching the *topic's* namespace against the same config rules, marked
  `source="convention"` and `confident=False`. This is a hint, never a
  confident claim. (Added during Phase 3 work; see `attribute_topic_name`.)
- **Explicit configuration** — *implemented*: `config/attribution.yaml`
  declares systems, robots, their namespaces, and exact node names. This is
  the authoritative truth.
- **Manual attribution** (interactive tagging) — *not implemented*; a future
  UI concern.
- **Automatic inference** (e.g., machine learning over names) — *not
  implemented*; this is the guessing we explicitly forbid.

## 6. Why UNCLASSIFIED matters

When evidence is weak, the debugger must not confidently guess ownership.

Example: `/lidar_node` at the root namespace. Why can't we automatically say
"Robot 1"?

- there is no namespace evidence linking it to Robot 1
- it could be any system's lidar, a shared tool, or an unrelated node
- guessing would produce **false attribution**

Why false attribution is dangerous for a debugging tool: later phases will
compute things like "Robot 2's LiDAR is stale". If attribution quietly
assigned the wrong node to Robot 2, the tool would send the developer to the
wrong robot, and the tool's credibility dies on its first confident wrong
answer.

`UNCLASSIFIED` is the honest state: "I see a node, I do not know who owns it,
and I will say so rather than guess." Guessing converts an unknown into a
confident lie; UNCLASSIFIED converts it into an explicit open question.

## 7. What happens when a node appears?

Attribution is event-driven and rides on Phase 1 discovery:

```
node appears on the graph
  → GraphModel emits NODE_ADDED
  → SystemModel.handle_graph_event() receives it
  → Attributor.attribute(node) matches config (namespace prefix / exact name)
  → the node joins its system/robot (or becomes UNCLASSIFIED)
```

Because `SystemModel` consumes the same `GraphEvent` stream, attribution stays
in sync with discovery automatically — new robots appear as their nodes do.

## 8. What happens when a node disappears?

```
node leaves the graph
  → GraphModel emits NODE_REMOVED
  → SystemModel.handle_graph_event() removes it
  → the rest of the system is untouched
```

Removal is scoped: only the departed node is removed. Robot 2's remaining
nodes, and every other robot's nodes, are unaffected. This isolation is
critical — a disappearing node must not corrupt the rest of the attributed
model.

## 9. Same ROS_DOMAIN_ID, multiple systems

One ROS domain does **not** mean one project. The domain is a network
isolation boundary, not a project boundary. Warehouse and a SLAM experiment
can share domain 10 and be completely invisible to each other's *logic* while
being visible to discovery.

Consequences:

- the debugger on domain 10 sees **one merged flat graph** from both systems
- without attribution, it cannot separate SLAM's behavior from the
  warehouse's
- with attribution, `AttributionConfig` assigns `/robot1/*` → Warehouse and
  `/slam/*` → SLAM, keeping the two systems' diagnostics separate

This is the concrete reason attribution must exist: **discovery merges;
attribution separates.**

## 10. What could go wrong?

- **Ambiguous namespaces** — a namespace that could mean a robot or a system
  (`/slam/...`); only configuration disambiguates.
- **Inconsistent naming** — some robots namespaced, others at root.
- **Nodes without namespaces** — `/fleet_manager`, `/rviz`; handled by exact
  node-name rules in config, else UNCLASSIFIED.
- **Same names** — two nodes with the same FQN on one domain collide in
  discovery; the graph represents one of them.
- **Insufficient evidence** — nothing to match → UNCLASSIFIED (safe).
- **Incorrect assumptions** — treating a namespace pattern as proof rather
  than a signal.
- **Attribution changing as the graph changes** — a node whose endpoint info
  is momentarily unknown can flip between states; we treat "unknown node
  info" as a transient `PENDING` state and retry rather than permanently
  discarding the subject.

## 11. Why attribution is necessary for future diagnostics

"/scan is stale" is a fact with no context. "**Robot 2 → LiDAR → /scan is
stale**" is actionable: the developer knows which robot, which subsystem, and
which topic to investigate.

Attribution provides the **subject** for every later observation and
diagnostic. Phase 3 measures topics; Phase 4 judges them. Both need to know
*whose* topic it is. Without attribution, the debugger would report "some
node is slow" — with it, "Robot 2's scan stream is slow".

## 12. What we actually implemented

**`ros2_debugger/attribution.py`** — the entire attribution layer, with no
rclpy imports:

- `Attribution` — the answer to "who owns this entity?" (`system`, `robot`,
  `source`, `confident`; `is_unclassified` property).
- `UNCLASSIFIED`, `MIXED`, `PENDING` — explicit states: unknown owner,
  multiple owners (e.g. `/tf`), and temporarily-unknown endpoint node info.
- `AttributionConfig` / `SystemConfig` / `RobotConfig` — declarative
  structure parsed from YAML (`from_dict`).
- `Attributor` — the decision procedure:
  - builds namespace-prefix rules and exact node-name rules from config
  - `attribute(node)` — longest-prefix matching; exact names beat prefixes;
    no match → UNCLASSIFIED
  - `attribute_topic_name(topic)` — low-confidence fallback for topics whose
    endpoint nodes are unknown
- `SystemModel` — the debugger's attributed view, maintained from
  `GraphEvent`s:
  - `handle_graph_event`, `sync_nodes`
  - queries: `nodes_in_system`, `nodes_in_robot`, `unclassified_nodes`,
    `system_names`
  - `attribute_topic` — attributes a topic via its endpoint nodes
    (single owner → that owner; multiple → MIXED; unknown endpoint → PENDING)

**`ros2_debugger/config/attribution.yaml`** — the `systems:` section declares
the warehouse example as data:

```yaml
systems:
  warehouse:
    namespaces: ["/warehouse"]
    robots:
      robot1: ["/robot1"]
      robot2: ["/robot2"]
      robot3: ["/robot3"]
    nodes: ["/fleet_manager"]
  slam:
    namespaces: ["/slam"]
    nodes: ["/rviz", "/slam_toolbox"]
```

**`ros2_debugger/debugger.py`** — wiring: loads the config, builds
`SystemModel`, feeds it graph events, prints the attributed summary
(`_attributed_summary`, `--config` flag).

**`test/test_attribution.py`** — scenarios S1–S7 (clear namespace, multiple
robots, multiple systems, ambiguous → UNCLASSIFIED, node appears, node
disappears, unrelated environment not mixed) plus topic attribution and config
handling.

## 13. What Phase 2 can and cannot tell us

**CAN:**

- organize runtime entities into System → Robot → Node
- provide context for later measurements ("which robot owns this?")
- associate entities wherever config-backed evidence exists
- represent unknown ownership honestly as UNCLASSIFIED

**CANNOT:**

- prove that a robot is healthy
- diagnose failures
- determine root cause
- magically know project boundaries when no evidence exists

Attribution is structure, not health. It prepares the ground; it does not
judge.

## 14. What I should be able to explain in an interview

1. Why do we need attribution at all — what can't the Phase 1 graph answer?
2. Why can't ROS 2 tell us that a node "belongs to the Warehouse"?
3. Why are namespaces useful evidence for attribution, but not proof?
4. What is the difference between a ROS 2 fact and a debugger interpretation?
5. Which attribution strategies are implemented, and which are explicitly not?
6. Why is UNCLASSIFIED better than guessing, and what would false attribution
   cause later?
7. What happens when a node appears / disappears, and why must removal be
   isolated from the rest of the model?
8. Why does one ROS domain not mean one project?
9. How does configuration remain the "truth" while namespaces are only the
   "signal"?
10. Why is "Robot 2 → LiDAR → /scan is stale" more useful than "/scan is
    stale"?
11. What is `PENDING`, and why do we retry instead of permanently discarding a
    topic whose endpoint node info is momentarily unknown?
