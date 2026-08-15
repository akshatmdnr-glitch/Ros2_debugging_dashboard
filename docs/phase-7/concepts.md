# Debugger Backend & API

*Phase 7 of the ROS 2 Debugging & Observability Platform.*

Phases 1–6 built the engine. Phase 7 asks:

> How can another application consume the debugger's information?

The answer is a backend API that is a **thin adapter** over the debugger's
authoritative state. This document describes **our** implementation in
`ros2_debugger/app.py` and `ros2_debugger/api.py`. It is not a generic REST
tutorial.

---

## What problem does the backend API solve?

The debugger now knows a lot:

- systems, robots, nodes, topics (Phases 1–2),
- telemetry (Phase 3),
- diagnostics (Phase 4),
- correlations and hypotheses (Phase 5),
- active and recovered incidents with timelines (Phase 6).

But all of it lives inside Python objects owned by the CLI's `main()`. A future
web dashboard cannot — and must not — import `collector.py`,
`diagnostics.py`, `correlation.py`, or `history.py`. Without a boundary, every
consumer would have to know the engine's internals and would couple itself to
rclpy, dataclasses, and lifecycle details that are none of its business.

The problem Phase 7 solves: **expose the debugger's knowledge through a stable
interface that a dashboard can consume without knowing how the engine works.**

## Why can't the dashboard directly use the internal debugger?

Because the internal debugger is a *library*, not a *service*:

- it imports rclpy and requires a live ROS environment,
- its objects (`Diagnostic`, `IncidentSession`) are internal contracts that
  evolve freely from phase to phase,
- it has no notion of HTTP, validation, or stable identifiers for external
  consumers.

The dashboard wants simple answers: *"what systems exist, which robots are
degraded, what incidents are active, what happened yesterday?"* It should not
care that an incident is an `IncidentSession` with a `_members` set.

## What is an API boundary?

An API boundary is a **stable contract between the engine and the UI**. The
engine owns *what is true*; the API owns *how it is spoken*; the UI owns *how
it is shown*. The API maps internal objects to response schemas, returns
predictable errors, and never changes engine behavior.

Our boundary has two pieces:

1. **`DebuggerApp`** (`app.py`) — the composition root. It owns all engines and
   the collector; it is the single source of truth. Both the CLI and the API
   are consumers of it.
2. **`create_app(app)`** (`api.py`) — the HTTP adapter. It reads snapshots from
   the `DebuggerApp` it is given and returns typed responses.

## Internal model vs API response model

Two different kinds of "model":

- **Internal model** — Python objects like `GraphModel`, `Diagnostic`,
  `IncidentSession`. They exist for the engine's convenience and change as the
  engine changes.
- **API response model (DTO / schema)** — Pydantic models in `api.py`
  (`System`, `Diagnostic`, `Incident`, ...). They are the **external contract**
  a dashboard depends on.

Why separate them? The engine can add a field to `IncidentSession` tomorrow
without breaking the dashboard, as long as the API schema stays stable. The
DTOs also validate/coerce data, so a malformed internal state cannot leak out
as garbage JSON. We avoid duplicating the *state* — the DTOs are just a typed
projection of the same objects, read under the same lock.

## Why does the API need a stable contract?

A dashboard (and anyone else) builds against the API, not the engine. If field
names, status strings, or error shapes change between releases, the dashboard
breaks even though the engine is fine. The contract we chose:

- stable, descriptive field names (`started_at`, `ended_at`, `duration`,
  `confidence`, `strategies`, `events`),
- consistent status strings (`ACTIVE` / `RECOVERING` / `RECOVERED`; `HEALTHY` /
  `DEGRADED` is deliberately **not** invented here),
- monotonic `timestamp`s and incident `id`s,
- empty results are `[]`, never `null` or a fake "no problems" object,
- errors are predictable HTTP codes (404 unknown resource, 422 invalid input).

## How does API data flow from ROS 2?

There is exactly **one** path from ROS to the API:

