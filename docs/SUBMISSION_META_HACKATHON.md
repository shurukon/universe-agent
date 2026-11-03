# Universe Agent — Meta Global AI Developer Hackathon Submission

**Track:** Indie Developer (Track 1) · **Theme:** Build for Your World —
"Bring Meta's models into your domain"

## Tagline

**Universe Agent** — a local-first desktop agent whose brain is mapped onto
the cosmos: eight domains (Stars · Blackholes · Orbits · Galaxies · Nebula ·
Dust · Probes · Planet) around one center — the model itself — which decides,
informed by the sessions (Dust) it keeps.

## What Meta models power (the real integration)

Every integration below calls the official **Meta Model API**
(`https://api.meta.ai/v1`, `Authorization: Bearer $MODEL_API_KEY`) or the
**Muse Code** CLI. There is no mock mode anywhere — the moment the user
pastes their key in *Settings → Providers → Meta*, everything works.

| Surface | Meta model | Endpoint / runtime |
| --- | --- | --- |
| Chat & agent reasoning | `muse-wonder-1.0`, `muse-spark-1.3` | OpenAI-compatible `/v1/chat/completions` via the provider chain |
| Key verification & model discovery | — | `GET /v1/models` (Settings → Test Connection) |
| Image generation (Studio → Design) | Muse Image `muse-image-1.0` | `POST /v1/images/generations` |
| Voice to text (voice channel + mic) | `muse-voice-transcribe-1.0` | `POST /asr/transcribe` (one-shot; local ffmpeg converts mic audio to 16 kHz WAV) |
| Segmentation (Studio media) | SAM 3.1 (`sam-3.1`) | Meta Model API media segmentation |
| **Bots runtime** | **Muse Code** (`muse exec --json`, MSP events) | Bots are built **on Muse Code** — Universe spawns real muse sessions in each bot's workspace and digests the official JSONL event stream |

## The three work surfaces (one shell toggle: Universe · Studio · Bots)

1. **Universe** — the chat/agent workspace: session-based engine
   (`transcript.jsonl` as the single source of truth, forkable sessions,
   200-step budget, 85 % compaction, background reflection), slash commands
   across three layers (CLI → bridge → UI), live side panel.
2. **Bots** — persistent task agents in the Grok-Bot spirit, adapted to
   Universe: *one bot, one job* (name + description holding job/sources/
   boundaries), a live **bot screen** panel, the **TASK → SKILL → ROUTINE**
   lifecycle (run once → save the method as a skill → schedule it daily /
   every N minutes / cron with timezone), and an **approvals queue**
   (Allow once / Always allow / Deny) that gates risky steps (send · publish
   · purchase · push). Runs on **Muse Code** when installed, else on
   Universe's own engine — and says so honestly in the transcript.
3. **Studio** — a creation workspace with three real tools, managed by
   Universe (clone → install → launch → health-probe):
   - **Video** — [AVE](https://github.com/shurukon/AVE), the author's own AI
     video editor (timeline, effects, export) driven through its MCP server
     (41 tools).
   - **Design** — [open-design](https://github.com/nexu-io/open-design):
     websites & presentations, exported to HTML / PDF / JSX.
   - **3D** — Blender via blender-mcp (preview or background rendering).
   Plus **Muse Image** generation inline in the Design panel.
4. **Nebula** (Memory) — an Obsidian-like markdown vault: plain `.md` files
   with `[[wikilinks]]`, computed backlinks, a constellation graph, and
   **Dust → Stars distillation** that condenses a session transcript into a
   linked memory note (LLM summary when a key is configured, honest raw
   excerpt otherwise).

## Engineering quality (verified in this repo)

- **612 tests passing / 0 failing** (pytest), plus Electron + Vite builds
  green and a live bridge smoke (`scripts/bridge_smoke_p0.py`).
- Keys are AES-encrypted at rest via the existing secrets vault; the Meta
  key is never logged, never committed.
- The bridge binds to 127.0.0.1 with bearer-token auth and origin checks;
  the bots' risky-step gate works with zero configuration.

## 90-second demo script

1. **0:00–0:08** — The compass logo over black. "This is Universe. Its brain
   has eight domains — and at the center, the model itself decides."
2. **0:08–0:22** — Universe chat: a real task run against
   `muse-wonder-1.0` (Settings shows *Meta · Test Connection ✓* with the
   live model list from `GET /v1/models`).
3. **0:22–0:45** — Toggle to **Bots** → create "Blog Pulse — prepare a daily
   review of the site" → run it once → the bot screen streams its work →
   *Save as skill* `/daily-blog-pulse` → schedule *every day at 05:00
   Europe/Rome* → a risky step pops the approval banner → *Deny*.
4. **0:45–1:10** — Toggle to **Studio** → pick **Video** (AVE launches and
   embeds; Universe edits the timeline through MCP) → **Design**: a Muse
   Image generated inside the panel → **3D**: Blender renders in the
   background.
5. **1:10–1:25** — Toggle to **Nebula** → *Distill session* → the transcript
   becomes a linked note; the constellation graph grows.
6. **1:25–1:30** — The compass again. "Universe — eight domains, one center:
   Meta's models."

## Application-form short answers

**What does it do?** Universe Agent is a desktop agent with a cosmos-mapped
brain: sessions (Dust) are distilled by the center into memory (Nebula),
skills (Orbits) and knowledge (Stars), while bots — built on Meta's Muse Code
— run real jobs on a schedule with human approvals, and a studio turns chat
into video, design and 3D.

**Why Meta's models?** Meta is not a bolt-on provider here: `muse-wonder` /
`muse-spark` reason through the agent core, Muse Image draws inside Studio,
Muse Voice Transcribe powers the mic and voice channel, SAM 3.1 segments
media, and **Muse Code is the bots runtime itself** — the bots literally run
on Meta's agent.
