# Universe Agent — Desktop

```
    ·  ✦  ·     ·  ✦  ·     ·  ✦  ·
         UNIVERSE AGENT · DESKTOP
    ✦ ─────────────── v1.0.0 ─────────────── ✦
      the mind that remembers everything — with a face
```

The Electron desktop app for Universe Agent: a local-first AI agent OS with
12 specialist agents, 98 tools, a 5-tier persistent memory, and the trusted
skills corpus (live counts in the universe) — now driven from a native window instead of the terminal.

```
Electron (main.ts)
 ├─ spawns + supervises the Python bridge (universe_desktop_bridge, FastAPI)
 │    └─ calls the real Universe core: orchestrator → agents → tools → memory
 └─ loads the React client (Photon Cyan #38C8FF identity)
      └─ HTTP (JSON) + WebSocket (live events + voice)
```

Nothing runs in the cloud. The window talks to `127.0.0.1:8738` only, and the
bridge talks to the local Universe core — your code, memory, and keys never
leave the machine.

---

## What you get

- **First-run onboarding (mandatory)** — `/welcome` gates the whole app until
  a provider is really connected: step 1 picks a catalog provider (or a custom
  OpenAI-compatible endpoint with its full settings) and verifies the key
  with one live request; step 2 sets voice (skippable); step 3 names your
  universe — the wake name for voice mode. Everything is changeable later.
- **Projects + chats** — the sidebar is a workspace: create projects, and
  each project holds its chats (status dot, title, relative time, branch
  tag). Every chat has a menu: Rename, Branch from here, Pin, Archive,
  Delete forever. Archived chats rest in a collapsible section; the General
  project is protected.
- **The MiniUniverse 3D** — while a goal runs, the thinking indicator *is* the
  memory universe miniaturized (~9k particles vs 168k, same builder, same GLSL,
  same real-memory `hash32` geometry): it slowly rotates, skills flare at
  their real system, and on completion it freezes into a still final frame
  with a "Worked for Xs" chip. Click it to open the full universe focused on
  the last-touched system.
- **The ActivityFeed** — the professional status surface: a live status line
  (rotating galactic verb + agent + tabular-nums elapsed), the plan
  checklist (◯ pending · ◐ working · ✓ done · ✕ failed), collapsible tool
  cards (Pending → Running → Completed / Error / Blocked with durations,
  args, and results), handoff lines between specialists, clickable skill
  chips that deep-link into the memory universe, and an inline consent banner
  (no modal hunting). While a goal runs the composer shows a strip with
  `esc to interrupt`.
- **Settings, in six sections** — Providers · General · Agents · Voice ·
  MCP servers · Memory. The Agents section shows the 13 specialists and
  lets you override each one's (provider, model). MCP servers speak all
  three transports — **stdio**, **HTTP (streamable)**, and **SSE** — and a
  server is registered only after a real handshake. Memory browses the live
  universe: 11 systems → orbits → the trusted skills with real values.
- **Full 3D memory universe** — 11 solar systems, 48 orbits, live skill counts;
  live goals drive agent comets and skill-star flares here too.
- **Voice, end to end** — a custom wake word (`wake up Nova` — whatever you
  named it), local faster-whisper STT, free edge-tts speech by default (or
  your own OpenAI keys), and a core that breathes with your voice level.
- **Consent, honestly** — file writes and shell runs pause for approval
  naming the exact tool, agent, and target — as a modal or the inline
  consent banner. 60 s with no answer is a deny. There is no hidden bypass.
- **A recovery path for everything** — bridge crashes restart under
  supervision; goals orphaned by a restart are swept to a failed state with
  the honest reason; the offline banner states what broke and what the app
  is doing about it; every failed goal offers Retry.

## Requirements

- **Python 3.11+** on PATH (`python --version`) — the desktop is dev-first:
  v1 runs the Universe core from source, it does not bundle a backend.
- **Node 18+** and npm (only for the source checkout).
- Windows 10/11 x64 (that's what v1 is built and tested on; the stack itself
  is portable).
- Optional for voice: a microphone. Local STT (faster-whisper int8, CPU)
  needs no key; edge-tts needs internet; your own OpenAI key works for both
  STT and TTS from Settings.

## Quick start (from source)

```bash
# 1. backend deps (once)
cd Universe-compter
pip install -e ".[voice]"

# 2. the desktop shell
cd universe-agent-ui
npm install
npm run desktop          # dev: vite on :5199 + electron + supervised bridge
```

Prefer the built app (no dev server)?

```bash
npm run build            # bundles electron main + builds the client
npm run desktop:prod     # electron loads app://universe/, spawns the bridge
```

The bridge binds **127.0.0.1 only** (CORS is restricted to `app://universe`
and `localhost`/`127.0.0.1` origins). The port is fixed at 8738 in v1;
`UNIVERSE_BRIDGE_PORT` overrides it for tests.

### Windows installer

```bash
npm run dist             # → release/UniverseComputer-Setup-1.0.0-x64.exe (NSIS)
```

The installer ships the desktop shell only. It finds the Universe backend via
the **`UNIVERSE_ROOT`** environment variable pointing at your `Universe-compter`
checkout (set it once in System Properties → Environment Variables), or by
looking next to the install directory. If the backend isn't found, the app
opens on a status screen that says exactly that — nothing runs silently.

## The voice channel

Click the mic in the composer:

