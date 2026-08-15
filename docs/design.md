# ROS 2 Debugger — Architecture & Design History

*Internal engineering documentation for the ROS 2 Debugging & Observability
Platform. This is not the final README and not a marketing document. It
records how the software actually evolved and why each file exists.*

> A note on history: Phases 1–4 were imported into the repository in a single
> Git commit (`d1ab96e`, "feat(diagnostics): add rule-based ROS 2 health
> diagnostics"), with a documentation commit (`27a8e68`) immediately after.
> Phase attribution for Phases 1–4 in this document is therefore **reconstructed
> from the code, its module docstrings/comments, and the development record**,
> not from Git history. Phase 5 was developed and committed separately (see
> §13). Where a design was considered but not implemented, it is labelled as
> such (IMPLEMENTED / CONSIDERED / FUTURE).

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
Phase 4 — Diagnostics       : "Is something wrong?"   (implemented)
Phase 5 — Correlation       : "Which abnormalities are related, and what
                               might be contributing?"  (implemented; see §13)
```

- **Phase 1** created the collector (ROS-facing boundary), the flat graph
  model, and the CLI. It can see and track the live graph.
- **Phase 2** layered an interpretation model (System → Robot → Node) on top
  of the flat graph, driven by explicit configuration, with an honest
  UNCLASSIFIED state. It can organize what it sees.
- **Phase 3** added runtime measurement: selective topic monitoring, rates,
  last-message timestamps, out-of-band process metrics, and TF freshness,
  kept in a separate telemetry model. It can measure what is happening.
- **Phase 4** added a deterministic diagnostic engine that judges
  telemetry against declared expectations and recovers when conditions clear.
  It can say what is abnormal.
- **Phase 5** added a correlation engine that groups related ACTIVE
  diagnostics into incidents and produces cautious hypotheses with qualitative
  confidence. It can say which abnormalities may be related — never a root
  cause.

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
    ↓
Correlation engine          (ros2_debugger/correlation.py — Phase 5)
    ↓
Incidents + hypotheses      (consumed by a future dashboard/history)
```

Boundaries that matter:

- the **collector** is the only component that imports rclpy and talks to ROS
- the **models** are DDS-agnostic (no rclpy imports) — a clean contract
- **diagnostics** consume observations; they never query ROS themselves
- **correlation** consumes diagnostics (and reads models); it never queries ROS
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

- **Created/introduced in**: Phase 1. Modified in Phases 2, 3, 4, and 5.
- **Why needed**: the composition root — it wires collector, models,
  telemetry, diagnostics, and (Phase 5) correlation together, loads config,
  and renders output.
- **What it achieves**: a runnable CLI (`ros2 run ros2_debugger debugger`)
  with a live event stream and exit summaries.
- **Responsibility**: wiring + presentation; no domain logic of its own.
- **What's inside**:
  - `_Printer` — live `[+node]`/`[-node]`/`[+topic]`/`[~topic]` and `[log]`
  - `_default_config_path`, `_load_configs` (attribution + telemetry +
    diagnostics + correlation)
  - `_attributed_summary`, `_telemetry_summary`, `_print_telemetry_live`,
    `_diagnostics_summary` (Phase 4), `_print_incident` / `_incident_summary`
    (Phase 5)
  - `main` — build components, register handlers, spin, print summaries
  - flags: `--timeout`, `--no-topics`, `--config`, `--process`
- **Interactions**: instantiates `CollectorNode`, `SystemModel`,
  `TelemetryModel`, `DiagnosticEngine`, `CorrelationEngine`; connects them via
  handlers; the correlation engine runs after each diagnostic evaluation.
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
  (`telemetry:`), Phase 4 (`diagnostics:`), and Phase 5 (owner evidence +
  `correlation:`).
- **Why needed**: ownership, monitor scope, and expectations are **data**, not
  code. The debugger stays generic; only this file knows the warehouse.
- **What it achieves**: the warehouse example declared declaratively: systems
  (`warehouse`, `slam`), robots with namespaces, exact node names, telemetry
  scope, diagnostic expectations, and (Phase 5) the correlation window plus
  optional owners for processes and required TF frames.
- **Responsibility**: all deployment-specific knowledge.
- **Interactions**: loaded by `debugger._load_configs`; consumed by
  `AttributionConfig`, `TelemetryConfig`, `DiagnosticConfig`, and
  `CorrelationConfig`.
- **Why here**: separating data from code is what keeps the debugger
  warehouse-agnostic — the warehouse appears in data, not logic.
- **Status**: active.

---

## `ros2_debugger/telemetry.py`

- **Created/introduced in**: Phase 3. Modified in Phase 5 (optional process
  owners).
- **Why needed**: Phase 1/2 answer "what exists / what belongs to what" but
  not "what is happening". Telemetry measures runtime behavior.
- **What it achieves**: selective topic monitoring with rates/counts/last-
  message, out-of-band process metrics, and per-frame TF freshness, in a
  model separate from the graph.
- **Responsibility**: measurement and observation decisions; no ROS; no
  judgment (no "is this abnormal").
- **What's inside**:
  - `TelemetryConfig` (monitor scope, processes) — Phase 5: a `processes`
    entry may optionally declare `system`/`robot`, parsed into
    `process_owners` so the diagnostics rules can attach an owner.
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
  diagnostic-engine documentation was written after the fact). Modified in
  Phase 5 to add optional owner evidence.
