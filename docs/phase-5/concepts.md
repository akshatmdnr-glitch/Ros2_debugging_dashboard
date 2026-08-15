# Diagnostic Correlation & Root-Cause Reasoning

*Phase 5 of the ROS 2 Debugging & Observability Platform.*

Phases 1–4 tell us *what exists*, *what belongs to what*, *what is happening*,
and *what is abnormal*. Phase 5 asks the next question:

> Which abnormalities are related, and what might be contributing?

This document is about **our** implementation in `ros2_debugger/correlation.py`
and the small owner-evidence extension to the diagnostics config. It is not a
generic correlation tutorial.

---

## What problem does correlation solve?

Phase 4 judges each subject independently. For Robot 2 it can produce several
true, deterministic verdicts:

```
WARNING [high_cpu]            robot2: process 'robot2_lidar_driver' is using high CPU (98%)
WARNING [frequency_degradation] /robot2/scan: 1.20 Hz below expected 8.0 Hz
WARNING [tf_stale]            required TF frame 'base_link' is stale (3.5s)
```

These may look like four independent failures, but they can be one chain:

```
CPU pressure → LiDAR processing slows → /robot2/scan rate drops → TF becomes stale
```

The diagnostic engine has no notion of relatedness. Without Phase 5 the debugger
reports a flat list of symptoms and a human has to do the grouping by hand. The
problem Phase 5 solves: **determine, with evidence, that multiple diagnostics
may be related — without claiming a root cause.**

## Observation vs diagnostic

These are already established (Phase 3 vs Phase 4), but correlation depends on
the distinction:

```
OBSERVATION   "/robot2/scan = 1.2 Hz"
DIAGNOSTIC    "/robot2/scan is below its expected 8.0 Hz"
```

An observation is a measured fact. A diagnostic is a fact **judged against a
declared expectation**. Correlation consumes diagnostics (and their evidence),
never raw observations — it does not re-measure anything.

## Diagnostic vs hypothesis

```
DIAGNOSTIC   "/robot2/scan is degraded"               (a verdict about a subject)
HYPOTHESIS   "resource pressure may be contributing to /robot2/scan degradation"
```

A diagnostic is a single-subject verdict produced by a named rule. A hypothesis
is a **relationship claim between subjects**, phrased as possible, never proven.
The correlation engine produces hypotheses; it never produces a new diagnostic
about a subject it did not measure.

## Correlation vs causation

This is the phase's central discipline:

```
CORRELATION  "CPU high + scan degradation + stale TF on Robot 2 in a 30s window"
CAUSATION    "high CPU caused the scan to slow down"   ← NOT justified by correlation
```

Correlation means the events co-occurred in time and place (entity). It says
nothing about *direction* or *mechanism*. High CPU is a *plausible mechanism*
for a slow topic, but the reverse is just as possible: a faulty LiDAR driver
spins the CPU while making the topic slow. Our hypothesis strings are therefore
template-constrained to "may be a contributing factor" and the tests enforce
that they never contain causal vocabulary.

The five-layer stack is the contract of this project:

```
OBSERVATION  →  DIAGNOSTIC  →  CORRELATION  →  HYPOTHESIS  →  ROOT CAUSE
```

The debugger may reach CORRELATION and HYPOTHESIS. It may **never** reach ROOT
CAUSE without deterministic evidence that does not exist yet.

## Temporal correlation

Two diagnostics are temporally related when their **activation timestamps**
(`Diagnostic.timestamp`, set when the diagnostic first fired) differ by at most
`temporal_window_s` (default 30 s, configurable). This is *onset proximity*.

Why activation time and not "currently both active"? Because everything that is
currently active trivially overlaps — that would group everything and mean
nothing. Onset proximity is a cheap, deterministic, explainable signal.

Limitations of onset-only temporal correlation:

- **Slow chains are missed (false negative).** If CPU creeps up over an hour and
  the scan degrades later, onsets are far apart and no grouping forms. We
  prefer this over over-grouping.
- **A coincidental burst (e.g., boot) clusters unrelated symptoms (false
  positive).** This is why temporal correlation alone is never enough — the
  entity gate (below) is mandatory.

## Entity correlation

Two diagnostics are entity-related when they share the identical `(system,
robot)` from attribution. This is the **safety gate**: a pair is a candidate
only if the entities match **and** the onsets are within the window. Different
robots are never merged, even in the same time window.

Why attribution matters here: Phase 2 is what gives diagnostics an owner in the
first place. `/robot2/scan` is attributed to Warehouse/Robot 2 because its
publisher's namespace `/robot2` matched config. Correlation inherits that
ownership — it does not rediscover it.

## Graph/dependency correlation