- **Idle:** say your wake phrase (`wake up Nova` — whatever you named the
  universe in onboarding; configurable in Settings → Voice) — the core wakes
  and the next utterance becomes the goal.
- **One breath:** `wake up Nova, write a poem about Mars` — the wake phrase
  is stripped and the rest is the goal.
- **Answers speak back:** when a spoken goal completes, a plain-English
  summary is synthesized (edge-tts by default) and streamed to you.

Providers (Settings → Voice, or `/settings?section=voice`):

| Slot | Options |
|---|---|
| Wake word | any custom phrase (STT-based fuzzy match; a trained openwakeword `.onnx` can be wired via `kws_model_path`) |
| STT | local faster-whisper (tiny→medium, int8 CPU, no key) · OpenAI Whisper (your key) |
| TTS | edge-tts voices (free) · OpenAI (your key) |

## Where things live

| Path | Role |
|---|---|
| `universe-agent-ui/electron/` | main process (bridge supervision, CSP, app:// protocol) + preload |
| `universe-agent-ui/client/` | React 19 + zustand + wouter + three.js client |
| `universe-agent-ui/client/tests/e2e/` | Python UI/e2e checks (diagnostics, mock servers, onboarding/settings/sidebar flows) |
| `universe_desktop_bridge/` | FastAPI bridge: goals, projects, chats, MCP, agents, `/api/events` + `/api/voice/stream` WS |
| `observability/events.py` | the async event bus every surface listens to |
| `scripts/voice_loop_test.py` | end-to-end voice verification (wake → STT → goal → spoken reply) |
| `scripts/ui_states_check.py` | per-state UI verification (8 states, DOM + pixel + a11y assertions) |

## Model providers — bring your own key (free tiers included)

Universe has **no built-in model**. Everything around the brain — agents, tools,
consent, memory — is fully real; the answers come from the provider you
connect in Settings → **Providers** (or the mandatory first-run wizard), grouped in the catalog as:

- **Free-tier cloud** — Groq, Google AI Studio (Gemini), OpenRouter (`:free`
  models), Mistral, Cerebras, NVIDIA NIM, Kilo Gateway. A "Get a free key"
  button opens the provider's key page directly.
- **Paid cloud** — OpenAI, Anthropic, xAI, DeepSeek, Together, Fireworks,
  Cohere, Perplexity, HuggingFace.
- **Local servers** — Ollama, LM Studio, vLLM: no key, no internet; the cards
  light up the moment the server is reachable.

Every install is verified with one live request before anything is stored — a
bad key is rejected and installs nothing. Keys are encrypted via the OS
keychain and never sent back to the UI. With **no provider configured**, the
UI says so honestly: the sidebar badge reads `no provider · setup` (rose) and
posting a goal returns an explicit `no_provider` error with the exact way out.

## Verification

Everything below runs against the real stack (no mocks of the orchestrator):

- `pytest tests/` — run in groups: bridge, voice, MCP, events, integration,
  wake+memory+skills, security+whitelist+acceptance, tools+repairs+eval,
  browser+web+thirdparty, plus `test_ui_overhaul.py`
  (projects, chat lifecycle, MCP stdio/HTTP/SSE against a real local server,
  setup gate, agents endpoint)
- `client/tests/e2e/` — the live UI suites (Playwright; vite dev + bridge up):
  - `settings_sections.py` — all six settings sections incl. a REAL stdio
    MCP handshake through the form (`mock_mcp_stdio.py`) and the live memory
    browser (11 systems, 245-skill orbit)
  - `activityfeed_live.py` — status line, plan checklist, tool-card state
    machine, handoffs, skill chips, inline consent, esc-to-interrupt, recap
  - `miniuniverse_live.py` — the 3D mini universe: mount, animation churn
    (pixel-diff), freeze frame, reduced-motion, deep-link
  - `sidebar_workspace.py` — projects + full chat lifecycle (rename/branch/
    pin/archive/unarchive/delete) through the real endpoints
  - `scripts/reset_setup.py` + bridge restart restores the first-run state
    for the onboarding suite
- `python scripts/voice_loop_test.py` — wake word → local STT → goal with
  `via=voice` → consent approve → completion → synthesized spoken reply
  (validates the returned mp3)
- `python scripts/ui_states_check.py` — welcome · running · consent ·
  offline · voice · universe · reduced-motion · a11y/contrast, each with
  DOM/pixel/console assertions; all 8 must pass with zero real console
  errors

## Security posture

- Bridge bound to loopback; CORS limited to `app://universe` + localhost.
- CSP: no remote scripts, `connect-src` limited to the bridge.
- `contextIsolation` + `sandbox` on; preload exposes two IPC calls
  (`openPath`, window controls) and nothing else.
- Every event crossing the wire passes the existing secret filter; consent
  is enforced in the capability gate, not in the UI.

## Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| "backend not found" status screen | `UNIVERSE_ROOT` unset or wrong — point it at the `Universe-compter` checkout |
| Window opens, no goals | bridge died on start — run `python -m universe_desktop_bridge` once in a terminal to see the error |
| Mic button errors "Voice channel unavailable" | voice extras missing — `pip install -e ".[voice]"` |
| "Universe backend is bridge unreachable" banner | the supervised bridge is restarting; if it persists, the port (8738) is taken — free it or set `UNIVERSE_BRIDGE_PORT` |

## License

MIT, same as Universe Agent.
