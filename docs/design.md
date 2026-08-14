# ROS 2 Debugger — Architecture & Design History

*Internal engineering documentation for the ROS 2 Debugging & Observability
Platform. This is not the final README and not a marketing document. It
records how the software actually evolved and why each file exists.*

> A note on history: the repository contains a **single Git commit**
> (`d1ab96e`, "feat(diagnostics): add rule-based ROS 2 health diagnostics")
> which imports the whole platform. There are no per-phase commits. Phase
> attribution in this document is therefore **reconstructed from the code,
> its module docstrings/comments, and the development record**, not from Git
> history. Where a design was considered but not implemented, it is labelled
> as such.

---

## 1. Project Goal

The debugger observes a **running ROS 2 system** (our test system is the
Warehouse) without reading or owning its source code, detects abnormal
behavior, and eventually explains probable causes.

Guiding constraints:

- the debugger must not require the warehouse project to be uploaded
- it observes whatever runs in the same ROS 2 environment (domain)
- it must remain generic — never hard-coded to the warehouse
- it should evolve from *observation* (what exists) → *organization* (what
  belongs to what) → *measurement* (what is happening) → *judgment* (is
  something wrong)

## 2. Architecture Evolution

Each phase builds on the previous one; each answers the question of the phase
it represents:

```
Phase 1 — Graph Discovery   : "What exists?"
Phase 2 — Attribution       : "What belongs to what?"
Phase 3 — Runtime Telemetry : "What is happening?"
Phase 4 — Diagnostics       : "Is something wrong?"   (implemented; see below)
```

- **Phase 1** created the collector (ROS-facing boundary), the flat graph
  model, and the CLI. It can see and track the live graph.
- **Phase 2** layered an interpretation model (System → Robot → Node) on top
  of the flat graph, driven by explicit configuration, with an honest
  UNCLASSIFIED state. It can organize what it sees.
- **Phase 3** added runtime measurement: selective topic monitoring, rates,
  last-message timestamps, out-of-band process metrics, and TF freshness,
  kept in a separate telemetry model. It can measure what is happening.
- **Phase 4** (implemented and committed, but outside the scope of this
  documentation pass) added a deterministic diagnostic engine that judges
  telemetry against declared expectations and recovers when conditions clear.

This document focuses on Phases 1–3. Phase 4 is referenced where the
architecture needs it to be complete and honest.

## 3. Core Architecture

The current architecture (all of this exists):

```
ROS 2 / DDS
    ↓
ROS-facing collector        (ros2_debugger/collector.py)
    ↓
Graph / Attribution / Telemetry   (model.py / attribution.py / telemetry.py)
    ↓
Observations (GraphEvents, TopicStats, ProcessStats, TfStats, /rosout logs)
    ↓
Diagnostic engine           (ros2_debugger/diagnostics.py — Phase 4)
```

Boundaries that matter:

- the **collector** is the only component that imports rclpy and talks to ROS
- the **models** are DDS-agnostic (no rclpy imports) — a clean contract
- **diagnostics** consume observations; they never query ROS themselves
- a **future dashboard/UI** will consume these models (see §12)

## 4. File-by-File Design History

Each entry uses the same template: why it exists, what it achieves, its
responsibility, what is inside, interactions, why the responsibility lives
here, and current status.

---

## `package.xml`

- **Created/introduced in**: Phase 1 (scaffolding).
- **Why needed**: declare the ROS 2 package (name, version, description,
  license) and runtime dependencies (`rclpy`, `rcl_interfaces`,
  `tf2_msgs`) for `ament_python`.
- **What it achieves**: makes the project a buildable/installable ROS 2
  package with `colcon`.
- **Responsibility**: package metadata + dependency declaration.
- **What's inside**: `format="3"` package with `exec_depend` entries.
- **Interactions**: consumed by `colcon`/`ament`; no runtime code path.
- **Why here**: packaging is build-system configuration; nothing else
  should own it.
