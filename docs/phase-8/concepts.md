# Full-Stack Concepts for a ROS 2 Debugging Dashboard

*Phase 8 of the ROS 2 Debugging & Observability Platform.*

Written for a robotics engineer who knows ROS 2, Python, and robots but is new
to web/full-stack engineering. Each concept is explained only as far as it
matters for one journey:

```
Robot → ROS 2 → Debugger → Backend API → HTTP → Frontend → Browser → You
```

We use the warehouse example throughout: **Robot 2 has high CPU, a degraded
`/robot2/scan`, a stale TF frame, and an active incident.** Everything below
explains how that fact travels from the robot to your eyeballs.

---

## Why are we building a web dashboard?

The debugger (Phases 1–7) already *knows* Robot 2 is degraded. The backend API
can *say* it over HTTP. But a robotics developer should not have to run `curl`
commands to understand a robot. A **visual interface** turns "Robot 2 has an
active incident (CPU, /scan, TF)" into something you can see at a glance.

The dashboard is **not separate from robotics** — it is another interface to the
robotics system, exactly like RViz is an interface to the TF/point cloud world.
It just happens to be a web interface instead of a Qt window.

## What is a web application?

A web application is a program whose **interface runs in a browser**. Compare:

| Tool | Where it runs | Interface |
|---|---|---|
| Python CLI debugger | your computer, terminal | text lines |
| ROS 2 node | your computer, inside `rclpy` | none (background) |
| RViz | your computer, desktop window | 3D Qt view |
| **Web dashboard** | **browser window** | HTML/CSS/JS page |

The debugger and the dashboard are **two separate programs**. The debugger
collects and reasons about the robot; the dashboard *shows* the result. They
talk to each other over HTTP. The browser never touches ROS directly.

## Client and server

- **Server** — a program that *holds data* and *answers requests*. In our
  project: the debugger + backend API (`debugger-api`) running on your machine,
  watching ROS 2.
- **Client** — a program that *asks* the server for data and *shows* it. In our
  project: the browser running the dashboard.

Analogy: the server is the kitchen, the client is the waiter bringing your
order. They are separate because **one server can serve many clients**, and the
client (browser) does not need to know how the food (data) is cooked.

When the browser requests information: the browser sends an HTTP request → the
backend reads the debugger's live state → sends an HTTP response → the browser
renders it.

## Frontend

The **frontend** is everything the user sees and interacts with: the pages,
cards, buttons, colors. In our project the frontend:

- displays systems, robots, diagnostics, and incidents,
- derives a visual "status" from the data (healthy / warning / critical),
- handles user interaction (clicking a robot, expanding an incident),
- **requests data from the backend API** — it does not produce data.

The frontend must **NOT**:

- discover ROS nodes, subscribe to topics, run diagnostics, correlate, or own
  history.

Why not? Because the frontend is a *view*. Putting robotics logic in the browser
would duplicate the debugger, split the truth across two places, and require the
browser to understand DDS/QoS/rules. The browser is great at *rendering*, not at
*robotics*.

## Backend

The **backend** is everything that runs *before* the browser — the logic and
data. In our project the backend is:

```
Debugger engine (collector, diagnostics, correlation, history)
   ↓
Backend API (FastAPI) — the HTTP door
```

The backend is the bridge between the engine and the frontend. **Why not let the
browser access ROS 2 directly?** Because the browser has no rclpy, no DDS, no
permissions to join a ROS domain, and no reason to. One clean door (the API) is
easier to secure, test, and reuse than a browser that somehow talks DDS.

## API

An **API** (Application Programming Interface) is a *defined way to ask a
program for things*. Phase 7 made the debugger's HTTP API: a set of **endpoints**
— URLs that return data.

An **endpoint** is one URL + one method. Example: `GET /incidents` means "give me
the incidents."

Step by step, `GET /incidents`:

```
Browser ──HTTP request──▶ Backend ──reads──▶ Debugger state
    ◀──────HTTP response──── Backend (JSON) ──so the UI can update────
```

The frontend never imports a Python class; it just asks URLs.

## HTTP

HTTP is the language the browser and backend speak. Only a few concepts matter
here:

- **request** — "please give me / get me something"
- **response** — "here it is" (or "not found")
- **method** — the verb: `GET` = fetch (read). `POST` = send (write). Our API is
  read-only, so it is all `GET`.
- **status code** — a number that says how it went: `200` = ok, `404` = not
  found, `422` = bad input, `500` = server error.
- **body** — the payload (for us, JSON data).
- **headers** — extra metadata about the request/response (e.g. `Content-Type:
  application/json`, CORS headers).