The idea: "Node A publishes Topic B; if Topic B degrades, Node A is relevant
evidence." We deliberately do **not** run a full graph-endpoint pass in Phase 5.
Same-robot entity correlation already covers most of what it would find. We keep
one lean, field-only proxy: `shared_subject` — two diagnostics addressing the
same `topic`, `node`, `tf_frame`, or `process`. It requires no graph lookup and
is used to upgrade confidence. Full graph correlation (using `GraphModel`
endpoints) is recorded as CONSIDERED / FUTURE in `design.md`, not implemented.

## Resource correlation

A pair where one side is a resource rule (`high_cpu`, `high_memory`) and the
other is a behavioral rule (stale topic, frequency degradation, missing
publisher, TF). This is the **chain hypothesis** mechanism — the warehouse
example. It does two things:

1. adds the `resource` strategy to the incident, and
2. selects the resource hypothesis template ("resource pressure may be a
   contributing factor") instead of the generic one.

It never asserts that CPU *caused* the degradation — only that the two are
plausibly connected through resource pressure.

## Why attribution matters

Everything safe about this design flows from attribution:

- Diagnostics on the same robot can be related (entity gate passes).
- Diagnostics on different robots stay separate (entity gate blocks) — even
  when both degrade at the same time.
- A diagnostic with **no owner** is never silently placed. It is reported as
  uncorrelated with the explicit reason "owner unknown; not grouped to avoid
  guessing", or — if it links to another ownerless diagnostic by subject — it
  forms a LOW-confidence, `attribution_uncertain` incident.

This is the "configuration is the truth" principle of Phase 2 applied to
correlation: we would rather report "I cannot place this" than guess.

## Why false correlations are dangerous

A debugger that merges unrelated robots over-groups and produces a single
alarming "incident" that is really several unrelated problems. The operator
starts chasing a shared cause that does not exist (e.g., blaming the machine
for two independent robot faults). Over-grouping also hides real signal: if
Robot 1 and Robot 2 are lumped together, you cannot tell that only Robot 1 is
actually sick.

Our design trades false positives for false negatives deliberately:

- different entities are never grouped;
- ownerless diagnostics are not absorbed into owned incidents;
- an incident needs at least `min_members` (2) diagnostics.

A genuinely shared cause — a global simulation slowdown affecting every robot —
is **out of Phase 5 scope**. Detecting "one system-wide event" is a different
mechanism and is recorded as FUTURE.

## What is an incident?

An `Incident` is a group of ≥ 2 related diagnostics with:

- `members` — the diagnostics themselves (their evidence travels with them),
- `strategies` — which signals linked them (`entity`, `temporal`, `resource`,
  `shared_subject`),
- `confidence` — qualitative LOW/MEDIUM/HIGH,
- `hypothesis` — a cautious relationship statement,
- `evidence` — a plain list of what the debugger actually saw,
- `system`/`robot` — the shared owner (or None for ownerless incidents),
- `attribution_uncertain` — true when an ownerless linkage formed,
- `state` — ACTIVE / RESOLVED, plus creation/update/resolution timestamps.

An incident **differs from a diagnostic**: a diagnostic is a single-subject
verdict ("X is abnormal"); an incident is a multi-subject grouping ("these may
be related") that makes no causal claim.

Identity is the sorted tuple of member diagnostic keys. When membership changes,
the old grouping no longer holds, so the old incident RESOLVES and a new one
forms. This makes **recovery automatic**: when a member diagnostic resolves, the
incident's membership shrinks; below `min_members` the incident resolves. The
engine reports both ACTIVE and RESOLVED incidents as events.

Future dashboard/history phases can consume incidents directly: an incident is
exactly the unit a UI should render as a collapsible group, and what history
should record over time. That is FUTURE — incidents exist now, the dashboard
does not.

## What is confidence?

A qualitative estimate of how much evidence supports the grouping:

| Condition | Confidence |
|---|---|
| any member lacks an owner (ownerless linkage) | LOW, `attribution_uncertain=True` |
| same entity + temporal co-occurrence | MEDIUM |
| same entity + temporal + a mechanism signal (`resource` or `shared_subject`) | HIGH |

Why qualitative and not a numeric score? There is no statistical basis for a
precise 0.73 confidence. A fabricated number would imply a rigor we do not
have. Qualitative confidence is honest about what it means: "this is strongly
supported" vs "this is barely more than coincidence".

## What can our debugger conclude?

Given the Robot 2 chain, the debugger produces exactly this:

```
[incident] ACTIVE HIGH warehouse/robot2 signals=entity,resource,temporal
  On warehouse/robot2, resource conditions (robot2_lidar_driver) and
  behavioral degradation (/robot2/scan, base_link) co-occurred in a related
  time window. Resource pressure may be a contributing factor.
  Correlation is not causation; root cause is not determined.
```

That is: the three diagnostics are related (entity + temporal), a plausible
mechanism exists (resource), the confidence is HIGH — and the system explicitly
says it is not claiming a cause.

## What can it NOT conclude?

- **Root cause.** It never says "the LiDAR driver is broken."
- **Direction.** It never says "CPU caused the scan drop" — the reverse is
  possible.
- **Shared global causes.** Robot 1 and Robot 2 degrading together is not
  reported as one event.
- **Long, slow chains.** Onsets far outside the window are not grouped.
- **Anything about unattributed subjects.** Diagnostics without owners are not
  placed (they are reported as uncorrelated with a reason).

## What we implemented

**`ros2_debugger/correlation.py`** (new, pure Python, no rclpy):

- `Confidence` (LOW/MEDIUM/HIGH), `IncidentState` (ACTIVE/RESOLVED)
- `CorrelationConfig` — `temporal_window_s`, `min_members`
- `Incident` — the group model described above
- `CorrelationEngine` — `update(active, now)` recomputes incidents each cycle
  from `DiagnosticEngine.active`; returns ACTIVE/RESOLVED events. Pairing gate:
  temporal **and** (entity, or ownerless-with-subject/resource link). Clusters
  via connected components; `uncorrelated` reports active diagnostics that did
  not join any incident, with the reason.

**Owner evidence extension** (the missing information, made data):

- `TelemetryConfig.process_owners` — a process pattern may optionally declare
  `system`/`robot`; `rule_resource_overload` attaches it to `high_cpu` /
  `high_memory` diagnostics.
- `RequiredTfFrame` — a required TF frame may be a plain name or `{frame,
  system, robot}`; `rule_tf_required` attaches the owner to `tf_stale` /
  `tf_missing` diagnostics.

Without this extension, CPU and TF diagnostics carried no owner and could never
be entity-correlated to Robot 2 — the flagship example would not work.

**`ros2_debugger/debugger.py`** — loads the `correlation:` config, wires
`CorrelationEngine` after the diagnostic evaluation, prints incidents live
(`[incident] ...`) and a final correlation summary (active incidents, hypotheses,
and uncorrelated diagnostics with reasons).

**`ros2_debugger/config/attribution.yaml`** — the `correlation:` section, owners
on the demo process pattern and the required TF frames.

**`test/test_correlation.py`** — 14 tests covering all eight required scenarios.

## Alternatives considered

- **Merge correlation into `diagnostics.py`.** Rejected: Phase 4 owns the
  per-subject verdict lifecycle; correlation is a separate judgment over groups.
  Mixing them couples a single diagnostic's lifecycle to group membership.
- **Run a full graph-endpoint correlation pass.** Deferred (CONSIDERED/FUTURE):
  same-robot entity correlation covers most of its value; `shared_subject` is a
  zero-cost proxy. The prompt's rule — smallest useful design — applied.
- **Numeric confidence scores.** Rejected: no statistical basis; qualitative
  confidence is honest.
- **Group cross-robot co-occurrences as a "global event".** Rejected for Phase 5
  (FUTURE): too easy to over-group; prefers false negatives.
- **Infer process→robot ownership automatically.** Rejected: inference is
  guessing; config is the truth (Phase 2 principle). The owner is deployment
  data.

## What could go wrong?

- **The entity gate is too strict** → real cross-robot chains (shared host) are
  missed. Accepted as a Phase 5 limitation, documented as FUTURE.
- **Onset-only temporal** → slow chains missed; coincidental bursts over-grouped
  (mitigated by the entity gate). Accepted.
- **Owners misconfigured** → a process/frame wrongly attributed to a robot
  creates a false grouping. Config is data; the debugger trusts it exactly as it
  trusts node attribution.
- **Hypothesis wording regresses** → a future edit could make hypotheses sound
  causal. Guarded by `test_hypotheses_never_claim_root_cause`.
- **Incident identity churn** → any membership change creates a new incident
  (old one resolves). Simple and honest, but chatty; a future incident-historian
  phase could merge or version them.

## What I should be able to explain in an interview

1. Why isn't a list of independent diagnostics enough? Give the Robot 2 chain
   and the four-layer problem it illustrates.
2. State the five layers (observation, diagnostic, correlation, hypothesis, root
   cause) and which ones the debugger is allowed to produce.
3. Why doesn't "CPU = 98%" prove CPU caused the scan degradation? Give a reverse
   mechanism (faulty driver spins CPU).
4. What is the pairing gate, and why is the entity match mandatory?
5. Why are two degrading robots not merged even in the same window?
6. What does onset-proximity temporal correlation miss, and why do we accept it?
7. What is an incident, and how does it differ from a diagnostic?
8. Why is confidence qualitative instead of numeric?
9. Why was owner evidence needed on CPU/TF diagnostics, and where does it come
   from?
10. How does recovery work for incidents?
11. What happens to a diagnostic with no owner — how does the debugger
    communicate that uncertainty?
12. What is recorded as CONSIDERED/FUTURE, and why?
