"""observability/events.py — the in-process async event bus.

The backbone of the Universe desktop live experience: every state change an
agent makes (goal lifecycle, agent steps, tool calls, skill activations,
memory writes, consent prompts) is emitted here, and every subscriber —
today the FastAPI desktop bridge's WebSocket — receives a sanitized copy.

Design constraints honored:
  * No blocking: emit is fire-and-forget; slow subscribers get bounded
    queues with oldest-dropped (a UI must never stall the orchestrator).
  * Sanitized: payloads pass through security.secret_filter so secrets
    never leave the process.
  * Zero-cost when unused: with no subscribers, emit builds nothing.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import time
from typing import Any, AsyncIterator

from security.secret_filter import redact_dict

# Which fields of an event payload get secret-redacted. The whole event is
# redacted anyway; this list is only documentation of the important ones.
_MAX_QUEUE = 512          # per-subscriber bound; oldest events dropped
_MAX_EVENT_BYTES = 32_768  # hard cap on a single serialized event
_GOAL_SEQ_MAX = 1000      # bounded per-goal seq table (engine-fix round)


class EventBus:
    """Fan-out async event bus with per-subscriber bounded queues."""

    def __init__(self) -> None:
        self._subs: dict[int, asyncio.Queue[dict[str, Any]]] = {}
        self._next_id = 0
        # per-subscriber oldest-dropped counts: a UI that sees dropped > 0
        # knows its stream has gaps and should refill via goal_events
        self._dropped: dict[int, int] = {}
        # per-goal monotonic seq, stamped HERE at the single source. Every
        # copy of an event (every subscriber queue) carries the SAME seq, so
        # the bridge ring, the live WS and the reconnect refill agree and
        # the UI's seq-dedup can never miss a re-fed duplicate (the old
        # flow stamped the ring and the socket independently — two
        # numberings for one event).
        self._goal_seq: dict[str, int] = {}
        # the goal currently executing in THIS task context, so emissions
        # from nested library code (tool registry, memory) carry turn_id
        # even when the caller doesn't pass it
        self._current_goal: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "universe_current_goal", default=None)

    # -- lifecycle ---------------------------------------------------------

    def subscribe(self) -> "Subscription":
        """Subscribe to all future events. Returns an async iterator."""
        sub_id = self._next_id
        self._next_id += 1
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subs[sub_id] = q
        return Subscription(self, sub_id, q)

    def unsubscribe(self, sub_id: int) -> None:
        self._subs.pop(sub_id, None)
        self._dropped.pop(sub_id, None)

    def dropped(self, sub_id: int) -> int:
        """Oldest-dropped count for one subscriber (gap detection)."""
        return self._dropped.get(sub_id, 0)

    # -- emission ----------------------------------------------------------

    def goal_seq(self, turn_id: str | None) -> int:
        """The next monotonic seq for a goal (single numbering source).
        Called from emit() for goal events; the bridge ring and refill
        reuse the value instead of stamping their own."""
        if not turn_id:
            return 0
        n = self._goal_seq.get(turn_id, 0) + 1
        self._goal_seq[turn_id] = n
        # bounded memory: a completed goal kept one int entry FOREVER, so a
        # long-lived bridge process grew this dict without bound. Evict the
        # oldest-inserted entries beyond the cap; a pruned goal that somehow
        # emits again just restarts at 1 (the UI ring only replays recent
        # goals, so a renumbered fossil is harmless).
        if len(self._goal_seq) > _GOAL_SEQ_MAX:
            for old in list(self._goal_seq)[:len(self._goal_seq) - _GOAL_SEQ_MAX]:
                self._goal_seq.pop(old, None)
        return n

    def emit(self, event_type: str, *, turn_id: str | None = None,
             **fields: Any) -> None:
        """Emit an event to all subscribers. Never raises, never blocks.
        Goal events are stamped with a per-goal monotonic seq HERE, at the
        single source, so every consumer (ring buffer, live socket,
        reconnect refill) shares one numbering and the UI's seq-dedup can
        never miss a re-fed duplicate."""
        try:
            if not self._subs:
                return
            if turn_id is None:
                turn_id = self._current_goal.get()
            event: dict[str, Any] = {
                "type": event_type,
                "ts": round(time.time(), 3),
                "turn_id": turn_id,
            }
            if turn_id:
                event["seq"] = self.goal_seq(turn_id)
            event.update(fields)
            event = _sanitize(event)
            for sub_id, q in self._subs.items():
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # drop the oldest — a live UI prefers fresh state over
                    # a complete history it can anyway re-fetch via timeline
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                        self._dropped[sub_id] = self._dropped.get(sub_id, 0) + 1
                    except Exception:
                        pass
        except Exception:
            # observability must never break the work being observed
            pass

    def bind_goal(self, turn_id: str | None) -> Any:
        """Context-manager: stamp all emissions inside the block with
        turn_id (used by the desktop bridge around run_goal)."""
        return _GoalBinder(self, turn_id)


class _GoalBinder:
    def __init__(self, bus: EventBus, turn_id: str | None) -> None:
        self._bus = bus
        self._goal_id = turn_id
        self._token: contextvars.Token | None = None

    def __enter__(self) -> "_GoalBinder":
        self._token = self._bus._current_goal.set(self._goal_id)
        return self

    def __exit__(self, *_: Any) -> None:
        if self._token is not None:
            self._bus._current_goal.reset(self._token)


class Subscription:
    """Async-iterator view over one subscriber's queue."""

    def __init__(self, bus: EventBus, sub_id: int,
                 queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._bus = bus
        self._id = sub_id
        self._q = queue

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events as they arrive. Ends on unsubscribe()."""
        while True:
            item = await self._q.get()
            if item is _SENTINEL:
                return
            yield item

    async def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Await a single event; None on timeout or stream end. Lets the WS
        loop interleave heartbeats without a worker task per socket."""
        try:
            item = await asyncio.wait_for(self._q.get(), timeout) \
                if timeout is not None else await self._q.get()
        except asyncio.TimeoutError:
            raise
        if item is _SENTINEL:
            return None
        return item

    def stop(self) -> None:
        self._bus.unsubscribe(self._id)
        try:
            self._q.put_nowait(_SENTINEL)
        except asyncio.QueueFull:
            pass


_SENTINEL: dict[str, Any] = {"type": "__stop__"}

# The event skeleton must survive even a total size blowout: type/ts/
# turn_id/seq are what consumers key on. Everything else is droppable
# detail and gets replaced with an honest marker (engine-fix round).
_EVENT_SKELETON_FIELDS = ("type", "ts", "turn_id", "seq")
_SIZE_CAP_NOTE = "(omitted: size cap)"


def _json_len(obj: Any) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:
        return _MAX_EVENT_BYTES + 1  # unmeasurable = over budget


def _drop_to_fit(event: dict[str, Any], budget: int) -> dict[str, Any]:
    """Progressively drop/truncate the heaviest detail fields until the
    event fits the budget. Skeleton fields stay; a dropped field keeps its
    key with the marker value, so the shape the UI keys on never changes."""
    out = dict(event)
    droppable = [k for k in out if k not in _EVENT_SKELETON_FIELDS]
    while droppable and _json_len(out) > budget:
        biggest = max(droppable, key=lambda k: _json_len(out.get(k)))
        out[biggest] = _SIZE_CAP_NOTE
        droppable.remove(biggest)
    if _json_len(out) > budget:
        # skeleton + markers alone are still over (tiny budget or giant
        # base fields): hard-truncate whatever strings remain
        for k in list(out):
            v = out[k]
            if isinstance(v, str) and len(v) > 512:
                out[k] = v[:512] + "…"
    return out


def _sanitize(event: dict[str, Any]) -> dict[str, Any]:
    """Secret-redact and size-cap an event before it leaves the process."""
    try:
        clean = redact_dict(event)
        capped = _cap(clean, _MAX_EVENT_BYTES)
        # the REAL hard cap (engine-fix round): _cap's per-level budgets
        # never measured the assembled result, so the advertised 32,768-
        # byte cap was unenforced — a huge tool_events payload sailed
        # through. Measure the whole thing now; drop detail fields until
        # it fits.
        if _json_len(capped) > _MAX_EVENT_BYTES:
            capped = _drop_to_fit(capped, _MAX_EVENT_BYTES)
        return capped
    except Exception:
        return {"type": event.get("type", "unknown"),
                "ts": event.get("ts", round(time.time(), 3)),
                "turn_id": event.get("turn_id"),
                "error": "event_sanitization_failed"}


def _cap(obj: Any, budget: int) -> Any:
    """Recursively serialize-safe an object and cap its JSON size."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        if isinstance(obj, str) and len(obj) > 2048:
            return obj[:2048] + "…"
        return obj
    if isinstance(obj, dict):
        out = {}
        running = 0
        for k, v in obj.items():
            if not isinstance(k, str):
                k = str(k)
            cv = _cap(v, budget // 4)
            running += len(k) + 2
            out[k] = cv
        return out
    if isinstance(obj, (list, tuple)):
        return [_cap(v, budget // 4) for v in obj[:64]]
    return str(obj)[:512]


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Module-level accessor used by every emit site."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus_for_tests() -> EventBus:
    global _bus
    _bus = EventBus()
    return _bus


def emit(event_type: str, *, turn_id: str | None = None,
         **fields: Any) -> None:
    """Convenience module-level emit — the one import every tap site needs."""
    get_event_bus().emit(event_type, turn_id=turn_id, **fields)