- **Why needed**: telemetry is evidence; judgment needs expectations.
- **Responsibility**: deterministic, evidence-backed diagnostics with an
  ACTIVE/RESOLVED lifecycle, driven by configured expectations.
- **What's inside**: `Severity`, `Diagnostic`, `DiagnosticConfig`, six rule
  functions in a registry, `DiagnosticEngine`. Phase 5 additions:
  - `RequiredTfFrame` — a required TF frame may be a plain name or `{frame,
    system, robot}`; the owner is optional deployment data.
  - `DiagnosticConfig.required_tf_frames` is now a tuple of `RequiredTfFrame`.
  - `rule_tf_required` attaches the configured owner to `tf_stale`/`tf_missing`.
  - `rule_resource_overload` attaches the owner from
    `TelemetryConfig.process_owners` (by process pattern) to
    `high_cpu`/`high_memory`.
- **Interactions**: consumes `GraphModel`, `SystemModel`, `TelemetryModel`;
  produces `Diagnostic`s consumed by `correlation.py`.
- **Why the Phase 5 addition here**: CPU/TF diagnostics carried no owner, so the
  correlation engine could not entity-link them to a robot. The owner is
  deployment knowledge; the truthful place to declare it is config, and the
  rules that produce the diagnostics are the right place to attach it. Existing
  behavior is unchanged when no owner is declared.
- **Status**: active (Phase 4, extended in Phase 5).

## `ros2_debugger/correlation.py`

