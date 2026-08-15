# Rule-Based Diagnostics: From Evidence to Verdict

*Phase 4 of the ROS 2 Debugging & Observability Platform.*

Phases 1–3 tell us *what exists*, *what belongs to what*, and *what is
happening*. Phase 4 answers the judgment question:

> Is something abnormal?

This document describes **our** implementation in
`ros2_debugger/diagnostics.py` — how telemetry becomes a verdict, why the
verdicts are deterministic, and why the engine still refuses to claim a cause.

---

## 1. What problem does the diagnostic engine solve?

Phase 3 measures facts:

```
/robot2/scan = 1.2 Hz
```

That number is not a problem — until someone declares it abnormal. A debugger
that prints every number would drown the operator in data. The diagnostic
engine solves:

> How does the debugger decide that 1.2 Hz is wrong, and how does it say so
> with evidence and without guessing?

Warehouse example: `/robot2/scan` is attributed to Robot 2 (Phase 2) and
measured at 1.2 Hz (Phase 3). Whether that is abnormal depends on the expected
rate for a LiDAR. The engine cannot *know* LiDAR runs at 10 Hz — so it requires
an explicit expectation, declared as data, and judges the measurement against
it.

What happens without this phase: telemetry is a stream of numbers with no
meaning, and the debugger cannot answer even "is Robot 2 healthy?"

## 2. Observation vs diagnostic — the core distinction

```
OBSERVATION   "/robot2/scan = 1.2 Hz"        (Phase 3: a measured fact)
DIAGNOSTIC    "/robot2/scan is below its expected 8.0 Hz"  (Phase 4: a judgment)
```

- An observation is **true by measurement**.
- A diagnostic is **true by judgment** — it needs an expectation to exist.

This is why telemetry never calls anything abnormal: the moment it did, it
would be inventing the expectation. Phase 4 is the first layer allowed to judge.

The phase also plants the boundary the rest of the project respects:

```
OBSERVATION → DIAGNOSTIC → CORRELATION → HYPOTHESIS → ROOT CAUSE
```

Phase 4 produces DIAGNOSTICs. It does **not** produce hypotheses or root
causes — the message of every diagnostic describes what is abnormal, never why.

## 3. Why deterministic rules, not ML/AI

The engine is a set of named rules, each producing diagnostics with evidence:

```python
RULES = [
    rule_stale_topic,
    rule_frequency_degradation,
    rule_topic_no_publisher,
    rule_node_disappeared,
    rule_tf_required,
    rule_resource_overload,
]
```

Why deterministic? Every verdict must be **explainable**: given the same
observations, the engine produces the same diagnostics. A probabilistic model
would produce different answers run-to-run and could not point at the exact
evidence behind a verdict. For a debugging tool, the audit trail matters more
than pattern-matching cleverness. AI/ML are recorded as future work, not used
here.

## 4. Expectations are data

`DiagnosticConfig` (parsed from `config/attribution.yaml`) declares what
"healthy" means:

```yaml
diagnostics:
  stale_after_s_default: 5.0
  topic_expectations:
    "/robot1/chatter": {min_hz: 0.5, stale_after_s: 3.0}
  required_tf_frames: ["odom", "base_link"]
  tf_stale_after_s: 3.0
  absence_grace_cycles: 3
  process_thresholds:
    cpu_warn_percent: 80.0
    mem_warn_mb: 1024.0
```

Two consequences:

- **"No expectation = no judgment."** A topic with no declared `min_hz` is
  never judged for frequency. The engine refuses to invent a "normal" rate —
  that is the Phase 2 principle ("configuration is the truth") applied to
  expectations.
- **The debugger stays generic.** Only this YAML knows the warehouse; the code
  contains no warehouse-specific threshold.

## 5. The Diagnostic model

Every verdict is a frozen `Diagnostic` with:

- `rule_id` — which rule produced it,
- `severity` (INFO / WARNING / ERROR),
- `message` — a human sentence about the subject,
- `evidence` — a tuple of plain strings (the measured values, the threshold),
- `timestamp` — when it became active,
- owner/subject fields — `system`, `robot`, `node`, `topic`, `tf_frame`,
  `process` (these come from attribution and make later correlation possible),
- a `key` — the rule_id + subject fields, i.e. *identity*, used for dedupe and
  recovery.

`subject` is a single string ("the topic, or node, or frame, or process") so a
summary line can point at the thing that is wrong.

## 6. The six rules

Each rule answers one question and does not overlap the others:

| Rule | Question | Verdict |
|---|---|---|
| `stale_topic` | "A topic that once delivered went quiet?" | `stale_topic` (idle ≥ threshold) |
| `frequency_degradation` | "Still delivering, but too slow?" | `frequency_degradation` (rate < min_hz, and not already stale) |
| `topic_no_publisher` | "Expected topic, no messages ever?" | `missing_publisher` (0 publishers on graph) or `not_receiving` (publishers exist, nothing arrives) |
| `node_disappeared` | "A node we knew left the graph?" | `node_disappeared` (structural change, strong evidence) |
| `tf_required` | "Required TF frame still fresh?" | `tf_missing` (never seen) or `tf_stale` (last transform too old) |
| `resource_overload` | "Process using too much CPU/RSS?" | `high_cpu` / `high_memory` |

Design notes on the tricky ones:

- **`frequency_degradation` skips topics already covered by `stale_topic`.** A
  stopped topic (idle ≥ stale threshold) is judged as stale, not as "0 Hz" —
  those are different conditions and one rule must not double-report the other.
- **`topic_no_publisher` distinguishes "no publisher" from "not receiving".**
  If publishers exist but no message arrives, a QoS mismatch is *one possible
  explanation* — the evidence literally says "possible causes: QoS mismatch,
  silent publisher, discovery lag". The rule does not pick one. That is the
  discipline of the whole phase: state the facts, refuse the verdict beyond
  them.
