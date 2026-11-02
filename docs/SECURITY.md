# Security Guide

Universe Agent v1 ships with a real security model from day one, not as a
later patch. This document explains what Universe protects you against, how those
protections are implemented, and how to configure them for your own threat
model. It is a user-facing companion to the architecture document; for the
authoritative engineering reference, see section 10 of the Universe Agent v1
Final specification.

Universe is MIT-licensed open source. Everything described here runs on your own
machine; there is no Universe-hosted backend involved in the Desktop edition.

---

## 1. Threat Model

Universe treats twelve specific threats as first-class concerns. The table below
lists each one, its severity, and the defense that ships in v1.

| Threat | Severity | Defense |
|---|---|---|
| Prompt injection via web/file content | High | All retrieved content is treated as data, never as instructions. It is tagged in context as `[UNTRUSTED: from web_search]`, and the system prompt explicitly forbids following any instruction found inside tagged content. |
| Secret leakage in logs | High | An output filter scrubs `sk-*`, `sk-ant-*`, `ghp_*`, `xox[baprs]-*`, `AIza...`, JWT-shaped strings, `Bearer ...` tokens, and `password=...` patterns before anything is logged or displayed. Entropy-based detection catches unknown secret shapes. |
| Exfiltration via tool call | High | SSRF guard on `HttpClient`/web tools: loopback, private, link-local and reserved targets are rejected before the request and on every redirect (§8). Not a general default-deny egress allowlist — browser tools can reach any http/https host behind consent until browser-level egress filtering lands; run Universe behind an egress firewall if your threat model needs one. |
| Runaway tool loops | High | Cascading-failure protection pauses and asks the user after repeated similar failures, rather than retrying indefinitely. |
| Malicious skill execution | High | Skill signing (SHA-256 at ingest). Community/unsigned skills are quarantined. Every tool call is capability-scoped through the Capability Gate. |
| Malicious MCP server | Medium | Capability manifest required. Egress filtering. Read-only filesystem by default for untrusted servers. |
| Local file exfiltration | Medium | File operations are scoped to the active project plus an explicit path allowlist. |
| Privilege escalation via shell | High | `Bash` prompts on first use per session. Long-running shell sessions are killed at 5 minutes. |
| Supply chain (compromised skill source) | Medium | Pinned versions. The Subconscious Loop re-verifies signatures weekly. |
| LLM provider exposure of user data | Medium | Clear documentation on what data goes to which provider. Local Mode (Ollama) is available for sensitive goals. |
| Local privilege escalation | Medium | Universe runs as the user and never requests `sudo`. If a task would need elevated privileges, Universe stops and explains why instead. |

---

## 2. Trust Boundaries

Every arrow in the diagram below is a trust boundary. The Capability Gate is
the single chokepoint through which every tool call must pass. The Audit Log is
append-only; the Subconscious Loop operates alongside the active session but never
bypasses the gate.

```
[User]  <-->  [Orchestrator]  <-->  [Agent]  <-->  [Capability Gate]  <-->  [Tool]  <-->  [Resource]
                  |                              |
                  v                              v
              [Audit Log]               [Subconscious Loop]
```

- The **User** issues goals and approves first-use prompts.
- The **Orchestrator** classifies, routes, and supervises agents. It owns the
  event loop.
- The **Agent** (Code, Research, Write, Review, etc.) executes work and
  requests tool calls.
- The **Capability Gate** checks each requested tool call against the active
  policy table, the user's overrides, and the trust tier of the requesting
  skill. A denied call never reaches the Tool layer.
- The **Tool** performs the actual side effect (file read, shell exec, HTTP
  request, etc.).
- The **Resource** is the file, process, network endpoint, or external API
  that the tool ultimately touches.
- The **Audit Log** records every gate decision and tool result as metadata
  only.
- The **Subconscious Loop** runs in the background (yielding entirely to any
  active user session) and is responsible for periodic tasks such as re-verifying
  skill signatures and decaying idle-skill confidence.

---

## 2b. Desktop Bridge Authentication (m1; enforced on every HTTP+WS route)

The FastAPI bridge binds 127.0.0.1 only AND requires a bearer token the
Electron main process generates at startup and hands to the bridge via env;
the renderer reads it through `bridgeInfo()` in preload — never baked into
the client bundle. The middleware additionally checks Origin/Host
(`app://universe` and localhost). No token or foreign origin → 401/403,
fail-closed. The 3D universe's `GET /api/memory` uses the same token
(`loadLiveMemory()`), falling back to the static snapshot when unreachable.

