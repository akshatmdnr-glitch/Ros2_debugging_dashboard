# Incident Detail & Dashboard Views

*Phase 9 of the ROS 2 Debugging & Observability Platform.*

Phase 8 delivered a single-scroll dashboard foundation. Phase 9 turns it into
**multiple, focused views** and, crucially, surfaces the one thing the engine
already recorded but the UI never showed: the **full incident timeline**
(Phase 6). This document is about **our** implementation — the routing layer,
the shared state, and the per-incident fetch — not a generic routing tutorial.

---

## What problem does this solve?

After Phase 8 the dashboard answered *"is anything wrong right now?"* on one
page. It could not:

- focus on a single incident and read its **entire event sequence**
  (what activated, in what order, what recovered, how long it lasted);
- browse **completed incidents** separately from active ones;
- inspect **telemetry** (topic rates, process CPU/RSS, TF freshness) without
  scrolling past everything else;
- give the browser a real **URL** for each view (so you can bookmark or share
  `#/incidents/1`).

The backend already had all of this data — `GET /incidents/{id}` returns the
full Phase 6 timeline. The UI simply never asked for it. Phase 9 closes that
gap with **routing** and **detail views**.

## What is client-side routing (SPA routing)?

A **single-page application** (SPA) is one HTML page whose content changes
without reloading. **Routing** maps a URL path to a view:

```
/                → Overview
/incidents       → incident list (active + history)
/incidents/1     → incident #1 detail + timeline
/telemetry       → topic / process / TF telemetry
```

When you click a link, React Router changes the URL *and* renders the matching
view — no page reload, no new HTTP request for the HTML. The URL is the
"state" the browser shares and bookmarks; the actual data still comes from the
API.

**Why routing matters here:** a debugging tool is easier to use when each
concern is its own screen. The overview answers *"what is wrong now?"*; the
incident view answers *"what exactly happened?"*; telemetry answers *"what are
the numbers?"*.

## Route params

`/incidents/1` — the `1` is a **route parameter**. The route is defined as
`/incidents/:id`; React Router gives the component `useParams() → { id: "1" }`.
The component then fetches *that specific incident*:

```ts
const { id } = useParams();
const { incident } = useIncident(id);   // GET /incidents/1
```

This is why one component (`IncidentDetailPage`) renders every incident: the
URL decides which one.

## Shared state via React context

The overview, incidents, and telemetry views all need the same polled snapshot
(health, systems, diagnostics, incidents, telemetry). Options:

- **Each page polls itself** → three pollers, three timers, wasted requests.
- **Prop-drill** the snapshot through every component → messy.
- **React context (chosen)** → `DashboardProvider` runs the single
  `useDashboard` poller once and every view reads it via
  `useDashboardContext()`.

This is the same "single source of truth" idea as the backend: one poller, one
snapshot, many readers. We did not add a state library (Redux/Zustand) — a
context over one hook is all a small dashboard needs.

## Per-resource fetch vs shared snapshot

The shared snapshot contains the *lists* (active + history incidents), but not
each incident's full timeline. The detail view needs a **per-resource fetch**:
`fetchIncident(id)` → `GET /incidents/{id}`. It lives in its own hook
(`useIncident`) with its own loading / error / not-found lifecycle, because it
depends on a URL parameter that changes.

This split is deliberate:

| Data | Fetch | Who reads it |
|---|---|---|
| Dashboard snapshot | one poller in `DashboardProvider` | Overview, Incidents, Telemetry |
| Single incident timeline | per-view `useIncident(id)` | Incident detail |

## Data flow (new views)

```
/incidents/1
   ↓  React Router reads the URL param
IncidentDetailPage → useIncident("1")
   ↓  services/api.fetchIncident
   GET /incidents/1 → HTTP → FastAPI → DebuggerApp → HistoryEngine
   ↓  JSON response (incident + ordered events)
IncidentDetailPage renders: state, confidence, members, started/duration/ended,
   and the timeline (each event shown as t+{offset}s from started_at)
```

The `t+offset` display is worth noting: the engine uses *monotonic* timestamps,
so the meaningful number is **relative time since the incident started**, not a
wall clock. `t+2.0s ACTIVATED /robot2/scan` is exactly what an operator wants.

