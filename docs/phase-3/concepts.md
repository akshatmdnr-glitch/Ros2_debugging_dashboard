# ROS 2 Runtime Telemetry & Observability

*Phase 3 of the ROS 2 Debugging & Observability Platform.*

> Observability is the step between "it exists" and "it works."

Phase 1 proved the graph exists. Phase 2 proved who owns what. Phase 3
measures what is *actually happening at runtime* — the evidence that later
phases will judge. This document is about our implementation, not a generic
monitoring tutorial.

---

## 1. What problem does runtime telemetry solve?

Graph information tells us only that something *exists*:

```
/robot2/scan exists
```

Telemetry tells us what is *happening*:

```
/robot2/scan is publishing at approximately 9.8 Hz
```

The graph never changes when a publisher silently stalls. A node can remain on
the graph while its topic stops flowing. Without telemetry the debugger cannot
even *ask* "is Robot 2 still publishing?", let alone answer it.

Warehouse example: `/robot2/scan` is discovered (Phase 1) and attributed to
Robot 2 (Phase 2), but the debugger still cannot say whether the LiDAR is
working. "It exists" and "it is publishing" are different facts that fail
independently. Telemetry is what turns the graph into a live system.

## 2. What is runtime telemetry?

In this debugger, runtime telemetry is the **live measurement of observable
behavior**, produced in `ros2_debugger/telemetry.py`:

- per-topic: message count, current rate (Hz), last-message time, idle time,
  whether the subscription ever delivered, and the publisher's declared QoS
- per-process: liveness, PIDs, CPU %, RSS (from `/proc`)
- per-TF-frame: message count, latest timestamp, last-seen time

It is deliberately **unopinionated**: it records measurements; it does not
judge them. Judgment belongs to the diagnostic phase.

## 3. What is observability?

Observability is the ability to answer questions about a system's internal
state from its external outputs. For our debugger: given the messages, the
graph, the processes, and the transforms a running ROS 2 system produces, can
we tell what it is doing? Observability here is achieved by combining *what
exists* (graph) with *what is happening* (telemetry) into a coherent view.

The debugger itself is an observability tool: it observes the warehouse by
participating in the same ROS 2 environment and measuring what flows through
it.

## 4. Graph vs telemetry

| | Graph | Telemetry |
|---|---|---|
| Question | "What exists?" | "What is happening?" |
| Changes | on discovery events | continuously |
| Example | `/robot2/scan` exists, type `LaserScan`, 1 publisher, RELIABLE/VOLATILE | `/robot2/scan` receiving ~9.8 Hz, last message 0.1 s ago |
| Failure it misses | silent publisher stall | node not present at all |

Both are needed: the graph gives telemetry its **subject** (which topic, whose
node), and telemetry gives the graph its **behavior** (is it actually
working?). A debugger with only the graph cannot see degradation; a debugger
with only telemetry cannot say who is degrading.

## 5. Topic telemetry

The metrics we actually support (`TopicStats` in `telemetry.py`):

- **message activity** — `message_count` (total received while monitored) and
  `receiving` (whether any message was ever delivered)
- **frequency** — `rate_hz`, computed over each 1-second sample window
  (only recomputed over windows ≥ 0.25 s, to avoid reporting a false drop from
  a degenerate sub-window)
- **last message time** — `last_message_time` (monotonic) and derived
  `idle_seconds`
- **stale behavior** — `idle_seconds` grows as no message arrives; whether
  that is "abnormal" is left to the diagnostic phase
- **message size/rate** — not measured. Computing payload size would require
  serializing every message, which is the kind of overhead we deliberately
  avoid. Rate and count answer the current questions.

## 6. Why don't we subscribe to everything?

The naive approach — subscribe to every discovered topic — fails for
engineering reasons:

- **CPU** — every message is deserialized and dispatched to a callback; many
  topics × high rates = real CPU in the debugger's own process
- **memory** — subscription buffers and per-topic state accumulate
- **bandwidth** — subscribing copies data over DDS to our participant
- **LiDAR / camera / point clouds** — exactly the large-message topics that
  are expensive to subscribe to and the least necessary to merely count
