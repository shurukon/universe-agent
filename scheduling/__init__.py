"""schedule/ — automations: cron-scheduled goal runs (shell parity).

A scheduler task ticks every TICK_SECONDS (30 s) and fires every
automation whose next_run_at has passed. Firing = running the prompt as
its own goal on the orchestrator, then emitting automation_fired.

Design rules (the kozum overflow lesson):
  * tick, never a long sleep — the loop re-reads the wall clock every
    cycle, so an OS sleep/wake can never overshoot a schedule
  * fire-and-forget goals — the tick never awaits a goal, so a slow goal
    never delays other automations
  * missed-run policy: fire at most ONE catch-up run per automation per
    tick (a machine that slept through 50 firings gets one, not 50)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from scheduling.cron import CronError, CronSchedule

TICK_SECONDS = 30.0


class AutomationScheduler:
    """Ticks the automations table and fires due goals."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running: set[str] = set()
        self._tasks: set["asyncio.Task[Any]"] = set()
        # last tick-error emit time — rate-limits automation_failed to one
        # per 10 min (the old comment claimed this held "automation ids",
        # which is self._running)
        self._last_tick_error: float = 0.0

    # -- persistence helpers ------------------------------------------------

    @staticmethod
    def _st():
        from storage.local import get_storage
        return get_storage()

    def _row_to_dict(self, row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "schedule": row["schedule"],
                "prompt": row["prompt"], "enabled": bool(row["enabled"]),
                "last_run_at": row["last_run_at"], "next_run_at": row["next_run_at"],
                "created_at": row["created_at"]}

    # -- CRUD (used by the cron.* tools) ------------------------------------

    def create(self, name: str, schedule: str, prompt: str) -> dict[str, Any]:
        name = str(name or "").strip()[:120]
        prompt = str(prompt or "").strip()[:4000]
        spec = CronSchedule(str(schedule or "").strip())  # raises CronError
        if not name or not prompt:
            raise CronError("name and prompt are required")
        aid = f"auto-{uuid.uuid4().hex[:12]}"
        now = time.time()
        nxt = spec.next_after(now)
        self._st().execute(
            "INSERT INTO automations(id, name, schedule, prompt, enabled, "
            "last_run_at, next_run_at, created_at) VALUES(?,?,?,?,1,NULL,?,?);",
            (aid, name, spec.spec, prompt, nxt, now))
        row = self._st().query_one("SELECT * FROM automations WHERE id=?;", (aid,))
        return self._row_to_dict(row)

    def list(self) -> list[dict[str, Any]]:
        rows = self._st().query_all(
            "SELECT * FROM automations ORDER BY created_at DESC;")
        return [self._row_to_dict(r) for r in rows]

    def update(self, aid: str, *, name=None, schedule=None, prompt=None,
               enabled=None) -> dict[str, Any] | None:
        row = self._st().query_one("SELECT * FROM automations WHERE id=?;", (aid,))
        if row is None:
            return None
        sets, params = [], []
        if name is not None:
            sets.append("name=?")
            params.append(str(name).strip()[:120])
        if schedule is not None:
            spec = CronSchedule(str(schedule).strip())
            sets.append("schedule=?")
            params.append(spec.spec)
            sets.append("next_run_at=?")
            params.append(spec.next_after(time.time()))
        if prompt is not None:
            sets.append("prompt=?")
            params.append(str(prompt).strip()[:4000])
        if enabled is not None:
            sets.append("enabled=?")
            params.append(1 if enabled else 0)
            if enabled and row["next_run_at"] is None:
                sets.append("next_run_at=?")
                params.append(CronSchedule(row["schedule"]).next_after(time.time()))
        if sets:
            params.append(aid)
            self._st().execute(
                f"UPDATE automations SET {', '.join(sets)} WHERE id=?;", tuple(params))
        return self._row_to_dict(
            self._st().query_one("SELECT * FROM automations WHERE id=?;", (aid,)))

    def delete(self, aid: str) -> bool:
        row = self._st().query_one("SELECT id FROM automations WHERE id=?;", (aid,))
        if row is None:
            return False
        self._st().execute("DELETE FROM automations WHERE id=?;", (aid,))
        return True

    # -- the tick loop --------------------------------------------------------

    def _task_done(self, task: "asyncio.Task[Any]") -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            import logging
            logging.getLogger(__name__).error(
                "automation fire task failed: %s", exc)

    async def _tick(self) -> None:
        now = time.time()
        try:
            rows = self._st().query_all(
                "SELECT * FROM automations WHERE enabled=1 AND "
                "next_run_at IS NOT NULL AND next_run_at <= ?;", (now,))
        except Exception as exc:
            # resilience yes, invisibility no: rate-limit to one event per
            # 10 min so a broken storage layer is visible without spam
            if now - self._last_tick_error > 600:
                self._last_tick_error = now
                from observability.events import emit
                emit("automation_failed", automation_id="",
                     name="tick", schedule="", prompt="",
                     error=f"tick query: {exc}"[:300])
            return  # pre-v19 schema or unreadable table — tick again later
        for row in rows:
            if row["id"] in self._running:
                continue  # never overlap the same automation with itself
            self._running.add(row["id"])
            # strong reference: a bare ensure_future can be GC'd mid-run and
            # its exception never retrieved — the task set keeps it alive and
            # the done-callback surfaces failures
            task = asyncio.ensure_future(self._fire(dict(row), now))
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

    async def _fire(self, row: dict[str, Any], fired_at: float) -> None:
        aid = row["id"]
        try:
            # exactly ONE catch-up run per tick; advance the schedule to the
            # next future fire so a slept machine doesn't re-fire a backlog
            try:
                nxt = CronSchedule(row["schedule"]).next_after(time.time())
            except CronError:
                # a stored row whose spec can never fire again (legacy /
                # hand-edited): disable it LOUDLY instead of writing a NULL
                # that silently drops it from every future tick
                nxt = None
                from observability.events import emit as _emit0
                _emit0("automation_stalled", automation_id=aid,
                       name=row["name"], schedule=row["schedule"],
                       reason="schedule can never fire again — automation disabled")
                self._st().execute(
                    "UPDATE automations SET enabled=0 WHERE id=?;", (aid,))
            try:
                self._st().execute(
                    "UPDATE automations SET last_run_at=?, next_run_at=? WHERE id=?;",
                    (fired_at, nxt, aid))
            except Exception as exc:
                # bookkeeping failure must NOT leave next_run_at stale —
                # that would re-fire the same automation every tick
                from observability.events import emit as _emit1
                _emit1("automation_failed", automation_id=aid,
                       name=row["name"], error=f"bookkeeping: {exc}"[:300])
                return
            turn_id = f"goal-{uuid.uuid4().hex[:12]}"
            from observability.events import emit
            emit("automation_fired", turn_id=turn_id, automation_id=aid,
                 name=row["name"], schedule=row["schedule"],
                 prompt=row["prompt"][:400])
            from core.agent.orchestrator import get_orchestrator
            # the configured language (cfg.set("language", …) in /setup)
            # rides into the goal like an interactive run — an automation
            # used to always answer in the default language
            from config import get_config
            lang = str(get_config().get("language", "") or "")
            try:
                await get_orchestrator().run_turn(
                    row["prompt"], language=lang, turn_id=turn_id,
                    owner_session_id="scheduler", project_id="",
                    session_id=None)
            except Exception as exc:
                from observability.events import emit as _emit
                # distinct type: a failed run must never look like a fire to
                # contract consumers (frozen v1 payload shapes)
                _emit("automation_failed", turn_id=turn_id, automation_id=aid,
                      name=row["name"], schedule=row["schedule"],
                      prompt=row["prompt"][:400], error=str(exc)[:300])
        finally:
            self._running.discard(aid)

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("automation tick failed")
                _ = exc  # a bad tick never kills the loop
            await asyncio.sleep(TICK_SECONDS)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Start the tick loop on the running event loop (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


_scheduler: AutomationScheduler | None = None


def get_scheduler() -> AutomationScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AutomationScheduler()
    return _scheduler


def reset_scheduler_for_tests() -> AutomationScheduler:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
    _scheduler = AutomationScheduler()
    return _scheduler
