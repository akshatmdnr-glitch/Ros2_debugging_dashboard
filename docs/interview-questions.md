# Project Interview Questions (Deferred Teach-Back)

*Living bank of teach-back interview questions, collected per phase and used
for the final project interview when the project is complete.*

The teach-back for each phase is deferred to the end of the project. When a
phase finishes, its questions are recorded here (plus any corrections to
answers given during development). The final interview will draw from this
bank plus the "What I should be able to explain in an interview" lists in each
`docs/phase-N/concepts.md`.

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
