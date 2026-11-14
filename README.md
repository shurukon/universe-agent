# Universe Agent

```
    ·  ✦  ·     ·  ✦  ·     ·  ✦  ·
         UNIVERSE AGENT
    ✦ ─────────── v1.0.0 ─────────── ✦
      the mind that remembers everything
```

**Universe Agent** is a local-first AI agent platform delivered through three connected work surfaces that share one brain, one memory, and one permission gate:

**Universe · Studio · Bots**

An agent that plans, thinks, spawns worker missions, calls **121** real tools, and keeps every thought, plan, tool call, and artifact on disk — the session transcript (`transcript.jsonl`) is the single source of truth and is restored across restarts, so nothing disappears. It runs on any installed OpenAI-compatible provider (Meta's Muse models are one preset among many) — and **Muse Code** is supported as an optional external runtime for bots.

---

## Providers and optional integrations

The chat and agent path runs through the standard OpenAI-compatible provider chain (`providers/client.py`) against whichever provider you install in **Settings → Providers**. The optional integrations below need their own key and are labelled as such — there is no mock mode anywhere.

| Surface | Model / tool | How it is called |
| --- | --- | --- |
| Chat & agent reasoning | the provider's own models (e.g. Meta `muse-wonder-1.0` · `muse-spark-1.3`) | OpenAI-compatible `/v1/chat/completions` through the provider chain |
| Key verification & model discovery | — | `GET /api/providers/{name}/models` (Settings → *Test Connection*) |
| Voice → text (voice channel + mic) | local faster-whisper, or a cloud STT provider you pick in Settings → Voice | the bridge's transcription path (local ffmpeg converts mic audio to 16 kHz WAV) |
| **Bots runtime (optional)** | **Muse Code** (`muse exec --json`, MSP events) | if Muse Code is installed, bots can run inside it; otherwise they run on Universe's own engine and say so in the transcript |

## Where do I put a provider key?

1. Create a key at your provider's console (e.g. [dev.meta.ai](https://dev.meta.ai) for the Meta Model API).
2. In the desktop app open **Settings → Providers**, install the provider, paste the key and press **Test Connection** — the key is probed live and the live model list comes from `/api/providers/{name}/models`.
3. Alternatively (headless / CI): export it as an environment variable:

```bash
export MODEL_API_KEY="..."   # or META_API_KEY — both are honored (Meta only)
```

**Scope, honestly:** `MODEL_API_KEY` / `META_API_KEY` are read by the Meta **media** endpoints (voice transcription) and are injected into the `muse` CLI for the optional Bots runtime. The **chat** chain does not read them: it resolves keys from the local vault (`providers/manager.py`), so a headless chat run still needs the provider + key installed once in Settings (or via the CLI provider wizard).

Keys are AES-encrypted at rest in the local secrets vault, are never logged, and are never sent anywhere except the provider's own endpoint.

## Muse Code — an optional bots runtime

**Muse Code** is Meta's coding agent. Universe can use it as an external runtime, not an add-on:

- In the **Bots** surface, if Muse Code is not connected, a **Install & connect Muse Code** button finds it on your system, installs it if missing, and connects it automatically.
- When it is connected, each bot runs inside a real `muse exec --json` session in its own workspace; Universe digests the MSP/JSONL event stream so the bot's thinking, tool calls, and reports appear live in the timeline.
- If Muse Code is not installed, bots fall back to Universe's own engine — and say so honestly in the transcript.

## The three work surfaces (one shell toggle)

1. **Universe** — the chat/agent workspace. Session-based engine (`transcript.jsonl` as the single source of truth, forkable sessions, 200-step budget with 85 % compaction, background reflection), slash commands across three layers (CLI → bridge → UI), and a **fully persistent timeline**: thinking, plans, subagents, tool calls, and artifacts are written live to `~/.universe/rings/<turn_id>.jsonl` and restored across sessions — in all three surfaces.
2. **Bots** — persistent task agents in the *one bot, one job* spirit: a live bot screen, the **TASK → SKILL → ROUTINE** lifecycle (run once → save the method as a skill → schedule it daily / every N minutes / cron with timezone), and an **approvals queue** (*Allow once / Always allow / Deny*) that gates risky steps — send · publish · purchase · push — with **no timers and no auto-approval**.
3. **Studio** — a creation workspace managed end-to-end by Universe (clone → install → launch → health-probe), with a **Connect Blender** button that detects Blender on your machine and connects it automatically:
   - **Video** — [AVE](https://github.com/shurukon/AVE), the author's own AI video editor, driven through its MCP server (41 tools).
   - **Design** — [open-design](https://github.com/nexu-io/open-design): websites & presentations, exported to HTML / PDF / JSX.
   - **3D** — Blender via blender-mcp (preview or background rendering).

## Why it is different

- **Local-first by default.** Your code, prompts, memory vault, and audit log stay on your machine; nothing leaves `~/.universe/` unless you connect a remote provider.
- **A memory mapped onto the cosmos.** Five memory tiers — Working (planets+moons), Episodic (asteroids, 7-day retention), Semantic (stars, promoted at gravity ≥ 0.45 idle / ≥ 0.85 real-time), Procedural (skills), Meta (dark matter) — distill every session into what the next one needs. Inspect it any time with `/memory`. **Honest scope:** the Procedural tier ships with an **empty catalog** (the corpus was purged on 2026-10-31 — `config.TRUSTED_SKILL_SOURCES == ()`, `skills/loader.py` is a stub); see [`docs/SKILLS.md`](docs/SKILLS.md).
- **Deep, auditable security.** Every tool call passes through a capability gate; the audit log is append-only; the desktop bridge binds to `127.0.0.1` with bearer-token auth and origin checks; risky bot steps always require explicit human approval.
- **Honest errors.** Send a goal with nothing configured and you get a clear, actionable no-provider error — never a canned answer.

## Quickstart

**Prerequisites:** Python 3.11+, Node 18+, ffmpeg (for media goals), tesseract (for OCR). See [Platform support](#platform-support).

**1 — Core + CLI:**

```bash
pip install -e .
python -m playwright install chromium
universe            # launches the REPL
universe> /setup    # 6-step onboarding wizard
universe> /goal "write a python function to read a csv file"
```

**2 — Desktop app (Universe · Studio · Bots):**

```bash
cd universe-agent-ui
npm install
npm run desktop     # dev mode: vite + electron + supervised Python bridge
npm run build       # production build
```

**3 — Connect Meta:** Settings → Providers → Meta → paste your key → *Test Connection ✓*

A hands-free scripted demo of the same flow: `python walkthrough.py`.

## Platform support

| Platform | Status |
| --- | --- |
| **Linux** | 🟡 **CI-verified** — the non-LLM pytest suite runs on `ubuntu-latest` (`.github/workflows/ci.yml`). The desktop app, Bots and Studio are not covered by an end-to-end Linux run. |
| **Windows** | 🟡 **CI-verified** — the same non-LLM suite runs on `windows-latest`; the computer-use broker (`universe_cua_helper.py`) is Windows-only by design and is exercised by `tests/test_computer_use.py`. |

## Architecture overview

| Module | Role |
| --- | --- |
| `core/` | Single harness agent (`universe`), turn engine, session store, 5-tier memory system. |
| `cli/` | The interactive REPL, slash-command dispatch, onboarding wizard. |
| `providers/` | LLM client + provider/key rotation; the provider catalog includes a **Meta** preset (`api.meta.ai/v1`, Muse Wonder / Muse Spark) alongside OpenAI, Anthropic, xAI and the free tiers. |
| `connectors/` | **121** registered tools grouped by domain: browser, files, shell, git, data, … |
| `universe_desktop_bridge/` | FastAPI bridge serving the Electron UI (bound to `127.0.0.1`). |
| `universe-agent-ui/` | Electron + Vite desktop app: the Universe · Studio · Bots shell. |
| `security/` | Capability gate, keyring-backed secrets (AES), append-only audit log. |
| `channels/` | Remote surfaces (Telegram polling). |
| `skill/`, `schema/`, `storage/` | Skill lifecycle, SQLite migrations, local persistence. |
| `eval/`, `observability/`, `failure/`, `concurrency/`, `context/`, `resources/`, `data/` | Cross-cutting subsystems wired into the orchestrator. |

**Agents:** one agent (`universe`) plans, executes, and verifies with the full toolset (`core/core_agents/agents.py`). The `Agent` tool spawns **worker** missions — unspecialised, sharing the universe toolset — bounded to depth 2, 4 missions per call and 8 per turn (`core/agent/swarm.py`). Every tool call, from the main agent or any mission, passes through the same Capability Gate.

**Sessions are the unit of work and of persistence.** There is no goal engine, no goal row, no planet, no checkpoint. A session owns its container row (`sessions`), its transcript (`~/.universe/sessions/<id>/transcript.jsonl`) and its **durable event log** (`<project>/sessions/<id>/events.jsonl` — every thinking delta, every tool call, every plan row, the terminal event, verbatim). Reopening a session replays that log instead of synthesising a stub, and it takes **milliseconds** (measured: 78 ms for a live restore; well under a second for a 5000-event log). See [`docs/SESSIONS.md`](docs/SESSIONS.md).

## Engineering quality

- **The non-LLM pytest suite is green in CI** — the exact counts are printed by the run (`pytest -m "not llm" -q`) and published as a JUnit artifact; see `.github/workflows/ci.yml`.
- Run the suite: `pytest` (needs `pip install -e .[dev]`).
- Keys are AES-encrypted at rest; provider keys are never logged, never committed.

## Demo

The 90-second walkthrough script lives at [`walkthrough.py`](walkthrough.py) in the repo root.

## Documentation

- [`docs/SECURITY.md`](docs/SECURITY.md) — capability gate, audit log, threat model.
- [`docs/PRIVACY.md`](docs/PRIVACY.md) — what stays local, what is sent to remote providers, how to disable telemetry.
- [`docs/MCP_CLIENT_REWRITE.md`](docs/MCP_CLIENT_REWRITE.md) — design and implementation of the in-process MCP client.
- [`docs/THIRDPARTY_CONNECTORS_BATCH1.md`](docs/THIRDPARTY_CONNECTORS_BATCH1.md) — third-party connector audit.
- [`SOURCES.md`](SOURCES.md) — full per-source attribution and license details.

## Acknowledgments

Universe Agent is built on the work of 18 open-source projects — including open skill registries, Open Design, Browserbase, DuckDB, ClickHouse, Apollo GraphQL, Auth0, Sentry, Brave Search, and more. Each source was verified directly against its upstream LICENSE; see [`SOURCES.md`](SOURCES.md).

## License

MIT © 2026 Universe Agent