- **high-frequency topics** — a 200 Hz control stream is cheap to count but
  only if we ignore the payload
- **unnecessary payload processing** — we need metadata (arrival, count,
  time), not contents
- **QoS compatibility** — one fixed profile cannot match every publisher

Our approach (**selective observation**): we monitor topics that are
*attributed to a system of interest* (Phase 2 gives this for free) plus an
explicit allowlist. Everything else is skipped, and the telemetry summary
records *why* each topic was skipped ("infrastructure", "unattributed",
"shared across owners", "system not in monitor scope"). Callbacks discard
payloads (counters only), and rates are sampled on a fixed 1-second cadence.

## 7. QoS

QoS (Quality of Service) is the publication/subscription contract at the DDS
wire level: reliability, durability, history depth, and more.

Why it matters to an observer: DDS only delivers when subscriber and publisher
QoS are **compatible**. We are a passive observer joining other people's
contracts — we must match what is there, not demand our own.

How an incompatible observer falsely appears to see "no data": a **RELIABLE
subscriber cannot match a BEST_EFFORT publisher**. The match never forms — no
error, no messages — while the topic still *looks present on the graph*. A
debugger that subscribed to everything with one profile would silently see
nothing from sensor streams and would appear to show a dead system.

Our strategy (implemented in `CollectorNode.ensure_topic_subscription`):
monitoring subscriptions use **BEST_EFFORT reliability + VOLATILE durability**
everywhere. This is maximally compatible — BEST_EFFORT matches both
BEST_EFFORT and RELIABLE publishers, VOLATILE matches both VOLATILE and
TRANSIENT_LOCAL — and it never imposes retries or latched-history buffering on
the observed system. We also display each publisher's declared QoS next to our
observed rate, so the observer's contract is visible.

