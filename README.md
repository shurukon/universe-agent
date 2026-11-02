# Universe Agent

```
    ·  ✦  ·     ·  ✦  ·     ·  ✦  ·
         UNIVERSE AGENT
    ✦ ─────────── v1.0.0 ─────────── ✦
      the mind that remembers everything
```

An open-source local-first AI agent harness with a single **universe** agent that executes real tools — 0 bundled skills (corpus purged 2026-10-31 per user), procedural memory only — powered by any real LLM you connect: a free-tier cloud key, a paid provider, or a fully local server.

## What is Universe Agent?

Universe Agent is a CLI-resident **AI agent harness**: one agent named `universe` owns every tool and every skill, turns plain-English messages into executed work inside durable sessions, and spawns specialist subagents (research/code/review/security/design) when a task benefits from focus. Sessions are file-backed (`sess_*`) with slash commands (`/new`, `/resume`, `/fork`, `/rewind`, `/compact`) — the full ZCode + Claude Code contract. Every
artifact, decision, and failure is written to a 5-tier memory (Working →
Episodic → Semantic → Procedural → Meta) so the next session picks up where
the last one left off. There is **no built-in model**: Universe answers with the
provider you connect in Settings → Model provider — a free-tier key (Groq,
Google AI Studio, OpenRouter, Mistral, Cerebras, NVIDIA NIM), a paid one
(OpenAI, Anthropic, xAI, DeepSeek, Together, Fireworks, Cohere, Perplexity,
HuggingFace), or a local OpenAI-compatible server (Ollama, LM Studio, vLLM)
that needs no key and no internet. Sending a goal with nothing configured
returns a clear, actionable no-provider error — never a canned answer.

## Why?

- **Local-first by default.** Your code, prompts, memory vault, and audit log
  stay on your machine. No data leaves `~/.universe/` unless you explicitly add
  a remote provider.
- **Deep, auditable security.** Every tool call passes through a capability
  gate, the audit log is append-only, and the Windows sandbox is described in
  `docs/WINDOWS_SANDBOX.md`. OAuth2 flows are not implemented — only
  pre-obtained tokens are accepted.
- **Procedural memory only.** 0 bundled skills after purge (2026-10-31) — the agent operates with tools + learned Episodic/Semantic memory, no curated skill corpus.

## Installation

```bash
pip install -e .
python -m playwright install chromium
```

System tools used at runtime (install only what you need):

- `ffmpeg` — used by the media-production goal pipeline (`core/media_enforcer.py`), which drives ffmpeg/ffprobe locally for video/audio production goals
- `tesseract` — for the `ImageOcr` tool

Optional extras:

```bash
pip install -e .[composio]   # Composio connector
pip install -e .[telegram]   # Telegram channel
pip install -e .[tree-sitter] # deeper code parsing
pip install -e .[dev]        # pytest, pytest-asyncio, pytest-cov
```

## Quickstart (5 minutes)

```bash
universe                  # launches the REPL
universe> /setup          # 6-step onboarding wizard
universe> /goal "write a python function to read a csv file"
```

For a hands-free, scripted run of the exact same flow (great for demos and
CI), use the walkthrough:

```bash
python walkthrough.py
```

It drives `/setup` with sample inputs, runs the CSV goal, then prints
`/memory` and `/version` so you can see what was learned and what version you
are on.

## Desktop App

Prefer a window over a terminal? The same core ships with an Electron desktop
app — the thinking indicator is a live, miniaturized version of your own
memory universe:

```bash
cd universe-agent-ui
npm install
npm run desktop        # dev mode: vite + electron + supervised Python bridge
npm run dist           # or: build the Windows NSIS installer
```

Goals, the 3D memory universe (11 solar systems, live skill-star counts), consent
prompts, and the full voice channel (custom wake word — e.g. *"wake up L"* —
local faster-whisper STT, free edge-tts speech) all run against this same
core, 100% locally. See [universe-agent-ui/README.md](universe-agent-ui/README.md).

## Architecture Overview

| Module        | Role                                                                 |
| ------------- | -------------------------------------------------------------------- |
| `core/`       | Single harness agent (`universe`), turn engine, session store, and the 5-tier memory system. |
| `cli/`        | The interactive REPL, slash-command dispatch, and onboarding wizard. |
| `providers/`  | LLM client + provider/key rotation + the curated catalog (free / paid / local). |
| `connectors/` | 132 registered tools grouped by domain: browser, files, shell, git, data, etc. |
| `schema/`     | SQLite migrations; `storage/` is the local persistence layer.        |
| `security/`   | Capability gate, keyring-backed secrets, audit log.                 |
| `channels/`   | Remote surfaces (Telegram polling; no WebSocket bridge).             |
| `skill/`      | Skill loader, confidence lifecycle, trusted-sources allowlist.       |
| `eval/`, `observability/`, `failure/`, `concurrency/`, `context/`, `resources/`, `data/` | Cross-cutting subsystems wired into the orchestrator. |

