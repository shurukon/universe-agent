# UI Overhaul Specification — v2 "Professional Universe"

Authoritative spec for the desktop-UI overhaul. Every wave lists deliverables,
exact API/component contracts, and acceptance criteria. Research sources:
universe-glm component map, bridge map, skills catalog (OpenDesign/UIUXProMax/
ECC), vercel-labs/ai-chatbot tool-card patterns, Claude Code status patterns.

Design identity stays **universe** (near-black #0b0d11, Photon Cyan #38C8FF,
Figtree/JetBrains Mono). Professional polish rules from
`skills_data/ECC/make-interfaces-feel-better.md` + `OpenDesign/linear-app.md`:
- concentric radii (outer = inner + padding), optical alignment
- `text-wrap: balance` headings, tabular-nums counters
- transitions: explicit properties only, 150ms exits, `active:scale(.96)`
- hit areas ≥40px, visible focus rings, no `transition: all`

---

## W1 — Backend & Bridge (enables everything else)

### 1.1 Schema v0014_ui_overhaul.py
```sql
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL DEFAULT '',
  created_at REAL, updated_at REAL, archived INTEGER DEFAULT 0
);
-- goals gain: project_id TEXT DEFAULT NULL, archived INTEGER DEFAULT 0,
--             deleted INTEGER DEFAULT 0, title TEXT DEFAULT NULL,
--             parent_goal_id TEXT DEFAULT NULL (branch), pinned INTEGER DEFAULT 0
-- mcp_servers gain: transport TEXT DEFAULT 'stdio', url TEXT DEFAULT '',
--                   headers TEXT DEFAULT '{}', enabled INTEGER DEFAULT 1
```
Migration also backfills: one default project "General" from current
project_root (INSERT OR IGNORE); goals.project_id NULL = General.

### 1.2 mcp_client.py — transports (stdio | sse | http)
- `MCPServer` dataclass: + `transport`, `url`, `headers`, `enabled`.
- `_ensure_started`: stdio → subprocess (unchanged); sse/http →
  httpx-based JSON-RPC per MCP Streamable-HTTP spec:
  POST `{url}` with `Accept: application/json, text/event-stream`,
  session header `Mcp-Session-Id` when server returns one; tools/list
  handshake identical; reuse one AsyncClient per server; egress proxy
  note: remote MCP URLs bypass the stdio egress model — validation =
  scheme must be https (or http+host allowlist loopback), documented.
- `add()`: accepts transport/url/headers; persists new columns.
- Tests: `tests/test_mcp_client.py` — (a) stdio path still passes,
  (b) new unit tests for HTTP handshake against a local aiohttp/starlette
  MCP-test-server fixture (real HTTP handshake, tools/list, tools/call),
  (c) SSE variant using the same fixture serving SSE stream responses.

### 1.3 Bridge endpoints (app.py)
- `GET /api/setup` → `{setup_complete, has_provider, assistant_name}` —
  first-run gate for the UI. `POST /api/setup/complete` writes
  `setup_complete=True, setup_at, assistant_name`.
- `GET/POST /api/projects` · `PATCH/DELETE /api/projects/{id}`
  (delete = soft when goals exist).
- `POST /api/session` gains optional `project_id`, `title`, `parent_goal_id`
  (branch start). Session rows + planets carry project_id.
- `GET /api/sessions?project_id=&include_archived=&q=` — filter support.
- `PATCH /api/session/{id}` `{title?, archived?, pinned?, project_id?}`,
  `DELETE /api/session/{id}` (soft delete: archived=1, deleted=1; hard only
  with `?hard=1`), `POST /api/session/{id}/branch` → duplicates session row with
  parent_goal_id set, returns new id (branch = new chat seeded from parent
  context; simplest honest model).
- MCP: `GET /api/mcp/servers` (list + health), `POST /api/mcp/servers`
  `{name, transport, command?, url?, headers?, declared_hosts?, read_only?}`
  → performs a real handshake then persists, `DELETE /api/mcp/servers/{id}`,
  `PATCH /api/mcp/servers/{id}` `{enabled?}`.
- `GET /api/agents` → per-agent: name, specialty, tools whitelist, current
  (provider, model), tier — for the Agents settings page.
- Voice settings unification: `DESKTOP_DEFAULTS` stays the single source;
  `voice.py::_settings` reads section `desktop` (kill the split-brain), with
  default fallbacks preserved. `voice_enabled` actually gates the WS.
- All endpoints get FastAPI TestClient coverage (tests/test_desktop_bridge.py
  grows a section: projects CRUD, goals archive/delete/branch, mcp servers
  with a mocked-transport happy/sad path + real stdio everything-server,
  setup gate).

### 1.4 Acceptance
- `pytest tests/ -k "bridge or mcp or project"` green without any API key.
- Setup gate honest: `setup_complete` false + `has_provider` false → UI
  shows onboarding; provider installed via onboarding flips it true.

---