We verified the failure mode directly: a RELIABLE probe against a BEST_EFFORT
publisher received 0 messages (ROS itself warned "offering incompatible
QoS"), while our BEST_EFFORT observer received the same topic fine.

## 8. Process health vs ROS node existence

"Node exists" (discovery) and "process is healthy" (OS) are different facts:

- a node can be **present on the graph** while its process thrashes at high
  CPU or leaks memory — DDS does not report resources
- a process can be **alive and healthy** while its node is evicted from the
  graph (name collision, discovery hiccup)

Why process metrics required OS-level information: DDS/RMW carries no OS
resource data by design — it is network middleware. CPU and memory only exist
in the operating system's own accounting (`/proc`).

So process health is **out-of-band**: `ProcessMonitor` reads
`/proc/<pid>/stat` (CPU ticks, starttime) and `/proc/<pid>/status` (`VmRSS`)
for processes matched by command-line pattern. It never pretends to be ROS
data — it is a separate, sampled plane.

## 9. CPU and memory telemetry

What we measure (per configured process pattern): liveness, PIDs, aggregate
CPU % (delta of `utime+stime` over delta of wall time, in clock ticks), and
max RSS in MB. PID reuse is guarded using the process starttime.

Why it matters: a node can be alive on the graph while its process is
resource-starved, which is exactly the kind of context that later explains
slow topics.

What it can tell us: whether a process is running, roughly how much CPU/RSS it
uses, and when it stops.

What it cannot tell us: *why* CPU is high, whether high CPU is the cause or
effect of another problem, or anything about processes we are not configured
to watch. CPU is an observation, not a diagnosis.

## 10. TF monitoring

Why TF matters in robotics: transforms define the spatial relationships
between frames (`map`, `odom`, `base_link`); robots are steered and localized
through them. A stale `map→odom` is a classic, high-signal symptom.

What our debugger observes: the collector subscribes to `/tf` and `/tf_static`
(as ordinary topics) and streams each transform to `TfStats`, which records
per-frame (`TfStats.record`): message count, latest timestamp, and last-seen
time. Both the parent (`header.frame_id`) and child (`child_frame_id`) frame
ids are recorded, so a required-frame configuration can name either side.

Why it is useful for future diagnostics: freshness facts ("`odom` last updated
3 s ago") become evidence for a TF-staleness rule in the diagnostic phase.
TF monitoring here records facts; whether 3 s is abnormal is a later decision.

## 11. Logs vs telemetry

Logs and telemetry are different *kinds* of evidence:

```
Telemetry:  "/scan frequency = 1 Hz"
Log:        "sensor connection lost"
```

- logs are **qualitative, self-reported events** from the node itself
- telemetry is **quantitative, independently-measured facts** about behavior

They are cross-checks, not substitutes. A node can log "all good" while its
publish rate silently halves — only telemetry exposes that mismatch. A node
that *crashes* (logs stop) and a node that *stalls* (logs continue) look
identical in the log stream but differ in telemetry. We already observe
`/rosout` (each node publishes its logs there); Phase 3 does not duplicate
that — it keeps both streams as separate evidence.

## 12. Timestamps and stale data

Timestamps are what make "no message" meaningful. There is a difference
between:

```
no message            (a state)
no message for 10s    (a state with a duration)
```

Our `TopicStats` records `last_message_time` (monotonic) and derives
`idle_seconds`. When a publisher stops, `last_message_time` freezes while
`idle_seconds` grows. That duration is the raw material for a stale-topic
diagnostic — but whether 10 s is abnormal still requires an expectation, which
is the diagnostic phase's job.

Timestamps are also why we never compute a rate over a degenerate sub-window:
the sample right after observation stops would see no message processing and
report a false rate of 0.

## 13. Why the debugger must monitor itself

"Who monitors the monitor?" — a monitoring system that disturbs what it
measures is a bad monitor. Our overhead budget:

- **CPU** — bounded by selective subscription (few attributed topics), O(1)
  per-message counters, and 1-second sampling
- **memory** — per-topic counters only; no payload retention
- **bandwidth** — only copied for the topics we actually subscribe to
- **high-frequency topics** — counted but payloads discarded
- **sampling** — rates on a fixed cadence, never per-message work beyond a
  counter
- **payload handling** — we deliberately do not serialize payloads (no message
  size measurement) because that cost is the bulk of observer overhead

The residual unavoidable cost is rclpy's mandatory deserialization of each
received message — which is precisely why selective subscription matters more
than clever counting. We even observe our own process in the process monitor
(when configured), so the tool's footprint is visible.

## 14. Why telemetry is evidence, not diagnosis

This distinction is critical:

```
Observation:  "/scan = 1 Hz"
Diagnosis:    "LiDAR is broken"   ← NOT automatically justified
```

The observation is true; the diagnosis assumes a cause. The actual cause of
1 Hz could be:

- CPU overload
- a sensor fault
- a driver bug
- a QoS issue
- a network problem
- a simulation slowdown
- an intentional rate change

Phase 3 reports *what the evidence supports* — the rate, the idle time, the
liveness. It never invents certainty. Deciding whether 1 Hz is abnormal
(needs an expectation) and *why* (needs root-cause analysis) are later phases.

## 15. Why GraphSnapshot and TopicStats remain separate

We deliberately keep the graph model and telemetry model apart rather than
merging them into one giant object:

- **Ownership & cadence** — the graph is event-driven (changes on discovery);
  telemetry is sampled continuously. One update mechanism cannot serve both.
- **Staleness semantics** — a topic that *exists* on the graph can be *stale*
  in telemetry. Folding them into one object forces a single "current"
  answer and hides the fact that existence and activity diverged.
- **Consumers** — diagnosis wants "what happened to this robot over time";
  the raw graph wants "what exists now". Different queries, different indexes.
- **Memory** — telemetry accumulates counters/timestamps; the graph holds
  structure. Letting the graph grow with telemetry would bloat it.

So `GraphModel` and `TelemetryModel` are **siblings**, cross-referenced but
never merged. Telemetry consumes attribution decisions but never mutates graph
or attribution state.

## 16. What we actually implemented

**`ros2_debugger/telemetry.py`** — the analysis-layer home for runtime
measurement (no rclpy imports):

- `TelemetryConfig` — monitor scope (`monitor_systems`, `monitor_topics`,
  `processes`)
- `TopicStats`, `ProcessStats`, `FrameStats` — the measurements
- `TfStats` — per-frame transform freshness (`record`, `frames`)
- `TopicMonitor` — decides what to watch and keeps per-topic stats:
  - `reconcile(graph, system_model, collector, now)` — builds the desired
    topic set from attribution, subscribes via the collector, drops topics
    that leave, records why others are skipped, samples rates
  - `sample(now)` — recomputes rates (only over ≥ 0.25 s windows) and idle
    times
- `ProcessMonitor` — `/proc` sampling: command-line matching, CPU ticks,
  `VmRSS`, starttime-guarded PID reuse detection
- `TelemetryModel` — aggregates topic/process/TF telemetry and drives one
  reconcile per cycle

**`ros2_debugger/collector.py`** (modified in Phase 3) — the ROS-facing side:

- `ensure_topic_subscription` — creates BEST_EFFORT/VOLATILE monitoring
  subscriptions on demand (maximally compatible observer QoS)
- `drop_topic_subscription` — removes them when a topic leaves the scope
- `_resolve_message_class` — resolves `std_msgs/msg/String` → the message
  class for subscription creation
- `post_refresh_handlers` — hook invoked after each graph refresh so
  telemetry (and later diagnostics) run once per cycle
- `_on_tf` — streams per-transform parent/child frame ids to `TfStats`

**`ros2_debugger/debugger.py`** (modified) — wiring: loads the telemetry
config, builds `TelemetryModel`, registers a post-refresh handler that
reconciles each cycle, and prints a telemetry summary (`_telemetry_summary`)
and periodic live lines (`_print_telemetry_live`); `--process` adds process
patterns.

**`ros2_debugger/config/attribution.yaml`** (modified) — the `telemetry:`
section declares monitor scope and process patterns.

**`ros2_debugger/attribution.py`** (modified during Phase 3) — added the
`PENDING` state and `Attributor.attribute_topic_name`: when rclpy reports an
endpoint node as `_NODE_NAME_UNKNOWN_` (an rmw discovery race), the topic is
not "unowned" — it is "not known yet". We retry each cycle and, if needed,
fall back to a low-confidence topic-name convention so telemetry can still
watch the topic.

**`test/test_telemetry.py`** — decision logic, rates, staleness,
unsubscribe-on-disappearance, `/proc` liveness, TF freshness, config parsing,
pending-endpoint recovery.

## 17. What Phase 3 can and cannot tell us

**CAN:**

- observe runtime behavior (rates, counts, last-message, idle time)
- measure telemetry against what is on the graph
- detect stale activity (idle time growing)
- observe resource metrics where supported (configured processes via `/proc`)
- observe TF/runtime evidence (per-frame freshness)
- collect logs (via `/rosout`)

**CANNOT:**

- determine whether behavior is abnormal without expectations
- determine root cause
- know whether 1 Hz is healthy without context
- automatically say "LiDAR is broken"

This is the boundary between measurement and judgment. Phase 3 provides the
evidence; the diagnostic phase will interpret it.

## 18. What I should be able to explain in an interview

1. Why isn't the graph enough to debug a running system? Give a warehouse
   example of a failure the graph cannot show.
2. What is the difference between graph information and runtime telemetry,
   and why do we need both?
3. Why shouldn't we subscribe to every topic? Name the costs and our
   selection rule.
4. What problem does QoS create for an observer, and why did we choose
   BEST_EFFORT/VOLATILE rather than mirroring each publisher?
5. What happens when a RELIABLE observer meets a BEST_EFFORT publisher, and
   how would that look to a naive debugger?
6. Why is "node exists" different from "process is healthy", and why did we
   go to `/proc`?
7. What CPU/memory measurements do we make, and what can they and can't they
   tell us?
8. Why do we need timestamps, and why is "no message" different from "no
   message for 10 seconds"?
9. How does the debugger monitor itself, and which design choices bound its
   overhead?
10. Why is telemetry evidence rather than a diagnosis? List plausible causes
    for "/scan = 1 Hz".
11. Why are logs and telemetry different evidence sources?
12. Why is `GraphModel` kept separate from `TelemetryModel`? What breaks if
    they are merged?
13. What is `PENDING` / `_NODE_NAME_UNKNOWN_`, and why did it force us to
    retry instead of silently dropping a topic?
14. What can Phase 3 tell us, and what must it refuse to claim?