- **Introduced/modified in**: Phase 5 (new).
- **Why needed**: Phase 4 produces independent per-subject verdicts; nothing
  relates them. Correlation is the judgment over groups ("which abnormalities
  may be related") that a dashboard/history phase will consume.
- **What problem it solves**: turns a flat list of warnings into structured
  incidents with evidence and a cautious hypothesis, while refusing to claim
  root cause.
- **What it achieves**: consumes `DiagnosticEngine.active` and emits incidents.
- **What's inside**: `Confidence` (LOW/MEDIUM/HIGH), `IncidentState`,
  `CorrelationConfig` (`temporal_window_s`, `min_members`), `Incident`
  (members, strategies, confidence, hypothesis, evidence, owner,
  attribution_uncertain, timestamps), and `CorrelationEngine`
  (`update(active, now)`, `active`, `uncorrelated`, `resolved`).
- **Pairing gate**: temporal (activation onsets within the window) AND (entity
  match, or both-members-ownerless with a shared-subject/resource link). Two
  different robots are never merged. Ownerless diagnostics are reported via
  `uncorrelated` with a reason instead of being guessed.
- **Strategies**: `entity`, `temporal`, `resource` (one resource rule + one
  behavioral rule), `shared_subject` (same topic/node/TF frame/process — a
  field-only proxy for graph correlation).
- **Confidence**: LOW if attribution uncertain; MEDIUM for entity+temporal;
  HIGH when a mechanism signal (resource or shared_subject) is present.
- **Interactions**: pure consumer of `Diagnostic`s; no rclpy; a future graph
  correlation pass would additionally read `GraphModel`/`SystemModel` (read-only).
- **Why this responsibility here**: it mirrors the `DiagnosticEngine` boundary —
  analysis stays ROS-free and unit-testable; the engine is not another collector.
- **Alternatives**: merging into diagnostics.py (rejected — couples a single
  diagnostic's lifecycle to group membership); full graph-endpoint pass
  (CONSIDERED, deferred); numeric confidence (rejected — no statistical basis);
  cross-robot "global event" grouping (FUTURE).
- **Limitations**: onset-only temporal (misses slow chains); incident identity =
  member-key set (membership change forms a new incident); no direction/root
  cause; owner evidence depends on config.
- **Status**: active (Phase 5).

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

### `test/test_correlation.py` — Phase 5

Grouping (unrelated stay separate; same-robot correlates; temporal window;
multi-robot never merged), the CPU+topic resource hypothesis, ambiguity and
uncertainty (ownerless evidence → LOW + flag; owned/unowned never pair),
recovery, the no-false-root-cause string assertions, and the optional owner
config parsing for processes and required TF frames.

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
- **Why correlation is a separate consumer (Phase 5)** — grouping diagnostics
  is a judgment over groups, distinct from the per-subject verdict lifecycle;
  it mirrors the diagnostics boundary and stays ROS-free.
- **Why the entity gate is mandatory (Phase 5)** — the only thing that makes a
  relationship credible is that the diagnostics belong to the same robot;
  relaxing it re-introduces the false correlations attribution exists to
  prevent.
- **Why CPU/TF owner evidence is config, not inference (Phase 5)** — process→
  robot and TF-frame→robot are deployment facts; inference would be guessing,
  exactly the failure Phase 2 already rejected.
- **Why qualitative confidence (Phase 5)** — no statistical basis for numeric
  scores; a fabricated number implies rigor we do not have.
- **Why onset-proximity temporal (Phase 5)** — "both active now" groups
  everything; onset co-occurrence is a cheap, deterministic, explainable signal,
  accepting that slow chains are missed.

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
- **Put rules in the collector** vs **separate diagnostic engine** —
  the former couples ROS I/O to policy; chosen: a consumer-side engine.
- **Correlate inside diagnostics** vs **separate correlation engine (Phase 5)**
  — mixing couples a diagnostic's lifecycle to group membership; chosen: a
  consumer-side engine mirroring the diagnostics boundary.
- **Full graph-endpoint correlation** vs **entity + shared_subject proxy
  (Phase 5)** — same-robot entity correlation covers most of a graph pass; a
  field-only `shared_subject` proxy covers the rest at zero cost. Full graph
  correlation: CONSIDERED / FUTURE.
- **Numeric confidence** vs **qualitative LOW/MEDIUM/HIGH (Phase 5)** — no
  statistical basis for numbers; chosen: qualitative.
- **Infer process/TF ownership** vs **config-declared owners (Phase 5)** —
  inference is guessing; chosen: optional config owners.
- **Cross-robot "global event" grouping** vs **keep robots separate
  (Phase 5)** — a shared-cause detector is a different mechanism with high
  false-positive risk; chosen: false-negative over false-positive. FUTURE.

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
- **Ownerless CPU/TF diagnostics (pre-Phase 5)** — CPU and TF diagnostics
  carried no system/robot, so they could not be entity-correlated; fixed in
  Phase 5 with optional config-declared owners (which must be configured to
  apply — an unconfigured owner still leaves the diagnostic unattributed).
- **Onset-only temporal correlation (Phase 5)** — slow chains with distant
  onsets are not grouped; coincidental bursts are mitigated by the entity gate.
- **No direction or root cause (Phase 5)** — the engine never claims a cause;
  reverse causation (a faulty driver spinning the CPU) is indistinguishable
  from the evidence.
- **No shared-cause detection (Phase 5)** — a global slowdown affecting every
  robot is deliberately not grouped; recorded as FUTURE.

## 12. Future Architecture

The following do **not** exist yet; they are clearly marked as future work.

```
Telemetry
    ↓
Diagnostic engine        ← Phase 4 (IMPLEMENTED — see §2/§4)
    ↓
Correlation engine       ← Phase 5 (IMPLEMENTED — see §2/§4/§13)
    ↓
Dashboard / web UI       ← future (visual system: dollar-green + cream, to be
                             implemented as a coherent design system in the
                             UI phase)
    ↓
Historical analysis      ← future (time series, baselines, trending)
    ↓
Root-cause assistance    ← future (hypothesis testing over evidence)
```

Additional FUTURE items explicitly considered but not implemented in Phase 5:

- **Graph/dependency correlation** — relate a degraded topic to the node that
  publishes it via `GraphModel` endpoints (read-only). CONSIDERED; the field-only
  `shared_subject` proxy covers the cheap cases today.
- **Shared-cause / "global event" detection** — same-window degradation across
  multiple robots (e.g., a simulation slowdown). Requires distinct machinery and
  has high false-positive risk; deliberately out of Phase 5 scope.
- **Incident historian / versioning** — today a membership change forms a new
  incident (the old one resolves); a future phase could merge or version
  incidents over time.
- **Directional evidence** — nothing today distinguishes "CPU drives slow topic"
  from "faulty driver spins CPU"; that would require per-mechanism evidence and
  is the entry point to real root-cause assistance.

Notes for the future phases:

- the **dashboard** will consume the existing models (graph, attribution,
  telemetry, diagnostics, incidents) rather than re-collecting anything
- **historical storage** will add time to the current in-memory snapshots
- **root-cause analysis** will operate on evidence, never invent certainty —
  consistent with the observation ≠ diagnosis ≠ correlation principle built so
  far
- when the UI phase begins, the visual design system (dollar-green as the
  primary/status accent, cream as the complementary color, professional and
  developer-tool oriented) must be established as a coherent system, not
  applied ad hoc

## 13. Phase 5 File History

Introduced / modified in Phase 5 (committed separately; unlike Phases 1–4 this
phase has its own Git commit):

- `ros2_debugger/correlation.py` — NEW. The correlation engine: `Confidence`,
  `IncidentState`, `CorrelationConfig`, `Incident`, `CorrelationEngine`
  (pairing gate → connected-component clustering → incidents with hypotheses
  and qualitative confidence; `uncorrelated` reports what could not be grouped
  and why).
- `ros2_debugger/diagnostics.py` — MODIFIED. `RequiredTfFrame` + owner-aware
  `required_tf_frames`; `rule_tf_required` and `rule_resource_overload` attach
  optional config-declared owners so CPU/TF diagnostics can be entity-correlated.
- `ros2_debugger/telemetry.py` — MODIFIED. `TelemetryConfig.process_owners` from
  optional `{pattern, system, robot}` process entries (backward compatible).
- `ros2_debugger/config/attribution.yaml` — MODIFIED. `correlation:` section;
  owners on the demo process pattern and the required TF frames.
- `ros2_debugger/debugger.py` — MODIFIED. Loads correlation config, wires
  `CorrelationEngine` after diagnostics, prints incidents live and in the
  summary (`_print_incident`, `_incident_summary`).
- `test/test_correlation.py` — NEW. 14 tests: the eight required Phase 5
  scenarios plus owner-config parsing and owner attachment by the rules.
- `docs/phase-5/concepts.md` — NEW. Phase 5 concept document.

Design decisions recorded for Phase 5: correlation is a separate ROS-free
consumer (never another collector); entity match is the mandatory safety gate
(false negatives over false positives); CPU/TF owners are config data, never
inference; confidence is qualitative; hypotheses are template-constrained to
never claim causation.

---

*End of design history for Phases 1–5.*