## W2 — MiniUniverse 3D thinking indicator

**Requirement (user, verbatim intent)**: replace the current "ugly small 2D
screen" with the real memory universe in 3D, tiny (~5% of current size or
smaller), with many interactions.

### 2.1 Component `components/desktop/MiniUniverse.tsx`
- A **sized** R3F `<Canvas>` (default 220×140 CSS px, prop-driven,
  `dpr={[1,1.25]}`, `frameloop` paused when frozen), NOT the full-bleed
  UniverseCanvas. Reuses from universe-glm: `buildUniverse('mini', …)` quality
  profile (new: armPoints ~8k, systems real-only, no nebulae/dark-cores/
  lattice — core + arms + system stars + MemoryStars flares only),
  `MemorySphere`-style group at radius ~10, subset of `glsl.ts` materials
  (starfield, systemStar, fresnel core), `PALETTE`.
- Camera: fixed cube corner `position=[9,7,13] lookAt(0,0,0)`, no Controls
  mount; **autoRotate** 0.15 rad/s on the group; wheel/pinch disabled.
- Interactions (many, but tiny): hover system → corona + cursor pointer +
  tooltip (name, skill_count); click system → `useUniverse.setMode('response')`
  + opens `/universe?focus=<id>`; `universe:event` mapping identical to the big
  universe: agent_started→comet, skill_activated→flare at real system,
  tool_running→spark line, llm_round→core intensify, session end→freeze +
  still frame + "Worked X.Xs" label.
- Perf: mounts `Suspense` + `lazy`; renders only when events.length>0 or
  running; `dispose()` on unmount; particle budget ≤8k points (three.js
  skill rules: pixelRatio cap, instancing, shared materials, pause when
  hidden via IntersectionObserver, prefers-reduced-motion → static frame).
- Placement: replaces `<MemoryPulse/>` in AgentMessage; EventConstellation
  stays beneath it. MemoryPulse.tsx is DELETED (2D retired).

### 2.2 Acceptance
- Visual: real spiral arms + core visible at 220px, not a flat disc.
- Live: sending a session (with a real key) shows comet + flares; frozen frame
  on completion; zero WebGL context leaks (context loss guard: single
  renderer per page, ≤2 mounted MiniUniverse at once — chat shows latest
  running only, older agent messages collapse to a static mini icon).
- e2e: `ui_states_check.py` updated — `.mini-universe canvas` present while
  running, pixel churn > threshold, reduced-motion honored.

---

## W3 — First-run onboarding (`/welcome`)

3 steps, full-screen, universe identity, progress dots (step N of 3):

1. **Model provider (MANDATORY)** — catalog grid (free/paid/local from
   `/api/providers/catalog`), key field with show/hide toggle, inline
   validation (format hint), custom provider card (name/base_url/model/
   key) always available; "Continue" disabled until one verified provider
   exists (`has_provider` poll after install). Cannot be skipped — the
   route is the app gate while `setup_complete=false`.
2. **Voice mode (OPTIONABLE)** — STT (local/OpenAI + key), TTS
   (edge-tts/OpenAI + key) selectors with "test" buttons; big "Skip —
   set it up later in Settings" link (ux-guidelines/user-freedom).
3. **Name your universe (OPTIONABLE, defaults "Universe")** — the wake word
   preview updates live ("'wake up Nova' …"); finish → POST
   `/api/setup/complete` → `/chat`.

App.tsx gate: `useChat.setup` state from `/api/setup` — while incomplete,
every route redirects to `/welcome` (except /universe? No — full gate, universe
needs a provider too).

### Acceptance
- Fresh UNIVERSE_HOME + TestClient: gate shows; install provider (real key)
  → step 2; skip → step 3; name → setup_complete true → /chat unlocked.
- Persisted: relaunch skips onboarding.

---

## W4 — Settings overhaul (sections + navigation)

`/settings` becomes a two-pane layout: left nav (Providers · General ·
Agents · Voice · MCP Servers · Memory) + right content pane.

- **Providers**: existing catalog grid + installed list (from Wave-0 work)
  moved here verbatim + per-agent default override link.
- **General**: assistant name (wake word), language, project root default,
  theme density note, data controls (export/import, wipe history).
- **Agents**: from `GET /api/agents` — 12 agent cards: specialty, tools,
  current provider/model per agent with override dropdown (uses
  `set_agent_model` bridge passthrough `PATCH /api/agents/{name}/model`),
  fallback chain editor (add/remove order).
- **Voice**: current voice card + wake word + live mic test; fixed by W1
  settings unification.
- **MCP Servers**: list (name, transport badge, tool count, health dot,
  enable toggle), add form (transport tabs: stdio command / SSE url /
  HTTP url + headers JSON), remove; "connect test" runs a real handshake.
- **Memory**: real memory browser — counts band (skills/systems/orbits/
  trusted/stars), systems accordion → orbits → skills table (name,
  confidence, use_count, agent) from `/api/memory`; recent goals/
  asteroids feed; link to /universe.

