"""connectors/builtin/cron_tools.py — the cron.* automation tools.

Four tools over the schedule/ package's AutomationScheduler:
  cron.create / cron.update / cron.delete  — explicit consent (they arm or
                                             disarm code that runs later,
                                             unattended)
  cron.list                                — auto (read-only)

Every schedule string is validated by CronSchedule before it is stored,
so a bad cron line can never be persisted and fail silently forever.
"""
from __future__ import annotations

from typing import Any

from scheduling import get_scheduler
from scheduling.cron import CronError


def _create(name: str = "", schedule: str = "", prompt: str = "",
            **_: Any) -> dict[str, Any]:
    try:
        row = get_scheduler().create(name, schedule, prompt)
    except CronError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "automation": row}


def _list(**_: Any) -> dict[str, Any]:
    return {"ok": True, "automations": get_scheduler().list()}


def _update(id: str = "", name: str | None = None,
            schedule: str | None = None, prompt: str | None = None,
            enabled: bool | None = None, **_: Any) -> dict[str, Any]:
    # the model-facing arg is "id", DECLARED in the schema (a tools-schema
    # round fix: when it hid behind **kw the model never saw it and could
    # not delete/update anything)
    aid = str(id or _.get("aid", ""))
    try:
        row = get_scheduler().update(
            aid, name=name, schedule=schedule, prompt=prompt, enabled=enabled)
    except CronError as exc:
        return {"ok": False, "error": str(exc)}
    if row is None:
        return {"ok": False, "error": f"automation {aid!r} not found"}
    return {"ok": True, "automation": row}


def _delete(id: str = "", **_: Any) -> dict[str, Any]:
    # "id" declared in the schema (schema round: hidden behind **kw)
    aid = str(id or _.get("aid", ""))
    if not get_scheduler().delete(aid):
        return {"ok": False, "error": f"automation {aid!r} not found"}
    return {"ok": True, "deleted": aid}


def register(reg) -> None:
    from core.agent.base_agent import Tool
    reg.register(Tool(
        name="CronCreate", capability="automation.write",
        description=("Schedule a recurring goal: a name, a 5-field cron "
                     "schedule (m h dom mon dow), and the prompt to run. "
                     "Runs unattended at each fire time."),
        handler=_create, consent="explicit"))
    reg.register(Tool(
        name="CronList", capability="automation.read",
        description="List every scheduled automation with its next run time.",
        handler=_list, consent="auto"))
    reg.register(Tool(
        name="CronUpdate", capability="automation.write",
        description=("Change an automation's name, schedule, prompt, or "
                     "enabled flag (by id from cron.list)."),
        handler=_update, consent="explicit"))
    reg.register(Tool(
        name="CronDelete", capability="automation.write",
        description="Delete an automation by id (from cron.list).",
        handler=_delete, consent="explicit"))