```
ROS 2
  ↓
CollectorNode (app.py, the only rclpy consumer)
  ↓
GraphModel / TelemetryModel (attribution, diagnostics)
  ↓
DiagnosticEngine → CorrelationEngine → HistoryEngine
  ↓
DebuggerApp.snapshot_*()  (plain dicts, built under a lock)
  ↓
API endpoints (create_app) → typed responses
  ↓
Future dashboard
```

The refresh cycle runs in the rclpy spin thread; snapshots are read in the API
thread. A `threading.Lock` guards both so a snapshot is never a torn read.

## Why should the API not collect ROS 2 data itself?

The prompt's warning is about *duplicate sources of truth*:

- If the API subscribed to ROS topics directly, it would maintain "State B"
  next to the engine's "State A". State B would drift (stale rates, missing
  incidents) and two codebases would own collection logic.
- Our `create_app` contains no `rclpy`, no `CollectorNode`, no subscription
  call — it only reads the `DebuggerApp` it is given. The API is verified by
  test to contain none of those (see the architecture-boundary test).

## What information should the API expose?

The endpoints match what the engine can *actually* produce — nothing more:

| Endpoint | Purpose / consumer |
|---|---|
| `GET /health` | Liveness + headline counts; dashboard banner / health check |
| `GET /systems` | Systems with robots, node lists, active counts; overview |
| `GET /robots` | Flat robot list with counts; fleet view |
| `GET /nodes` | Attributed nodes with owners; detail pages |
| `GET /topics` | Graph topics with endpoint counts; graph view |
| `GET /telemetry` | Topic rates, process resources, TF freshness; detail pages |
| `GET /diagnostics` | Active + resolved verdicts; alerts list |
| `GET /correlation` | Phase 5 groups and cautious hypotheses |
| `GET /incidents` | Active + history in one call |
| `GET /incidents/active` | Only open incidents |
| `GET /incidents/history` | Completed incidents with timelines |
| `GET /incidents/{id}` | One incident incl. ordered event timeline |

Counts like `active_diagnostics` / `active_incidents` are **aggregations of
existing state**, not new judgment: the API does not decide "healthy" vs
"degraded" — it reports what Phase 4/6 already concluded.

## Current state vs historical state

- **Current state**: `GET /diagnostics` (active), `GET /incidents/active`,
  `GET /correlation` (active), `GET /telemetry`. These answer *"what is wrong
  right now?"*
- **Historical state**: `GET /incidents/history` and `GET /incidents/{id}`.
  These answer *"what happened, when, in what order, how long did it last?"*
  — the Phase 6 timelines.

## Error handling

Predictable behavior for realistic failures:

| Situation | Response |
|---|---|
| No ROS system running | Valid empty lists; `/health` shows `nodes: 0`, counts `0` — never fake data |
| Nothing discovered yet | Same: `[]` / zero counts |
| No diagnostics / incidents | Empty arrays |
| Unknown incident id | `404` with `detail` |
| Unknown endpoint | `404` |
| Invalid path parameter (`/incidents/abc`) | `422` validation error |
| Wrong method | `405` |

Empty is a *valid, meaningful state*: the API never invents data to look alive.

## Empty state

A fresh `DebuggerApp` (or one watching an empty domain) returns:
`/systems` → configured systems with empty robots, `/diagnostics` →
`{"active": [], "resolved": []}`, `/incidents/active` → `[]`. This is the
honest "nothing observed yet" contract, matching the engine's own UNCLASSIFIED /
no-expectation honesty.

## API validation

FastAPI + Pydantic validate path parameters (`incident_id: int`) and coerce
response bodies against the schemas. Bad input → `422`; unknown ids → `404`.
We did not add authentication or a database — nothing in the project requires
them, and the prompt forbids introducing them without justification.

## What could go wrong?

- **Torn snapshots** — reading engine state while the refresh cycle mutates it.
  Prevented by the shared lock around `refresh()` and every `snapshot_*`.