- **Status**: active.

## `setup.py` / `setup.cfg`

- **Created/introduced in**: Phase 1 (scaffolding), modified later to ship
  config data.
- **Why needed**: define the Python package, its console entry point
  (`debugger = ros2_debugger.debugger:main`), and `package_data` so the
  bundled YAML config ships with the install.
- **Responsibility**: build/install definition.
- **What's inside**: `find_packages`, data_files for ament, `package_data`
  for `config/*.yaml`, entry point registration.
- **Interactions**: `setup.cfg` sets script install dirs; `package_data`
  ensures `config/attribution.yaml` is available at runtime.
- **Status**: active.

## `resource/ros2_debugger` / `ros2_debugger/__init__.py`

- **Created/introduced in**: Phase 1.
- **Why needed**: ament index marker (empty resource file) and package
  version constant.
- **Responsibility**: packaging conveniences.
- **Status**: active, trivial.

## `.gitignore`

- **Created**: when the repo was first committed (after Phase 4).
- **Why needed**: exclude `build/`, `install/`, `log/`, `__pycache__/`,
  `.pytest_cache/` from version control.
- **Status**: active.

---

## `ros2_debugger/model.py`

- **Created/introduced in**: Phase 1.
- **Why needed**: the collector must hand its observations to downstream code
  through a stable, ROS-free contract. Nothing else can import rclpy if we
  want analysis to be testable without a live ROS environment.
- **What it achieves**: a flat, event-emitting snapshot of the graph that is
  independent of DDS/rclpy.
- **Responsibility**: define the flat graph types and the diff/event logic.
- **What's inside**:
  - `ChangeKind` (NODE_ADDED / NODE_REMOVED / TOPIC_ADDED / TOPIC_REMOVED /
    TOPIC_UPDATED)
  - `NodeInfo` (name, namespace, `fully_qualified_name`)
  - `EndpointInfo` (node, endpoint type, topic type, QoS fields, gid)
  - `TopicInfo` (name, types, publishers, subscribers)
  - `GraphEvent` (timestamp, kind, node/topic payload)
  - `GraphModel` — `sync_nodes`/`sync_topics` diff against the previous
    snapshot and record events; `drain_events` flushes them
- **Interactions**: the collector feeds it; `SystemModel` and `TelemetryModel`
  consume its events and topics.
- **Why this responsibility here**: separation of *observation source* (ROS)
  from *representation* (plain model) — Phase 0 decision D5 made concrete.
- **What if elsewhere**: putting rclpy types into the model would force every
  consumer to import ROS and make unit testing impossible without a node.
- **Status**: active.

## `ros2_debugger/collector.py`

- **Created/introduced in**: Phase 1. Modified in Phase 3 (telemetry
  subscriptions, per-transform TF, post-refresh hook) and later.
- **Why needed**: someone must actually join the ROS 2 environment and query
  the graph; this is the only ROS-facing module.
- **What it achieves**: the debugger becomes a participant on the domain and
  produces `GraphEvent`s plus subscription plumbing for telemetry.
- **Responsibility**: all rclpy/DDS interaction. Nothing else talks to ROS.
- **What's inside**:
  - `CollectorNode` (rclpy node `debugger_collector`)
  - 1-second graph timer (`_refresh_graph`), `_collect_nodes`,
    `_collect_topics`, `_endpoint`
  - `flush_pending_events` — delivers the initial discovery burst
  - `domain_id` (from `ROS_DOMAIN_ID`), `rmw_identifier`
  - subscriptions: `/rosout`, `/tf`, `/tf_static`
  - Phase 3: `ensure_topic_subscription` / `drop_topic_subscription`
    (BEST_EFFORT/VOLATILE observer QoS), `_resolve_message_class`,
    `post_refresh_handlers`, per-transform TF streaming
- **Interactions**: feeds `GraphModel`; exposes subscription APIs to
  `TopicMonitor`; invokes post-refresh handlers owned by `debugger.py`.