## 3. Secret Management

### Storage

User API keys and connector tokens are encrypted with **AES-256-GCM**. The
encryption key is derived (scrypt) from a master passphrase resolved in this
order:

1. **`UNIVERSE_PASSPHRASE` env var** — for servers/CI where the operator
   controls the environment.
2. **OS keychain** (preferred on desktops). On the first secret ever stored,
   Universe generates a random master passphrase and saves it via the `keyring`
   Python library (macOS Keychain, Windows Credential Manager / DPAPI, GNOME
   Keyring, KWallet). The write is round-trip-verified before being trusted.
3. **Machine-derived fallback** (degraded mode). When no usable keychain
   backend exists, the passphrase is derived from a stable machine-local
   value. Secrets stay encrypted at rest, but any local process that can
   read both the home directory string and `universe.db` can derive the key —
   treat this as obfuscation, not protection, on such hosts.

The fallback exists on purpose: most headless Linux servers and containers do
not run a keychain daemon, and refusing to start there would break the
Server edition. Building the fallback into v1 means the same code path is
exercised on day one.

Voice STT/TTS keys are AES-encrypted in `config.json` the same way (stored as
blobs; decrypted only in-process). MCP server auth headers are the one
remaining plaintext-at-rest surface (the `mcp_servers` table) — acceptable
only because adding an MCP server is an explicit-consent action.

### Rules

- A key is never pasted into chat. `/provider add` is the only path in.
- LLM API keys in request headers are never logged. They are stripped at the
  log boundary by the secret filter described below.
- `/provider keys` can rotate keys without restarting Universe.
- Exported archives (`/export`) never contain secrets. Re-importing on a new
  machine requires re-adding API keys.

---

## 4. Output Secret Filter

Pattern-matched redaction is applied at every log boundary and at the display
boundary. The filtered output is what gets logged AND what gets shown to the
user. The default pattern set is:

```python
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",                                          # OpenAI / most providers
    r"sk-ant-[A-Za-z0-9-]{20,}",                                     # Anthropic
    r"ghp_[A-Za-z0-9]{30,}",                                         # GitHub PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}",                                 # Slack
    r"AIza[A-Za-z0-9_-]{35}",                                        # Google API key
    r"eyJ[A-Za-z0-9_=-]+\.eyJ[A-Za-z0-9_=-]+\.?[A-Za-z0-9_.+/=-]*",  # JWT
    r"Bearer\s+[A-Za-z0-9_.-]{20,}",                                 # Bearer tokens
    r"password\s*[:=]\s*\S+",                                        # password= in config
]
```

In addition, an entropy-based detector flags high-entropy strings of unknown
shape that look like credentials. Anything flagged is replaced with
`[REDACTED:secret]` before it reaches the log file or the terminal.

---

## 5. Audit Log

Every tool call, capability decision, and significant state transition is
written to an immutable, append-only JSONL file at `~/.universe/audit.log`:

```json
{"ts": "2026-06-16T22:38:39Z", "actor": "code_agent", "action": "tool:shell.exec",
 "args": {"cmd": "npm test"}, "result": "ok",
 "duration_ms": 4200, "nonce": "0192b7c1-...", "prev_hash": "ab12…", "hash": "cd34…"}
```

- **Rotation:** at 100 MB.
- **Retention:** 90 days by default, applied at every startup.
- **Content:** metadata only. Prompts and completions are never written to the
  audit log, so it cannot become a long-lived file full of sensitive content.
- **Inspection:** `/audit` lets the user query their own log.
- **Non-repudiation:** each entry carries a random UUIDv4 nonce so duplicates
  and retries are distinguishable from genuine duplicate actions.
- **Tamper evidence:** entries are hash-chained — each entry's `hash` is
  `sha256(prev_hash + entry)`. Accidental truncation or in-place edits break
  the chain and are reported at startup and via the `audit_chain_broken` event.
  This makes tampering *detectable*, not impossible: a process with write
  access to the file can always re-forge a whole chain.

---

## 6. Sandboxing

The execution environment for `Bash` depends on the platform:

| Platform | Execution environment |
|---|---|
| Windows (native, no WSL2) | `security/windows_sandbox.py` — an elevated→unelevated restricted-user chain cloned from the Codex design, **when the vendored Rust binaries are built** (see `docs/WINDOWS_SANDBOX.md`; they do not ship prebuilt). Until they are built, `Bash` fails closed and points at `BashUnsandboxed` (explicit consent). |
| Linux / macOS / WSL2 | Native execution through the Capability Gate and Audit Log, at per-session consent. A Docker backend exists (`security/sandbox.py`) but is **not wired** — there is no quarantine tier in the current build (all bundled skills are ingested from the 18 trusted sources; the community-submission pipeline is not connected). |

