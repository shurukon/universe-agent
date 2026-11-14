# Sessions — the unit of work, and of persistence

**Status (2026-11-11): the goal engine is DELETED. The session is everything.**

## What a session is

A session is one conversation. It owns:

| Piece | Where | What it holds |
|---|---|---|
| the container row | `sessions` table (`id`, `title`, `project_id`, `archived`, `deleted`, `pinned`, timestamps) | the sidebar entry |
| the transcript | `~/.universe/sessions/<id>/transcript.jsonl` | the user messages, the assistant replies, the tool results, the turn_start/turn_end rows — the append-only record of what was said |
| the event log | `<project>/sessions/<id>/events.jsonl` | EVERY bus event of the session, verbatim: the thinking deltas, the tool calls and their results, the plan rows, the subagent rows, the artifacts, the terminal event |

There is no goal row, no planet, no checkpoint, no ladder, no plan annex.
`GAX_ENGINE=goal` is read and ignored (it warns).

## The sessions folder inside the project

```
<project_root>/
└── sessions/
    └── <session_id>/
        └── events.jsonl        ← one JSON object per line, append-only
```

The project root is resolved in this order: the session's own project
(`projects.root`) → the configured `project_root` → the Universe home. The
folder is created on the first event of a session and is **git-ignored**
when the project happens to be a repository.

Why inside the project (user order, 2026-11-11): the session belongs to the
work, so the work carries it. Move the project, move the sessions with it.

## What was broken before, and what fixed it

| Symptom | Cause | Fix |
|---|---|---|
| Reopening a session showed **no events** — no thinking, no tool calls | the timeline was rebuilt from the `goals` table's message children; that table is gone since the goal engine was deleted | the durable **session log** is the authority (`GET /api/turn/{session}/events` reads it first) |
| Reopening a session showed **no messages** | `_task_messages()` read the same dead `goals` rows | `_session_messages()` rebuilds the thread from the **transcript**; the goals rows survive only as a legacy fallback |
| The live ring (`~/.universe/rings/<turn>.jsonl`) is per TURN and in memory | a restart or a TTL eviction emptied it | the log is per SESSION and on disk |
| The fallback synthesised a fake timeline from `final_summary.tool_history` | that was all that was left | nothing is synthesised any more — the real stream is replayed |

## How the events get into the log

```
the turn engine  ──emit──▶  EventBus  ──▶  live subscribers (the UI's WS)
                                    └──▶  core/session/event_log.append_event()
                                            → <project>/sessions/<id>/events.jsonl
```

The bus stamps the session id from the **session binding** the session
engine takes for the whole turn (`bus.push_session(session_id)`), so every
event — the engine's, the tools', the bridge's `turn_started`, the terminal
`turn_completed` — is filed under the right session automatically. The
bridge's bus consumer files the leftovers (bot turns, synthetic turns) under
the turn's own session.

Each row carries three numbers:

| Field | Meaning |
|---|---|
| `seq` | the bus's per-TURN counter (the UI dedups on it) |
| `session_seq` | the log's own counter across the whole session (the restore's `after_seq`) |
| `ts` | the wall clock |

## The restore (open a session)

1. `GET /api/sessions` → the sidebar (the `sessions` table).
2. `GET /api/turn/{id}` → the thread messages (the transcript).
3. `GET /api/turn/{id}/events` → the whole event stream (the durable log).

Measured on the running desktop app (2026-11-11): a session with 8 events
replayed in **78 ms**; a 5000-event log reads in well under a second. The
budget was 2 seconds.

## Tests

| File | What it pins |
|---|---|
| `tests/test_session_event_log.py` | the log lands in the project, every event is there, the restore is fast, no goals row is involved |
| `tests/test_session_restore_bridge.py` | the bridge's own restore path (events + messages), live and after the live state is gone |
| `tests/test_no_goal_system.py` | the proof: one engine, no goal rows, no goal machinery in the runtime |
| `tests/test_session_tool_persistence.py` | the tool protocol rows in the transcript (role:"tool" + their call ids) |