- **Why this responsibility here**: the ROS-facing boundary must be a single
  choke point so every other layer stays ROS-free.
- **What if elsewhere**: analysis code importing rclpy would couple the whole
  system to ROS and kill unit-testability.
- **Status**: active.

## `ros2_debugger/debugger.py`

- **Created/introduced in**: Phase 1. Modified in Phases 2, 3, and 4.
- **Why needed**: the composition root — it wires collector, models,
  telemetry, and (Phase 4) diagnostics together, loads config, and renders
  output.
- **What it achieves**: a runnable CLI (`ros2 run ros2_debugger debugger`)
  with a live event stream and exit summaries.
- **Responsibility**: wiring + presentation; no domain logic of its own.
- **What's inside**:
  - `_Printer` — live `[+node]`/`[-node]`/`[+topic]`/`[~topic]` and `[log]`
  - `_default_config_path`, `_load_configs` (attribution + telemetry +
    diagnostics)
  - `_attributed_summary`, `_telemetry_summary`, `_print_telemetry_live`,
    `_diagnostics_summary` (Phase 4)
  - `main` — build components, register handlers, spin, print summaries
  - flags: `--timeout`, `--no-topics`, `--config`, `--process`
- **Interactions**: instantiates `CollectorNode`, `SystemModel`,
  `TelemetryModel`, `DiagnosticEngine`; connects them via handlers.
- **Why this responsibility here**: a composition root should own wiring, not
  logic, so each component stays independently testable.
- **Status**: active.

---

## `ros2_debugger/attribution.py`

- **Created/introduced in**: Phase 2. Modified in Phase 3 (added `PENDING`
  and the low-confidence topic-name fallback when rclpy reports
  `_NODE_NAME_UNKNOWN_` endpoint nodes).
- **Why needed**: the graph says what exists but not who owns it; attribution
  imposes the debugger's logical organization (System → Robot → Node) and
  refuses to guess.
- **What it achieves**: config-grounded ownership decisions with provenance
  (source, confidence) and an explicit UNCLASSIFIED state.
- **Responsibility**: pure decision procedure over `NodeInfo`/`TopicInfo`
  values; no ROS.
- **What's inside**:
  - `Attribution`, `UNCLASSIFIED`, `MIXED`, `PENDING`
  - `AttributionConfig` / `SystemConfig` / `RobotConfig` (`from_dict`)
  - `Attributor` — namespace-prefix + exact-node rules, longest-prefix
    matching; `attribute`, `attribute_topic_name`
  - `SystemModel` — event-driven attributed view (`handle_graph_event`,
    `sync_nodes`, `nodes_in_system`, `nodes_in_robot`,
    `unclassified_nodes`, `attribute_topic`)
- **Interactions**: consumes `GraphEvent`s from `GraphModel`; `TelemetryModel`
  and the diagnostic rules ask it "who owns this topic/node?".
- **Why this responsibility here**: attribution is analysis, so it must not
  live in the collector (Phase 0 D5); keeping it pure makes it unit-testable.
- **What if elsewhere**: embedded in the collector, attribution would couple
  ROS I/O to interpretation and make it untestable without ROS.
- **Status**: active.

## `ros2_debugger/config/attribution.yaml`

- **Created/introduced in**: Phase 2 (`systems:` section). Extended in Phase 3
  (`telemetry:`) and Phase 4 (`diagnostics:`).
- **Why needed**: ownership, monitor scope, and expectations are **data**, not
  code. The debugger stays generic; only this file knows the warehouse.
- **What it achieves**: the warehouse example declared declaratively: systems
  (`warehouse`, `slam`), robots with namespaces, exact node names, telemetry
  scope, and (Phase 4) diagnostic expectations.
- **Responsibility**: all deployment-specific knowledge.
- **Interactions**: loaded by `debugger._load_configs`; consumed by
  `AttributionConfig`, `TelemetryConfig`, `DiagnosticConfig`.
