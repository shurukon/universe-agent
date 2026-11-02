# BridgeEvent contract — v1 (frozen 2026-10-01)

The single source of truth for every event that crosses the desktop
bridge's WebSocket. Emission lives in `observability/events.py`
(`EventBus.emit`); every consumer — the bridge ring buffer, the live WS
(`/api/events/ws`), the reconnect refill (`GET /api/session/{id}/events`),
and the UI store (`stores/chat.ts` seq-dedup) — sees the SAME payload.

## The rule that governs the whole rebuild program

**Additive only.** No type is deleted, renamed, or repurposed, and no
existing field changes name or meaning. New types and new optional fields
may be appended at any time (consumers must ignore unknown keys). If a
semantics change is ever truly needed, a NEW type is introduced and the
old one is kept emitting until a coordinated deprecation.

## Envelope (every event)

```jsonc
{
  "type": "tool_finished",     // one of the types below — never renamed
  "ts": 1760000000.123,        // unix seconds, 3dp, set at emit()
  "goal_id": "g-2026...",      // string | null
  "seq": 42,                   // per-session monotonic 1..N — present iff
                               // goal_id is set; single numbering source
                               // is the bus itself (ring, live WS and
                               // refill share it; UI dedups on it)
  // ...payload fields per type below
}
```

Guarantees provided by the bus, not repeated per type:

- **Sanitized**: every payload passes `security.secret_filter.redact_dict`
  before fan-out; strings >2048 chars are truncated by `_cap`, single
  events are capped at 32 KiB, lists at 64 items.
- **Never blocks / never raises**: slow subscribers get bounded queues
  (512) with oldest-drop; a dropped count is kept per subscriber for gap
  detection (refill via `GET /api/session/{id}/events`).
- **`goal_id` propagation**: emissions inside a bound session context carry
  `goal_id` even when the caller doesn't pass it (`bind_goal`
  contextvar); passing `goal_id=None` explicitly does NOT force null —
  the contextvar fills it.

## Event types (v1 inventory — 27 wire types)

### Session lifecycle

| type | emitted from | payload fields |
|---|---|---|
| `goal_started` | bridge `app.py` (HTTP), `voice.py` (voice) | `text` (≤2000), `language?`, `task_id?`, `via?=voice` |
| `goal_planned` | `base_agent.py` (visible plan parsed) | `plan: [{step:int, agent, instruction(≤200)}]` |
| `goal_completed` | `orchestrator.py` | `reply?`, `conversation?=true` (chat branch) |
| `goal_failed` | `orchestrator.py` | `error` (reason) |
| `goal_cancelled` | `orchestrator.py` | `reason="user"` |
| `chat_reply` | `orchestrator.py` (chat branch) | `reply`, `via="orchestrator"` |
| `chat_reply_rejected` | `orchestrator.py` (anti-hallucination gate) | `reason` |

Note: session-branch completions emit `goal_completed` with a summary
`reply` as well; `conversation=true` distinguishes the chat branch.

### Agent loop

| type | emitted from | payload fields |
|---|---|---|
| `llm_round` | `base_agent.py` | `agent`, `round_index`, `has_tools` |
| `llm_delta` | `base_agent.py` (stream tap) | `agent`, `text` (tail ≤800 chars) |
| `context_compacted` | `base_agent.py` | `agent`, `approx_tokens_before` |
| `tool_loop_throttled` | `base_agent.py` | `agent`, `consecutive_failures`, `round_index` |
| `steer_received` | `base_agent.py` (drain point) | `agent`, `messages: [≤200-char strings]` |

### Tool lifecycle (single choke-point: `connectors/builtin/__init__.py` `ToolRegistry.call`)

| type | payload fields |
|---|---|
| `tool_requested` | `agent`, `tool`, `args` |
| `tool_running` | `agent`, `tool`, `capability`, `args_summary` |
| `tool_finished` | `agent`, `tool`, `duration_ms`, `detail?` |
| `tool_failed` | `agent`, `tool`, `error`(≤200), `blocked_by?`, `duration_ms` |
| `tool_blocked` | same shape as `tool_failed` (`blocked_by` set, e.g. `"consent"`) |
| `artifact_created` | `agent`, `tool`(`file.write|file.edit`), `path`(≤400), `bytes` |

`args`/`args_summary` are gate-shaped summaries — full argument payloads
for consent travel on `consent_required.args_full`, not here.

### Swarm