Honest limitation: a force-Docker toggle and per-tier sandbox routing are not
implemented in v1. `TestRunner`, `CoverageReport`, `CodeExecute` and the
docker/k8s tools run natively behind their consent policies, with the same
sanitized child environment as `Bash`.

---

## 7. Skill Signing

At ingest time, Universe computes a SHA-256 hash of each skill's normalized
content. The hash is stored alongside the skill record.

- For the four trusted sources, the hash is stored at load time and
  **re-verified weekly by the Subconscious Loop**. This protects against the
  upstream repo silently changing after Universe has already vetted it, which is
  a different and narrower concern than trusting the content in the first
  place.
- For community skills, both the hash and the source URL are stored. A hash
  mismatch on re-fetch quarantines the skill and notifies the user, rather
  than silently accepting changed code.

A quarantined skill appears in `/quarantine` and is not auto-activated. If the
user manually approves it, it is promoted as prompt content at lowered
confidence (0.70, or the trusted-load value when the source is on the
bundled allowlist). Quarantined and approved skills are never executed as
code — they are indexed prompt text ranked lower in retrieval; the Docker
backend (`security/sandbox.py`) is available for shell/tool sandboxing, not
as a skill sandbox (§6).

---

## 8. Network Egress Policy

Universe's network posture, honestly:

**Guarded by design:**

- `HttpClient`, `GraphqlClient`, `OpenapiParser` and the web tools run a
  DNS-resolving SSRF guard that rejects loopback, private, link-local and
  reserved targets — checked before the request and again on every redirect.
- Browser tools cannot open `file://` URLs, and screenshots/upload targets are
  scope-checked like any other file resource.
- Third-party connector tools each declare their fixed API endpoint and are
  matched against it.
- MCP servers: stdio servers launch behind a per-server egress CONNECT proxy
  restricted to their declared hosts (bypassable by raw sockets — documented
  in `docs/MCP_CLIENT_REWRITE.md`); remote http/sse servers require https
  off-localhost.
- `GAX_OFFLINE=1` restricts the provider routing chain to loopback (local)
  providers only.

**Not a general allowlist:** there is no default-deny egress list and no
per-host extension UI. Public-internet requests from `HttpClient`/web
tools to any public host are allowed behind consent, and `Bash` and
`Ssh` (explicit consent) can reach anything a shell can. If a host-level
egress firewall is required for your threat model, run Universe behind one.

---

## 9. User Overrides

The per-profile override panel described in early drafts is not implemented
in v1. The security knobs that DO exist today:

| Knob | How |
|---|---|
| Offline mode | `GAX_OFFLINE=1` — LLM routing restricted to local providers. |
| Consent levels | `capability_policy` rows in the DB (via `/profile` identity fields only; per-tool policy rows are respected by the gate when present). |
| Black hole rules | `/blackhole` — block any action whose description matches a rule. |
| Browser headless | `UNIVERSE_BROWSER_HEADLESS=0/1`. |
| Skill quarantine | weekly signature re-verification auto-quarantines hash mismatches. |

Tightening `Write`, whitelisting shell commands, and forcing Docker are
on the roadmap, not in the build.

---

## 10. The Capability-Scoped Tool Model

Every tool exposed to an agent is a `Tool(...)` record whose `capability`
field declares the capability it requires (for example, `Write`,
`Bash`, `http.egress`, `mcp.invoke`). The Capability Gate consults a
default policy table to decide whether a given agent invoking a given
capability is allowed, denied, or requires an interactive first-use prompt.

The default policy is conservative: high-impact capabilities (`Write`,
`Bash`, `http.egress`) require a first-use prompt per session. The
first-use prompt, once approved by the user, is saved as a **White Hole** in
the agent's session memory: a permanent, auditable record of what the user
consented to and when. Subsequent calls within the same session do not re-prompt;
calls in a new session start the flow over again.

Capabilities never invoked by an agent are simply absent from its policy
context, so an agent cannot escalate by requesting a capability it has never
been granted.

---

## 11. Reporting a Security Issue

Security issues should be reported responsibly. Please do not open a public
GitHub issue for a vulnerability. Instead, open a private vulnerability report
through the repository's security tab, or contact the maintainers directly with
details and reproduction steps.

Universe is MIT-licensed and accepts responsible disclosure in good faith.
