# Project Interview Questions (Deferred Teach-Back)

*Living bank of teach-back interview questions, collected per phase and used
for the final project interview when the project is complete.*

The teach-back for each phase is deferred to the end of the project. When a
phase finishes, its questions are recorded here (plus any corrections to
answers given during development). The final interview will draw from this
bank plus the "What I should be able to explain in an interview" lists in each
`docs/phase-N/concepts.md`.

---

## Phase 4 — Diagnostic Engine

1. What is the difference between an observation and a diagnostic? Use the
   Robot 2 `/scan` example (measured 1.2 Hz vs judged against an expected rate).
2. Why deterministic rules instead of ML/AI for diagnosis?
3. Why are expectations data (config), and what happens to a topic that has no
   declared expectation?
4. What is the ACTIVE/RESOLVED lifecycle, and why is recovery a first-class
   property?
5. What is the diagnostic `key`, and why is identity subject-scoped per rule?
6. Why does `frequency_degradation` deliberately skip topics that are already
   stale?
7. What is the difference between `missing_publisher` and `not_receiving`, and
   why is "QoS mismatch" listed as a possible cause rather than a verdict?
8. Why do grace periods exist, and why is missing-publisher grace per-topic
   rather than global?
9. Why does `node_disappeared` stay ACTIVE until the node returns to the graph?
10. What does a diagnostic's `evidence` contain, and why does every diagnostic
    carry it?
11. What can Phase 4 conclude, and what must it refuse to claim (causation,
    expectations for unconfigured subjects)?
12. Why is the diagnostic engine ROS-free, and how does that make it testable?

---

## Phase 5 — Diagnostic Correlation

1. Why aren't independent diagnostics enough? Use the Robot 2 chain (CPU high →
   `/robot2/scan` degraded → TF stale → navigation affected).
2. Lay out observation → diagnostic → correlation → hypothesis → root cause,
   and say which ones the debugger is *allowed* to produce.
3. Why doesn't "CPU = 98%" prove CPU caused the scan degradation? Give a
   plausible reverse mechanism (e.g., a faulty LiDAR driver spinning the CPU).
4. Why is attribution the backbone of correlation — what exactly does it enable
   and what does it block?
5. What two conditions make two diagnostics candidates for correlation, and why
   is each mandatory? (temporal onset window AND entity match)
6. When can temporal correlation mislead, and what did we do about it?
   (slow chains with distant onsets; coincidental bursts — mitigated by the
   entity gate)
7. Why aren't two robots merged even if they degrade in the same time window?
   (false negatives over false positives; shared global causes are FUTURE)
8. How does the debugger communicate uncertainty about a diagnostic with no
   owner? (`engine.uncorrelated` with a reason; never guessed into an incident;
   ownerless-only linkages are LOW + `attribution_uncertain`)
9. Why is "resource pressure *may be* a contributing factor" safer than
   "resource pressure *caused* the degradation"? What would change in the code
   to say the latter? (template-constrained hypotheses; removing the denial
   would require deterministic evidence we do not have)
10. What information flows into correlation from Phase 3 (telemetry evidence),
    and what from Phase 4 (diagnostics with rule_id/timestamp/owner/subject)?
11. What is an incident, and how is it different from a diagnostic? (single-
    subject verdict vs multi-subject grouping with no causal claim)
12. Why qualitative confidence instead of a number, and what evidence moves
    LOW → MEDIUM → HIGH? (no statistical basis for numeric scores;
    LOW = attribution uncertain, MEDIUM = entity+temporal,
    HIGH = + resource or shared_subject)

---

## Phase 6 — Incident History & Temporal Analysis

1. Why isn't a diagnostic itself an incident?
2. What is the difference between a diagnostic and an event?
3. Why do we need timestamps, and why does "CPU became high at 14:30:01 and
   stayed high 19 s" beat "CPU was high"?
4. How does the debugger know when an incident starts? (earliest member
   activation, back-filled from the correlation group)
5. What happens when one diagnostic recovers but another remains active?
   (RECOVERING — the incident is never falsely declared RECOVERED)
6. Why shouldn't two incidents separated by 40 minutes automatically become
   one incident?
7. Why shouldn't we store every raw LiDAR/camera message in incident history?
8. What is the difference between raw telemetry history and incident history?
9. Why should the incident/history layer not communicate directly with ROS 2?
10. What happens if Robot 1 and Robot 2 have simultaneous incidents?
11. What does "incident duration" actually tell us?
12. What limitations does an in-memory history have compared with persistent
    storage?

---

## Phase 7 — Debugger Backend / API

1. Why does the project need a backend API at all?
2. Why shouldn't the frontend directly import `collector.py` (or the internal
   engine modules)?
3. What is the difference between the internal debugger model and an API
   response model / DTO, and why do we keep them separate?
4. Why shouldn't the API itself subscribe to ROS 2 topics?
5. Where does API data actually come from? (single path: collector → engines →
   `DebuggerApp.snapshot_*` → DTOs)
6. How do we avoid two sources of truth (State A / State B)?
7. What should the API return when no ROS system is running? (valid empty, no
   fake data)
8. Why are stable API contracts important for the future dashboard?
9. What happens if a requested incident/robot does not exist? (404 vs empty)
10. What information comes from Phase 4, 5, and 6 respectively into the API?
11. Why should UI logic not be placed inside the backend API?
12. What makes this API reusable for any ROS 2 system, not just the warehouse?
13. Why did we choose FastAPI + Pydantic over Flask or stdlib `http.server`?

---

## Notes for the final interview

- The five-layer distinction (observation ≠ diagnostic ≠ correlation ≠
  hypothesis ≠ root cause) is the project's central contract — expect it to be
  probed from several angles.
- Be ready to explain *why* each design choice exists, not just what the code
  does: collector boundary, polling+diffing, selective observation,
  config-as-truth attribution, deterministic diagnostics, and the correlation
  safety gate.
- Cross-cutting themes: ROS-free analysis layers (unit-testability), monitor
  overhead, and honest uncertainty over fabricated precision.
