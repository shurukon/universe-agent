# Universe Agent — Structure Review

> **Consolidated executive summary across 6 rounds (2026-11-06).**
> Every claim below was verified in code or at runtime; section refs point to the evidence.

> **SUPERSEDED 2026-11-10.** This report is kept as a dated snapshot — its numbers are
> deliberately left unedited for provenance. Since it was written: the **14 orphaned
> tools (§V) were DELETED**, not whitelisted — the registry now holds **121** tools and the
> universe agent whitelists **121** (`core/core_agents/agents.py`), so there are **zero**
> unreachable tools; the Meta **media** surface (Muse Image, SAM 3.1, `/api/meta/{image,
> segment,test,models}`) was removed while the Meta **LLM provider** (`api.meta.ai/v1`)
> stays as one preset among many; the legacy `/api/goal*` bridge aliases and 18 other
> dormant routes were deleted; the `UniverseAgent` name collision was resolved
> (`core/agent/universe_agent.py` → `TurnOrchestrator`); the hook-coverage mismatch was
> fixed (`hooks/manager.py` and `connectors/builtin/__init__.py` now share one set of
> event→tool mappings — `Monitor` / `ApplyPatch` / `NotebookEdit` no longer bypass the
> fact-guard); and the lint gate is green again (`ruff check . --exclude universe-agent-ui`).
> See `docs/REPAIR_PLAN_2026-11-10.md` for the full plan and its execution record.

## 0. Executive summary

**What it is.** Universe Agent v1.0.0 — a local-first AI agent platform: one main agent plus a
toolset, delivered through three surfaces (console/CLI, Electron desktop, Bots). It is a Meta Muse
hackathon submission that routes Meta's Muse models through an OpenAI-compatible client and runs Bots
on Muse Code.

