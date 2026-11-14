# Execution record (2026-11-10, session 1)

This file is the PLAN. What actually landed is below; the plan above is left
untouched for provenance. Command results are the exact numbers measured on
this machine at the time of writing.

## Verification (the whole set, re-run at the end)

| Check | Result |
|---|---|
| `pytest -m "not llm" -q --timeout=180` | **659 passed, 13 skipped, 15 deselected, 0 failed** (9m54s, exit 0) |
| `ruff check . --exclude universe-agent-ui` | **All checks passed** (was 69 errors at the start) |
| `npx tsc --noEmit` (client) | **0 errors** (exit 0) |
| registry / whitelist / orphans | **121 / 121 / []** (was 135 / 121 / 14) |
| bridge routes | **96** `@app.*` decorators (was 116, incl. 9 `/api/goal*` aliases) |
| `GAX_ENGINE=goal` | read, warned about, ignored — the goal engine no longer exists |
| `/api/meta/{image,segment,test,models}` | removed |

## Phase status

| Phase | State | Notes |
|---|---|---|
| P0 baseline + test structure | **DONE** | `tests/fixtures/muse_echo_run.jsonl` (21 lines, synthesized MSP), TTL env override + `UNIVERSE_HOME`, hermetic CUA broker, env-gated RE-evidence test, bounded `_wait_for` harness, CI without `-x` + junit artifact |
| P1 the 14 orphan tools | **DONE** | deleted from `misc.py`/`vision.py`/`shell.py` (definitions, not just un-whitelisting); hook-event sets unified in `hooks/manager.py`; builtin fact-guard matcher `"Bash"` → `"*"`; `AUTO_MODE_NEVER_LIFT` no longer names a deleted tool; client icon maps + `fold.ts` + `ztimeline.ts` cleaned; `VulnScanner` policy comment removed |
| P2 dead surface + Meta | **DONE (with corrections)** | 11 dead client helpers + the Muse Image / SAM 3.1 / segment / test / models surface deleted; `providers/meta_media` keeps `resolve_key` + `transcribe` only. **Four routes that were cut as "dormant" were RESTORED after grep + tests proved they have live callers**: `GET /api/providers/{name}/keys`, `PATCH` + `DELETE /api/projects/{pid}`, `GET /api/session/{session_id}`, `GET` + `PATCH /api/agents*` |
| P3 engine A1–A9 | **DONE** | see below; each fix has its own test file |
| P4 goal engine deleted | **DONE** | body, `GAX_ENGINE` switch, `goals` rows, planets, checkpoints; `record_provider_halt` writes the transcript; 9 `/api/goal*` bridge aliases removed; terminal bus events added |
| P5 memory + galaxy | **STARTED** | subconscious writer + bots scheduler now run on the bridge's own loop (started in the lifespan — the desktop app never wrote memory before); live L4/L3 recall on every session turn. **Not done:** the L2 asteroid write itself, the client galaxy contract |
| P6 bridge/UI contract | **PARTIAL** | bridge owns one long-lived loop (this unblocked the whole threading test file); terminal events + `error_kind` on the halt summary. **Not done:** the 401/403 surface, the Electron settings pass, the remaining UI truth items |
| P7 docs truth | **DONE for the files touched** | README/SECURITY/UI-OVERHAUL/UI-STATUS/events-v1/parity/failure-diagnosis/vendor/.gitignore; STRUCTURE_REVIEW carries a SUPERSEDED banner; `docs/SKILLS.md` + `tests/test_skills_corpus.py` document the honest empty corpus |
| P8 final verification | **DONE for Python** | the table above; the Electron build was not re-verified this session |

### P3 detail (each item, with the test that pins it)

