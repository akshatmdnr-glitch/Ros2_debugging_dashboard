# Real-Time Updates & Live Debugging

*Phase 11 of the ROS 2 Debugging & Observability Platform.*

Phases 0–10 built a pipeline that answers *"what is wrong?"* and *"why might it
be related?"* — but the dashboard only learned about changes by re-downloading
everything every 2 seconds. Phase 11 makes the dashboard **live**: the backend
now broadcasts each observation cycle's transitions, and the browser applies
them as they happen, with an honest connection state (LIVE / STALE /
RECONNECTING / DISCONNECTED).

This document is about **our** implementation. It explains the real-time
concepts for a robotics engineer, why WebSocket was chosen over polling or SSE,
and how the existing single-source-of-truth design shaped the solution.

---

## What problem does this solve?

Before Phase 11 the browser polled `GET` endpoints every 2000 ms:

- **Latency**: a change could take up to 2 s + request time to appear.
- **Wasted work**: every poll re-downloaded all 9 resources whether or not
  anything changed.
- **Unknown freshness**: the UI said "updated 14:30:02" — it could not say
  whether that was 2 seconds ago or 2 minutes ago (backend died silently).

The backend already *produced* the events we needed — `DebuggerApp.refresh()`
returns `(diagnostic_events, correlation_events, history_events)` — but they
were only consumed by the CLI and then discarded. Phase 11 relays them.

## The real-time concepts (as they apply to this debugger)

**Hard vs soft vs near-real-time.** Hard real-time = a missed deadline is a
failure (an ECU braking loop, microseconds, bounded worst case). Soft real-time
= deadlines matter but a miss degrades quality, not safety. Near-real-time =
seconds of staleness is fine. **A debugging dashboard is near-real-time and
soft** — it *observes* a robot and never *controls* it. "Real-time" here means:
changes reach the screen fast enough that you trust what you see, without
pressing refresh. Our observation cycle runs once per graph poll (~1 s), so the
event stream is inherently ≤ ~1 s old.

**State vs events — the core distinction.** *State* is a snapshot: "Robot 2 has
3 active diagnostics." *Events* are facts: "`frequency_degradation` on
`/robot2/scan` became ACTIVE." Our pipeline already separated these
(`snapshot_*()` = state; `MemberEvent`, ACTIVE/RESOLVED, group changes =
events). Phase 11 delivers events and *patches* state from them, instead of
re-downloading state.

**Source of truth.** One authority decides what's true: `DebuggerApp`. The API
reads it, never rebuilds it. The real-time channel does not add a second
opinion — it broadcasts *exactly* the events `refresh()` already produced for
the CLI, so the CLI, the HTTP API, and the WebSocket all observe the same world.

**Sync (synchronization).** A client must first converge to the current truth,
then stay converged: **full HTTP snapshot on every connect, then small event
patches**. This is the canonical real-time pattern. If a client misses events,
it re-syncs with a fresh snapshot — patching from a gap produces a silently
wrong dashboard, so we never try.

**Polling** (what we had): client asks on a timer. Simple, stateless server,
survives disconnects by default — but fixed latency, wasted work, and a fuzzy
"is it live?" answer.

**WebSocket**: a persistent full-duplex connection after an HTTP upgrade
(`ws://`). The server pushes the moment an event happens. Costs: stateful
server, and we must build reconnection + re-sync ourselves.

**SSE (Server-Sent Events)**: one long-lived HTTP response streaming text
events; the browser's `EventSource` auto-reconnects. Simpler than WS, one-way.
Its magic auto-reconnect hides the very connection states we want to *show*.

**Latency / freshness / throughput.** Latency = change → pixel (before: ≤2 s;
now: ~1 s cycle + network). Freshness = how old the shown data is (now an
honest "as of HH:MM:SS"). Throughput = events/sec the channel carries — for us
a handful per cycle, so a non-issue.

**Connection lifecycle.** States must be visible, not guessed:
`connecting → live → stale → reconnecting → disconnected`. The header badge
shows exactly which one is true.

**Reconnection.** Connections die (backend restart, network blip, laptop
sleep). We detect it, retry with exponential backoff (1 s → 30 s cap), and on
every successful connect **refetch the full snapshot** before resuming events.
The trap avoided: reconnect and keep patching stale data.

**Stale data.** Old data shown as if fresh is a lie. When the socket drops we
label the view **STALE** (data stays, stamped "as of …", with a visible
warning banner) and retry. We never present old data as current.

**Heartbeat / liveness.** When no cycle has been produced for 5 s the server
sends `{"type":"heartbeat"}`; the client knows "alive but quiet" vs "dead".
(Our refresh cycle is itself a natural heartbeat.)

**Event messages — the schema.** Typed messages with a discriminator, mirroring
the DTO discipline from Phase 7:
`hello` (on connect), `cycle` (per observation cycle), `heartbeat` (liveness).
A `cycle` carries that cycle's transitions: `diagnostic_events`,
`correlation_events`, `incident_events`, plus `topology_changed`.