Example: `GET /robots` → backend replies `200 OK` with a JSON body listing the
robots, or `404` if the route did not exist. We do not need the full protocol —
just that the browser asks a URL and gets a status + a body.

## JSON

**JSON** (JavaScript Object Notation) is a text format for structured data.
Example:

```json
{
  "system": "warehouse",
  "name": "robot2",
  "active_diagnostics": 3,
  "active_incidents": 1
}
```

Three different shapes of the "same" data:

```
Python object            JSON over HTTP              JavaScript object in browser
(dict, dataclass)        (a text string)             (an object you can read)
```

The backend serializes Python objects → JSON text → the frontend parses JSON
text → JavaScript objects. JSON is the *common language* both languages
understand. That is why Python (backend) and JavaScript (frontend) can talk:
they agree on JSON.

## Request / response — the full interaction

1. You open the dashboard in the browser.
2. The frontend (running in the browser) sends `GET /systems`.
3. The backend receives it and reads the debugger's live state.
4. The backend builds a response (JSON) and sends it back.
5. The frontend receives the JSON.
6. The frontend updates the screen — "Warehouse" appears with its robots.

## JavaScript / TypeScript

The browser only understands HTML, CSS, and **JavaScript**. So anything that
runs *in the browser* must be JavaScript (or a language compiled to it). That is
the whole reason JavaScript exists in this project.

- **TypeScript** = JavaScript + types. It lets us write:

```ts
interface Robot { system: string; name: string; active_diagnostics: number }
```

so the editor and compiler catch mistakes before the browser does. The types
mirror the backend's JSON contract.

**Why is Python still the backend?** Python is the right tool for ROS/DDS,
telemetry math, and the engine. JavaScript/TypeScript is the right tool for the
browser. They never compete — they sit on opposite sides of the HTTP boundary
and agree on JSON.

```
Python            =   ROS / debugger / backend
TypeScript/JS     =   browser / frontend
```

## React

**React** is a library for building browser UIs out of *components*. The problem
it solves: a live dashboard has a lot of moving parts (robots, diagnostics,
incidents, timelines). Writing it as one giant pile of DOM code is unmanageable.
React lets us declare **what the UI should look like for a given state**, and it
re-renders when the state changes.

Instead of one giant webpage:

```
Dashboard
 ├── SystemOverview
 ├── RobotCard            (per robot)
 ├── DiagnosticPanel
 └── IncidentPanel
```

We build small reusable components and compose them. This is how the dashboard
stays maintainable as the debugger grows.

## Component

A **UI component** is a small, reusable piece of the interface. Example:
`RobotCard` — it *receives* a robot (name, nodes, active diagnostics count,
active incidents count), *derives* a status badge, and *renders* a card.

Components make the dashboard easier to maintain because each has one job and
can be tested/edited in isolation. Change `RobotCard` once and every robot
improves.

## State

"State" = the data that drives what the UI shows **right now**.

- **Backend state**: the debugger's real knowledge (Robot 2: 3 active
  diagnostics). This lives in the `DebuggerApp` on the server.
- **Frontend state**: the copy the browser currently displays. It starts as
  whatever the last HTTP response said, and it may be **stale** until the next
  poll.

They are related but not identical — which is exactly why a live debugger
polling has a *refresh gap*: Robot 2 recovers in the backend at 14:30:25, but the
browser still shows DEGRADED until the next poll picks it up. Understanding this
gap is central to a live dashboard.

## Props

Props ("properties") are how a parent component passes data to a child:

```
<RobotCard robot={robot} status="warning" />   (parent gives data)
```

`RobotCard` receives `robot` and `status` and renders them. Parent → child
one-way data flow. We use props so components stay dumb and testable: they render
whatever they are given.

## Routing

Routing = mapping a URL to a view:

```
/dashboard   → overview
/robots      → fleet list
/incidents   → incident list
```

Phase 8 is a single-page foundation — one overview view is enough, so **we do
not add routes yet**. Routing is a concept we understand for Phase 9, not a
feature we build now.

## Asynchronous requests

The browser cannot "wait" for the backend like a Python function call. A network
request takes time (milliseconds to seconds), so the browser **does not block**:
it sends the request, keeps running (the page stays responsive), and handles the
response *later* via a callback (a Promise).

```
Frontend sends request
   ↓ (keeps running)
... backend responds later ...
   ↓
Frontend handles response, updates screen
```

This matters for live ROS debugging: the dashboard keeps responding while it
waits, and when the robot's state changes the next poll brings the update.

## CORS

**CORS** (Cross-Origin Resource Sharing) is a browser security rule. A browser
page loaded from `http://localhost:5173` (the Vite dev server) is **origin**
5173. The API is at `http://localhost:8000` — a *different origin*. By default
the browser **blocks** the frontend's request to the backend to stop one site
reading another's data.

