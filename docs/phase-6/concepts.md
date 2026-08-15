# Incident History & Temporal Analysis

*Phase 6 of the ROS 2 Debugging & Observability Platform.*

Phase 5 answers *"which abnormalities are related?"* — as a snapshot. Phase 6
answers the temporal question:

> What happened, when did it start, how did it evolve, and when did it recover?

This document describes **our** implementation in
`ros2_debugger/history.py` — a stable incident lifecycle and an in-memory
history layer. It is not a generic observability tutorial.

---

## What problem does incident history solve?

Phase 5 correlates diagnostics into incident *snapshots* (what is related right
now), but it cannot remember:

- when an incident started,
- in what order its diagnostics appeared,
- when each one recovered,
- how long the incident lasted,
- whether the same type has happened before.

Warehouse example — Robot 2 over 26 seconds:

```
14:30:01  CPU high
14:30:02  /robot2/scan degraded
14:30:04  TF stale
14:30:20  CPU recovers
14:30:25  /scan recovers
14:30:27  TF recovers
```

Phase 5 sees a group form and a group dissolve. Phase 6 preserves the whole
story: *"incident #1 on warehouse/robot2 ran 26 seconds; members joined in this
order and recovered in this order."*

Without Phase 6, the debugger reports snapshots, not a narrative — and the
future dashboard has nothing to draw a timeline from.

## Why is time important in debugging?

There is a world of difference between:

```
"CPU was high"              (a state)
"CPU became high at 14:30:01 and stayed high for 19 seconds"   (an incident)
```

The second tells the operator *what triggered it, how it evolved, and when it
was safe to consider the system healthy*. Time gives us ordering (which symptom
came first), duration (how long the system was degraded), and repetition (has
this happened before?). A debugger without timestamps can only say "something
is wrong"; with timestamps it can say "this chain of events ran for 26 seconds
and fully recovered."

## Observation vs diagnostic vs event vs incident

These are deliberately different objects with different lifetimes:

| Object | Example | What it is |
|---|---|---|
| OBSERVATION | "/scan message received at 14:30:02" | a measured fact (Phase 3) |
| DIAGNOSTIC | "/scan below expected rate" | a verdict against an expectation (Phase 4) |
| EVENT | "diagnostic became ACTIVE at 14:30:02" | a state *transition* in time (Phase 6 `MemberEvent`) |
| INCIDENT | "Robot 2 sensor degradation" | a stable session grouping related diagnostics (Phase 6 `IncidentSession`) |
| HISTORY | "the complete ordered sequence during the incident" | the incident's event timeline + closed occurrences |

Why not one object? They answer different questions and live different lengths
of time. A diagnostic is a *verdict about a subject*; an event is a *timestamped
transition*; an incident is a *session* that spans many diagnostics over time;
history is the *record* of those sessions. Collapsing them would lose the very
thing Phase 6 adds: what happened over time.

## What is an incident?

In Phase 6, an incident (`IncidentSession`) is a stable occurrence of a related
group of diagnostics, tracked over its whole life. It carries:

