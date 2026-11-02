"""core/agent/steer.py — user course-corrections for a RUNNING goal.

The desktop bridge (POST /api/turn/{id}/steer) pushes a message here; the
orchestrator drains it at the nearest decision point (the boundary between
plan steps — never mid-tool, so a tool's current round always finishes).
The whole exchange is in-memory and goal-scoped: nothing persists, a
restart simply drops pending steers.
"""
from __future__ import annotations

import time
from typing import Any

# turn_id -> FIFO of {"text": str, "ts": float}
_queues: dict[str, list[dict[str, Any]]] = {}

MAX_TEXT = 4000
MAX_PENDING = 16


def push_steer(turn_id: str, text: str) -> dict[str, Any]:
    """Queue a steering message for a running goal. Returns the queued item."""
    clean = " ".join((text or "").split())[:MAX_TEXT]
    if not clean:
        raise ValueError("empty steer text")
    q = _queues.setdefault(turn_id, [])
    if len(q) >= MAX_PENDING:
        q.pop(0)  # never let a runaway client wedge the orchestrator
    item = {"text": clean, "ts": time.time()}
    q.append(item)
    return item


def take_steer(turn_id: str) -> list[dict[str, Any]]:
    """Drain and return every pending steer for the goal (FIFO)."""
    return _queues.pop(turn_id, [])


def pending_count(turn_id: str) -> int:
    return len(_queues.get(turn_id, []))


def clear(turn_id: str) -> None:
    _queues.pop(turn_id, None)