- **Stale State B** — the API keeping its own copy. Prevented by design: the
  API holds a reference to `DebuggerApp` and always reads through it; the
  `test_state_updates_reflected` test proves responses change when the app
  changes.
- **API drifting into business logic** — prevented by keeping `create_app`
  read-only and by the architecture test asserting no collection code exists.
- **Schema drift breaking the dashboard** — mitigated by the typed DTO layer;
  a field the dashboard depends on changes deliberately, not accidentally.
- **Unbounded history** — the Phase 6 in-memory history is exposed as-is; a
  long session grows memory. Documented as FUTURE (retention policy).

## What we actually implemented

**`ros2_debugger/app.py`** (NEW) — `DebuggerApp`, the shared composition root:

- builds all engines + (optionally) the collector; `load_configs` /
  `default_config_path` moved here,
- `refresh(now)` — one full observation/evaluation cycle under a lock,
- `snapshot_*()` — plain-dict snapshots (health, systems, robots, nodes,
  topics, telemetry, diagnostics, correlation, incidents, incident-by-id).

**`ros2_debugger/api.py`** (NEW) — FastAPI adapter:

- Pydantic DTOs (the stable contract),
- `create_app(app)` — read-only endpoints over one `DebuggerApp`,
- `main()` — uvicorn in a background thread + rclpy spin loop; `debugger-api`
  console script.

**`ros2_debugger/debugger.py`** (MODIFIED) — the CLI is now a consumer of
`DebuggerApp`; it only prints. No engine wiring lives here anymore.

**`setup.py` / `package.xml`** — added `debugger-api` entry point and
fastapi/uvicorn dependencies.

**`test/test_api.py`** (NEW, 16 tests) — startup, empty state, every resource,
404/422/405, single-source-of-truth updates, and the architecture-boundary
test (the API contains no collection code).

## Alternatives considered

- **API subscribes to ROS directly** — rejected: creates a second state store
  that drifts; duplicated collection logic. The API reads the engine.
- **Expose internal objects as JSON directly** — rejected: internal key tuples
  and lifecycle internals would leak into the contract; DTOs give a stable,
  readable schema.
- **Flask / plain `http.server`** — rejected: no typed validation, no automatic
  OpenAPI docs for the dashboard, hand-rolled error handling.
- **A separate state store / database** — rejected: the engine already *is* the
  state; a database is unjustified until historical querying is required.
- **Auth** — rejected: local developer tool; nothing requires it.

## What the API can now provide

- a live, typed view of the whole pipeline (structure, telemetry, diagnostics,
  hypotheses, incidents),
- incident timelines with ordering, duration, and lifecycle states,
- predictable empty and error responses,
- a single source of truth shared with the CLI — no drift.

## What it still cannot provide

- **Status derivation** — "healthy vs degraded" is left to the dashboard (the
  API reports counts and incidents, not verdicts about verdicts).
- **Historical persistence** — history is in-memory; no query across restarts.
- **Any write capability** — the API is read-only by design; there is no
  command/control (yet).
- **Authentication** — none.
- **A UI** — that is the next phase.

## What I should be able to explain in an interview

1. Why does the project need a backend API?
2. Why shouldn't the frontend import `collector.py`?
3. What is the difference between the internal debugger model and an API
   response model?
4. Why shouldn't the API subscribe to ROS 2 topics itself?
5. Where does API data actually come from? (one path, one source of truth)
6. How do we avoid two sources of truth? (shared `DebuggerApp`, lock, test)
7. What should the API return when no ROS system is running?
8. Why are stable contracts important for the future dashboard?
9. What happens if the requested robot/incident does not exist? (404/empty)
10. What information comes from Phase 4, 5, and 6 respectively?
11. Why is UI logic not placed inside the backend API?
12. What makes this API reusable for any ROS 2 system, not just the warehouse?
    (config-driven engines; the API only exposes whatever the engine produced)