- a stable `incident_id` (monotonic — this fixes Phase 5's identity churn),
- the owner (`system`/`robot`),
- correlation meta (`strategies`, `confidence`),
- `started_at` (earliest member activation) and `ended_at` (last recovery),
- an ordered `events` timeline of member transitions.

**It differs from a diagnostic**: a diagnostic is a single-subject verdict; an
incident is a multi-subject session with no causal claim.
**It differs from a Phase 5 correlation snapshot**: Phase 5's incident is keyed
by its current member set, so any membership change spawns a new incident; the
Phase 6 session keeps one identity while its membership evolves.

## Incident lifecycle

```
ACTIVE → RECOVERING → RECOVERED
```

- **ACTIVE** — every member is still active (or the incident just formed).
- **RECOVERING** — at least one member has recovered but at least one is still
  active. This state exists so the debugger does not shout "recovered!" while
  something is still broken, and can show a winding-down incident.
- **RECOVERED** — every member has recovered; the session closes.

**Why an incident needs state:** the operator (and a future dashboard) must
distinguish "ongoing problem", "partially improving", and "done". Without
state, recovery would be a binary surprise.

**What causes each transition:**
- ACTIVE → RECOVERING: the first member resolves while others remain active.
- → RECOVERED: the last active member resolves.
- A new member joining moves RECOVERING back to ACTIVE (something new became
  wrong again).

**What if diagnostics disappear temporarily?** In our model a diagnostic does
not "disappear" silently — it either stays active (still wrong) or resolves
(a RESOLVED event). A node vanishing from the graph is itself a diagnostic
(`node_disappeared`), so it follows the same lifecycle. There is no limbo
state to model.

**What if the incident reappears later?** After RECOVERED, a later similar
group on the same entity opens a **new** occurrence with a new `incident_id`.
Same symptoms later ≠ one giant incident.

## How does an incident start?

The **correlation layer** decides what is related (Phase 5). The history layer
starts a session when a correlated group appears. Two facts matter:

- The session's `started_at` is the **earliest member activation** — so if CPU
  went high at 14:30:01 and the group only formed at 14:30:04, the incident
  still started at 14:30:01. Activation times come from the diagnostic objects
  themselves (`Diagnostic.timestamp` while ACTIVE).
- The entity/owner (`system`/`robot`) comes from the correlation group's
  attribution, so the incident is immediately attributable.

An incident therefore forms *exactly* when Phase 5 would call something an
incident (a correlated group of ≥ 2 diagnostics) — no fake incidents, no
inventing incidents from singletons.

## How does an incident evolve?

When a new related diagnostic appears (e.g., TF becomes stale after CPU and
scan are already wrong), the correlation group grows. The history layer sees
the same entity-scoped session and **appends** an ACTIVATED event for the new
member — the incident evolves instead of becoming a new incident. This is the
direct fix for the Phase 5 limitation documented in `design.md`: membership
change used to resolve the old incident and create a new one.

## How does an incident recover?

Recovery is driven by the **diagnostic event stream** (Phase 4's RESOLVED
transitions). This is the only trustworthy source of recovery timestamps: when
a diagnostic resolves, its own `timestamp` is overwritten with the resolution
time, so the activation time would otherwise be lost.

- A single member recovering → the session enters RECOVERING (never RECOVERED
  while another member is active).
- **All** members recovered → RECOVERED. This avoids falsely declaring recovery
  when any symptom persists.
- `ended_at` is the **last** member's recovery time (not the cycle in which we
  noticed), so duration is accurate.

## Incident duration

`duration = ended_at - started_at` (seconds, monotonic timebase). For the
warehouse example: 27 s. Duration is what turns "things were bad" into "the
system was degraded for 27 seconds" — the number an operator actually wants.

## Event ordering

Each session keeps an append-only list of `MemberEvent`s
(timestamp, diagnostic key, subject, transition), and `events` returns them
sorted by timestamp. Ordering answers "what came first?" — CPU activated before
scan degraded before TF went stale. Without ordering, the operator cannot see
the chain.

## Repeated incidents

Two occurrences of the same symptoms at different times are **separate
sessions** with different `incident_id`s. This is what distinguishes:

- *same incident continuing* (members still active, session open), from
- *new occurrence of the same incident type* (previous session RECOVERED, new
  one starts).

The debugger records both occurrences in history; it does not merge them.

## Multiple simultaneous incidents

Incidents are scoped by **entity** (`system`, `robot`). Robot 1 and Robot 2
experiencing simultaneous incidents produce two separate sessions — consistent
with Phase 5's rule that different robots are never merged. Ownerless incidents
are scoped by their shared subject, so two unrelated ownerless groups do not
coalesce.

## Raw telemetry vs diagnostic history

These have very different lifetimes and must not be conflated:

- **Raw telemetry** (message counts, rates, CPU samples) is high-volume and
  short-lived. We keep only the *current* values in `TelemetryModel`; we do
  not append them to incident history.
- **Diagnostics** are meaningful events — verdicts that fire and resolve.
- **Incidents** are longer-lived sessions that summarize many diagnostics over
  time.

History stores the diagnostic-level events inside incidents, never raw samples.
Storing every raw LiDAR/camera/control message would drown the meaningful
record in noise and memory.

## History retention

Because we store only diagnostic transitions inside incidents (not raw
telemetry), history is naturally small. Retention is currently **unbounded in
memory** — acceptable for a live observation session. A retention cap or
archival policy is recorded as FUTURE, not implemented.

## Persistence vs in-memory history

We chose **in-memory**: the debugger is a live observation tool; there is no
requirement yet to query history across restarts; the prompt's storage checklist
(scale, frequency, dashboard queries) does not justify a database today.
Consequences, by design:

- **Restart behavior (test 10):** a fresh `HistoryEngine` starts empty — active
  incidents and closed history are lost on restart. The debugger re-learns the
  world from scratch, as it already does for the graph and telemetry.
- JSON/file and SQLite are recorded as CONSIDERED/FUTURE for a historical-analysis
  or dashboard phase.

## What can go wrong?

