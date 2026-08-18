# ROS 2 Debugger — Architecture & Design History

*Internal engineering documentation for the ROS 2 Debugging & Observability
Platform. This is not the final README and not a marketing document. It
records how the software actually evolved and why each file exists.*

> A note on history: Phases 1–4 were imported into the repository in a single
> Git commit (`d1ab96e`, "feat(diagnostics): add rule-based ROS 2 health
> diagnostics"), with a documentation commit (`27a8e68`) immediately after.
> Phase attribution for Phases 1–4 in this document is therefore **reconstructed
> from the code, its module docstrings/comments, and the development record**,
> not from Git history. Phases 5–9 were developed and committed
> separately (see §13–§17). Where a design was considered but not
> implemented, it is labelled as such (IMPLEMENTED / CONSIDERED / FUTURE).

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
Phase 6 — History & Time    : "What happened over time — when did it start,
                               how did it evolve, and when did it recover?"
                               (implemented; see §14)
Phase 7 — Backend / API     : "How can another application consume the
                               debugger's information?"  (implemented; see §15)
Phase 8 — Web Dashboard     : "How does a human developer see the system?"
                               (implemented; see §16)
Phase 9 — Views & Detail    : "How does the developer focus on one incident's
                               timeline, browse history, and inspect
                               telemetry?"  (implemented; see §17)
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
- **Phase 6** added an incident-history layer that gives incidents a stable
  identity and a temporal lifecycle (ACTIVE → RECOVERING → RECOVERED), records
  the ordered activation/recovery event sequence, and keeps an in-memory
  history of occurrences. It can say what happened over time.
- **Phase 7** extracted the composition root into a shared `DebuggerApp` (one
  source of truth for the CLI and a new FastAPI backend) and added a read-only
  HTTP API that exposes typed snapshots of systems, robots, telemetry,
  diagnostics, hypotheses, and incident timelines to a future dashboard.
- **Phase 8** added the web dashboard foundation (`web/`): a React +
  TypeScript + Vite frontend that polls the Phase 7 API and presents the
  system, robots, diagnostics, and incidents with a dollar-green/cream visual
  design system. The browser is a *view* over the API; it contains no robotics
  logic.
- **Phase 9** turned the single-scroll dashboard into routed views: Overview,
  Incidents (active + history), Incident Detail (the full Phase 6 timeline via
  `GET /incidents/{id}`), and Telemetry. A `DashboardProvider` context shares
  one polled snapshot across views; the detail view does its own per-resource
  fetch. Backend unchanged.

This document focuses on Phases 1–3. Later phases are referenced where the
architecture needs them to be complete and honest.

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
Incident history           (ros2_debugger/history.py — Phase 6)
    ↓
DebuggerApp (single source of truth)   (ros2_debugger/app.py — Phase 7)
    ↓
CLI (debugger.py)  ·  Backend API (api.py — Phase 7)
    ↓  HTTP (JSON, CORS)
Web dashboard  (web/ — React + TypeScript + Vite, Phase 8)
    ↓
Browser
    ↓
Human developer
```

Boundaries that matter:

- the **collector** is the only component that imports rclpy and talks to ROS
- the **models** are DDS-agnostic (no rclpy imports) — a clean contract
- **diagnostics** consume observations; they never query ROS themselves
- **correlation** consumes diagnostics (and reads models); it never queries ROS
- **history** consumes diagnostics events + correlation groups; it never queries
  ROS and never re-collects anything
- **`DebuggerApp`** is the single source of truth; both the CLI and the API are
  consumers, so there is no duplicate state
- **the API** is a thin read-only adapter; it contains no collection code
- **the frontend** (`web/`) is a pure view: it polls the API and renders; it
  contains no robotics logic and never talks to ROS
- the **browser** is the final client; CORS lets the Vite origin read the API

## 4. File-by-File Design History

Each entry uses the same template: why it exists, what it achieves, its
responsibility, what is inside, interactions, why the responsibility lives
here, and current status.

---

## `package.xml`

- **Created/introduced in**: Phase 1 (scaffolding). Modified in Phase 7
  (added `fastapi`, `uvicorn` exec_depends).
- **Why needed**: declare the ROS 2 package (name, version, description,
  license) and runtime dependencies (`rclpy`, `rcl_interfaces`,
  `tf2_msgs`, and Phase 7 `fastapi`/`uvicorn`) for `ament_python`.
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
  config data. Modified in Phase 7 (API entry point + web deps).
- **Why needed**: define the Python package, its console entry points
  (`debugger = ros2_debugger.debugger:main` and Phase 7
  `debugger-api = ros2_debugger.api:main`), `package_data` so the bundled YAML
  config ships with the install, and web runtime deps (`fastapi`, `uvicorn`).
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

- **Created/introduced in**: Phase 1. Modified in Phases 2, 3, 4, 5, 6, and 7.
- **Why needed**: Phase 1–6 the CLI was the composition root; Phase 7 moved the
  composition to `app.py` so the API and CLI share one `DebuggerApp`. This file
  is now presentation only.
- **What it achieves**: a runnable CLI (`ros2 run ros2_debugger debugger`)
  with a live event stream and exit summaries.
- **Responsibility**: rendering live events + exit summaries; no engine wiring.
- **What's inside**:
  - `_Printer` — live `[+node]`/`[-node]`/`[+topic]`/`[~topic]` and `[log]`
  - `_attributed_summary`, `_telemetry_summary`, `_print_telemetry_live`,
    `_diagnostics_summary` (Phase 4), `_print_incident` / `_incident_summary`
    (Phase 5), `_print_history_event` / `_history_summary` (Phase 6)
  - `main` — builds `DebuggerApp`, appends a printing post-refresh handler,
    spins, prints summaries
  - flags: `--timeout`, `--no-topics`, `--config`, `--process`
- **Interactions**: consumes `DebuggerApp` (its `refresh()` return values and
  engine references); config loading and handler wiring live in `app.py`.
- **Why this responsibility here**: a consumer of shared state, so it can be
  replaced by another consumer (the API) without touching the engine.
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
- **Status**: active (Phase 5). The member-key identity churn is addressed by
  the Phase 6 history layer (§14).

## `ros2_debugger/history.py`

- **Introduced/modified in**: Phase 6 (new).
- **Why needed**: Phase 5 incidents are snapshots — they churn on membership
  change and carry no temporal record. The debugger could not answer "what
  happened over time?" (start, order, recovery, duration, recurrence).
- **What problem it solves**: gives incidents a stable identity and an ordered
  event timeline so a related group *evolves* instead of churning, and records
  when it started, evolved, recovered, and whether it has happened before.
- **What it achieves**: a temporal incident/history layer consuming Phase 4
  events + Phase 5 groups; no new ROS collection.
- **What's inside**:
  - `LifecycleState` (ACTIVE / RECOVERING / RECOVERED),
    `MemberTransition` (ACTIVATED / RECOVERED), `MemberEvent`
    (timestamp, key, subject, transition)
  - `IncidentSession` — stable `incident_id`, owner, strategies/confidence,
    `started_at`/`ended_at`, ordered `events`, derived `state`, `duration`
  - `HistoryEngine` — `update(diagnostic_events, correlation_groups, now)`:
    routes RESOLVED events into sessions, creates/updates sessions per
    entity-scope, closes fully-recovered sessions; `active`/`closed`/`all`
- **Interactions**: consumes `DiagnosticEngine.evaluate()` events (RESOLVED
  transitions are the only source of recovery timestamps) and
  `CorrelationEngine.active` (groups). Wired in `debugger.py` after the
  correlation pass.
- **Why this responsibility here**: it mirrors the diagnostics/correlation
  boundary — analysis stays ROS-free and unit-testable; it owns the lifecycle
  that Phase 5 deliberately left to "a future incident-historian phase".
- **Alternatives**: persist to SQLite now (rejected — nothing queries history
  across restarts; in-memory is the smallest Phase 6 design); reuse Phase 5
  incidents as the history object (rejected — their member-key identity
  churns); track raw telemetry in history (rejected — retention).
- **Limitations**: in-memory only (restart loses history); ownerless groups
  fall back to a member-set scope when they share no subject (rare churn);
  no retention cap yet; no explicit incident-type classification/recurrence
  stats.
- **Status**: active (Phase 6).

## `ros2_debugger/app.py`

- **Introduced/modified in**: Phase 7 (new).
- **Why needed**: the engine's state was owned by `debugger.py`'s `main()`; an
  API could not consume it without duplicating it. The composition root had to
  become a shared object.
- **What problem it solves**: a single authoritative `DebuggerApp` that both the
  CLI and the API read — no "State A / State B" drift.
- **What it achieves**: extracts composition + the refresh cycle + lock-protected
  snapshots.
- **What's inside**: `default_config_path` / `load_configs` (moved from
  `debugger.py`), `DebuggerApp` (builds all engines + collector, `refresh()`,
  `start_refresh()`, `snapshot_*()` plain-dict views).
- **Interactions**: creates `CollectorNode` (the only rclpy consumer); feeds
  `DiagnosticEngine`/`CorrelationEngine`/`HistoryEngine`; consumed by
  `debugger.py` (CLI) and `api.py` (HTTP).
- **Why this responsibility here**: composition and authoritative state must
  live outside any single consumer so every consumer shares it.
- **Alternatives**: keep composition in the CLI and give the API a copy
  (rejected — duplicate state); put state in a database (rejected — unjustified).
- **Limitations**: in-memory only; snapshots are plain dicts (a typed contract
  is applied at the API layer).
- **Status**: active (Phase 7).

## `ros2_debugger/api.py`

- **Introduced/modified in**: Phase 7 (new). Modified in Phase 8 (CORS + dev
  flags + demo seeding).
- **Why needed**: expose the debugger through a stable interface a future
  dashboard can consume without importing internal Python classes.
- **What problem it solves**: the external contract — typed responses, empty
  state, predictable errors — decoupled from internal dataclasses.
- **What it achieves**: a read-only FastAPI adapter (`create_app(app)`) plus a
  `debugger-api` entry that runs uvicorn in a thread alongside the rclpy spin
  loop. Phase 8 additions:
  - `CORSMiddleware` (default origins `localhost:5173`) so the Vite dev origin
    can read the API from the browser;
  - `--no-ros` (serve without joining a ROS domain — frontend development) and
    `--demo` (seed a clearly-labelled synthetic warehouse state, produced by
    the REAL engines, for UI development); `seed_demo(app)`.
- **What's inside**: Pydantic DTOs (`System`, `Robot`, `Diagnostic`,
  `Incident`, `MemberEvent`, ...); endpoints `/health`, `/systems`, `/robots`,
  `/nodes`, `/topics`, `/telemetry`, `/diagnostics`, `/correlation`,
  `/incidents`, `/incidents/active`, `/incidents/history`, `/incidents/{id}`;
  `seed_demo`, `_parse_args`, `main`.
- **Interactions**: reads `DebuggerApp.snapshot_*` only; contains no collection
  code (verified by test).
- **Why this responsibility here**: the API is the adapter between engine and
  UI; it must stay thin and read-only. CORS and dev-mode belong here because
  they are API-serving concerns.
- **Alternatives**: Flask/`http.server` (no typed validation/OpenAPI); direct
  exposure of internal objects (leaks internals); a Vite proxy instead of CORS
  (would hide the real cross-origin contract in dev).
- **Limitations**: read-only; no auth; no persistence; status derivation
  ("healthy/degraded") deliberately left to the dashboard; `--demo` data is
  synthetic and clearly labelled (never used in normal operation).
- **Status**: active (Phase 7, extended in Phase 8).

## `web/` — frontend dashboard (Phase 8)

The `web/` directory is an independent npm package. It is a **view** over the
Phase 7 API: it contains no robotics logic and never talks to ROS.

### `web/package.json`

- **Phase 8. Why**: declares the frontend package, its dependencies (React,
  React DOM) and dev tooling (Vite, TypeScript, Vitest, Testing Library), and
  the scripts (`dev`, `build`, `test`). The JavaScript analogue of
  `setup.py`/`requirements.txt`.
- **Interactions**: `npm install` → `node_modules/`; `npm run dev`/`build`/`test`
  drive the rest of the toolchain.

### `web/vite.config.ts`

- **Phase 8. Why**: Vite config (React plugin, dev-server port 5173) and Vitest
  config (jsdom, globals, setup file). Keeps build/test configuration in one
  place.

### `web/tsconfig.json`

- **Phase 8. Why**: strict TypeScript options; `npm run build` runs `tsc` as a
  type-check so API contract drift is caught before the browser sees it.

### `web/src/types.ts`

- **Phase 8. Why**: mirrors the backend DTOs as TypeScript interfaces — the
  contract flows *backend schema → TS type → component*. Changing a backend
  field without updating this file breaks `tsc` (intentionally).
- **What's inside**: `Health`, `System`, `Robot`, `Diagnostic`, `Incident`,
  `MemberEvent`, `TelemetryResponse`, ... plus the frontend-only derived
  `RobotStatus` / `RobotView`.

### `web/src/services/api.ts`

- **Phase 8. Why**: the single place that talks HTTP (the API service layer).
  Components never call `fetch()` directly.
- **What's inside**: `get<T>(path)` with error handling and
  `fetchDashboard()` which fetches the dashboard's resources in parallel.
- **Why separate**: fetching is testable in one file and not repeated across
  components; the API contract lives here.

### `web/src/hooks/useDashboard.ts`

- **Phase 8. Why**: owns the data lifecycle — fetch once, then **poll** every
  2 s. Exposes `loading | connected | error` plus `lastUpdated` so the UI can
  show what is happening (and how stale it may be).
- **Update mechanism decision**: simple polling is sufficient for the
  foundation; WebSockets are CONSIDERED/FUTURE.

### `web/src/status.ts`

- **Phase 8. Why**: frontend-only derivation of a robot's visual status
  (`HEALTHY`/`WARNING`/`CRITICAL`) from active diagnostics + incidents. The
  backend deliberately does not judge "healthy/degraded"; the view does.

### `web/src/App.tsx`

- **Phase 8. Why**: the root component — composes Header, SystemOverview,
  DiagnosticPanel, IncidentPanel; renders loading/error/empty states.

### `web/src/components/*`

- `Header` — brand + connection badge + last-updated.
- `StatusBadge` — semantic health badge.
- `SystemOverview` / `RobotCard` — systems and robots with derived status.
- `DiagnosticPanel` — active diagnostics table.
- `IncidentPanel` — active incidents with their ordered event timelines.
- **Why**: small, single-responsibility, testable UI components.

### `web/src/styles/global.css`

- **Phase 8. Why**: the visual design system as CSS design tokens (dollar green
  primary, cream background, warm borders, status colors, monospace for
  numbers/timestamps, spacing scale). One token source, no per-component random
  colors.

### `web/index.html`, `web/src/main.tsx`

- **Phase 8**: the HTML shell and the React mount point (renders `<App/>` into
  `#root`).

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

### `test/test_history.py` — Phase 6

Incident creation (started_at = earliest member activation), update when a new
related diagnostic joins the same incident (stable id, no churn), event
ordering, full recovery (RECOVERED + duration), partial recovery (RECOVERING,
never falsely RECOVERED), repeated incidents as separate occurrences, multiple
robots separate, empty system, rapid activation/recovery (re-activation
handled), restart behavior (fresh engine = empty history), and ownerless
subject scoping.

### `test/test_api.py` — Phase 7

Backend API via FastAPI TestClient against `DebuggerApp(ros=False)` (no live
ROS): startup/health, valid empty state, systems/robots/nodes/topics,
telemetry, diagnostics, active incidents, incident history + timelines,
incident detail, correlation hypotheses, 404 (unknown id/endpoint), 422/405
(invalid request), single-source-of-truth state updates, and the
architecture-boundary test (the API adapter contains no ROS collection code).
Phase 8 additions: a CORS test (Origin header → allow-origin) and a demo-seed
test (the synthetic warehouse state is non-empty and attributed).

### Frontend tests — `web/` (Phase 8)

- `npm run build` (`tsc && vite build`) — type-check against the API contract
  plus production bundle (this is the "frontend starts / compiles" gate).
- `npm test` (Vitest, 11 tests):
  - `src/services/api.test.ts` — the service parses the API contract and throws
    on error/unreachable backend;
  - `src/status.test.ts` — status derivation (healthy/warning/critical) and
    per-robot diagnostic attribution;
  - `src/App.test.tsx` — connecting state, offline/error banner, rendering of
    systems/robots/diagnostics/incidents, and honest empty states (no fake
    data).
- The real browser path (Vite dev server ↔ backend with CORS) is verified
  manually with the `--no-ros --demo` backend.

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
- **Incident snapshot churn (pre-Phase 6)** — a Phase 5 incident's identity was
  its member set, so any membership change resolved the old incident and formed
  a new one; fixed in Phase 6 with stable entity-scoped sessions.
- **No temporal record (pre-Phase 6)** — recovery timestamps existed only in
  the diagnostic event stream and were lost; Phase 6 records the ordered
  lifecycle.
- **In-memory history only (Phase 6)** — incident history is lost on restart;
  persistence (JSON/SQLite) recorded as FUTURE.
- **Read-only API (Phase 7)** — the backend exposes state but cannot command
  the system; no auth; no persistence; status derivation ("healthy/degraded")
  is deliberately left to the future dashboard rather than judged in the API.

## 12. Future Architecture

The following do **not** exist yet; they are clearly marked as future work.

```
Telemetry
    ↓
Diagnostic engine        ← Phase 4 (IMPLEMENTED — see §2/§4)
    ↓
Correlation engine       ← Phase 5 (IMPLEMENTED — see §2/§4/§13)
    ↓
Incident history         ← Phase 6 (IMPLEMENTED — see §2/§4/§14)
    ↓
Backend API              ← Phase 7 (IMPLEMENTED — see §2/§4/§15)
    ↓
Web dashboard            ← Phase 8 (IMPLEMENTED — foundation, see §2/§4/§16;
                             visual system: dollar green + cream)
    ↓
Historical analysis      ← future (time series, baselines, trending)
    ↓
Root-cause assistance    ← future (hypothesis testing over evidence)
```

Additional FUTURE items explicitly considered but not implemented in Phases 5–8:

- **Graph/dependency correlation** — relate a degraded topic to the node that
  publishes it via `GraphModel` endpoints (read-only). CONSIDERED; the field-only
  `shared_subject` proxy covers the cheap cases today.
- **Shared-cause / "global event" detection** — same-window degradation across
  multiple robots (e.g., a simulation slowdown). Requires distinct machinery and
  has high false-positive risk; deliberately out of Phase 5 scope.
- **Incident type classification / recurrence statistics** — occurrences are
  tracked (Phase 6) but not labelled by type or summarized (e.g., "this
  incident has occurred 3 times"); the events already carry the raw material.
- **History persistence** — in-memory only today; JSON/file export or SQLite for
  a historical-analysis/dashboard phase. CONSIDERED.
- **History retention policy** — unbounded in memory today; a cap/archive policy
  is FUTURE.
- **Directional evidence** — nothing today distinguishes "CPU drives slow topic"
  from "faulty driver spins CPU"; that would require per-mechanism evidence and
  is the entry point to real root-cause assistance.
- **API write/command channel** — the API is read-only; commanding the system
  (restarting nodes, tuning expectations) would be a deliberate new capability.
- **Authentication** — none; the API is a local developer tool.
- **Frontend depth** — Phase 8 delivered the single-overview foundation; Phase 9
  added routing + incident detail + telemetry views. Still future: WebSockets
  push (polling today), advanced TF/graph visualization, historical charts,
  and per-incident live refresh.
- **WebSockets for push updates** — polling every 2 s is sufficient for the
  foundation; push-on-change is CONSIDERED for lower latency and less overhead.

Notes for the future phases:

- **historical storage** will add time to the current in-memory snapshots
- **root-cause analysis** will operate on evidence, never invent certainty —
  consistent with the observation ≠ diagnosis ≠ correlation principle built so
  far
- the visual design system (dollar-green as the primary/status accent, cream as
  the complementary color) is now established in `web/src/styles/global.css`
  and must be extended coherently, not applied ad hoc

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

## 14. Phase 6 File History

Introduced / modified in Phase 6 (committed separately):

- `ros2_debugger/history.py` — NEW. The incident-history layer:
  `LifecycleState` (ACTIVE/RECOVERING/RECOVERED), `MemberTransition`,
  `MemberEvent`, `IncidentSession` (stable id, owner, strategies/confidence,
  started/ended, ordered events, derived state, duration), `HistoryEngine`
  (consumes diagnostic events + correlation groups; creates/updates/closes
  entity-scoped sessions).
- `ros2_debugger/debugger.py` — MODIFIED. Wires `HistoryEngine` after the
  correlation pass, feeds it the diagnostic event stream + `correlation_engine
  .active`, prints incident events live and a history summary with ordered
  timelines (`_print_history_event`, `_history_detail`, `_history_summary`).
- `test/test_history.py` — NEW. 11 tests: the ten required Phase 6 scenarios
  plus ownerless scoping.
- `docs/phase-6/concepts.md` — NEW. Phase 6 concept document.

Design decisions recorded for Phase 6: history is a separate ROS-free consumer
(never another collector); incidents get a stable identity owned by the history
layer while Phase 5 groups stay snapshot-shaped; sessions are scoped by entity
(ownerless by shared subject); recovery requires ALL members recovered;
`ended_at` is the last member's recovery time; history is in-memory only
(restart loses it — documented, persistence is FUTURE).

## 15. Phase 7 File History

Introduced / modified in Phase 7 (committed separately):

- `ros2_debugger/app.py` — NEW. `DebuggerApp`, the shared composition root and
  single source of truth: builds all engines + collector, `refresh()`,
  `start_refresh()`, lock-protected `snapshot_*()` plain-dict views;
  `default_config_path` / `load_configs` moved here from `debugger.py`.
- `ros2_debugger/api.py` — NEW. FastAPI adapter: Pydantic DTOs (the stable
  external contract), `create_app(app)` with 12 read-only endpoints, and a
  `debugger-api` console entry that runs uvicorn in a thread alongside the
  rclpy spin loop.
- `ros2_debugger/debugger.py` — MODIFIED. Now a consumer of `DebuggerApp`;
  engine wiring and config loading moved to `app.py`; presentation only.
- `setup.py` / `package.xml` — MODIFIED. `debugger-api` entry point;
  `fastapi`/`uvicorn` added to `install_requires` and `exec_depend`.
- `test/test_api.py` — NEW. 16 tests over `DebuggerApp(ros=False)` (no live
  ROS): health, empty state, all resources, 404/422/405, single-source-of-truth
  updates, and the architecture-boundary test (the API contains no collection
  code).
- `docs/phase-7/concepts.md` — NEW. Phase 7 concept document.

Design decisions recorded for Phase 7: the API is a thin read-only adapter over
a single authoritative `DebuggerApp` (never a second state store); internal
models are projected to stable Pydantic DTOs; the API contains no ROS/rclpy
collection code; empty state is a valid contract (no fake data); counts are
aggregations, status derivation is left to the dashboard; no auth/database.

## 16. Phase 8 File History

Introduced / modified in Phase 8 (committed separately):

- `web/` — NEW. The frontend npm package (React + TypeScript + Vite):
  `package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, and `src/`
  (`main.tsx`, `App.tsx`, `types.ts`, `services/api.ts`, `hooks/useDashboard.ts`,
  `status.ts`, `components/*`, `styles/global.css`, tests). A pure view over
  the Phase 7 API — no robotics logic.
- `ros2_debugger/api.py` — MODIFIED. `CORSMiddleware` (Vite origin),
  `--no-ros` / `--demo` dev flags, and `seed_demo()` (clearly-labelled
  synthetic warehouse state produced by the real engines).
- `test/test_api.py` — MODIFIED. +2 tests: CORS headers and demo-seed state.
- `.gitignore` — MODIFIED. `web/node_modules/`, `web/dist/`.
- `docs/phase-8/concepts.md` — NEW. Full-stack learning reference (the
  "robot → debugger → API → browser" journey, written for a robotics engineer).

Design decisions recorded for Phase 8: the browser is a *view*, never a data
source (no ROS in the frontend); the API service layer owns all HTTP; frontend
state is local + polling (2 s), no state library and no WebSockets yet
(CONSIDERED/FUTURE); TypeScript mirrors the API contract so drift breaks the
build; robot status is derived in the view (HEALTHY/WARNING/CRITICAL), not
judged by the backend; the visual design system (dollar green + cream, semantic
status colors, monospace numerals) is centralized in CSS design tokens;
frontend dev needs no robot (`--no-ros --demo`).

## 17. Phase 9 File History

Introduced / modified in Phase 9 (committed separately). Frontend only — the
backend was unchanged (the Phase 7 API already served everything).

- `web/package.json` / `web/package-lock.json` — MODIFIED. Added
  `react-router-dom` (routing) and `@testing-library/user-event` (tests).
- `web/src/main.tsx` — MODIFIED. Wraps the app in `<BrowserRouter>` (the
  production router; tests use `MemoryRouter`).
- `web/src/App.tsx` / `web/src/AppShell.tsx` — MODIFIED/NEW. App = provider;
  AppShell = nav bar + `<Routes>` for `/`, `/incidents`, `/incidents/:id`,
  `/telemetry`.
- `web/src/context/DashboardContext.tsx` — NEW. Shares the single polled
  `useDashboard` snapshot across all views via React context (one poller, no
  prop-drilling, no state library).
- `web/src/pages/OverviewPage.tsx` — the Phase 8 overview, now a routed view.
- `web/src/pages/IncidentsPage.tsx` — NEW. Active incident cards + history
  table (id, owner, state, confidence, members, duration), linking to detail.
- `web/src/pages/IncidentDetailPage.tsx` — NEW. Fetches `GET /incidents/{id}`
  and renders the full Phase 6 timeline as `t+offset` events, with loading and
  404/not-found states.
- `web/src/pages/TelemetryPage.tsx` — NEW. Topic rates/counts/idle, process
  CPU/RSS, TF frames.
- `web/src/hooks/useIncident.ts` — NEW. Per-resource fetch lifecycle for the
  detail view.
- `web/src/services/api.ts` — MODIFIED. Added `fetchIncident(id)`.
- `web/src/components/NavBar.tsx` — NEW. Overview / Incidents / Telemetry links
  with an active-state indicator.
- `web/src/styles/global.css` — MODIFIED. Nav bar, incident stat grid, subhead
  styles (extends the Phase 8 design tokens).
- `web/src/App.test.tsx` / `web/src/services/api.test.ts` — MODIFIED. Router
  navigation, incident detail timeline, 404 state, telemetry view, and
  `fetchIncident` cases (17 frontend tests total).
- `docs/phase-9/concepts.md` — NEW. Phase 9 concept document (routing, SPA,
  route params, context, per-resource fetch, timeline display).

Design decisions recorded for Phase 9: client-side routing (SPA) so each view
has a bookmarkable URL and the shared snapshot is preserved across clicks; one
`DashboardProvider` poller feeds every view (no duplicate pollers); the
incident detail is a per-resource fetch (its data is not in the snapshot);
timeline events render as `t+offset` from `started_at` because the engine uses
monotonic time; no state library and no WebSockets yet.

---

*End of design history for Phases 1–9.*