### Acceptance
- All six sections render with real data (no placeholders), keyboard
  navigable, `?section=` deep-links, focus rings per a11y rules.

---

## W5 — Live status & activity feed (the professionalism core)

Pattern source: vercel tool.tsx (collapsible ToolHeader + status badge +
icon pulse) + Claude Code task list (pending/in-progress/complete
checklist, "esc to interrupt") + universe identity.

### 5.1 `components/desktop/ActivityFeed.tsx` (inside AgentMessage)
Replaces raw event spam with a structured professional feed:
- **Status line** (always visible while running): `[spinner verb] <agent>
  is <action>` — rotating verbs ("Charting", "igniting", "reading the
  star charts"… galactic flavor), agent name in cyan, elapsed timer
  tabular-nums. Updates from the latest event.
- **Plan checklist** (from `goal_planned.plan`): rows `◯ pending /
  ◐ in-progress (pulse) / ✓ done (teal) / ✕ failed (rose)` with agent
  chip; current step highlighted; auto-collapses when complete.
- **Tool cards** (from tool_requested/running/finished/failed/blocked):
  collapsible card per call — header: wrench icon, `Write`
  mono name, status badge (Running pulse / Completed ✓ / Error ✕ /
  Blocked ⊘ / Awaiting approval ⏳ with countdown), duration_ms;
  body: args summary mono block + result preview (2 lines, clamp).
  Verbatim states from vercel: input-streaming→Pending, input-available→
  Running (pulsing clock), output-available→Completed, output-error→
  Error, output-denied/blocked→Blocked.
- **Agent handoff line**: `from → to` with what_was_done (1 line).
- **Skill activation chip**: violet, name + solar-system + orbit, click →
  /universe?focus=.
- **LLM round dots**: `think N` subtle.
- Consent prompt inline banner (in addition to modal): "file.write wants
  permission — Approve / Deny" inline with countdown ring.

### 5.2 Composer status strip
While a session runs: top of composer shows `Running · Esc to interrupt ·
step 2/5 · 12.3s` — Esc actually triggers cancelGoal (keyboard parity
with Claude Code).

### 5.3 Acceptance
- llm-marked e2e: during a real session, feed shows ≥1 tool card completing,
  plan checklist progressing, status line verb rotating; no layout shift
  (CLS-safe animations); reduced-motion → no pulses, static states only.

---

## W6 — Projects & Chats (sidebar rework)

Sidebar becomes: Projects section (list, + new project, active highlight)
→ Chats (session threads) of active project:
- chat row: status dot (running/completed/failed/cancelled), title
  (first 40 chars or session.title), relative time, hover → actions
  (⋯ menu): Rename · Archive · Branch · Pin · Delete (confirm dialog).
- Archived chats collapse under "Archived" subsection (unarchive action).
- Branch: `POST /api/session/{id}/branch` → new chat with "⑂ branched" tag
  + parent link.
- New chat targets active project (project picker on hover-composer or in
  menu). ProviderBadge stays at brand.

### Acceptance
- CRUD works across restarts (SQLite truth), archive hides from main
  list, branch duplicates with parent link, delete with confirm soft-
  deletes; all via bridge tests + e2e DOM assertions.

---

## W7 — Verification matrix (run ALL before "done")

1. `pytest tests/` — full suite, no key: llm tests skip honestly; new
   bridge/mcp/project/setup tests green.
2. With `UNIVERSE_TEST_*`: llm suite incl. new ActivityFeed-driven asserts.
3. `tsc --noEmit` + `npm run build` — zero errors, 1 external script,
   0 inline (CSP).
4. `ui_states_check.py` extended states: onboarding gate (fresh home),
   settings ×6 sections, activity feed (running with real key),
   projects/chats CRUD DOM, MiniUniverse canvas + churn + reduced-motion.
5. `voice_loop_test.py` still PASS (settings unification regression).
6. Visual review pass per page against the ECC polish checklist
   (concentric radii, focus, hit areas, balance) — screenshot audit.
7. Fresh-home smoke: brand-new UNIVERSE_HOME → onboarding gate → (real key)
   → first session completes with professional feed + MiniUniverse animating.

Definition of done: every wave's acceptance criteria met and re-verified
after the final integration pass; regression suites green twice in a row.

## W8 — m6–m8 hardening addendum (2026-09)

- No native `window.prompt` remains (inline rename); no `as never` remains;
  mutations are never retried; mutations carry AbortController timeouts.
- Events dedup/sort by `seq`; refill exposes ring `dropped`; `?session=` views
  a previous conversation; local failed entries survive refresh.
- The universe badges `live` only on a successful `GET /api/memory`
  (bridge token), else `snapshot`; canvases isolate state per mount;
  PerfGovernor recovers as well as degrades; `npm run check` = tsc + eslint.
- Full changelog: `docs/BUGFIX-2026-09.md`.