For deep dives see `docs/SECURITY.md`, `docs/MCP_CLIENT_REWRITE.md`, and
`docs/THIRDPARTY_CONNECTORS_BATCH1.md`.

## The Agents

Universe runs a **single agent `universe`** that plans, executes and verifies on its own, with the full toolset in its hands. Specialist capabilities live on
as bounded **swarm subagents** (research/code/review/security/design),
spawned on demand through the `Agent` tool — depth 2, up to 8 missions per
goal, 600s per mission. Every tool call, from the general agent or any
subagent, passes through the same Capability Gate.

| Capability | How it runs                                                     |
| ---------- | ---------------------------------------------------------------- |
| universe   | The main loop: message → session history → tools → verify.       |
| Research   | Swarm subagent — web + knowledge-base gathering.                 |
| Code       | Swarm subagent — writes/edits/runs code.                         |
| Review     | Swarm subagent — critiques prior output.                          |
| Security   | Swarm subagent — audits changes and secrets.                     |
| Design     | Swarm subagent — UI/UX artifacts and visual notes.               |

## The 5 Memory Tiers

- **L1 Working** — Planets + Moons. The live scratchpad of the current goal.
- **L2 Episodic** — Asteroids. Time-stamped events with a 7-day minimum
  retention; promoted or discarded by the Subconscious Loop.
- **L3 Semantic** — Stars. Distilled, high-confidence knowledge that survives
  across sessions (gravity score ≥ 0.85).
- **L4 Procedural** — Skills. Reusable how-to bundles loaded into the
  context budget on demand.
- **L5 Meta** — Dark matter. Self-model, calibration, and cross-agent
  statistics.

Use `/memory` to inspect every tier at once. Sessions persist on disk — `/resume sess_*` to continue.

## Key Slash Commands

| Command         | What it does                                                |
| --------------- | ----------------------------------------------------------- |
| `/new`          | Start a fresh session.                                      |
| `/resume`       | Resume a previous session by id.                            |
| `/fork`         | Branch the current session into a new one.                  |
| `/compact`      | Summarize an overgrown transcript.                          |
| `/setup`        | First-run onboarding (language, provider, identity).        |
| `/memory`       | Overview of all 5 memory tiers and recent activity.         |
| `/agents`       | List and manage Core + Moon agents.                         |
| `/skills`       | Browse, pin, and quarantine skills.                         |
| `/providers`    | Add or switch LLM providers; configure per-agent models.    |
| `/audit`        | Inspect the append-only audit log.                          |
| `/eval`         | Run the v1 smoke-test suite.                                |
| `/migrate`      | Apply pending SQLite schema migrations.                    |
| `/version`      | Show Universe version and current schema version.             |
| `/export`       | Snapshot the full Universe state to a portable archive.       |
| `/import`       | Restore from a previous export.                             |
| `/forget-all`   | Wipe memory (double confirmation required).                 |

Run `/help` or `/help <command>` for the full reference. Custom commands in `~/.zcode/commands` and workspace `.zcode/commands` are also listed.

## Documentation

- [`docs/SECURITY.md`](docs/SECURITY.md) — Capability gate, audit log,
  threat model.
- [`docs/PRIVACY.md`](docs/PRIVACY.md) — What stays local, what is sent to
  remote providers, and how to disable telemetry.
- [`docs/SKILLS_EXPANSION.md`](docs/SKILLS_EXPANSION.md) — Per-source
  license verification for the 18 trusted skill sources.
- [`docs/MCP_CLIENT_REWRITE.md`](docs/MCP_CLIENT_REWRITE.md) — Design and
  implementation of the in-process MCP client.
- [`docs/WINDOWS_SANDBOX.md`](docs/WINDOWS_SANDBOX.md) — Windows-specific
  process isolation story.
- [`docs/THIRDPARTY_CONNECTORS_BATCH1.md`](docs/THIRDPARTY_CONNECTORS_BATCH1.md) —
  Third-party connector audit (Composio, etc.).
- [`SOURCES.md`](SOURCES.md) — Full per-source attribution and license
  details for the bundled skills.

## Acknowledgments

Universe Agent is built on the work of 18 open-source projects, including
(but not limited to) Everything Claude Code, Anthropic's official skills,
Open Design, Browserbase, DuckDB, ClickHouse, Apollo GraphQL, Auth0, Sentry,
Brave Search, UnitOne Security, BagelHole DevOps Security, Alireza Rezvani's
Claude Skills, Google Workspace CLI, Corey Haines Marketing Skills, and
UI UX Pro Max. Each source was verified directly against its upstream
LICENSE; see `SOURCES.md` for the full list, deliberate exclusions, and
licensing rationale.

## License

MIT © 2026 Universe Agent