| type | emitted from | payload fields |
|---|---|---|
| `subagent_spawned` | `swarm.py` | `parent`, `agent`(role), `brief`(≤200), `depth` |
| `subagent_complete` | `swarm.py` | `agent`(role), `success: bool`, `summary`(≤200) |

### Skills

| type | emitted from | payload fields |
|---|---|---|
| `skill_searched` | `core_agents/agents.py` | `agent`, `skill`, `matches` |
| `skill_read` | `core_agents/agents.py` | `agent`, `skill`, `solar_system`, `orbit`, `read_ok` |
| `skill_activated` | `core_agents/agents.py` | `agent`, `skill`(≤120), `skill_name`, `solar_system`, `orbit` |

### Memory

| type | emitted from | payload fields |
|---|---|---|
| `memory_updated` | `core/memory/__init__.py` | `layer` (`"L2"`/`"L3"`), plus either `kind="asteroid", gravity_score, success` or `kind="star_born", topic(≤80), domain` |

### Consent (bridge)

| type | emitted from | payload fields |
|---|---|---|
| `consent_required` | `universe_desktop_bridge/consent.py` | `pending_id`, `tool`, `agent`, `args_summary`, `args_full`, `timeout_s` |
| `consent_decided` | `consent.py` | `pending_id`, `decision` (`"approved"`/`"denied"`), `reason?="timeout"` |

Timeout denies: `consent_decided{decision:"denied", reason:"timeout"}`.

### Steering (bridge)

| type | emitted from | payload fields |
|---|---|---|
| `steer_queued` | bridge `app.py` | `text`(≤200), `queued_as`?(="followup" — v1.1, present only when the message was buffered as a follow-up instead of steered into the running session) |

### Providers / settings / integrity

| type | emitted from | payload fields |
|---|---|---|
| `provider_retry` | `providers/client.py` | `agent`, `provider`, `model`, `attempt`, `max_retries`, `kind` |
| `provider_halt` | `orchestrator.py` | `reason`, `error`(≤300) |
| `catalog_probes_updated` | bridge `app.py` | `local_reachable: bool` |
| `settings_updated` | bridge `app.py` | `**changed` (only the keys that changed) |
| `audit_chain_broken` | bridge `app.py` | `first_bad_line` |

`spam` / `weird_event` appear only in test fixtures — NOT wire types; do
not emit them in production code.

The UI additionally groups some tool-detail metadata (`browser_meta`,
`browser_error`) INSIDE `tool_finished.detail` payloads — those are
detail sub-shapes of `tool_finished`, not bus types of their own.

## v1 → v1.x additions log (append only)

| date | addition | phase |
|---|---|---|
| 2026-10-01 | contract frozen (this document) | Phase 0 |
| 2026-10-02 | `todo_updated` (`todos`, `in_progress_idx`) — from `connectors/builtin/todos.py` | Phase 1 |
| 2026-10-02 | `hook_intercepted` (`hook_id`, `event`, `action`, `tool`, `agent`, `message`) — from `ToolRegistry.call` | Phase 1 |
| 2026-10-02 | `context_usage` (`round_index`, `used_tokens`, `window_tokens`, `window_is_estimate`, `model`) — from `base_agent.run_tool_loop` | Phase 1 |
| 2026-10-02 | `question_required` (`pending_id`, `question`, `options`, `allow_free_text`, `timeout_s`) + `question_decided` (`pending_id`, `reason`) — from `universe_desktop_bridge/questions.py` | Phase 1 |
| 2026-10-02 | `automation_fired` (`automation_id`, `name`, `schedule`, `prompt`) — from `schedule/` | Phase 1 |
| 2026-10-02 | `steer_queued` gains OPTIONAL `queued_as` field (additive; absent on the classic steer path) | Phase 1 |
| 2026-10-03 | `automation_stalled` (`automation_id`, `name`, `schedule`, `reason`) — never-fire schedule disables the row loudly | Phase 1 review |
| 2026-10-03 | `automation_failed` (`automation_id`, `name`, `schedule`, `prompt`, `error`) — replaces the differently-shaped error reuse of `automation_fired` | Phase 1 review |
| 2026-10-03 | `permission_mode_changed` (`mode`, `previous`) — mode chip stays live when plan.submit flips plan→full | Phase 1 review |
| 2026-10-03 | `steer_queued` gains OPTIONAL `dropped` field with `queued_as="dropped"` (fail-safe queue drain) | Phase 1 review |