## What we implemented

- `react-router-dom` — the routing library (path → view mapping, `Link`,
  `NavLink`, `useParams`).
- `main.tsx` — wraps the app in `<BrowserRouter>` (the production router; tests
  use `MemoryRouter`).
- `App.tsx` / `AppShell.tsx` — provider + router shell with the nav bar and the
  four `<Route>`s.
- `context/DashboardContext.tsx` — the single shared polled snapshot.
- `pages/OverviewPage.tsx` — the Phase 8 overview (systems/robots, diagnostics,
  active incidents, now with links to details).
- `pages/IncidentsPage.tsx` — active incident cards + a history table (id,
  owner, state, confidence, members, duration), each linking to its detail.
- `pages/IncidentDetailPage.tsx` — full timeline view with `t+offset` events,
  loading, and a 404/not-found state.
- `pages/TelemetryPage.tsx` — topic rate/count/idle tables, process CPU/RSS,
  TF frames.
- `hooks/useIncident.ts` — per-resource fetch lifecycle.
- `services/api.ts` — added `fetchIncident(id)`.
- `components/NavBar.tsx` — Overview / Incidents / Telemetry links with an
  active-state indicator.
- CSS — nav bar, incident-detail stat grid, subheads (extending the Phase 8
  design tokens).

**Backend: zero changes.** All data already existed in the Phase 7 API.

## Alternatives considered

- **Server-side pages (each URL a full page load)** — rejected: the SPA already
  holds the polled snapshot; server rendering would lose it on every click.
- **One page + in-page tabs without routing** — rejected: no bookmarkable URLs,
  and "view incident #1" couldn't be shared.
- **Per-page pollers** — rejected: duplicate timers/requests; context gives one
  snapshot.
- **A state library (Redux/Zustand)** — rejected: a context over one hook is
  enough; a library would be ceremony, not value, at this size.
- **WebSockets for the timeline** — rejected for this phase: polling serves the
  foundation; the timeline is a snapshot per fetch, not a live stream (yet).

## Tests

- `src/App.test.tsx` (updated, 8 cases): overview render, offline banner, empty
  states, **navigation to Incidents**, **incident detail timeline**
  (`t+0.0s` / `t+1.0s`), **404 not-found**, and **Telemetry view**. Uses
  `MemoryRouter` (a router that lives in memory — perfect for tests) and a
  mocked API service.
- `src/services/api.test.ts` (updated): `fetchIncident` parses an incident and
  rejects on 404.
- `npm run build` type-checks the routes and types against the API contract.

What the tests prove: the router renders the right view per URL, the detail view
fetches by id, not-found is handled honestly, and telemetry/incident data flows
from the mocked API to the screen. What they do not prove: the real browser ↔
backend path (verified separately with the `--no-ros --demo` backend and the
Vite dev server).

## What could go wrong?

- **Deep-link reload 404s** — the dev server must fall back to `index.html` for
  unknown paths; Vite does this by default (verified). A misconfigured static
  host would break bookmarked `/incidents/1` URLs.
- **Stale incident after poll** — the detail view fetches once per id; a live
  incident may update between fetches. Polling the detail view is a Phase 10+
  refinement.
- **Missing id / 404** — handled with an explicit "incident not found" state
  (never a blank page or fake data).
- **Multiple pollers** — avoided by design (context); a future view that polls
  its own data must justify it.

## What I should be able to explain in an interview

1. What is client-side routing in a SPA, and why does our dashboard use it?
2. What is a route param, and how does `/incidents/:id` work end to end?
3. Why does one `DashboardProvider` feed every view instead of each page
   polling?
4. Why is the incident detail a per-resource fetch rather than part of the
   snapshot?
5. What does `GET /incidents/1` return, and what does the detail page render
   from it?
6. Why do we display timeline events as `t+offset` rather than wall-clock
   timestamps?
7. Where does the new data come from? (it already existed in the Phase 7 API)
8. How does the 404 state avoid showing fake data?
9. What is `MemoryRouter` and why do tests use it instead of `BrowserRouter`?