- **Why here**: separating data from code is what keeps the debugger
  warehouse-agnostic — the warehouse appears in data, not logic.
- **Status**: active.

---

## `ros2_debugger/telemetry.py`

- **Created/introduced in**: Phase 3.
- **Why needed**: Phase 1/2 answer "what exists / what belongs to what" but
  not "what is happening". Telemetry measures runtime behavior.
- **What it achieves**: selective topic monitoring with rates/counts/last-
  message, out-of-band process metrics, and per-frame TF freshness, in a
  model separate from the graph.
- **Responsibility**: measurement and observation decisions; no ROS; no
  judgment (no "is this abnormal").
- **What's inside**:
  - `TelemetryConfig` (monitor scope, processes)
  - `TopicStats`, `ProcessStats`, `FrameStats`
  - `TfStats` — per-frame freshness
  - `TopicMonitor` — `reconcile` (choose topics, subscribe/unsubscribe via
    the collector, explain skips) and `sample` (rates over ≥ 0.25 s windows,
    idle times)
  - `ProcessMonitor` — `/proc` sampling (CPU ticks, `VmRSS`, starttime
    guards)
  - `TelemetryModel` — aggregate + drive one reconcile per cycle
- **Interactions**: uses `CollectorNode.ensure_topic_subscription`;
  consults `SystemModel` for topic ownership; feeds `diagnostics.py`.
- **Why this responsibility here**: measurement is analysis and must stay ROS-
  free; the collector only provides the raw subscription mechanism.
- **What if elsewhere**: measuring inside the collector would mix ROS I/O with
  analysis and make the telemetry logic untestable.
- **Status**: active.

---

## `ros2_debugger/diagnostics.py`

