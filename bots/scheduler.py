"""bots/scheduler.py — ROUTINE: run a bot's skill on a schedule or event.

Same tick discipline as scheduling/ (the automations engine): a 30s tick
that re-reads the wall clock every cycle — an OS sleep/wake can never
overshoot, and a slow bot run never delays other bots. Fires at most one
run per bot per tick. Schedules:
  daily    {time: "HH:MM", tz: "Area/City"}     — fires at that local time
  interval {expr: "30" | "30m"}                 — minutes since last run
  cron     {expr: "5-expr"}                     — via scheduling/cron.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger

from bots import runtime, store
from config import get_config

TICK_SECONDS = 30.0


def _tz(name: str | None):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or "UTC")
    except Exception:
        return None


def _due(bot: dict[str, Any], now: float) -> bool:
    sched = bot.get("schedule") or {}
    kind = sched.get("kind") or "none"
    if kind == "none":
        return False
    if bot.get("status") in ("running", "waiting_approval"):
        return False
    if kind == "interval":
        raw = str(sched.get("expr") or "").strip().lower().removesuffix("m")
        try:
            minutes = max(1, int(float(raw)))
        except ValueError:
            return False
        last = bot.get("last_run_at_ts") or 0.0
        return (now - last) >= minutes * 60
    if kind == "daily":
        want = str(sched.get("time") or "").strip()
        if len(want) < 4 or ":" not in want:
            return False
        zinfo = _tz(sched.get("tz"))
        now_dt = datetime.now(zinfo) if zinfo else datetime.now()
        hh, mm = (want.split(":") + ["0"])[:2]
        try:
            target = f"{int(hh):02d}:{int(mm):02d}"
        except ValueError:
            return False
        today = now_dt.strftime("%Y-%m-%d")
        if (sched.get("last_fired_date") or "") == today:
            return False
        return now_dt.strftime("%H:%M") >= target
    if kind == "cron":
        try:
            from scheduling.cron import CronSchedule
            sched_obj = CronSchedule(str(sched.get("expr") or ""))
        except Exception:
            return False
        try:
            nxt = sched_obj.next_after(now)
        except Exception:
            return False
        ts = _to_ts(nxt)
        if ts is None:
            return False
        return now >= ts and (sched.get("last_cron_ts") or 0.0) < ts
    return False


def _to_ts(value: Any) -> float | None:
    try:
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return datetime.fromisoformat(value).timestamp()
    except Exception:
        return None
    return None


class BotScheduler:
    """Ticks bots.json and fires due bots as scheduled routines."""

    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                self.tick()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 — the tick must survive
                logger.warning(f"bot scheduler tick failed: {exc}")

    def tick(self) -> None:
        now = datetime.now().timestamp()
        for bot in store.list_bots():
            raw = store.get_bot(bot["id"])
            if raw is None:
                continue
            due_view = {**bot, "last_run_at_ts": _last_run_ts(raw)}
            if _due(due_view, now):
                prompt = _standing_prompt(bot)
                store.append_event(
                    bot["id"], "system",
                    f"Routine fired ({bot['schedule'].get('kind')}).")
                try:
                    runtime.request_run(bot["id"], prompt)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"routine fire failed for {bot['id']}: {exc}")
                if bot["schedule"].get("kind") == "daily":
                    _mark_daily(bot["id"])
                if bot["schedule"].get("kind") == "cron":
                    _mark_cron(bot["id"], now)


def _last_run_ts(public: dict[str, Any]) -> float:
    iso = public.get("last_run_at")
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _standing_prompt(bot: dict[str, Any]) -> str:
    skill = bot.get("skill")
    base = (f"Run the saved routine {skill}. " if skill
            else "Run your scheduled routine. ")
    return (f"{base}Job definition: {bot['description']} Produce the "
            "routine's result and report it concisely.")


def _mark_daily(bot_id: str) -> None:
    bot = store.get_bot(bot_id)
    if bot is None:
        return
    sched = dict(bot.get("schedule") or {})
    sched["last_fired_date"] = datetime.now().strftime("%Y-%m-%d")
    store.update_bot(bot_id, schedule=sched)


def _mark_cron(bot_id: str, now: float) -> None:
    bot = store.get_bot(bot_id)
    if bot is None:
        return
    sched = dict(bot.get("schedule") or {})
    sched["last_cron_ts"] = now
    store.update_bot(bot_id, schedule=sched)


def get_scheduler() -> BotScheduler:
    global _scheduler
    try:
        return _scheduler
    except NameError:
        _scheduler = BotScheduler()
        return _scheduler


_scheduler = BotScheduler()