- **`node_disappeared` is structural, not sampled.** It keeps a record
  (`_ever_seen`) of attributed nodes it has observed, so a node leaving the
  graph keeps the diagnostic ACTIVE until it returns. It cannot be "fixed" by a
  sample window — it resolves only on reappearance.
- **`resource_overload`** is explicitly annotated as "a relevant observation;
  explicitly NOT a root-cause claim about anything else". High CPU is a fact,
  not an accusation.

## 7. The ACTIVE/RESOLVED lifecycle — recovery is first class

`DiagnosticEngine.evaluate(graph, system_model, telemetry, now)` runs every
rule, then:

1. previously-ACTIVE diagnostics that no longer fire → **RESOLVED**, emitted as
   an event, moved to `history`;
2. newly-fired diagnostics → **ACTIVE**, emitted once (deduped by `key`).

So the engine **remembers what was wrong and forgets it when it stops being
wrong**. Without recovery, a debugger would keep reporting a fault that was
already fixed — a "diagnosis of a ghost". The dashboard can later show
ACTIVE/RESOLVED as a timeline, but even now the summary distinguishes them.

## 8. Grace periods

The first cycles after startup are noisy: topics may not have delivered yet,
nodes may not be discovered, TF frames may not have appeared. Rules that judge
absence therefore require grace:

- `absence_grace_cycles` (default 3) gates `missing_publisher` and `tf_missing`;
- `missing_publisher` grace is **per-topic** (`monitored_cycles`), so a topic
  discovered mid-session gets the same startup grace as one present from the
  start;
- `frequency_degradation` also respects the stale threshold, so it does not fire
  during a genuine startup stall that `stale_topic` should own.

Without grace, the debugger would alarm on every healthy system's first
seconds — a false positive the operator learns to ignore, which trains them to
ignore real alarms.

## 9. Evidence: the audit trail

Every diagnostic carries `evidence` — the actual numbers behind the verdict:

```
/robot1/scan frequency 1.20 Hz is below the expected minimum 8.0 Hz
  evidence: observed=1.20Hz
  evidence: expected_min=8.0Hz
  evidence: idle=0.40s
```

This is what makes the engine explainable: a user can check the verdict against
the measurement. It is also the raw material Phase 5 uses to correlate — an
incident's evidence is built from its members' evidence.

## 10. What Phase 4 can and cannot conclude

**CAN:**

- judge a subject against a declared expectation;
- report stale topics, degraded frequencies, missing publishers, silent
  publishers, disappeared nodes, missing/stale TF frames, and CPU/memory
  overload;
- distinguish ACTIVE from RESOLVED and recover when conditions clear;
- attach owner (system/robot) so the verdict is attributable.

**CANNOT:**

- claim a *cause*. "Below expected frequency" never says "the LiDAR is broken";
- judge a subject with no declared expectation;
- explain why a QoS mismatch happens;
- combine two diagnostics into a relationship (that is Phase 5);
- infer root cause (no phase does this yet).

## 11. Alternatives considered

- **ML/probabilistic diagnosis** — rejected for Phase 4: non-explainable,
  non-deterministic, no labeled data, and unnecessary for expectation-based
  judgment. Recorded as future work.
- **Hard-coded thresholds in code** — rejected: makes the debugger
  warehouse-specific and re-deployable per site. Expectations live in config.
- **Rules in the collector** — rejected: couples ROS I/O to policy and makes
  the judgment untestable without ROS. The engine is a consumer-side, ROS-free
  layer (the same boundary principle as every phase).
- **A single giant "is anything wrong" rule** — rejected: six focused rules are
  each explainable, independently testable, and deliberately non-overlapping.

## 12. What we implemented

**`ros2_debugger/diagnostics.py`** — the engine, no rclpy imports:

- `Severity`, `DiagnosticState`, `Diagnostic`, `TopicExpectation`,
  `DiagnosticConfig`, `RequiredTfFrame` (Phase 5 addition)
- six rule functions + the `RULES` registry
- `DiagnosticEngine` — `evaluate()` (run rules + diff the ACTIVE set),
  `active` / `resolved` properties, `history`, `evaluation_count`

**`ros2_debugger/config/attribution.yaml`** — the `diagnostics:` section:
expectations, required TF frames, grace, process thresholds.

**`ros2_debugger/debugger.py`** — wires the engine after telemetry in the
post-refresh cycle, prints diagnostics live and in the final summary
(`_print_diagnostic`, `_diagnostics_summary`).

**`test/test_diagnostics.py`** — healthy negative, stale + recovery, frequency
degradation, no-expectation-no-judgment, missing publisher after grace, not
receiving, node disappearance + recovery, TF missing→stale→recover, CPU
healthy/overload, and dedupe of repeated fires.

## 13. What I should be able to explain in an interview

1. What is the difference between an observation and a diagnostic? Warehouse
   example.
2. Why deterministic rules instead of ML/AI?
3. Why are expectations data, and what happens to a topic with no expectation?
4. What is the ACTIVE/RESOLVED lifecycle and why does recovery matter?
5. What is the `key`, and why is identity subject-scoped per rule?
6. Why does `frequency_degradation` skip topics that are already stale?
7. What is the difference between `missing_publisher` and `not_receiving`, and
   why is a QoS mismatch not a verdict?
8. Why do grace periods exist, and what goes wrong without them? Why is
   missing-publisher grace per-topic?
9. Why does `node_disappeared` stay ACTIVE until the node returns?
10. What is in a diagnostic's `evidence`, and why does every diagnostic carry
    it?
11. What can Phase 4 conclude, and what must it refuse to claim?
12. Why is the engine ROS-free, and how does that make it testable?