**Ordering.** A single backend produces everything in one thread under the
state lock, so messages arrive in generation order and the client applies them
in arrival order.

**Duplicates / idempotency.** Events can be redelivered at a reconnect
boundary. Our patch logic is set-based, exactly like the backend's
`HistoryEngine`: "set member X active" twice = the same result. Diagnostics are
keyed by stable `key`, incidents by stable `id`.

**Initial state + live events.** Snapshot on connect (correct current state),
then patches (keep it current). We never need the full event history, because
we never replay it — consistent with Phase 6's in-memory history.

**Missed events.** If the socket drops for 5 s, events happened that we never
saw. The only safe recovery is a fresh snapshot (sync). The backend's `seq`
per cycle enables gap *detection* later; today "re-sync on every reconnect" is
sufficient and simpler.

**Bursts & backpressure.** If 50 diagnostics fire at once (a node dies), the
backend batches the whole cycle into ONE message, and the client's idempotent
patches make repeated updates cheap and correct.

**No raw sensor streams.** We ship *verdicts and transitions* ("below expected
rate", "ACTIVATED", "RECOVERED"), never `/scan` point clouds or 1 kHz
telemetry. Raw data stays in ROS; the dashboard sees the analysis. Hard scope
boundary.

**Observability vs control.** We observe, never command. The WebSocket is
push-only events; there is no command channel and no plan to add one casually.

**Security.** LAN debugging tool, no auth (Phase 7 decision). WebSocket
specifics: the server validates the `Origin` header on upgrade (rejects
unexpected origins with code 4403), and the frontend connects to the same
host/port as the API. Documented as: auth is a future requirement if the
dashboard ever leaves localhost.

## Transport recommendation: WebSocket (why not the others)

| | Polling (before) | WebSocket | SSE |
|---|---|---|---|
| Push latency | ≤ 2 s + request | ~instant | ~instant |
| Server state | none | client set | client set |
| Reconnect | free | must build | built-in (`EventSource`) |
| State visibility | fuzzy | full control | hidden |
| Bidirectional | n/a | yes (unused) | no |
| Test complexity | low | medium | medium |

**WebSocket wins for us** because (1) the backend has a natural push producer —
the refresh cycle; (2) FastAPI's WebSocket support is first-class and testable
with `TestClient`; (3) we want to *show* the operator the connection state
machine (LIVE/STALE/RECONNECTING/DISCONNECTED), which `EventSource`'s
auto-reconnect hides behind the scenes. Polling is not deleted: it becomes the
**fallback** when WebSocket is unavailable (feature-detected), and the full
snapshot refetch on every (re)connect is itself the "poll" that re-syncs us.

## Backend design

`refresh()` already returned the event triple; Phase 11 adds one thin layer:

- **`EventBroadcaster`** (`broadcast.py`): a thread-safe fan-out. Sinks
  register a callable; `publish(message)` calls each. Deliberately **no buffer
  and no replay** — a late subscriber starts at the next message and re-syncs
  via HTTP snapshot. A broken sink never breaks the observation cycle.
- **`DebuggerApp`** owns one broadcaster and a per-cycle `seq`. `refresh()`
  builds a `cycle` message (reusing the existing snapshot DTO builders) under
  the state lock and publishes it *after* releasing the lock. It also captures
  graph events (node/topic added/removed) from the collector and sets
  `topology_changed` so clients refetch — the graph + attribution live only on
  the backend, so the frontend must not re-derive them.
- **`GET /ws/stream`** (`api.py`): validates the `Origin`, subscribes an
  `asyncio.Queue` bridged from the publisher thread via
  `loop.call_soon_threadsafe`, sends `hello`, then forwards `cycle` messages
  and `heartbeat`s. Disconnects unsubscribe cleanly.
- **Demo driver** (`--no-ros --demo`): a thread calls `refresh()` every second,
  so the synthetic-but-real demo state evolves live (topics go stale, the fake
  process drops off `/proc`, diagnostics resolve) and the dashboard shows real
  transitions without a ROS system.

No new collection, no second state store, no commands. The CLI still consumes
the same returned events.

## Frontend design

- **`useRealtime` hook** replaces the polling `useDashboard`. State machine:
  `connecting → live → stale → reconnecting → disconnected`. On mount: fetch
  the full snapshot immediately, open the WebSocket, and on every (re)connect
  refetch the snapshot (re-sync). `cycle` messages patch the current snapshot;
  `topology_changed: true` triggers a full refetch. Falls back to 2 s polling
  when WebSocket is unavailable.
- **`realtime.ts`** — pure, testable patch logic. Each event type is applied
  idempotently (upsert by diagnostic `key` or incident `id`), then the
  aggregation counts (active diagnostics/incidents per system/robot) are
  recomputed from the patched verdicts — presentation aggregation, mirroring
  the backend's `_counts_locked`, not a re-judgment.
- **UI**: the header badge now shows CONNECTING / LIVE / STALE / RECONNECTING /
  DISCONNECTED, and "as of HH:MM:SS". A STALE state shows a warning banner
  ("connection lost, retrying") — old data is always labelled old, never
  presented as fresh.

## Responsibilities (unchanged)

```
Backend decides:  observations → diagnostics → correlation → incidents
                  → broadcasts the SAME transitions it gives the CLI
Frontend shows:   the snapshot + patches it with backend events,
                  and reports its own connection state honestly
```

The frontend never judges anything; it only applies what the backend says and
labels its own connectivity.

## What we implemented

**Backend:**
- `broadcast.py` — NEW. `EventBroadcaster` (thread-safe, no replay).
- `app.py` — MODIFIED. Owns the broadcaster + `seq`; `_capture_graph_event`;
  `_cycle_message()`; `refresh()` publishes the cycle message.
- `api.py` — MODIFIED. `GET /ws/stream` (hello/cycle/heartbeat, Origin check),
  `create_app(..., heartbeat_s=5.0)`, `_run_demo` driver for `--no-ros --demo`.
- `test/test_api.py` — MODIFIED. +10 WebSocket tests (89 total).

**Frontend (`web/`):**
- `types.ts` — `CycleMessage` / `HelloMessage` / `HeartbeatMessage` /
  `DiagnosticEvent` / `CorrelationEvent` / `IncidentEvent`.
- `services/api.ts` — `streamUrl()`.
- `realtime.ts` — NEW. Idempotent `applyCycle` + `recomputeCounts`.
- `hooks/useRealtime.ts` — NEW. Connection state machine + re-sync + polling
  fallback (replaces `hooks/useDashboard.ts`, removed).
- `context/DashboardContext.tsx` — uses `useRealtime`.
- `components/Header.tsx` — LIVE/STALE/RECONNECTING/DISCONNECTED badge + "as of".
- `AppShell.tsx` — connecting / disconnected / stale banners.
- `styles/global.css` — badge + warning-banner styles.
- `test/mockWebSocket.ts`, `test/fixtures.ts` — NEW test helpers.
- `realtime.test.ts`, `hooks/useRealtime.test.tsx` — NEW tests; `App.test.tsx`
  updated for the new labels and WebSocket mock.

## Tests

- Backend (89 pass, +10): hello + empty cycle; diagnostic activation;
  incident lifecycle (create → recover → close over the wire); multiple robots;
  rapid updates stay ordered (seq); no replay after reconnect; topology-changed
  flag; heartbeat when quiet; Origin allow/reject.
- Frontend (39 pass, +20): patch logic is idempotent and recomputes counts;
  hook reaches LIVE, shows DISCONNECTED when the backend never connects, applies
  cycles without refetch, refetches on topology change, ignores cycles before
  the first snapshot, and recovers STALE → LIVE via reconnection; App-level
  render tests updated to the new labels.
- Live smoke: `--no-ros --demo` backend → real `hello` + continuous `cycle`
  messages (including an organic event) over a real WebSocket client;
  `--no-ros` (no demo) → `hello` then `heartbeat` on an empty system.

What the tests prove: the transport delivers the backend's transitions, the
client applies them idempotently and reports its state honestly, and both ends
survive reconnects without fabricating data. What they do not prove: latency
perception on a real robot fleet (acceptable — the cycle is bounded by the
1 s refresh) and browser-proxy edge cases (mitigated by the polling fallback).

## What could go wrong?

- **Backend restarts** → the socket drops, the client shows STALE, reconnects,
  refetches a fresh snapshot: state is correct after a few seconds.
- **Burst of events** → batched per cycle; idempotent patches keep re-renders
  cheap. A pathological flood is still bounded by the refresh cycle.
- **WebSocket blocked by a proxy** → construction fallback switches to 2 s
  polling (slower, but the snapshot path always works).
- **A client disconnects mid-burst** → messages between close and reconnect are
  dropped by design; the reconnect snapshot replaces them. No partial state.
- **Demo state drifts over time** (topics go stale, the fake process "dies") —
  that is the point: it produces real transitions so the dashboard can be
  exercised live without a robot. It is clearly labelled synthetic.

## What I should be able to explain in an interview

1. What did the dashboard do before this phase, and what were the costs?
2. What is the difference between state and events, and where does each live
   in this project?
3. Why is the backend the single source of truth for the real-time stream?
4. How does a client synchronize? Why a full snapshot on connect instead of a
   replay?
5. Why WebSocket over SSE? (state visibility, FastAPI support, push producer)
6. Why does polling survive? (fallback + the re-sync refetch IS a poll)
7. What is the connection state machine and why must it be visible?
8. What does STALE mean and why do we never show old data as fresh?
9. How is ordering guaranteed? How is idempotency handled?
10. What happens to missed events? (no replay; snapshot re-sync)
11. What is `topology_changed` and why can't the frontend re-derive it?
12. How are bursts handled without a queue/backpressure machinery?
13. Why do we never stream raw sensor data, and why is that a scope boundary?
14. What did the demo driver add, and why is it not fake data?
15. What are the security properties of the WebSocket endpoint?