- **Identity churn on ownerless groups**: an ownerless group with no shared
  subject falls back to a member-set scope, which changes as members change —
  the same churn Phase 5 had. Mitigated by subject scoping; rare and
  low-confidence, documented.
- **Falsely declaring recovery**: guarded by requiring *all* members recovered
  (tested by partial-recovery test).
- **Wrong `ended_at`**: guarded by using the last member's recovery time rather
  than the discovery cycle.
- **Merging unrelated entities**: guarded by entity scoping (tested by the
  multi-robot test).
- **Memory growth**: history is unbounded in a very long session; a retention
  cap is FUTURE.
- **Losing activation times**: a diagnostic that is already active when its
  incident forms is back-filled from the group's member timestamp; recovery
  times come from the event stream. A member that activated before the history
  engine started observing is still captured because the correlation group
  carries its activation timestamp.

## What we actually implemented

**`ros2_debugger/history.py`** (new, pure Python, no rclpy):

- `LifecycleState` (ACTIVE / RECOVERING / RECOVERED),
  `MemberTransition` (ACTIVATED / RECOVERED), `MemberEvent`
  (timestamp, key, subject, transition).
- `IncidentSession` — stable id, owner, strategies/confidence, `started_at` /
  `ended_at`, ordered `events`, derived `state`, `duration`.
- `HistoryEngine` — `update(diagnostic_events, correlation_groups, now)`
  routes RESOLVED events into sessions, creates/updates sessions from
  correlation groups, closes fully-recovered sessions; `active` / `closed` /
  `all` views. Session scope = entity `(system, robot)` or an ownerless shared
  subject.

**`ros2_debugger/debugger.py`** (modified) — instantiates `HistoryEngine`,
feeds it the diagnostic event stream + `correlation_engine.active` each cycle,
prints incident events live (`[history] ...`) and an incident-history summary
with per-incident ordered timelines.

**`test/test_history.py`** (new, 11 tests) — the ten required scenarios plus
ownerless scoping.

**No changes** to `correlation.py`, `diagnostics.py`, `telemetry.py`,
`collector.py`, or `model.py`.

## Alternatives considered

- **Persist to SQLite now** — rejected: nothing queries history across
  restarts yet; in-memory is the smallest design that satisfies Phase 6.
  Recorded as FUTURE.
- **Reuse the Phase 5 incident as the history object** — rejected: its
  member-set identity churns on every membership change, which is exactly the
  bug Phase 6 exists to fix. The history layer owns a *stable* identity and
  treats Phase 5 groups as input, not as the lifecycle.
- **Track raw telemetry samples in history** — rejected: conflates
  high-volume short-lived samples with meaningful events; see retention above.
- **Merge repeated occurrences into one incident** — rejected: a closed
  incident followed by a later one is a new occurrence, matching how Phase 5
  already refuses to merge across time and entities.
- **A separate RECOVERING state vs just ACTIVE until fully recovered** —
  adopted RECOVERING: partial recovery is real (one member can clear while
  another persists) and the state is directly tested.

## What the debugger can now tell us

- which diagnostics were related and in what order they appeared,
- when the incident started (earliest member activation) and ended (last
  recovery),
- how long it lasted,
- whether it is still active, recovering, or recovered,
- whether the same type of incident has happened before (separate occurrences),
- the full ordered event timeline per incident — the exact material a future
  dashboard timeline needs.

## What the debugger still cannot tell us

- **Causation** — an incident still does not claim root cause or direction.
- **Anything across restarts** — history is in-memory; no persistence.
- **Why a member recovered** — only that it did.
- **Incident type classification** — occurrences are tracked, but there is no
  explicit "incident type" label or recurrence statistics yet (FUTURE).
- **Retention management** — no cap on memory yet (FUTURE).

## What I should be able to explain in an interview

1. Why isn't a diagnostic itself an incident?
2. What is the difference between a diagnostic and an event?
3. Why do we need timestamps, and why does "became high at 14:30:01 and stayed
   high 19 s" beat "CPU was high"?
4. How does the debugger know when an incident starts? (earliest member
   activation, back-filled from the group)
5. What happens when one diagnostic recovers but another remains active?
   (RECOVERING, never falsely RECOVERED)
6. Why shouldn't two incidents 40 minutes apart become one incident?
7. Why don't we store raw LiDAR/camera samples in incident history?
8. What is the difference between raw telemetry history and incident history?
9. Why does the history layer not talk to ROS directly?
10. What happens when Robot 1 and Robot 2 have simultaneous incidents?
11. What does "incident duration" actually tell us?
12. What limitations does in-memory history have compared with persistent
    storage?
