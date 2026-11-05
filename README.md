# Universe Agent

```
    ·  ✦  ·     ·  ✦  ·     ·  ✦  ·
         UNIVERSE AGENT
    ✦ ─────────── v1.0.0 ─────────── ✦
      the mind that remembers everything
```

**Universe Agent** is a local-first AI agent platform powered by **Meta's latest Muse models** (Muse Wonder · Muse Spark) and delivered through three connected work surfaces that share one brain, one memory, and one permission gate:

**Universe · Studio · Bots**

> 🏆 **Meta Global AI Developer Hackathon submission** — Track 1 (Indie Developer) · Theme: *Build for Your World — bring Meta's models into your domain.*

An agent that plans, thinks, spawns subagents, calls 132 real tools, and remembers everything: every thought, plan, tool call, and artifact is written live to disk and restored across sessions — nothing disappears, even after a full restart. It runs end-to-end on **Meta's Model API** — and the bots literally run on **Muse Code**, Meta's own coding agent.

---

## Where Meta's models are used

Every integration below calls the official **Meta Model API** (`https://api.meta.ai/v1`, OpenAI-compatible) or the **Muse Code** CLI. There is no mock mode — the moment your key is configured, everything works.

| Surface | Meta model | How it is called |
| --- | --- | --- |
| Chat & agent reasoning | `muse-wonder-1.0` · `muse-spark-1.3` | OpenAI-compatible `/v1/chat/completions` through the provider chain |
| Key verification & model discovery | — | `GET /v1/models` (Settings → *Test Connection*) |
| Image generation (Studio → Design) | Muse Image `muse-image-1.0` | `POST /v1/images/generations` |
| Voice → text (voice channel + mic) | `muse-voice-transcribe-1.0` | `POST /asr/transcribe` (local ffmpeg converts mic audio to 16 kHz WAV) |
| Media segmentation (Studio) | SAM 3.1 `sam-3.1` | Meta Model API media segmentation |
| **Bots runtime** | **Muse Code** (`muse exec --json`, MSP events) | Bots are **built on Muse Code**: Universe spawns real `muse` sessions in each bot's workspace and digests the official JSONL event stream |

## Where do I put the Meta API key?

1. Create a key at [dev.meta.ai](https://dev.meta.ai) (Meta Model API → *Generate API key*).
2. In the desktop app open **Settings → Providers → Meta**, paste the key, press **Test Connection** — it lists the live Muse models from `GET /v1/models`.
3. Alternatively (headless / CI): export it as an environment variable:

```bash
export MODEL_API_KEY="..."   # or META_API_KEY — both are honored
```

The key is AES-encrypted at rest in the local secrets vault, is never logged, and is never sent anywhere except `api.meta.ai`.

## Muse Code — the bots runtime

**Muse Code** is Meta's coding agent, and Universe treats it as a first-class runtime, not an add-on:

- In the **Bots** surface, if Muse Code is not connected, a **Install & connect Muse Code** button finds it on your system, installs it if missing, and connects it automatically.
- Each bot runs inside a real `muse exec --json` session in its own workspace; Universe digests the MSP/JSONL event stream so the bot's thinking, tool calls, and reports appear live in the timeline.
- If Muse Code is not installed, bots fall back to Universe's own engine — and say so honestly in the transcript.

## The three work surfaces (one shell toggle)

1. **Universe** — the chat/agent workspace. Session-based engine (`transcript.jsonl` as the single source of truth, forkable sessions, 200-step budget with 85 % compaction, background reflection), slash commands across three layers (CLI → bridge → UI), and a **fully persistent timeline**: thinking, plans, subagents, tool calls, and artifacts are written live to `~/.universe/rings/<turn_id>.jsonl` and restored across sessions — in all three surfaces.
2. **Bots** — persistent task agents in the *one bot, one job* spirit: a live bot screen, the **TASK → SKILL → ROUTINE** lifecycle (run once → save the method as a skill → schedule it daily / every N minutes / cron with timezone), and an **approvals queue** (*Allow once / Always allow / Deny*) that gates risky steps — send · publish · purchase · push — with **no timers and no auto-approval**.
3. **Studio** — a creation workspace managed end-to-end by Universe (clone → install → launch → health-probe), with a **Connect Blender** button that detects Blender on your machine and connects it automatically:
   - **Video** — [AVE](https://github.com/shurukon/AVE), the author's own AI video editor, driven through its MCP server (41 tools).
   - **Design** — [open-design](https://github.com/nexu-io/open-design): websites & presentations, exported to HTML / PDF / JSX, with inline **Muse Image** generation.
   - **3D** — Blender via blender-mcp (preview or background rendering).

## Why it is different

- **Local-first by default.** Your code, prompts, memory vault, and audit log stay on your machine; nothing leaves `~/.universe/` unless you connect a remote provider.
- **A memory mapped onto the cosmos.** Five memory tiers — Working (planets+moons), Episodic (asteroids, 7-day retention), Semantic (stars, gravity ≥ 0.85), Procedural (skills), Meta (dark matter) — distill every session into what the next one needs. Inspect it any time with `/memory`.
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
| **Linux** | ✅ **Verified and tested** — core, bridge, desktop app, Bots, and Studio all run on Linux. |
| **Windows** | ⚠️ **Not tested.** The project has not been validated on Windows yet. |

## Architecture overview

| Module | Role |
| --- | --- |
| `core/` | Single harness agent (`universe`), turn engine, session store, 5-tier memory system. |
| `cli/` | The interactive REPL, slash-command dispatch, onboarding wizard. |
| `providers/` | LLM client + provider/key rotation; the **Meta provider** (`api.meta.ai/v1`, Muse Wonder / Muse Spark / Muse Image / Muse Voice Transcribe / SAM 3.1). |
| `connectors/` | 132 registered tools grouped by domain: browser, files, shell, git, data, … |
| `universe_desktop_bridge/` | FastAPI bridge serving the Electron UI (bound to `127.0.0.1`). |
| `universe-agent-ui/` | Electron + Vite desktop app: the Universe · Studio · Bots shell. |
| `security/` | Capability gate, keyring-backed secrets (AES), append-only audit log. |
| `channels/` | Remote surfaces (Telegram polling). |
| `skill/`, `schema/`, `storage/` | Skill lifecycle, SQLite migrations, local persistence. |
| `eval/`, `observability/`, `failure/`, `concurrency/`, `context/`, `resources/`, `data/` | Cross-cutting subsystems wired into the orchestrator. |

**Agents:** one main agent (`universe`) plans, executes, and verifies with the full toolset; specialist capabilities live on as bounded swarm subagents (research / code / review / security / design) spawned through the `Agent` tool — depth 2, up to 8 missions per goal. Every tool call, from the main agent or any subagent, passes through the same Capability Gate.

## Engineering quality

- **609 tests passing / 0 failing** (pytest, 41 skipped) — plus green Electron + Vite builds and a live bridge smoke (`scripts/bridge_smoke_p0.py`).
- Run the suite: `pytest` (needs `pip install -e .[dev]`).
- Keys are AES-encrypted at rest; the Meta key is never logged, never committed.

## Demo

The 90-second walkthrough script (and the full submission details) live in [`docs/SUBMISSION_META_HACKATHON.md`](docs/SUBMISSION_META_HACKATHON.md).

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
