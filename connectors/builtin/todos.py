"""connectors/builtin/todos.py — the visible todo list (plan-progress tools).

The ZCode TodoWrite twin: one list per goal, full-replace semantics — the
model sends the WHOLE list every time and the list IS the state. The list
lives in memory keyed by turn_id (a goal's todos die with its process
lifetime, same as ZCode's per-task todos); the todo_updated event is
persisted through the events ring like every other event, so the UI's
reconnect refill recovers the panel, and todo.read lets the model itself
re-ground after compaction.

Both tools are consent=auto and touch nothing outside the process — the
gate passes them (no path/url surface). The registry injects turn_id
(runtime context the model must not fabricate), like skill.activate.
"""
from __future__ import annotations

import json
from typing import Any

from core.agent.base_agent import Tool

MAX_TODOS = 50
MAX_CONTENT = 200
_STATUSES = ("pending", "in_progress", "completed")

# turn_id -> normalized list. Module-level like the steer bus: the registry
# and the bridge share one process, and tests reset via reset_for_tests().
_todos: dict[str, list[dict[str, str]]] = {}


def _normalize(raw: Any) -> dict[str, Any]:
    """Validate + normalize a whole-list write. Returns
    {"ok": False, "error": …} or {"ok": True, "todos": […]}."""
    if isinstance(raw, str):
        # runtime tolerance: models occasionally stringify the array
        # (OpenAI providers with weak typing) — parse it if it is JSON
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                raw = parsed
        except (ValueError, TypeError):
            pass
    if not isinstance(raw, list):
        return {"ok": False, "error": "todos must be a list of {content, status}"}
    if len(raw) > MAX_TODOS:
        return {"ok": False,
                "error": f"too many todos ({len(raw)} > {MAX_TODOS}) — split the goal"}
    out: list[dict[str, str]] = []
    saw_in_progress = False
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return {"ok": False,
                    "error": f"todo[{i}] must be an object with content and status"}
        content = str(item.get("content", "")).strip()
        status = str(item.get("status", "pending")).strip()
        if not content:
            return {"ok": False, "error": f"todo[{i}].content is empty"}
        if status not in _STATUSES:
            return {"ok": False,
                    "error": f"todo[{i}].status must be one of {', '.join(_STATUSES)}"}
        # at most ONE in_progress at a time (ZCode semantics): the first
        # wins, later ones degrade to pending
        if status == "in_progress":
            if saw_in_progress:
                status = "pending"
            else:
                saw_in_progress = True
        # CC zod: each todo carries an imperative activeForm ("Run the test
        # suite") shown while the step is in_progress; optional in Universe,
        # defaulting to the content so old callers keep working.
        active_form = str(item.get("activeForm", "") or "").strip() or content
        out.append({"content": content[:MAX_CONTENT], "status": status,
                    "activeForm": active_form[:MAX_CONTENT]})
    return {"ok": True, "todos": out}


async def todo_write(turn_id: str = "", todos: list | None = None,
                     **_: Any) -> dict[str, Any]:
    norm = _normalize(todos)
    if not norm.get("ok"):
        return norm
    _todos[turn_id] = norm["todos"]
    idx = next((i for i, t in enumerate(norm["todos"])
                if t["status"] == "in_progress"), -1)
    from observability.events import emit
    emit("todo_updated", turn_id=turn_id or None,
         todos=norm["todos"], in_progress_idx=idx)
    return {"ok": True, "count": len(norm["todos"]),
            "in_progress_idx": idx, "todos": norm["todos"]}


async def todo_read(turn_id: str = "", **_: Any) -> dict[str, Any]:
    todos = list(_todos.get(turn_id, []))
    idx = next((i for i, t in enumerate(todos)
                if t["status"] == "in_progress"), -1)
    return {"ok": True, "todos": todos, "count": len(todos),
            "in_progress_idx": idx}


def reset_todos_for_tests() -> None:
    _todos.clear()


def register(reg) -> None:
    reg.register(Tool(
        name="TodoWrite",
        capability="TodoWrite",
        description=(
            "Maintain the goal's visible todo list. Send the WHOLE list every "
            "time (full replace): [{content, status, activeForm}] where "
            "status is pending | in_progress | completed and activeForm is "
            "the imperative phrase shown while the step runs (defaults to "
            "content). At most one in_progress. Use it whenever the plan "
            "has 2+ steps — the user watches this list live in the Progress "
            "panel."
        ),
        handler=todo_write,
        consent="auto",
    ))
    reg.register(Tool(
        name="TodoRead",
        capability="TodoRead",
        description="Read back the goal's current todo list (recover after compaction).",
        handler=todo_read,
        consent="auto",
    ))