The backend solves this with **CORS headers**: it tells the browser "this
origin is allowed to read me". We add `CORSMiddleware` to FastAPI listing
`http://localhost:5173`. Without it, the dashboard's fetch would fail in the
browser even though the API works in `curl` — a classic confusion.

## npm and package.json

**npm** is the package manager for frontend dependencies — the JavaScript
equivalent of `pip`.

| Python | Frontend |
|---|---|
| `requirements.txt` / `pyproject.toml` | `package.json` |
| `pip install` | `npm install` |
| `venv` | `node_modules/` |
| `pip` | `npm` |

`package.json` declares the project, its dependencies (`react`, `react-dom`,
dev tools like `vite`, `typescript`, `vitest`) and its scripts (`npm run dev`,
`npm run build`, `npm test`). The `web/` folder is its own little package.

## Development server

Source code is not what the browser runs — it needs a *build step*. For fast
development, **Vite** runs a **development server**: it watches your source,
compiles TypeScript/React on the fly, and serves the app at
`http://localhost:5173`, hot-reloading as you edit.

```
source code → development server → browser (localhost:5173)
```

`npm run dev` starts this. It is for *writing* the app, not for deploying it.

## Build

A **production build** is the optimized, final set of files:

```
TypeScript/React source
   ↓  (vite build: type-check, bundle, minify)
browser-ready static files  (HTML, JS, CSS)
```

Dev code is split into many files and readable; the build merges + minifies it
so the browser loads quickly. `npm run build` also runs the TypeScript compiler,
which catches type errors — our cheap "frontend typecheck".

## Static files

After the build, the browser receives **static files**:

- **HTML** — the page skeleton (`<div id="root">`).
- **CSS** — colors, spacing, fonts (our design system).
- **JavaScript** — the React app (components, logic, fetching).

They are called *static* because the server just serves them as-is; the
dynamism (live robot state) comes from the app calling the API at runtime.

## Polling vs WebSockets

**Polling** (what Phase 8 uses): the frontend asks on a timer.

```
Browser ──GET /systems──▶ Backend ──▶ Browser
Browser ──GET /systems──▶ Backend ──▶ Browser   (2 s later)
...
```

**WebSocket**: one persistent two-way connection; the backend *pushes* updates
the moment they happen.

```
Browser ⇄ (persistent connection) ⇄ Backend   (push on change)
```

For real-time ROS debugging, WebSockets eventually give lower latency (no up-to-
2 s delay) and less overhead. But they add complexity: connection management,
reconnect, and backend push logic. **Phase 8 uses simple polling** (every 2 s)
because it is sufficient for a foundation, dead simple to reason about, and
needs no new backend machinery. WebSockets are recorded as a Phase 9+ option.

## Full-stack data flow (the whole journey)

```
Robot publishes /robot2/scan
        ↓
DDS / ROS 2 (the robot's data plane)
        ↓
Debugger collector  (joins the ROS domain, observes)
        ↓
Telemetry  (rates, counts, timestamps)
        ↓
Diagnostics  (Phase 4: "/scan below expected")
        ↓
Correlation  (Phase 5: CPU + scan + TF related)
        ↓
Incident history  (Phase 6: the timeline)
        ↓
Backend API  (Phase 7: JSON endpoints)
        ↓
HTTP  (the network hop)
        ↓
Frontend  (React app in the browser)
        ↓
UI components  (RobotCard, DiagnosticPanel, IncidentPanel)
        ↓
Browser window  (you see "Robot 2: DEGRADED")
        ↓
Human developer  (understands the robot at a glance)
```

Every arrow is a *boundary*: ROS/DDS, collector→analysis, analysis→API, API→UI.
Each phase owns its boundary, so the whole system can be reasoned about layer by
layer.

## What happens when I open the dashboard?

1. Browser opens the page; the frontend app loads (HTML/CSS/JS).
2. The app reads the API base URL (`http://localhost:8000`).
3. It sends `GET /health`, `GET /systems`, `GET /diagnostics`, `GET /incidents`.
4. The backend reads the live `DebuggerApp` state and serializes it to JSON.
5. The JSON travels over HTTP (CORS allows it).
6. The frontend stores the data in its state.
7. Components render: header, system cards, robot cards, diagnostic list,
   incident list.
8. You see "Warehouse / Robot 1: HEALTHY, Robot 2: DEGRADED".

While open, the app **polls every 2 s** so the view tracks the robot.

## What happens when Robot 2 develops a problem?

1. Robot 2's CPU rises → its `/scan` rate drops.
2. The debugger's rules fire → a `high_cpu` and a `frequency_degradation`
   diagnostic become ACTIVE (Phase 4).
