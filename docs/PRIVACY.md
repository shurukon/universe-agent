# Privacy and Data Ownership

Universe Agent v1 is local-first. Your data lives on your machine, under your
control, in formats you can read with tools you already own. This document
explains exactly what Universe stores, where, who can see it, and how to take it
with you or delete it. It is the user-facing companion to section 17 of the
Universe Agent v1 Final specification.

Universe is MIT-licensed open source. In the Desktop edition, there is no Universe
server. Nothing described here depends on a hosted backend.

---

## 1. Local-First Guarantee

Everything Universe writes lives under `~/.universe/` by default. Concretely:

```
~/.universe/
  universe.db             # SQLite, WAL mode
  memory_vault/         # Plain Markdown, with YAML frontmatter
  orbits/               # Per-orbit (solar-system) profiles
  skills/               # Custom + active skills
  connectors/           # Connector configs (no secrets)
  audit.log             # Append-only JSONL
  checkpoints/          # Per-session resumable state
  maps/                 # Codebase maps (per-project, cached)
```

- No network call happens unless you explicitly enable a connector.
- `GAX_OFFLINE=1` restricts LLM routing to local (loopback) providers only —
  Ollama, LM Studio, vLLM. A remote provider configured in the chain is
  dropped while the flag is set, so an LLM call physically cannot leave the
  machine. Tool-driven network calls (web tools, browsers, shell) are NOT
  blocked by this flag — it governs LLM provider routing.
- There is no telemetry to any Universe server, because in the Desktop edition
  there is no Universe server.
- No anonymous usage stats are collected unless you explicitly opt in via
  `/profile`.

This is a deliberate design choice, not a configuration default. The same
discipline that says "no cloud sync, no mobile client, no hosted backend"
(section 21 of the spec) is what makes the local-first guarantee credible: the
code path that would phone home does not exist.

---

## 2. Export

`/export` produces a portable archive:

```
universe-export-<timestamp>.zip
  memory_vault/         # All Markdown, with frontmatter
  orbits/               # User profile and per-orbit settings
  skills/               # Custom + active skills (Quarantine skills excluded by default)
  connectors/           # Connector configs (NO secrets)
  audit.log             # Last 30 days only
  eval-history.json
  MANIFEST.yaml         # Versions, schema version, SHA-256 integrity line
```

Key points:

- **Secrets are never exported.** The `connectors/` directory contains
  configuration only (names, base URLs, scopes), not credentials. Re-importing
  on a new machine requires re-adding API keys via `/provider add`.
- **The audit log is capped at 30 days** in the export, even though it retains
  90 days locally. The export is a portable snapshot, not a forensic copy.
- **Quarantine skills are excluded by default.** They have not been vetted, so
  they do not travel with the export. Re-importing requires re-approving them
  individually.
- **MANIFEST.yaml** includes a SHA-256 integrity line for the archive itself,
  so you can verify after transfer that nothing was corrupted or tampered with
  in transit.

Example manifest:

```yaml
universe_version: 1.0.0
schema_version: 8
exported_at: 2026-06-16T22:38:39Z
owner_client_id: 0192b7c1-...
contents:
  - memory_vault/
  - orbits/
  - skills/
  - connectors/
  - audit.log
  - eval-history.json
integrity:
  sha256: 9f2c1e8d...e4a7
```

---

## 3. Import and Forget

### Import

`/import <archive>` performs the following steps in order:

1. Reads `MANIFEST.yaml` and verifies the archive's SHA-256 integrity line.
2. Validates the schema version against the running Universe instance and runs
   any needed forward-only migrations (see section 20 of the spec).
3. Restores state: `memory_vault/`, `orbits/`, `skills/`, `connectors/`,
   `audit.log`, `eval-history.json`.
4. Imported files and rows REPLACE their local counterparts outright
   (keep-imported). There is no interactive merge-strategy prompt and no
   three-way merge — back up `~/.universe` first if the current state matters.
5. Reports a summary of exactly what was replaced (vault files, skill files,
   orbit and connector row counts) and what needs manual attention
   (re-adding API keys, re-approving quarantined skills).

### Forget

- `/forget <id>` removes a single memory entry, or, if the `id` is a solar
  system's identifier, the entire solar system's worth of content.
- `/forget-all` is a double-confirmed nuclear option. It wipes `~/.universe/`
  entirely and re-runs the initial setup wizard. There is no undo; the
  audit log is included in the wipe.

`/forget` operates on memory only. Real-world side effects that an agent
already executed on your behalf (files written, emails sent, connectors
called) are not rolled back; that is not generally possible. Universe always
tells you what was actually done, so you can clean up manually if needed.

---

## 4. Portability

### Markdown-first memory

`memory_vault/` is plain Markdown with YAML frontmatter. It opens directly in
Obsidian, Logseq, VS Code, or any text editor. A typical entry looks like:

```markdown
---
id: star-0192b7c1-abcd-7123-...
title: "How we handled the provider rate-limit error"
type: star
layer: L3
privacy_tier: Personal
owner_client_id: 0192b7c1-...
created_at: 2026-06-16T22:38:39Z
tags: [python, retry, providers]
schema_version: 8
---

While debugging the multi-key rotation logic, we found that a 429 from the
provider wasn't being matched against the right error kind, so the reactive
rotation to the next key wasn't firing...
```

### Portable across machines