- **Created/introduced in**: Phase 4 (implemented and committed; the
  diagnostic-engine documentation is outside this task's scope).
- **Why needed**: telemetry is evidence; judgment needs expectations.
- **Responsibility**: deterministic, evidence-backed diagnostics with an
  ACTIVE/RESOLVED lifecycle, driven by configured expectations.
- **What's inside**: `Severity`, `Diagnostic`, `DiagnosticConfig`, six rule
  functions in a registry, `DiagnosticEngine`.
- **Interactions**: consumes `GraphModel`, `SystemModel`, `TelemetryModel`.
- **Status**: active (Phase 4).

---

## Tests

### `test/test_model.py` — Phase 1

Unit tests for `GraphModel` diffing: add/remove events, no-change-no-event,
topic lifecycle, FQN normalization.

### `test/test_attribution.py` — Phase 2

Scenarios S1–S7 (clear namespace, multiple robots, multiple systems on one
domain, ambiguous node → UNCLASSIFIED, node appears, node disappears,
unrelated environment not mixed) plus topic attribution and config handling.

### `test/test_telemetry.py` — Phase 3

Selection decisions, rate/staleness math, unsubscribe on disappearance,
`/proc` liveness/RSS, TF freshness, config parsing, pending-endpoint recovery.

### `test/test_diagnostics.py` — Phase 4

Healthy negative, stale/degradation/missing-publisher/node-gone/TF/CPU rules,
and recovery.

- **Why tests here**: they mirror the modules they test and run without ROS
  (the models are DDS-agnostic). Live behavior is verified separately with
  integration runs against demo publishers.

## 5. Phase 1 File History

Introduced in Phase 1:

- `package.xml`, `setup.py`, `setup.cfg`, `resource/ros2_debugger`,
  `ros2_debugger/__init__.py` — scaffolding to make it a buildable package.
- `ros2_debugger/model.py` — the flat, DDS-agnostic graph model + events.
- `ros2_debugger/collector.py` — the ROS-facing `CollectorNode`: poll + diff
  graph, buffer initial events, subscribe to `/rosout`/`/tf`/`/tf_static`.
- `ros2_debugger/debugger.py` — CLI entry, printer, `--timeout`/`--no-topics`,
  domain banner.
- `test/test_model.py` — model diffing tests.

Key Phase 1 findings baked into the code: rclpy/Jazzy has no graph-event
callback → polling + diffing; the initial discovery burst must be buffered and
flushed or the debugger looks empty on arrival.

## 6. Phase 2 File History

Introduced / modified in Phase 2:

- `ros2_debugger/attribution.py` — the full attribution layer
  (`Attribution`, `AttributionConfig`, `Attributor`, `SystemModel`,
  `UNCLASSIFIED`/`MIXED`).
- `ros2_debugger/config/attribution.yaml` — the `systems:` section declaring
  the warehouse example.
- `ros2_debugger/debugger.py` — `--config`, config loading, `SystemModel`
  wiring, `_attributed_summary`.
- `test/test_attribution.py` — S1–S7 scenario tests.

Design decision recorded: **configuration is the truth, namespaces are the
signal**; UNCLASSIFIED beats guessing.

## 7. Phase 3 File History

Introduced / modified in Phase 3:

- `ros2_debugger/telemetry.py` — the telemetry layer
  (`TelemetryConfig`, `TopicStats`, `ProcessStats`, `FrameStats`, `TfStats`,
  `TopicMonitor`, `ProcessMonitor`, `TelemetryModel`).
- `ros2_debugger/collector.py` — added `ensure_topic_subscription` /
  `drop_topic_subscription` (BEST_EFFORT/VOLATILE observer QoS),
  `_resolve_message_class`, `post_refresh_handlers`, per-transform TF.
- `ros2_debugger/attribution.py` — added `PENDING` and
  `attribute_topic_name` (the `_NODE_NAME_UNKNOWN_` fix).
- `ros2_debugger/config/attribution.yaml` — added the `telemetry:` section.
- `ros2_debugger/debugger.py` — `TelemetryModel` wiring, `_telemetry_summary`,
  `_print_telemetry_live`, `--process`.
- `test/test_telemetry.py` — telemetry tests.

Design decisions recorded: selective observation (never subscribe to
everything); observer QoS maximally compatible; process metrics out-of-band;
telemetry kept separate from the graph.

## 8. Responsibility Boundaries

- **Collector vs Model** — the collector owns rclpy and produces a plain
  model; the model owns representation and diffing. This is why analysis is
  testable without ROS.
- **Graph vs Telemetry** — graph = structural identity (event-driven); telemetry
  = continuous measurements (sampled). Different cadence, different staleness
  semantics, different consumers. They cross-reference but never merge.
- **Observation vs Diagnosis** — telemetry records facts; diagnostics judge
  them against expectations. Evidence never becomes a verdict inside the
  observation layer.
- **ROS-facing vs application logic** — only `collector.py` imports rclpy;
  everything else consumes its output. Coupling the rest to ROS would break
  unit testing and bury the architectural boundaries.

These boundaries exist so each layer can be tested, replaced, and reasoned
about independently — the core of the Phase 0 architecture.

## 9. Important Engineering Decisions

- **Why Python/rclpy** — rapid iteration, matches the analysis-heavy nature,
  and the models are pure Python; performance-sensitive hot paths are not
  required yet.
- **Why a collector boundary** — a single choke point for all ROS I/O keeps
  everything else ROS-free.
- **Why polling + diffing** — rclpy/Jazzy offers no graph-event API; the
  decision is hidden behind the event interface so it can be swapped later.
- **Why graph/telemetry separation** — different cadence and staleness
  semantics; merging would blur "exists" with "is active".
- **Why attribution exists** — discovery merges; attribution separates.
  Ownership is an organizational fact, not a runtime fact.
- **Why telemetry is selective** — the monitor must not distort the system it
  observes (CPU, bandwidth, QoS, large/high-frequency topics).
- **Why we avoid subscribing to everything** — observer overhead + QoS
  incompatibility + unnecessary payload processing.
- **Why diagnostics are not mixed into collectors** — observation and judgment
  are different concerns; mixing them couples ROS I/O to policy and breaks
  testing (Phase 4 decision, consistent with the earlier boundaries).

## 10. Alternatives Considered

Alternatives are reconstructed engineering options (the repo has no record of
them beyond the implementation):

- **Read the warehouse source** vs **observe the running system** — source
  gives intent but requires the project and can't see runtime; chosen:
  runtime observation (universal, honest).
- **Shell out to `ros2` CLI** vs **in-process rclpy APIs** — CLI is pull-only,
  text-parsing, slow; chosen: in-process collector (event-style output,
  real-time, first-class graph participant).
- **Event-driven discovery** vs **polling + diff** — event-driven is ideal but
  unavailable in rclpy/Jazzy; chosen: polling behind an event interface.
- **Mirror each publisher's QoS** vs **one observer profile** — mirroring is
  fragile and churny; chosen: BEST_EFFORT/VOLATILE (maximally compatible).
- **Subscribe to everything** vs **selective monitoring** — everything is
  expensive and wasteful; chosen: attributed topics + allowlist, with reasons
  recorded for every skip.
- **Infer ownership automatically** vs **configured attribution** — inference
  is guessing; chosen: config as truth, namespaces as signal, UNCLASSIFIED
  when weak.
- **Single merged state object** vs **separate graph/telemetry models** —
  merged blurs semantics; chosen: separate siblings.
- **Put rules in the collector** vs **separate diagnostic engine** — the
  former couples ROS I/O to policy; chosen: a consumer-side engine.

## 11. Known Limitations

Discovered during Phases 1–3 (not exhaustive, but honest):

- **Discovery timing** — the graph APIs lag reality by discovery latency;
  snapshots can be momentarily inconsistent.
- **Polling** — change detection is bounded by the 1 s interval; very short
  appearances can be missed.
- **No rclpy graph events on Jazzy** — we poll; a push API would be nicer.
- **Ambiguous attribution** — flat/inconsistent namespaces, duplicate node
  names, and nodes without namespaces can only be handled as well as config
  allows; unknowns become UNCLASSIFIED.
- **`_NODE_NAME_UNKNOWN_`** — rclpy can report unknown endpoint node info
  (rmw race); handled via `PENDING` + retry + low-confidence fallback, but
  it adds a cycle of latency.
- **QoS limitations** — an observer that required RELIABLE would silently
  miss best-effort streams; we avoid that, but some contracts are still
  invisible to us (e.g. transient-local history we don't replay).
- **Telemetry overhead** — rclpy still deserializes each received message;
  selective subscription bounds but does not eliminate this.
- **Lack of expectations (pre-Phase 4)** — telemetry alone cannot say "1 Hz
  is abnormal"; judgment requires configured expectations.
- **No diagnostics before Phase 4** — Phase 3 deliberately reports facts
  without verdicts.

## 12. Future Architecture

The following do **not** exist yet; they are clearly marked as future work.

```
Telemetry
    ↓
Diagnostic engine        ← Phase 4 (IMPLEMENTED — see §2/§4)
    ↓
Dashboard / web UI       ← future (visual system: dollar-green + cream, to be
                             implemented as a coherent design system in the
                             UI phase)
    ↓
Historical analysis      ← future (time series, baselines, trending)
    ↓
Root-cause assistance    ← future (hypothesis testing over evidence)
```

Notes for the future phases:

- the **dashboard** will consume the existing models (graph, attribution,
  telemetry, diagnostics) rather than re-collecting anything
- **historical storage** will add time to the current in-memory snapshots
- **root-cause analysis** will operate on evidence, never invent certainty —
  consistent with the observation ≠ diagnosis principle built so far
- when the UI phase begins, the visual design system (dollar-green as the
  primary/status accent, cream as the complementary color, professional and
  developer-tool oriented) must be established as a coherent system, not
  applied ad hoc

---

*End of design history for Phases 1–3 (with Phase 4 noted where required for
accuracy).*