3. Correlation groups them into an incident (Phase 5); the history engine
   records the timeline (Phase 6).
4. On the next poll, the frontend's `GET /diagnostics` and `GET /incidents`
   return the new state.
5. The frontend state updates → `RobotCard` flips to WARNING, a diagnostic row
   appears, an incident card appears.
6. Because we poll, the change appears **within the poll interval** (≤2 s) — not
   instantly. WebSockets could make it instant later.

## Why not put ROS 2 directly in the frontend?

The "bad" architecture:

```
Browser ──▶ ROS 2            (bad)
```

Why it is bad:
- **Browsers cannot run rclpy/DDS** — no domain join, no QoS, no environment.
- **Security** — a web page should not be able to control your robot's network.
- **Separation of responsibilities** — the debugger owns knowledge; the browser
  would have to duplicate rules, correlation, and history.
- **Maintainability** — two sources of truth drift apart.
- **Portability** — the API is reusable by *any* tool (CLI, dashboard, another
  robot's tooling); a browser-ROS link only works in that one browser.

The good architecture:

```
Browser ──▶ API ──▶ Debugger ──▶ ROS 2
```

One clean, stable door. This is exactly why Phases 5–7 drew the boundaries they
did.

## Why full-stack knowledge matters to a robotics engineer

Modern robotics tooling is rarely one program. A real robot stack is:
ROS 2 nodes (motion, perception) + tools that *observe* it (our debugger) +
interfaces that *present* it (a web dashboard) — all connected by well-defined
boundaries (DDS, HTTP, JSON). Understanding the full stack means you can build
the tools that make a robot understandable, not just the robot itself. The
dashboard is not a distraction from robotics; it *is* part of the robotics
product.

---

## What each technology is doing in OUR project

| Technology | Role in the project |
|---|---|
| ROS 2 / DDS | the robot's data plane |
| Python (debugger) | collection + analysis (Phases 1–6) |
| FastAPI (backend) | HTTP door over the debugger state (Phase 7) |
| HTTP | the network language between backend and browser |
| JSON | the data format both sides agree on |
| React | component-based UI rendering in the browser |
| TypeScript | typed JavaScript; mirrors the API contract |
| Vite | dev server + production build |
| npm | dependency manager for the `web/` package |
| CSS variables | the design system (colors, spacing, typography) |

## Alternatives

- **WebSockets instead of polling** — deferred: polling is simpler and
  sufficient for the foundation; WebSockets are Phase 9+.
- **React vs Vue/Svelte** — React chosen: the largest ecosystem, the most
  familiar to collaborators, and idiomatic component model; alternatives are
  equally valid but React fits the "component + typed props" teaching goal.
- **TypeScript vs plain JavaScript** — TypeScript chosen: the API already has a
  typed schema, and TS catches contract drift before the browser does.
- **Vite vs webpack/CRA** — Vite chosen: fast dev server, minimal config, and it
  pairs with the modern React/TS setup. (CRA is deprecated; webpack is heavier.)
- **State library (Redux/Zustand) vs local state** — local state chosen: a
  single-page foundation polling one snapshot does not need a state library.
- **CORS vs Vite proxy** — CORS chosen so the real cross-origin contract is
  explicit and testable; a proxy would hide it in dev.

## What could go wrong?

- **CORS misconfigured** → the dashboard loads but every fetch fails in the
  browser (works in curl). The error state shows "backend unreachable".
- **Stale frontend state** → the poll gap shows old data; we show `last
  updated` so you know how fresh it is.
- **Backend down** → the frontend must show a clear "backend unreachable"
  banner, not a broken page.
- **Contract drift** → backend field renamed but frontend type not updated; the
  TypeScript compiler + tests catch it at build/test time.
- **Fetching too fast** → pointless load; 2 s is calm for a local tool.
- **Demo data mistaken for real data** → `--demo` is explicitly labelled; normal
  mode returns only real (possibly empty) state.

## What I should be able to explain in an interview

1. What the frontend, backend, and API each are, in this project.
2. The full path from `Robot 2 publishes /scan` to the browser rendering it.
3. Why the browser does not talk to ROS 2 directly.
4. What `GET /incidents` does, step by step.
5. What HTTP request/response, status codes, and JSON are doing.
6. Why Python runs the backend and TypeScript/JS runs the browser.
7. What a React component, props, and frontend state are.
8. Backend state vs frontend state — and why they can disagree briefly.
9. What CORS is and why `localhost:5173` → `localhost:8000` needs it.
10. What npm/package.json/dev-server/build do.
11. Polling vs WebSockets and why Phase 8 chose polling.
12. Why the dashboard is part of robotics tooling, not a bolted-on website.
