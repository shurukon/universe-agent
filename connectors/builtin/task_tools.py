"""connectors/builtin/task_tools.py — the tracked task-family tools.

Round-7 RE: the reference engine ships a tracked task system used
by agent teams — TaskCreate/TaskUpdate/TaskGet/TaskList/TaskStop over one
shared ledger whose rows carry subject/description/activeForm/metadata,
addBlocks/addBlockedBy/owner dependency edges, and a status set that includes
"deleted". TaskOutput (Universe's TaskOutput; alt names AgentOutput/BashOutput)
reads the transcript a tool collected so far.

Universe implementation: an in-process ledger keyed by turn_id + task_id —
the same shape as the todos ledger (module-level, tests reset via
reset_for_tests()). TaskStop records a cooperative stop: running bridge
goals are the real stop authority (the events ring carries
task_stop_requested); the ledger row is the model-facing mirror.

All tools are consent="auto" (they touch nothing outside this process —
same discipline as todos.py), and the registry injects turn_id.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from core.agent.base_agent import Tool

_STATUSES = ("pending", "in_progress", "completed")
_DELETED = "deleted"
_STOPPED = "stopped"
_MAX_SUBJECT = 200
_MAX_DESCRIPTION = 2000
_MAX_OUTPUT_LINES = 2000

# turn_id -> {task_id -> row}. Module-level like the todos ledger: the
# registry and the bridge share one process, and tests reset it.
_tasks: dict[str, dict[str, dict[str, Any]]] = {}


def _ledger(turn_id: str) -> dict[str, dict[str, Any]]:
    return _tasks.setdefault(turn_id, {})


def _row_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "taskId": row["taskId"],
        "subject": row["subject"],
        "description": row["description"],
        "activeForm": row["activeForm"],
        "status": row["status"],
        "metadata": row["metadata"],
        "owner": row["owner"],
        "addBlocks": row["addBlocks"],
        "addBlockedBy": row["addBlockedBy"],
        "createdAt": row["createdAt"],
        "updatedAt": row["updatedAt"],
    }


def _find(task_id: str,
          turn_id: str) -> tuple[dict[str, Any], str] | None:
    """Find a task id either under the goal's own ledger or globally
    (background rows live under other goal keys — CC's TaskUpdate/Stop take
    a bare taskId). Returns (row, goal_key) or None."""
    row = _ledger(turn_id).get(task_id)
    if row is not None:
        return row, turn_id
    for key, ledger in _tasks.items():
        row = ledger.get(task_id)
        if row is not None:
            return row, key
    return None


def reset_for_tests() -> None:
    _tasks.clear()


def task_create(turn_id: str = "", subject: str = "", description: str = "",
                activeForm: str = "", metadata: Any = None,
                owner: str = "") -> dict[str, Any]:
    subject = str(subject or "").strip()
    description = str(description or "").strip()
    form = str(activeForm or "").strip() or subject
    if not subject:
        return {"ok": False, "error": "subject is required"}
    ledger = _ledger(turn_id)
    task_id = "tc-" + uuid.uuid4().hex[:12]
    now = time.time()
    ledger[task_id] = {
        "taskId": task_id, "subject": subject[:_MAX_SUBJECT],
        "description": description[:_MAX_DESCRIPTION],
        "activeForm": form[:_MAX_SUBJECT], "status": "pending",
        "metadata": metadata if isinstance(metadata, dict) else {},
        "owner": str(owner or ""), "addBlocks": [], "addBlockedBy": [],
        "createdAt": now, "updatedAt": now, "output": [],
    }
    from observability.events import emit
    emit("task_ledger", turn_id=turn_id or None, action="created",
         task=ledger[task_id]["taskId"])
    return {"ok": True, "taskId": task_id, "task": _row_view(ledger[task_id])}


def task_update(taskId: str = "", status: str = "",
                addBlocks: Any = None, addBlockedBy: Any = None,
                owner: str = "", subject: str = "", description: str = "",
                activeForm: str = "", metadata: Any = None,
                **_: Any) -> dict[str, Any]:
    # turn_id is NOT in this signature on purpose: taskId is unique (uuid)
    # and background rows live under other keys — CC's TaskUpdate needs no
    # runtime context either.
    found = _find(str(taskId or ""), turn_id="")
    if found is None:
        return {"ok": False, "error": f"task not found: {taskId}"}
    row, goal_key = found
    changed: list[str] = []
    if subject:
        row["subject"] = str(subject)[:_MAX_SUBJECT]
        changed.append("subject")
    if description:
        row["description"] = str(description)[:_MAX_DESCRIPTION]
        changed.append("description")
    if activeForm:
        row["activeForm"] = str(activeForm)[:_MAX_SUBJECT]
        changed.append("activeForm")
    if isinstance(metadata, dict):
        row["metadata"] = metadata
        changed.append("metadata")
    if owner:
        row["owner"] = str(owner)
        changed.append("owner")
    if isinstance(addBlocks, list) and addBlocks:
        merged = list(dict.fromkeys(row["addBlocks"] + [str(x) for x in addBlocks]))
        row["addBlocks"] = merged[:50]
        changed.append("addBlocks")
    if isinstance(addBlockedBy, list) and addBlockedBy:
        merged = list(dict.fromkeys(
            row["addBlockedBy"] + [str(x) for x in addBlockedBy]))
        row["addBlockedBy"] = merged[:50]
        changed.append("addBlockedBy")
    if status:
        if status == _DELETED:
            _tasks[goal_key].pop(row["taskId"], None)
            from observability.events import emit
            emit("task_ledger", turn_id=goal_key or None,
                 action="deleted", task=row["taskId"])
            return {"ok": True, "deleted": row["taskId"]}
        if status not in _STATUSES + (_STOPPED,):
            return {"ok": False,
                    "error": ("status must be one of "
                              f"{', '.join(_STATUSES + (_STOPPED,_DELETED))}")}
        row["status"] = status
        changed.append("status")
    row["updatedAt"] = time.time()
    return {"ok": True, "updated": changed or [], "task": _row_view(row)}


def task_get(taskId: str = "", turn_id: str = "",
             **_: Any) -> dict[str, Any]:
    found = _find(str(taskId or ""), turn_id)
    if found is None:
        return {"ok": False, "error": f"task not found: {taskId}"}
    row, _key = found
    return {"ok": True, "task": _row_view(row),
            "output": list(row["output"][-50:])}


def task_list(turn_id: str = "", **_: Any) -> dict[str, Any]:
    rows = sorted(_ledger(turn_id).values(), key=lambda r: r["createdAt"])
    return {"ok": True, "tasks": [_row_view(r) for r in rows],
            "count": len(rows)}


def task_stop(taskId: str = "", task_id: str = "", shell_id: str = "",
              turn_id: str = "", **_: Any) -> dict[str, Any]:
    """CC's TaskStop twin: camelCase taskId (schema), snake task_id (older
    alias), deprecated shell_id accepted. A ledger row is marked stopped; a
    bridge background run with matching id gets a stop-requested event on
    the ring (the bridge's own cancel path stays the real executor)."""
    tid = str(taskId or task_id or shell_id or "").strip()
    if shell_id and not (taskId or task_id):
        # honest deprecation echo, CC-style guidance
        note = ("shell_id is deprecated — pass task_id")
    else:
        note = ""
    if not tid:
        return {"ok": False, "error": "taskId (or deprecated shell_id) required"}
    found = _find(tid, turn_id)
    if found is not None:
        row, _key = found
        row["status"] = _STOPPED
        row["updatedAt"] = time.time()
    from observability.events import emit
    emit("task_stop_requested", turn_id=turn_id or None, task_id=tid)
    return {"ok": True, "taskId": tid, "stopped": True,
            "ledger_known": found is not None,
            **({"note": note} if note else {})}


def task_output(taskId: str = "", task_id: str = "", turn_id: str = "",
                **_: Any) -> dict[str, Any]:
    """Universe's TaskOutput (alt names AgentOutput/BashOutput in the alias
    table): the transcript collected so far for a task id."""
    tid = str(taskId or task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "taskId required"}
    row = _find(tid, turn_id)
    if row is not None:
        row, _key = row
    lines = list(row["output"]) if row is not None else []
    return {"ok": True, "taskId": tid,
            "ledger_known": row is not None,
            "status": (row or {}).get("status", "unknown"),
            "output": lines[-_MAX_OUTPUT_LINES:], "count": len(lines)}


def append_output(task_id: str, line: str, turn_id: str = "") -> None:
    """Internal: other Universe surfaces (orchestrator, bridge) may append
    transcript lines to a tracked task so TaskOutput has substance."""
    found = _find(task_id, turn_id)
    if found is not None:
        row, _key = found
        row["output"].append(str(line)[:800])
        del row["output"][:-_MAX_OUTPUT_LINES]


def register(reg) -> None:
    reg.register(Tool(
        name="TaskCreate", capability="TaskCreate",
        description=(
            "Create a tracked task for this goal — the shared ledger the "
            "user can watch in the tasks sidebar. The model uses it to "
            "record background or deferred work items it will pick up "
            "later, not for the live step-by-step todo chain (TodoWrite)."
        ),
        handler=task_create, consent="auto"))
    reg.register(Tool(
        name="TaskUpdate", capability="TaskUpdate",
        description=(
            "Update a tracked task: subject/description/activeForm/metadata, "
            "claim it (owner), add dependency edges (addBlocks/"
            "addBlockedBy), or set status (pending | in_progress | "
            "completed | stopped). status 'deleted' removes the row."
        ),
        handler=task_update, consent="auto"))
    reg.register(Tool(
        name="TaskGet", capability="TaskGet",
        description="Read one tracked task by taskId.",
        handler=task_get, consent="auto"))
    reg.register(Tool(
        name="TaskList", capability="TaskList",
        description=(
            "List tracked tasks for the current goal (empty input)."
        ),
        handler=task_list, consent="auto"))
    reg.register(Tool(
        name="TaskStop", capability="TaskStop",
        description=(
            "Stop a background task or tracked task id. shell_id is "
            "deprecated — pass task_id."
        ),
        handler=task_stop, consent="auto"))
    reg.register(Tool(
        name="TaskOutput", capability="TaskOutput",
        description=(
            "Read the transcript accumulated so far for a background or "
            "tracked task id (Universe TaskOutput; alias names AgentOutput/"
            "BashOutput accepted)."
        ),
        handler=task_output, consent="auto"))