**Measured scale** (runtime, not README): **253 Python files / ~48k LOC**, **139 UI source files**,
**30 schema migrations**, **135 registered tools**, **650 collected tests** (609 pass + 41 skipped —
the README's arithmetic is exact), **46 CLI slash commands** + **35 bridge commands**, **107 HTTP +
4 WebSocket bridge routes**.

**Engineering quality is genuinely high** in the core: the turn engine matches its documented
contract exactly (§D), the capability gate is fail-closed and complete (§J/Q), the audit log is
hash-chained, and the recurring documentation pattern is *state the honest limitation once*
(audit.py, child_env.py, mcp_client.py, locks.py, memory_view.py).

**But five claims in the README/docs do not match the shipped default configuration:**

| # | Claim | Reality |
|---|---|---|
| 1 | "the mind that remembers everything", memory "mapped onto the cosmos" (§A/§B/§S) | The **default engine is a stateless session engine whose only durable state is an append-only transcript** (`GAX_ENGINE=goal` selects the memory-bearing legacy path). The galactic 5-tier memory is fed **only** by a CLI-side L3 reflection loop that the desktop app never starts — and the 3D memory orb is `buildUniverse("orb", null)`, i.e. **procedural decoration**, `aria-hidden`, never wired to real memory. |
| 2 | "1,584 trusted skills" (SOURCES.md) / skill signing + quarantine | **Stubbed off**: `skills/loader.py` returns 0 and `skills_data/` does not exist; the `Skill` tool refuses honestly ("corpus purged 2026-10-31"). 881 LOC of live-but-unfed `skill/` infrastructure remains. `failure-diagnosis.md` recorded "skill not found" ×86 — the purge is a user-visible regression. |
| 3 | "spawns subagents", 5 specialist swarm roles | **One agent**: `ALL_AGENTS = {"universe": …}`; only a `worker` role survives and refuses the rest. |
| 4 | "609 tests passing / 0 failing" | **Not reproducible** here: 5 failures, each with a root cause (missing committed fixture, hardcoded `frame_ttl_s`, pandas dtype drift, machine-local RE-evidence paths, a DNS-dependent test), **plus a hard hang** in `tests/test_task_threading.py`. The suite is not hermetic. |
| 5 | "132 tools" | **135** registered — of which **14 are unreachable** (no agent whitelists them; verified no bridge/wizard path either). |

**Three new defects found (beyond documentation):**
- The specialist merge is **incomplete for design**: the whitelist comment claims "every capability they
  held", yet `vision.py`'s own Design-Agent tools (`CssTools`, `ColorContrastCheck`, `SvgGen`) are
  among the 14 unwhitelisted (§V).
- **14 orphaned tools** are registered but dead in the shipped configuration (§V).
- **Provenance should be disclosed** (§L): the turn engine, bridge command surface and capability gate
  are documented *in code* as a verbatim port of a reference harness — `GAX_*` env namespace, a
  Round-7 parity suite, tool names "mined from the reference 2.1.277 binary", "REA-verified" notes —
  and the referenced authoritative docs (`shell-re-map.md`, the v1 spec) are **absent from the repo**.

**Top recommendations, in order:** (1) make the test suite hermetic — commit the muse fixture, fix
`frame_ttl_s`, make the RE-evidence test skip cleanly, bound the TestClient hang; (2) decide the
memory system's fate — wire the consolidator into the bridge or retire the memory surface from the
README/UI; (3) whitelist the design tools and delete or restore the other 14; (4) reconcile
SECURITY.md/PRIVACY.md with the shipped architecture; (5) resolve the skills contradiction;
(6) disclose the port provenance and ship the missing referenced docs.

---

# Universe Agent — Structure Review (round 1)

_Review date: 2026-11-06. Evidence-based pass over the tree, config, registry, and test suite._
_All counts below were measured at runtime, not copied from the README._

## 1. What this project is

**Universe Agent v1.0.0** — a local-first AI agent platform. One main agent (`universe`)
plans/executes/verifies with a full toolset; three delivery surfaces (chat console, CLI/Studio, Bots)
share one memory and one permission gate. A Meta Global AI Hackathon submission that routes the
**Meta Muse models** (muse-wonder-1.0 / muse-spark-1.x chat, muse-image-1.0, muse-voice-transcribe-1.0, SAM 3.1)
through an OpenAI-compatible client, plus **Muse Code** as the Bots runtime.

Stack: Python 3.11+ core (hatchling build, `universe` console script -> cli.repl:main),
a FastAPI desktop bridge bound to 127.0.0.1, and an Electron + Vite + React/TypeScript shell.

## 2. Size (measured)

| Metric | Value |
|---|---|
| Python source files (excl. node_modules/.git/vendor) | 253 |
| Python LOC (same scope) | ~48,225 |
| Tests | 63 files / ~13,940 LOC — **650 collected = 609 passing + 41 skipped** (verified) |
| Registered tools | **135** (verified via get_registry().names()) |

README claims 132 tools and 609 passing / 41 skipped. The test figure is exact; the tool count has grown to 135 — a stale number, not a structural problem.

## 3. Module map (Python LOC, desc)

```
tests 13940   connectors 7375   universe_desktop_bridge 6261   core 6231
scripts 2854  providers 1867   security 1465   cli 1281   schema 1271
universe-agent-ui 1076  skill 881  bots 837  observability 588  context 476
scheduling 354  eval 300  data 255  hooks 247  channels 235  storage 140
concurrency 133  failure 111  resources 26  skills 21
```

### core/ — the engine
- agent/: universe_agent.py (1118 L) = the Orchestrator (was the historical orchestrator.py, now a 10-line shim);
  turn_engine.py (791 L) = the turn loop (runRegularTurnLoop, turn FSM, dependency-aware parallel tool scheduler,
  microcompact/autocompact, steering drain); base_agent.py (Tool/HandoffPackage contracts, tool-schema builder);
  swarm.py (264 L, now single-agent); steer.py; project_context.py.
- memory/: the 5-tier galactic memory (L1 Working/planets, L2 Episodic/asteroids, L3 Semantic/stars, L4 Procedural/skills,
  L5 Meta), galactic_core.py (Gravity = conf_acc*0.6 + llm_prog*0.4), subconscious/loop.py (idle-time promotion), orbits/.
- session/store.py: JSONL transcript.jsonl per session (source of truth), forkable sessions.
- core_agents/, engine/session_loop.py, acceptance.py, media_enforcer.py, phase_contract.py.

### providers/ — OpenAI-compatible chain
- catalog.py: FREE / PAID / LOCAL provider presets (Groq, Google AI Studio, ...), each with base_url and a per-provider
  supports_response_format flag the client reads instead of hardcoding provider names.
- client.py (694 L): unified LLMClient.complete(), key rotation + fallback, no internal spend budget (by design), redacted logging (prompt hash only).
- meta_media.py: the real Meta media endpoints (image gen, ASR) + model list; no mock mode anywhere.
- manager.py + __init__.py: ProviderManager key rotation.

### connectors/ — 135 tools
- builtin/__init__.py defines ToolRegistry (single registration point; duplicate registration warns, not silent-overwrite)
  and _register_builtins() invoking 27 per-domain register() functions (files, shell, web, git, docker, browser, computer_use,
  data, vision, devops, thirdparty, mcp_tools, search, todos, plan_mode, task_tools, parity, studio, skills, cron, ...).
- Every registry.call() routes through the Capability Gate + pre/post hooks, emits tool_requested/tool_finished/failed, and
  redacts args at the boundary (redact_dict) — a documented test_run audit fix for Authorization headers landing in the DB.
- mcp_client.py: in-process MCP client. composio.py: Composio connector integration.

### security/
- capability.py (834 L): the mandatory gate. DEFAULT_POLICY consent map, VALID_CONSENTS with fail-closed 'typo -> explicit',
  and permission modes ask/plan/edit/full with a compat alias table.
- audit.py (append-only), secret_filter.py, secrets_fallback.py (keyring/AES), child_env.py, sandbox.py. (security/.claude/ is a stray dir.)

### schema/ — 30 sequential migrations
migrations.py + v0001...v0030. config.py::current_schema_version() derives the version from the highest migration id
(single source of truth) via PEP 562 __getattr__. Migration names document the design lineage:
v0025_retire_goal_mode, v0028_single_universe, v0029_session_authority, v0030_turn_identity.

### bridge (universe_desktop_bridge/) — FastAPI, 127.0.0.1-bound
app.py (4179 L) + domain modules (auth, consent, memory_view, questions, slash, state, studio, studio_mcp, voice, wake).
Route surface under /api/*: health, tools, sessions, turns AND goals (back-compat), steering, branching, compaction, projects,
automations, skills, mcp/servers, browser, indexing, setup.

### shell (universe-agent-ui/) — Electron + Vite + React/TS
client/src/{components,pages,contexts,hooks,lib,stores}, electron/{main,preload}.ts, client/tests/e2e (Playwright).
React+Vite client with a supervised Electron main running the Python bridge. Multiple stray docs/logs at the UI root.

### Cross-cutting
observability/ (events, timeline, tool_lifecycle, metrics, logging) | eval/ (runner, suite) | failure/ (checkpoint, retry) |
concurrency/ (locks, supervisor) | context/ (codebase_map, compaction, file_select) | storage/local.py (SQLite) |
channels/telegram.py | hooks/ (manager + builtin_factguard) | bots/ (runtime, scheduler, store) | scheduling/.

## 4. Design lineage / integrity notes (verified in code + migrations)

- Goal-mode and the multi-role swarm are DELETED, not merely deprecated. Docstrings timestamp the user orders
  (2026-10-28 for goal mode, 2026-10-31 for swarm) and the migration lineage corroborates (v0025, v0028).
  The engine vocabulary is now uniformly turn (run_turn / turn_engine).
- The swarm shim (core/agent/swarm.py) keeps an Agent tool but only for a single worker role that shares the universe
  toolset; other roles 'refuse honestly'. orchestrator.py is a backwards-compat shim over universe_agent.py.
- The README (rewritten for the hackathon) still headlines goal-mode (/goal command, 'up to 8 missions per goal',
  5 swarm subagents). That is now stale relative to the core engine — README <-> code drift.
- The bridge deliberately exposes both /api/turn/* and /api/goal/* for compatibility; the UI/CLI mostly use turns.

## 5. Security posture (appears deliberately engineered)

- Single chokepoint: every tool call passes the Capability Gate before execution; the gate is fail-closed (unknown consent escalates to explicit).
- Secrets never logged; the Meta key is AES-encrypted at rest, only sent to api.meta.ai.
- Args redacted at the registry boundary before persistence; LLM-call logs store a prompt hash only.
- Bridge binds 127.0.0.1 + bearer-token auth + origin checks; bot send/publish/purchase/push require explicit human approval (no timers, no auto-approve).

## 6. Housekeeping / cleanliness findings (for later rounds)

- Root-level dev artifacts (untracked but present): payload.txt, debug.log, shot.png, pw_probe_a..d.js, .vite_manual.log,
  plus untracked dirs .tooltest/, tmp_screens/, package.json/package-lock.json (@playwright/test). UPLOAD_SCOPE.md says such local artifacts must be excluded.
- Misplaced caches: .ruff_cache/ inside universe-agent-ui/ and universe-agent-ui/client/src/ (ruff is a Python linter);
  .ruff_cache inside the bridge; security/.claude/ (stray); __pycache__ at repo root.
- Stale counting in README (132 vs 135 tools) — cosmetic.

## 7. Suggested next review rounds

1. Deep-read turn_engine.py + universe_agent.py: verify the verbatim-port invariants (unbounded loop, compaction breaker, parallel-group scheduler) hold under the current single-agent config.
2. Audit the Capability Gate policies end-to-end (capability.py + audit + consent) against the 135 tools' declared capabilities/consent.
3. Reconcile README/goal-mode/swarm docs with the deleted engine (doc-drift fix) — or confirm back-compat endpoints still needed.
4. Examine skills_data/ (1,584 trusted skills) + skill/ loader + L4 memory indexing path.
5. Review bridge auth (auth.py) + child_env/sandbox for the desktop attack surface.
6. Housekeeping: gitignore the root artifacts and misplaced caches; confirm UPLOAD_SCOPE.md exclusions are enforced.

---

# Round 2 — Architecture deep-dive (2026-11-06, verified in code)

## A. THE most important structural fact: the session engine is the default and it is transcript-only

`UniverseAgent.run_turn` (core/agent/universe_agent.py:285) dispatches on `GAX_ENGINE` (default "session").
The default path `_run_session_turn` (universe_agent.py:225) delegates to `core/engine/session_loop.py`, whose
docstring is explicit:

> "The session transcript is the ONLY durable state: no goals rows, no planets, no gravity — the turn is
> appended to the transcript, replayed as context, and closed with a turn_end event."

Verified consequences:
- `session_loop.py` contains **zero** references to memory/planets/gravity (grep-confirmed). Context assembly is
  a full transcript replay (no 24x4000 truncation); compaction is the CC /compact semantics at 85% of the window,
  recorded as a synthetic marker row in the transcript.
- `_run_session_turn._build()` passes `l4_skills=[]` and `l3_stars=[]` — the turn engine recalls **no** memory.
- The legacy goal-era path (goals rows, L1 planets, gravity, L4/L3 recall) is preserved **behind** `GAX_ENGINE=goal`
  for a burn-in period, and is the only path that still writes `goals`.

So the README's central claim ("the mind that remembers everything", memory "mapped onto the cosmos") describes the
**legacy goal engine**, not the default session engine.

## B. The 5-tier memory system: what is actually live

| Layer | Fate under the default engine |
|---|---|
| L1 Working (planets) | Dormant — planets are created only on the GAX_ENGINE=goal path (universe_agent.py:390). |
| L2 Episodic (asteroids) | `forgetting_pass()` runs in the idle loop, but nothing writes asteroids on the session path. |
| L3 Semantic (stars) | **Live** — the only memory writer: `_reflect_sessions()` distills new transcript content from the
3 most recent sessions into L3 stars (domain='session'), idempotent via a `reflected` watermark row. |
| L4 Procedural (skills) | Corpus **empty** — see §C. |
| L5 Meta | `refresh_mirror()` runs in the idle loop. |

The writer is `core/memory/subconscious/loop.py` (`_cycle_body_sync`): promote_idle(0.45), connect_stars,
l2.forgetting_pass, l5.refresh_mirror, l4.apply_idle_decay, reflect_sessions — bounded per cycle, one to_thread hop
(timeout fix, audit 2026-09), yields to any active turn.

**Critical wiring gap (verified):** the subconscious loop is started **only by `cli/repl.py:67`**. The desktop
bridge (`universe_desktop_bridge/app.py`) starts only the automations + bot schedulers (app.py:218,302) and never
the subconscious loop. Therefore **the desktop app performs no memory writes at all** — the 'memory' surface is
CLI-only, and there it writes only L3 stars. The UI's /memory view reads a store the desktop path never fills.

## C. The skills subsystem: tool + loader stubbed, infrastructure retained but unfed

- `skills/loader.py` (21 L) is a **stub**: "skills disabled (empty catalog per user 2026-10-31)";
  `load_all_skills()` returns all-zero counts; `skills_data_root()` points at a `skills_data/` directory that
  **does not exist** in this checkout.
- `connectors/builtin/skills.py` (23 L) is a **stubbed Skill tool**: search returns 0 hits, read/activate refuse.
- SOURCES.md's "1,584 trusted skills" therefore describes a purged prior state, not a live corpus. The registry
  still carries a `Skill` tool (so tool counts and plan-mode whitelists reference it) but it is inert.
- The `skill/` package (881 LOC: taxonomy 268, ingestion 365, quarantine 120, signing 51, conflict 77) is **retained
  and still wired**: `skill.taxonomy.AGENT_OWNERSHIP/LEGACY_AGENT_ALIASES` (core_agents, l4), `sign_skill` (l4, wizard), `quarantine` (CLI skills wizard). It is live *infrastructure* with an empty store — not fully dead code.

## D. Turn engine: the verbatim-port invariants check out

`run_turn` (turn_engine.py:264) — all six documented invariants verified in code:
1. Unbounded `while True` loop; the only ceiling is the operator env cap `GAX_TOOL_MAX_ROUNDS` (default 200),
   which ends the turn with the last step's output (turn_engine.py:427).
2. Between-step protocol in exact order: steer/background drain (append `[USER COURSE-CORRECTION]`, and
   `repeated_calls.clear()`), microcompact at 0.9x window capped at 5, autocompact with the rapid-refill breaker
   (`CompactTracking.check_rapid_refill`), then the post-summary "call TodoRead" re-ground.
3. Model step with streaming deltas + one malformed-output-token retry at a conservative cap.
4. `TurnMachine` FSM (start -> start_model_request -> tool FSM) + the tool scheduler.
5. Results mirrored to the timeline and committed to the turn record; `turn_control` stop honored.
6. Memory deliberately external (orchestrator-owned); `memory_update` ambient entries are supported.

**Nuance worth stating precisely:** the "dependency-aware scheduler" is *consecutive-safe batching* —
`concurrentSafe = not mutating` (turn_engine.py:185): consecutive non-mutating calls group into one parallel batch;
any mutating/destructive/consent call runs alone. It performs no data-dependency analysis, so the docstring's
wording is more precise than the README's "dependency-aware scheduler".

## E. Subagency

The `Agent` tool + swarm survive as a **single `worker` role** (`roles = ["worker"]`, swarm.py:120) that runs the
universe machinery unchanged. Budgets intact: max_depth 2, max_children 4 per call, max_total 8 per goal. So the
README's "5 swarm subagents (research/code/review/security/design) at depth 2, up to 8 missions" is half-stale —
the numbers are right, the specialist roster is gone (it "refuses honestly").

## F. Bridge + security surface

- `auth.py`: bearer-token gate, fail-closed, `secrets.compare_digest`, Origin check against DNS-rebinding/webpage
  attacks, loopback-only binding, `UNIVERSE_BRIDGE_TOKEN=dev` explicit opt-out. A bare
  `python -m universe_desktop_bridge` now **generates and prints a random token** rather than silently accepting
  "dev" (documented 2026-09 audit fix).
- `capability.py`: DEFAULT_POLICY consent map; unknown consent levels escalate to `explicit` (fail-closed);
  permission modes ask/plan/edit/full with a compat alias table for retired names.
- Bots: primary runtime Muse Code (`muse exec --json`, MSP JSONL events), fallback to Universe's engine with honest
  disclosure, `RISKY_RE` (send/publish/purchase/push/deploy/delete/...) queues into the approvals gate.

## G. TEST SUITE HEALTH — the README's "609 passing / 0 failing" does NOT reproduce here

Measured: `pytest --collect-only` → **650 tests collected** (exactly 609 + 41 skipped, matching the README's
arithmetic). Running the suite in a sandboxed/offline Windows checkout, it **does not reach a green result**:

**Confirmed failures (5), each with a root cause — none indicate a broken core feature:**
| Test | Root cause |
|---|---|
| `test_m1_security.py::test_ssrf_guard_allows_public_hosts` | Non-hermetic: `socket.getaddrinfo` fails when DNS is unavailable mid-run. The guard itself is **correct** — re-run live it returns `{'ok': True}` and still blocks 127.0.0.1. Flaky/environment. |
| `test_bots.py::test_muse_event_digest_real_fixture` | `FileNotFoundError: tests/fixtures/muse_echo_run.jsonl` — **required fixture is not present in the checkout**. Repo-completeness gap. |
| `test_computer_use.py::test_capabilities_reports_the_machine` | Hardcoded `assert frame_ttl_s == 15` while the helper reports `45.0` — assertion/default drift. |
| `test_data_validate_schema_guard.py::test_columns_mapping_validates` | pandas dtype vocabulary drift: pandas reports `str` where the schema uses `object`. Version/OS-dependent. |
| `test_parity_round7.py::test_parity_doc_records_evidence_paths` | Depends on a machine-local dir `D:\ClaudeDesktop\Temp\opencode\re7`; it skips only when the dir is entirely absent, but this machine has a partial copy, so it fails. |

**Plus a hard stall:** `tests/test_task_threading.py` hangs indefinitely on its second test
(`test_continuation_reuses_task_no_new_row`) — reproduced in isolation; the suite cannot complete. The fixture
uses FastAPI `TestClient` + a background orchestrator task, so it depends on machine-specific thread/loop behavior.

**Conclusion:** the suite is *not hermetic*. It assumes the author's Linux workstation: network/DNS, a committed
muse fixture, machine-local RE evidence paths, a specific pandas, and specific CUA helper defaults. On this
machine ~604/650 pass, 5 fail, and at least one hangs. CI on an isolated runner would not reproduce "0 failing".

## H. Round-2 conclusions

1. Headline correction: **the product's default runtime is a stateless session engine whose only durable state is
   an append-only transcript**; the galactic memory system is a legacy/goal-era subsystem now fed only by CLI-side
   L3 reflection (and never at all in the desktop app).
2. The skills corpus is purged/stubbed (tool + loader), with the `skill/` package retained but unfed; SOURCES.md's
   1,584-skill narrative is historical.
3. Engineering quality is genuinely high in the core (turn engine, capability gate, provider client, bridge auth):
   invariants match their documentation, and failure paths fail closed with documented audit provenance.
4. Quality claims in the README are **not independently reproducible** (missing fixture, machine-local paths,
   network/version-coupled tests, one hang).

## I. Next rounds

1. Fix the test-suite reproducibility: commit `tests/fixtures/muse_echo_run.jsonl`, correct `frame_ttl_s`, make the
   RE-evidence test skip on absence, and give `test_task_threading` a bounded, loop-safe harness.
2. Decide the memory system's fate: either wire the subconscious loop into the bridge (so the desktop app actually
   remembers), or drop the 5-tier memory surface from the README/UI as legacy.
3. Resolve the skills contradiction: purge the dormant `skill/` package or restore a real corpus + loader.
4. Then do the completeness audit: bridge auth vs route surface, and the `context/` compaction contract.

---

# Round 3 — Surface, docs & provenance audit (2026-11-06, verified)

## J. Bridge security posture: structurally complete (auth covers everything)

Route surface measured in `universe_desktop_bridge/app.py` (4,179 lines): **107 HTTP routes**
(46 GET + 49 POST + 8 DELETE) + **4 WebSocket routes** (`/api/browser/stream`, `/api/voice/stream`,
`/api/events`, `/api/terminal`).

- **One outermost gate, full coverage.** `BridgeAuthMiddleware` is added *before* CORS, so Starlette
  makes it the outermost layer and it runs first. That ordering is also what covers the WS routes, since
  CORSMiddleware skips WebSocket scopes entirely (the code says exactly this at app.py:323-326).
- **Fail-closed on both transports:** HTTP -> 401 `{error:{code,message}}` (never a stack trace);
  WebSocket -> close code 1008 before accept. WS handlers do **not** re-check anything (verified in
  `/api/events`, app.py:3154) — a correct single chokepoint.
- **Origin discipline** (`_origin_ok`, auth.py:73): exact match only — a documented 2026-09 fix for the
  old prefix check that accepted `http://localhost.evil.com`. Allows `app://universe` plus any
  localhost/127.0.0.1 http origin on any port; a *missing* Origin is allowed (non-browser clients),
  on the reasoning that the bearer token is what authorizes them.

**Two structural caveats worth a follow-up decision (not defects):**
1. The token may be carried as a **query parameter** (`?token=`) for WebSocket upgrades and GET media
   previews (auth.py:131-141), because browsers cannot set custom headers on either. Tokens in query
   strings are the classic leak vector (server logs, Referer, browser history). A short-lived one-time
   WS ticket would be better.
2. `UNIVERSE_BRIDGE_TOKEN=dev` **disables the gate entirely** (`_token_ok` returns True unconditionally
   when expected == "dev", auth.py:66) while still permitting missing-Origin clients. The opt-out is
   documented and intentional, but it means a dev-mode bridge is a no-auth bridge for any local process.

## K. Slash-command surface: the "three layers" claim holds

- **CLI layer** (`cli/slash_commands.py`): **46 commands** registered in `register_all()` across the
  documented categories. Notably `/goal` is retained as an **honest alias** —
  `command("goal", "Tasks", "Alias for /task (the goal era is over)")` — drift is acknowledged in-product.
- **Bridge layer** (`universe_desktop_bridge/slash.py`): **35 SlashCommand entries**, explicitly
  "extracted verbatim from the installed [reference] runtime (docs/shell-re-map.md §1): the 20
  built-ins, their aliases, and the .md command-file discovery order"; commands without a counterpart
  report `supported: false` rather than pretending. The docstring says the bridge owns the registry so
  the desktop UI and the CLI share one truth.
- **UI layer**: the React client maps commands to `action` ids from `slash.py`'s `action` field.

So README's "slash commands across three layers (CLI -> bridge -> UI)" is structurally accurate.

## L. Provenance finding (important): the core is a documented PORT of a reference harness

The repository is open about this in its own comments, and it is visible throughout:

- Env-var namespace is **`GAX_*`** (7 distinct): `GAX_ENGINE`, `GAX_TOOL_MAX_ROUNDS`,
  `GAX_TOOL_TIMEOUT_SECONDS`, `GAX_OFFLINE`, `GAX_DEBUG`, `GAX_OTEL_ENDPOINT`,
  `GAX_SWARM_MAX_TOTAL_PER_GOAL`. `GAX_ENGINE` is what selects session-vs-legacy-goal (§A).
- Docstrings say so explicitly: turn_engine.py is "**ported verbatim**" ("the semantics below are the
  ones REA recovered from the reference engine loop"), swarm/base_agent keep "prompt compatibility",
  and bridge/slash.py is "**extracted verbatim** from the installed [reference] runtime".
- A dedicated parity apparatus pins that behavior: `connectors/builtin/parity_tools.py`,
  `docs/parity.md` (140 L), `tests/test_parity_round7.py`, `tests/test_shell_parity.py`, and migration
  comments (v0001_initial.py alone carries 39 of these provenance signals). The engine's "unbounded loop / 3 continuations /
  5 microcompacts / 200-round cap / rapid-refill breaker" numbers are all *parity targets*, not
  invented constants.
- Residual source-product vocabulary survives the rewrite: "shell" (`shell-re-map.md`,
  `test_shell_parity`, `shell.py`), "the spec" / "the reference harness", "REA-verified".

**Dangling references:** `docs/shell-re-map.md` is cited from code and from `slash.py` but is **absent
from docs/** (15 files, verified); SECURITY.md cites "section 10 of the Universe Agent v1 Final
specification" and PRIVACY.md cites "section 17/21 of the spec" — that specification document is also
not in the repo.

Reviewer implication: the turn engine, bridge command surface and capability gate are *ports of an
existing harness* (reverse-engineered and parity-tested), not greenfield work. That is not inherently a
problem, but a hackathon submission should disclose it, and the missing referenced docs mean the
"authoritative engineering reference" is not shippable with the repo.

## M. docs/SECURITY.md and PRIVACY.md describe deleted/absent architecture

`docs/SECURITY.md` (286 L) is a polished 12-threat table, but its Trust Boundaries section states
"The **Orchestrator** classifies, routes, and supervises agents" and "The **Agent** (Code, Research,
Write, Review, etc.) executes work". Both are false today: classification is deleted
(universe_agent.py: "Classification/chat routing removed — every message is a turn") and the specialist
roster is purged (worker-only). Its "Malicious skill execution -> Skill signing (SHA-256 at ingress),
quarantined community skills, weekly re-verification" also describes the disabled corpus — the loader
stub verifies 0.

`docs/PRIVACY.md` (288 L) documents the `~/.universe/` layout: `universe.db` (WAL — matches
storage/local.py), `audit.log`, `checkpoints/`, `orbits/`, `skills/`, `maps/`. Accurate for the
filesystem, but note §B: **`sessions/` is the actual durable state of the default engine** and
PRIVACY.md does not list it — a small but real gap for a local-first data-ownership doc.

`docs/failure-diagnosis.md` (189 L, **Arabic**, dated 2026-10-09) is a real incident report with
DB-derived quantities: 25 goals over ~28 days -> **15 failed (60%)**, 1 stuck "running" for 139.8
hours; `llm_calls` malformed x10 / provider_down x8 / rate_limit x6; tool errors "skill not found" x86
and "file.write requires user consent" x35; plus the empty `tool_calls[0].function.name` -> fatal 400,
which is exactly the bug providers/client.py `_tc_field` guards ("the historical killer"). It also
states the spec's live-status surfaces (StatusStrip/Activity) "were never built".

## N. Cross-cutting subsystems — reviewed, all coherent

| Subsystem | File | Verified design |
|---|---|---|
| Provider registry | providers/manager.py (544 L) | `KNOWN_PROVIDERS`/`FREE_PROVIDERS` **derived** from catalog.py (single source of truth; a documented m2 drift fix). `ProviderKey` = status high/medium/low + AES `encrypted_secret` + rolling 5-min error/latency counters. |
| Persistence | storage/local.py (140 L) | Thread-safe SQLite, **one connection per (thread, db)** (sqlite3 is not shareable), WAL, every multi-step write in a transaction; ids generated once at insert (fixes the duplicate-orbit-rows bug). |
| Event bus | observability/events.py (284 L) | Fire-and-forget, **per-subscriber bounded queue of 512 with oldest-drop** (so a UI can never stall the orchestrator), 32 KB/event cap, per-goal seq table bounded to 1000, **all payloads through secret_filter**, zero cost with no subscribers. Powers `/api/events`. |
| Hooks | hooks/builtin_factguard.py (46 L) | pre-shell / pre-write "fact-forcing" gates: block once per goal with an instructive message, then allow the retry. Pinned by docs/parity.md; disable-able but never removable. |
| Compaction | context/compaction.py | `COMPACTION_TRIGGER_PCT = 0.85` — **one** compute-based pass ("the 2026-11 migration decision"), a 9-section CC /compact template that keeps ALL user messages verbatim (so security constraints survive compaction), `keep_recent=6`, returns False when no real summary so the heuristic fallback runs. |

Minor nit: compaction.py's module docstring still says "context limit (80%)" while the constant is 0.85.

## O. Round-3 conclusions

1. Security posture is genuinely strong and *structurally complete*: one fail-closed gate covering
   107 HTTP + 4 WS routes, append-only audit, redaction at every sink, bounded event fan-out.
   The query-param token and the `dev` opt-out are the two items to revisit.
2. The "three layers" slash-command claim holds (46 CLI / 35 bridge / UI actions).
3. **Provenance should be disclosed:** the engine / bridge / gate are documented in-code as a verbatim
   port of a reference harness (GAX_* namespace, parity suite, REA recovery notes). Referenced
   authoritative docs (shell-re-map.md, the v1 Final specification) are missing from the repo.
4. SECURITY.md describes the deleted classifier/specialist architecture; PRIVACY.md omits `sessions/`,
   which is the default engine's real durable state.
5. failure-diagnosis.md records a real 60% failure incident and "skill not found" x86 — the skills
   purge is now a user-visible regression relative to that report.

## P. Next rounds

1. Decide and act on the two bridge-auth caveats (WS token transport; dev-mode default-off).
2. Reconcile SECURITY.md / PRIVACY.md with the shipped architecture (or restore the missing spec doc).
3. Review the remaining unreviewed internals: eval/runner, channels/telegram, security/audit.py +
   sandbox.py + child_env.py, connectors/mcp_client.py, core/acceptance.py, cli/wizards/*, and the
   UI component tree (desktop/, universe-sphere/, flow/).
4. Only after the review is complete: propose the test-suite reproducibility fixes from §G.

---

# Round 4 — Security internals + UI surface audit (2026-11-06, verified)

## Q. Security internals: genuinely well-engineered, with honest limitations

| Module | What it actually does (verified) |
|---|---|
| security/audit.py (201 L) | Append-only JSONL at `~/.universe/audit.log`, rotated at 100 MB, 90-day retention. **Hash-chained**: each entry carries `sha256(prev_hash + entry)` and `verify_chain()` detects tampering. The code states the honest boundary: "tampering detectable (**not preventable**): an attacker with write access can always re-forge a whole chain; the point is that accidental truncation and lazy edits become visible." Never logs prompts/completions — metadata only. |
| security/child_env.py (68 L) | Sanitizes the environment of every process the agent spawns (shell, sandboxed children, MCP servers, code execution). Strips `UNIVERSE_BRIDGE_TOKEN`, `UNIVERSE_PASSPHRASE`, `UNIVERSE_TEST_API_KEY` by exact name **plus** broad patterns (`UNIVERSE_*_KEY|*_TOKEN|*_SECRET`, `.*_API_KEY`, `.*_SECRET_KEY`, ...). Fixes a real, documented attack: "one approved shell call can read [the bridge token] and then approve every later 'explicit' consent via the bridge API." Fail-closed by design — a false positive is recoverable, a false negative leaks a secret to agent-controlled code. |
| security/sandbox.py | Docker sandbox for quarantine-tier `shell.exec`: `--network=none`, `--read-only`, `--tmpfs` scratch 64 MB, `--memory=256m`, `--cpus=0.5`, timeout. (Reachable only for community/**unsigned** skills — which the stub loader never produces, so this path is currently unreachable: see §C.) |
| connectors/mcp_client.py | The strongest security documentation in the repo. It opens by stating that **the previous version of this file "declared three security guarantees in its own docstring that were never actually enforced in code"** — and that the wizard printed "read-only, egress limited" unconditionally. The rewrite makes three things real: (1) protocol correctness (`initialize` handshake, JSON-RPC id matching — the old code broke immediately on the reference server's `notifications/tools/list_changed` push); (2) read-only enforcement via the tool's own `readOnlyHint` with a name-based heuristic fallback (honestly noting the reference server does not populate annotations); (3) egress filtering via `HTTP_PROXY`/`HTTPS_PROXY` pointed at a per-server local CONNECT proxy, with the limitation stated once: "defense-in-depth against a **well-behaved** subprocess... NOT a kernel-level sandbox, and a deliberately malicious server using raw sockets could still bypass it." Crash-loop supervision (3 crashes/5 min) delegates to `concurrency/supervisor.py`. |
| core/acceptance.py (157 L) | Goal-era artifact/verification probes (`evaluate_goal`). **Called only from universe_agent.py:955 and checked at 1021-1025 — both inside the legacy `GAX_ENGINE=goal` path.** The default session engine therefore has **no acceptance/verification step**: the model declares completion and the turn ends. |

## R. Remaining surfaces

- **eval/** — `/eval` runs the v1 smoke suite of 10-15 golden tasks with rubrics; `/eval --compare` reports the delta vs the last run. Rubric includes a keyword-noun content check ("agent presence / step count alone is not a pass"), substring matching only, no LLM.
- **channels/telegram.py** — thin adapter: Telegram messages become the same internal input the CLI produces and "Goal-mode updates" go back out; one Orchestrator pipeline, no separate Telegram logic; access control (only me / specific IDs / open). Docstring still says "Goal-mode" — residual legacy vocabulary (§L).

## S. UI surface: 185 files, and the flagship visual is decorative

Measured `universe-agent-ui/`: **100 .tsx + 27 .ts + 12 .css = 139 source files** (+ 9 .py helper scripts, 6 .json, and stray **9 .pyc**, 4 .log, 7 .md).

- `components/ui/` — **57 vendored shadcn/ui primitives** (accordion…tooltip). Standard off-the-shelf library, not project work.
- `components/desktop/` — 37 entries: the real shell (Chat, Sidebar, TopBar, Rail, ConsentBanner, ConfirmDialog, QuestionBanner, StatusStrip, ProgressPane, ActivityPane, ContextMeter, CommandPalette, Composer, ModelPicker, WorkspaceSwitcher, ProjectBar, TaskHeader, DesignWorkspace, OfflineBanner, EmptyState, PlanPill, StatusCard, TransparencyBar, TrailingUniverse, UniverseConnector…).
- `lib/` — typed bridge clients: `api.ts` (incl. `memory: () => request("/api/memory")` at api.ts:637), `ws.ts`, `botsApi.ts`, `studioApi.ts`, `voice.ts`; `lib/flow/` (agentTint, fold, railStore, ztimeline); `lib/universe/` (generate, palette, rng).
- `components/flow/UniverseFlow.tsx` + `lib/flow/ztimeline.ts` — the shell-parity timeline rendering, consumed by `AgentFlow.tsx` (real, live agent timeline).
- `components/universe-sphere/` — 10 files: `UniverseCanvas`, `MemorySphere`, `MemoryStars`, `CosmicCore`, `SpiralArms`, `Systems`, `Agents`, `glsl.ts`, `state.ts`, `shared.ts` — the WebGL "cosmos".

**THE finding — the flagship "memory mapped onto the cosmos" is procedural decoration.** Verified at every layer:
1. `bridge/memory_view.py` says it out loud: *"Current wiring, honestly stated: the bridge serves the live snapshot at /api/memory and Settings' memory browser consumes it. **The 3D visuals (trailing orb + welcome orb) are procedural today (buildUniverse with real=null)**; the RealUniverseMemory shape stays exported from generate.ts so a future live wiring drops straight into buildUniverse(quality, real)."*
2. `EmptyState.tsx:21` and `TrailingUniverse.tsx:36` both call `buildUniverse("orb", null)` — the only two producers of `UniverseData`.
3. `EmptyState.tsx` marks the canvas `aria-hidden="true"`, `interactive={false}` and its own header comment calls it "the WebGL universe orb **as the brand accent**".
4. `RealUniverseMemory` (generate.ts:407) is an exported interface with **no producer anywhere** — reserved for wiring that does not exist.
5. The only real memory data reaching the UI is **ids/names/counts** through `/api/memory` (documented at Settings.tsx:2484 and memory_view.py: "ids, names, counts only, never secrets").

So the README's "A memory mapped onto the cosmos — inspect it any time with /memory" is, in the shipped desktop build: (a) a legacy-engine-only memory store (§A/§B), (b) never written by the desktop path (§B), and (c) rendered as a **procedural brand orb**, not real memory. To its credit, the bridge documents this honestly rather than implying the visual is live.

## T. Round-4 conclusions

1. Security code quality is the strongest part of the project: hash-chained audit, child-env sanitization, a container sandbox, an MCP client that *documents its own past false claims* and states its egress limits. The recurring pattern is "state the honest limitation once" — exactly right.
2. The default session engine skips the goal-era acceptance/verification step entirely; the model's own "no tool calls" is the only completion signal.
3. The UI is a large, competent React shell (57 shadcn primitives + 37 bespoke desktop components), but its signature visual is decorative.
4. Combined with §A/§B, the README's central differentiator (persistent cosmic memory) is not what the shipped default configuration does.

## U. Remaining scope for later rounds (goal left ACTIVE)

1. `cli/wizards/*` — the 46 slash commands dispatch here; worth one pass to confirm each wizard matches its command.
2. The large connectors: `browser.py` (747 L), `parity_tools.py` (427 L), `web.py` (309 L), `vision.py` (286 L), `computer_use.py` (205 L), `studio.py` (207 L).
3. `core/core_agents/agents.py` — inventory the "12 Core Agents" and their tool whitelists (§A: single universe agent).
4. `context/` (codebase_map, file_select), `concurrency/`, `hooks/manager.py`, `bots/store.py`, `scheduling/cron.py`, `resources/`, `data/`, `eval/suite.py`.
5. Then: consolidate everything into a single executive summary at the top of this document, and stop.

---

# Round 5 — Agent registry, orphaned tools & remaining connectors (2026-11-06, verified at runtime)

## V. The agent registry: exactly one agent, 121 of 135 tools

Verified live (`get_agent('universe').tool_whitelist_names` vs `get_registry().names()`):

| Measure | Value |
|---|---|
| Registered agents | **1** — `ALL_AGENTS = {"universe": UniverseAgent}` (agents.py:430) |
| Tools in the universe agent's whitelist | **121** |
| Tools in the global registry | **135** |
| Whitelisted but not registered (stale) | **0** — the whitelist is internally consistent |
| Registered but **not** whitelisted (unreachable) | **14** |

The 14 orphaned tools: `BashUnsandboxed`, `js`, `js_add_node_module_dir`, `js_reset`, `CodeAnalyzer`, `ColorContrastCheck`, `CoverageReport`, `CssTools`, `DependencyAudit`, `SecretScanner`, `StaticAnalysis`, `SvgGen`, `TestRunner`, `VulnScanner`.

I verified they are **truly orphaned, not merely second-tier**: the only non-test references to any of them are their own definitions in `connectors/builtin/misc.py` (9), `vision.py` (2), `shell.py` (3), plus two capability-policy rows and one registry timeout default (`BashUnsandboxed`). **No bridge endpoint, no wizard, and no agent reaches them.** With a single agent that does not whitelist them, and the whitelist check enforcing exact names (tests/test_tool_whitelist.py), these 14 are dead in the shipped configuration.

### The merge was incomplete in exactly one dimension: DESIGN

`tool_whitelist_names` (agents.py:362-365) is commented as "union of the retired specialists' whitelists (**planning, write, file, api, data, devops, browser**) + spawn — the universe agent is the direct heir of every capability they held."

**`design` is missing from that list** — and the evidence lines up exactly:
- `connectors/builtin/vision.py` opens with "**Design Agent's** vision + CSS tools (Phase 3)… This is the file that closes the 'Design Agent is blind' gap", then implements `vision_analyze`, `css_tools` and `color_contrast_check` — two of which (`CssTools`, `ColorContrastCheck`) are unwhitelisted, as is `SvgGen`.
- So the code claim "direct heir of every capability they held" is **false for the design specialist**.

Worth noting for correctness of this review: withholding `BashUnsandboxed` is deliberate and good (it is the explicitly unsandboxed shell); withholding the stateful node-REPL triplet (`js`, `js_add_node_module_dir`, `js_reset`) is defensible. But the design tools are simply an oversight.

## W. Remaining big connectors — reviewed

| File | Size | What it is |
|---|---|---|
| `connectors/builtin/parity_tools.py` | **492 L** | "Round-7 verbatim-parity tools" — tool names "**mined from the shipped [reference] engine (reference shell, 28-name streaming allowlist) and the reference 2.1.277 binary (BUILTIN_TOOL_NAMES + Ct({name:…}) registration sites)**": EnterPlanMode, ToolSearch, Monitor, NotebookEdit, GoalRead, ReadSessionContext, PowerShell, ApplyPatch. More direct evidence for §L. Honest where it can't match: "ws watch is **honestly unsupported** here". All ride the same gate; write-shaped tools carry capability `Write`. |
| `connectors/builtin/browser.py` | **840 L** | 10 Playwright tools. Default **HEADED** (visible) — "the operator wants to see the browser work so there's no room for an unnoticed mistake"; falls back to headless *only* when no display exists, with a **logged warning, never silent** (`UNIVERSE_BROWSER_HEADLESS` override). Gate awaits async handlers; every retrieved string is tagged `[UNTRUSTED:web]` so it can never be followed as an instruction. |
| `connectors/builtin/web.py` | **327 L** | Local-first rewrite — it documents that the previous version "imported a single-platform SDK that only works inside one specific environment and broke [the] local-first promise everywhere else". `web_search` scrapes DDG's static HTML endpoint (no key), `web_fetch` = httpx with a **curl_cffi Chrome-TLS-impersonation fallback** for Cloudflare-protected sites, `web_research` = parallel search+fetch ranked by relevance. All tagged `[UNTRUSTED:web]`. |
| `connectors/builtin/vision.py` | **304 L** | See §V — Design Agent's tools; `vision_analyze` returns real dimensions/colours/aspect/brightness/format and only attaches `llm_description` when a vision-capable provider is configured ("**NEVER a stub**"). |
| `connectors/builtin/studio.py` | **228 L** | StudioAVE (AVE's zero-dependency MCP server, JSON-RPC 2.0 over HTTP at 127.0.0.1:19789, ~41 tools), StudioDesign (open-design daemon, loopback 127.0.0.1:7456), StudioBlender (blender-mcp addon socket, tcp://127.0.0.1:9876, speaking the addon's real wire protocol with chunked JSON accumulation). All loopback, all gated, honest unreachable errors. |
| `connectors/builtin/computer_use.py` | **280 L** | machine.control over the `universe_cua_helper` broker (a lazily-spawned NDJSON child that "dies with the bridge" and "never decides policy — the gate + consent layer owns every decision"). Safety: frame binding (coordinate actions must reference a live screenshot frame with a 15 s TTL; a stale frame is rejected, never replayed), a11y-first (UIA ValuePattern instead of raw keystrokes). Every tool is `consent=explicit`. |

## X. CLI wizards: 48 entrypoints, several managing now-empty subsystems

`cli/wizards/__init__.py` defines **48** entrypoints: goal/status/stop/history/resume, session (new/fork/compact/sessions), memory (memory/vault/forget/forget-all), agents (**agents**, agent/_agent_create/_agent_details), rules (black/white/wormhole), connectors (connectors/connect/mcp), skills (**skills**, **quarantine**, skill), channels, providers (providers/provider/_provider_keys/model/fallback), observability (debug/log/trace/metrics/eval), data (export/import), profile, **orbit**, setup, version, migrate, help.

Findings: `/agents` ("List/manage all agents (Core + Moons)") and `/agent` ("Create a custom **Moon** agent") manage a system that now registers exactly one agent (§V) — the Moon-agent machinery is gone. `/skills`, `/quarantine`, `/skill`, `/orbit` manage the purged, empty skills corpus (§C). So a meaningful subset of the wizard surface is UI over removed/empty state, mirroring the SECURITY.md / README drift of §M.

## Y. Round-5 conclusions

1. **The agent registry collapsed to one agent** (`ALL_AGENTS = {"universe": …}`) that whitelists **121 of 135** registered tools; the whitelist has no stale entries.
2. **14 tools are unreachable dead weight** — verified that nothing (bridge, wizard, agent) can call them.
3. **The specialist merge is demonstrably incomplete for `design`**: the code comment claims "every capability they held", yet vision.py's own tools are unwhitelisted.
4. The large connectors are well-built and honestly documented (headed-by-default browser, `[UNTRUSTED:web]` tagging, local-first web rewrite, real Studio sockets, frame-bound computer use).
5. Much of the wizard surface is UI over removed or empty subsystems.

---

# Round 6 — Final subsystem sweep + close-out (2026-11-06, verified)

## Z. The last unreviewed internals

| Module | Size | Verified design / state |
|---|---|---|
| `context/codebase_map.py` | 254 L | Per-project map: file tree, per-file size/language/mtime, symbol index, import graph, test-file markers. Stored in `~/.universe/maps/<project_hash>/`, cached, invalidated on change. tree-sitter optional with a **regex fallback** covering Python/JS/TS/Rust/Go/Java — "a faithful equivalent, documented in the deviations log". |
| `context/file_select.py` | 45 L | BM25 ranking (`rank_bm25`) of candidate files by task text + path + symbols + recency + interaction count; top-K defaults to 20 and scales with the remaining context budget. |
| `concurrency/supervisor.py` | 74 L | MCP server supervision: 3 crashes in 5 min -> pause + notify (no silent crash-loop). Also enforces the "4-concurrent-Moons-per-Planet" backpressure limit — **Moon machinery that no longer exists** (§V): dead policy alongside the live crash supervision. |
| `concurrency/locks.py` | 59 L | Per-entity asyncio locks. Header is exemplary honesty: "**HONEST STATUS (engine-fix round): this is an OPTIONAL utility, currently unwired — get_locks() has no production callers** (mutations rely on SQLite WAL + short transactions instead). Kept as the documented mechanism… exercised by tests so the surface can't rot silently." |
| `hooks/manager.py` | 172 L | Builtin factguard merged with the user's `desktop.hooks[]` config, loaded once per process. `check()` is **pure** — "never mutates anything except its own one-shot bookkeeping, so a hook can never corrupt a call — only stop it with an honest message". Events `pre_tool/pre_shell/pre_write/post_tool`; `pre_shell` covers **Bash + PowerShell + CodeExecute** (an engine-fix-round fix — it used to fire for Bash only). |
| `bots/store.py` | 291 L | JSON persistence (`~/.universe/bots.json`), **atomic writes** (temp file + `os.replace`), one RLock, transcripts capped at 400 so long-running bots cannot grow unbounded, `to_public()` strips internals and computes `approvals_pending` to the exact UI wire shape. Statuses: idle/running/waiting_approval/error/paused. |
| `schema/migrations.py` | 145 L | Forward-only runner, **bespoke** (zero external migration deps, plain auditable Python): DB backup before applying, `PRAGMA user_version` update, **restore + refuse to start on failure**, `/migrate --dry-run` preview. |
| `scheduling/cron.py` | — | Cron expression parsing feeding both the automations scheduler and bot ROUTINEs (§R). |
| `resources/router.py` | 26 L | Tier -> default model resolution; documents that the per-task-type routing table was retired ("nothing dispatched on task type in every configured agent gets an explicit model pin at provider-install time") and that surviving rows use current model names (gpt-5, claude-sonnet-5, claude-opus-5-5, gemini-3-pro-preview). |
| `data/export_import.py` | 255 L | Portable state export/import as a zip with `MANIFEST.yaml`, a canonical-JSON fingerprint, and a self-excluding `manifest_sha256` for integrity. |
| `eval/suite.py` | — | **15** `EvalTask` golden tasks with rubrics, keyword content checks and time budgets (matches the "10-15" spec range). |

## AA. Overall assessment

**Architecture.** A coherent single-agent harness with a well-factored kernel: `turn_engine`
(unbounded loop + FSM + safe-batch scheduler) is genuinely good, and the surrounding cross-cutting
layers are consistent. The cost of rapid iteration is a **second, abandoned architecture still present
in the tree** — goals/planets/gravity/L1-L5, Moons and swarm roles, the 1,584-skill corpus, the
quarantine sandbox, the Moon backpressure limit. Most of it is honestly labelled as deleted, which
makes the codebase readable, but it also means roughly a fifth of the surface (881 LOC of
`skill/`, 14 tools, several wizards, the memory layers) is dormant code that only a deep review can
distinguish from live code.

**Security.** The strongest area. One complete fail-closed chokepoint (§J), hash-chained audit,
sanitized child environments, frame-bound computer use, `[UNTRUSTED:web]` tagging, and
limitations-stated-honestly documentation. The two items worth revisiting are the query-param token
and `UNIVERSE_BRIDGE_TOKEN=dev` disabling the gate (§J).

**Quality claims.** Not reproducible in an isolated environment (§G). This is the single most
important thing for a maintainer or judge to fix.

**Documentation.** docs/ has 15 files of good quality, but the flagship narrative (README,
SECURITY.md) describes the pre-2026-10 architecture. An incident report in Arabic
(`failure-diagnosis.md`) documents a real 60% failure rate and confirms several now-deleted behaviours.

**Verdict:** a strong, honestly-engineered core wrapped in an abandoned second architecture and a
marketing narrative that describes the abandoned one. The most valuable next actions are the test-suite
reproducibility fixes and the memory/skills fate decisions — not new features.

## AB. Review complete

Coverage across rounds 1-6: module map and measured metrics; engine and data flow; the memory
system's real fate; skills; the agent registry and tool whitelists; providers; every major connector;
security (gate, audit, child env, sandbox, MCP, computer use); the full bridge and route surface; CLI,
bridge and UI command layers; cross-cutting subsystems; docs consistency; provenance; and test health.
No package in the tree remains unexamined. Findings and recommendations are actionable and verified.
