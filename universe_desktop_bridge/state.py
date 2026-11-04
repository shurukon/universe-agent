"""universe_desktop_bridge/state.py — live goal/task registry.

Tracks every goal spawned through the bridge: its asyncio task, its events
(as a bounded ring, for late subscribers / reconnect refill), and its cancel
handle. This is the only mutable bridge state.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

# events kept per goal for late subscribers (matches the timeline's value:
# the UI refills gaps on reconnect, it never replays everything)
GOAL_EVENT_RING = 512

# terminal goals older than this are evicted from the live registry
# (their durable rows stay in the DB): the bridge is a live view, not an
# archive — without a TTL a long-lived process accumulates every goal
# it ever started.
GOAL_TTL_S = 30 * 60


class GoalTask:
    """One goal started via POST /api/turn."""

    def __init__(self, turn_id: str, text: str, task_id: str = "") -> None:
        self.turn_id = turn_id
        self.text = text
        self.task_id = task_id        # parent task container (kind='task')
        self.created_at = round(time.time(), 3)
        self.completed_at: float | None = None  # set on terminal status
        self.status = "queued"          # queued -> running -> completed|failed|cancelled
        self.task: asyncio.Task | None = None
        self.result: dict[str, Any] | None = None
        self.error: str = ""
        self.events: list[dict[str, Any]] = []
        self.seq: int = 0               # monotonic per-goal event sequence
        self.dropped: int = 0           # ring-overflow count (gap detection)

    def set_status(self, status: str) -> None:
        """Single choke point for status changes: stamps completed_at on
        terminal transitions so the TTL pruner can evict finished goals."""
        self.status = status
        if status in ("completed", "failed", "cancelled"):
            self.completed_at = time.time()

    def add_event(self, event: dict[str, Any]) -> None:
        """Append one bus event to the ring. The seq is stamped at the
        single source (EventBus.emit) — the ring only records it and keeps
        its own monotonic view in sync for refill (after_seq) queries."""
        event = dict(event)
        seq = event.get("seq")
        if isinstance(seq, int) and seq > self.seq:
            self.seq = seq
        elif not isinstance(seq, int):
            # defensive: a hand-fed event with no seq (tests) still gets
            # a monotonic number so refill queries keep working
            self.seq += 1
            event["seq"] = self.seq
        self.events.append(event)
        self._persist_event(event)
        if len(self.events) > GOAL_EVENT_RING:
            overflow = len(self.events) - GOAL_EVENT_RING
            del self.events[:-GOAL_EVENT_RING]
            self.dropped += overflow

    def _persist_event(self, event: dict[str, Any]) -> None:
        """Durable wire ring: append every event to
        ``~/.universe/rings/<turn_id>.jsonl`` so a goal survives the
        process. The in-memory ring is a live view with a 512-event cap;
        without this file a reopened task could only be rebuilt from the
        final_summary's tool_history — tool calls and todos — and every
        other row the timeline showed live (thinking, plan, subagents,
        artifacts…) vanished between sessions. The file IS the full
        stream the user saw, replayed verbatim on reopen. Observability
        must never break the observed work: best-effort, never raises."""
        try:
            from config import get_config
            ring_dir = Path(get_config().home) / "rings"
            ring_dir.mkdir(parents=True, exist_ok=True)
            with open(ring_dir / f"{self.turn_id}.jsonl", "a",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False,
                                    default=str) + "\n")
        except Exception:
            pass


class BridgeState:
    """Process-wide registry of bridge-started goals."""

    def __init__(self) -> None:
        self.goals: dict[str, GoalTask] = {}
        self._order: list[str] = []
        self._seen: set[str] = set()

    def touch(self, turn_id: str) -> None:
        """Remember a goal id (even one we didn't start) for /api/sessions."""
        if turn_id in self._seen:
            return
        self._seen.add(turn_id)
        self._order.append(turn_id)

    def create(self, turn_id: str, text: str, task_id: str = "") -> GoalTask:
        gt = GoalTask(turn_id, text, task_id=task_id)
        self.goals[turn_id] = gt
        self.touch(turn_id)
        return gt

    def get(self, turn_id: str) -> GoalTask | None:
        return self.goals.get(turn_id)

    def running(self) -> list[GoalTask]:
        return [gt for gt in self.goals.values()
                if gt.status in ("queued", "running")]

    def prune_expired(self, ttl_s: float = GOAL_TTL_S) -> int:
        """Evict terminal goals older than the TTL. Durable rows stay in
        the DB (goal_detail serves them); only the live registry (tasks,
        ring buffers) is released. Returns the evicted count."""
        now = time.time()
        expired = [gid for gid, gt in self.goals.items()
                   if gt.status not in ("queued", "running")
                   and (gt.completed_at or gt.created_at) < now - ttl_s]
        for gid in expired:
            gt = self.goals.pop(gid, None)
            if gt is not None and gt.task is not None:
                try:
                    if not gt.task.done():
                        gt.task.cancel()
                except Exception:
                    pass
        return len(expired)


_state: BridgeState | None = None


def get_state() -> BridgeState:
    global _state
    if _state is None:
        _state = BridgeState()
    return _state


def reset_state_for_tests() -> BridgeState:
    global _state
    _state = BridgeState()
    return _state