| # | Fix | Test |
|---|---|---|
| A1 | `turn_control` is lifted from the handler result; `ExitPlanMode` announces `stop_after_result` and the engine emits `turn_control_stop` | `tests/test_turn_control.py` |
| A2 | `compact_tracking.reset()` after a compaction; the breaker counts MODEL STEPS, not batches; `reset()` no longer clears the refill streak (that made the breaker untrippable) | `tests/test_turn_compaction_breaker.py` |
| A3 | the duplicate detector counts identical calls (no "reset on success"); the dead `pass` is now an explained `pass` | `tests/test_engine_fix_engine.py` |
| A4 | `readOnly`/`concurrentSafe`/`sideEffectScope` derive from the REAL capability namespace (`file.read`, `memory.read`, …), with a 4-slot parallel cap | `tests/test_turn_scheduler.py` |
| A5 | tool results persist as real `role:"tool"` rows with their call ids, preceded by the assistant row that declares them | `tests/test_session_tool_persistence.py` |
| A6 | the compaction window is the ACTIVE model's (catalog field `context_window`), not a hardwired 32 000 | `tests/test_context_window.py` |
| A7 | streaming requests `stream_options={"include_usage": true}` with a fallback when an endpoint rejects the field | `tests/test_provider_usage.py` |
| A8 | an empty reply is re-asked once, then closes as `stop_reason="model_empty_reply"` with the event emitted | `tests/test_turn_empty_reply.py` |
| A9 | a cancelled tool becomes an honest failed row and its id still commits; `return_exceptions=True` on the batch | `tests/test_turn_batch_cancel.py` |

### Bugs found and fixed while executing (not in the original review)

1. **Plan mode was a locked room.** `_is_plan_allowed` returned `Tool.allow_in_plan`
   (default `False`) for every registered tool — and no builtin declared it — so
   the dynamic check answered False for everything and `ExitPlanMode` itself was
   blocked with `blocked_by=plan_mode`. Now a missing declaration falls through
   to the documented static research/plan-ux list, and the two plan doors declare
   `allow_in_plan=True`.
2. **The bridge's background work was owned by whichever REQUEST started it.**
   `asyncio.create_task(_run())` inside `POST /api/turn` runs on the request's
   event loop; under starlette's TestClient that loop is created and destroyed
   per request, so the request never returned (the whole
   `tests/test_task_threading.py` file hung). `Runtime` now owns one daemon-thread
   loop; the turn runner, the bus consumer, the model pre-warm, the automations
   scheduler, the bots scheduler and the subconscious writer all run on it.
3. **`reset_hook_manager_for_tests()` re-registers the builtins** — widening the
   fact-guard matcher (a real security fix) broke every test that asserts its
   own hook behaviour; those tests now disarm builtins explicitly.
4. **`str` vs `object` dtype.** pandas 3 reports the text column kind as `str`,
   so `DataValidate` flagged every valid text column as `dtype_mismatch`.
   `_canonical_dtype` folds the aliases.
5. **Loop-safe schedulers.** `AutomationScheduler`/`BotScheduler` kept a task
   from a CLOSED loop looking "running" forever, and stopping it then raised
   "Event loop is closed" inside unrelated tests.
6. **The MCP line-cap test allocated a real 16 MiB payload** and raised
   `MemoryError` on this RAM-starved machine; the abort path is now exercised
   at a patched 4 KiB cap with the shipped constant asserted separately.
7. **`pytest-timeout` was missing.** It is now a dev extra and
   `timeout = 180` in `[tool.pytest.ini_options]` — a hang can never freeze the
   suite again (it did, twice).

## Next session's remaining work

- **P5 B2b**: the L2 asteroid write for a tool turn (off the hot path, through
  the subconscious writer) + the client galaxy contract
  (`generate.ts` star injection, `/api/memory/universe`).
- **P4 finish**: `schema/v0031_drop_goal_engine.py` (the `turn_feedback` repair +
  `goals` → `sessions`), `scripts/migrate_goals_to_sessions.py` run to
  completion, `/resume` reworked from the transcript, the `/agents` + `/agent
  create` Moons machinery deleted.
- **P6 finish**: the 401/403 surface, the Electron settings/memory pass, the
  remaining `docs/UI-STATUS-MASTER-SPEC.md` wave components.
- **Commit decision** — everything above is uncommitted; `git status` shows
  70 modified + 15 untracked test/doc files.