`~/.universe/` itself is portable across machines. Copy the directory (or use
`/export` and `/import`) to move between a work laptop and a personal
machine. The `owner_client_id` field on every record ensures that data from
different owners does not contaminate each other when imported (see section 6
below).

### Documented schema

The SQLite schema is documented and stable across a schema version. A
technically inclined user can query their own memory directly:

```bash
sqlite3 ~/.universe/universe.db "SELECT id, title, created_at FROM stars
                              WHERE tags LIKE '%python%' ORDER BY created_at DESC LIMIT 10;"
```

The full set of tables and columns is documented in the schema migration files
under `schema/`. Unknown fields in imported Markdown are ignored; missing
fields default sensibly, so hand-editing the vault will not break Universe.

---

## 5. Privacy Tiers

Every solar system (an "orbit") carries a privacy level. The level controls
how content in that orbit is treated by export, sync (when present in future
editions), and the Subconscious Loop's promotion path.

| Tier | Behavior |
|---|---|
| **Public** | Safe to export and safe to commit to a shared repository. Included in every export. |
| **Personal** | Exported but not meant to be shared. Included in `/export` archives; the user is responsible for not committing the archive itself. |
| **Sensitive** | Excluded from export by default. Lives only on this machine. The Subconscious Loop will not promote content from this tier to L3 stars that participate in cross-orbit search. |
| **Ephemeral** | Kept in L2 (episodic) only. Never promoted to L3. Deleted automatically after 7 days. |

Set the tier via `/profile` -> Privacy for the default, or per solar system
via `/orbit`. The tier is stored in frontmatter, so it travels with the
content if you copy a single Markdown file out of the vault by hand.

---

## 6. Deletion by Owner

Every Star, Asteroid, and audit entry carries an `owner_client_id`. This is
the session identifier of the Universe install that originally created the
record, not a remote account. Its purpose is cross-machine hygiene:

- A user running Universe on both a work machine and a personal one can export
  from one and import into the other without cross-contamination: imported
  records retain their original `owner_client_id`, so they are
  distinguishable from records created locally.
- `/forget-all` cleanly wipes everything tied to one owner, even on a machine
  that has accumulated records from multiple imports.
- Audit-log entries carry the same field, so forensic inspection (`/audit`)
  can distinguish "I did this" from "I imported this from elsewhere."

---

## 7. What Goes to Which LLM Provider

When you send a session that uses a remote LLM provider (OpenAI, Anthropic,
Google, OpenRouter, Groq, Together, NVIDIA NIM, KiloCode, etc.), the
following data leaves your machine as part of the request:

- The system prompt for the active agent.
- The conversation history for the current session (recent turns, summarized
  older turns per the context-window strategy in section 13 of the spec).
- Tool results that the agent has chosen to include in context.
- Any file contents the agent has read into context (subject to the 50K
  per-file pagination limit and the file-selection policy).

The following data does NOT leave your machine as part of an LLM call:

- Your API keys (sent only in the `Authorization` header, never in the body).
- Audit log contents.
- Memory vault entries that are not actively included in context for this
  session.
- Connector credentials.
- Records marked **Sensitive** or **Ephemeral** are not promoted into
  cross-orbit search, which means they will not be retrieved into context for
  an unrelated session even by accident.

### Local Mode for sensitive goals

For goals that involve confidential code, personal documents, or anything you
do not want to send to any external provider, switch the relevant orbit to
Local Mode via `/profile` -> Security. In Local Mode, all LLM calls route to a
local model (Ollama, LM Studio, vLLM) running on your own hardware. `GAX_OFFLINE=1`
applies this globally.

Local Mode is slower and the models are smaller, but the data path is
entirely on your machine. Universe is designed so that the same agent code,
skills, and tools work in either mode; the routing decision is the only
difference.

---

## 7b. Voice Data (m5)

- The voice channel is **off by default** (`voice_enabled: False`).
- Mic audio is VAD-gated locally; wake-word matching and (by default)
  speech-to-text run on your machine via faster-whisper (int8 CPU).
  Transcription follows the STT language in Settings (e.g. Arabic
  installs transcribe Arabic) — no audio leaves the machine in this mode.
- If you switch STT/TTS to a cloud provider, that provider receives the
  audio/text under YOUR key, like any other provider call (section 7).
  The bridge preflights this: the voice hello reports `stt_ready` /
  `tts_ready` so the UI never shows an armed mic for a backend that
  will fail.
- Spoken goals are goals: their text, steps, and artifacts are stored like
  any typed session (section 1) and subject to the same tiers and deletion.

---

## 8. Your Rights, in Plain Terms

- **You own your data.** It is on your disk, in formats you can read.
- **You can export it.** `/export` produces a portable archive at any time.
- **You can import it elsewhere.** `/import` restores it on any compatible
  Universe install.
- **You can delete it.** `/forget <id>` removes one entry or one orbit;
  `/forget-all` wipes everything.
- **You can inspect it.** The vault is Markdown; the database is SQLite; the
  audit log is JSONL. No proprietary formats, no lock-in.
- **You can audit what Universe did.** `/audit`, `/trace <goal_id>`,
  and `/debug --llm` give you visibility into every action and every LLM call
  (metadata only, never prompt content).

If a feature in this document does not match what you observe in a running
Universe install, the running install is the source of truth for behavior; this
document is the source of truth for intent. Please file an issue if the two
diverge.
