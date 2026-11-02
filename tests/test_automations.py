"""tests/test_automations.py — the cron parser + scheduler (Phase 1.7).

Covers the parser's field shapes and the classic dom/dow OR merge, the
never-fire clamp (a pathological spec raises instead of looping), the
scheduler CRUD through the cron.* tools, and one live fire with a stubbed
orchestrator.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from scheduling.cron import CronError, CronSchedule


# -- the parser ------------------------------------------------------------------

def test_cron_parse_wildcards_and_values():
    s = CronSchedule("0 9 * * 1")  # Mondays 09:00
    assert 0 in s.minutes and 9 in s.hours
    assert s.dow_restricted and not s.dom_restricted


def test_cron_lists_ranges_steps():
    s = CronSchedule("*/15 9-17 1,15 * *")
    assert s.minutes == {0, 15, 30, 45}
    assert s.hours == set(range(9, 18))
    assert s.dom == {1, 15}


def test_cron_names():
    s = CronSchedule("0 12 * jan mon")
    assert s.months == {1} and s.dow == {1}


def test_cron_dom_dow_or_merge():
    """Both restricted => a day matches if EITHER dom or dow matches."""
    s = CronSchedule("0 0 1 * 5")  # the 1st OR any Friday
    assert s.dom_restricted and s.dow_restricted
    # a Friday that is NOT the 1st
    friday = time.struct_time((2026, 10, 2, 0, 0, 0, 4, 275, 0))  # 2026-10-02 Fri
    assert s.matches(friday) is True
    # the 1st that is NOT a Friday (2026-11-01 is a Sunday)
    first = time.struct_time((2026, 11, 1, 0, 0, 0, 6, 305, 0))
    assert s.matches(first) is True
    # neither
    other = time.struct_time((2026, 10, 5, 0, 0, 0, 0, 278, 0))  # Monday the 5th
    assert s.matches(other) is False


def test_cron_invalid_specs_raise():
    for bad in ("* * * *", "61 * * * *", "* 25 * * *", "0 0 32 * *",
                "0 0 * foo *", "0 0 * * 8"):
        with pytest.raises(CronError):
            CronSchedule(bad)


def test_cron_never_fire_spec_raises_instead_of_looping():
    """Feb 30 can never occur — next_after must raise, not spin forever."""
    s = CronSchedule("0 0 30 2 *")
    with pytest.raises(CronError):
        s.next_after(time.time())


def test_cron_next_after_is_future_and_matches():
    s = CronSchedule("* * * * *")  # every minute
    nxt = s.next_after(time.time())
    assert nxt > time.time()
    assert s.matches(time.localtime(nxt))


# -- CRUD through the tools -------------------------------------------------------

def test_cron_create_list_update_delete(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        r = await reg.call("CronCreate", agent="universe", turn_id="gc",
                           args={"name": "standup", "schedule": "0 9 * * 1-5",
                                 "prompt": "post the standup"})
        assert r["ok"] is True
        aid = r["result"]["automation"]["id"]
        assert r["result"]["automation"]["next_run_at"]

        r2 = await reg.call("CronList", agent="universe", turn_id="gc", args={})
        assert r2["ok"] is True
        assert [a["id"] for a in r2["result"]["automations"]] == [aid]

        r3 = await reg.call("CronUpdate", agent="universe", turn_id="gc",
                            args={"id": aid, "enabled": False})
        assert r3["ok"] is True
        assert r3["result"]["automation"]["enabled"] is False

        r4 = await reg.call("CronDelete", agent="universe", turn_id="gc",
                            args={"id": aid})
        assert r4["ok"] is True
        r5 = await reg.call("CronList", agent="universe", turn_id="gc", args={})
        assert r5["result"]["automations"] == []

    asyncio.run(go())


def test_cron_create_rejects_bad_schedule(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        r = await reg.call("CronCreate", agent="universe", turn_id="gc2",
                           args={"name": "bad", "schedule": "not a cron",
                                 "prompt": "x"})
        assert r["ok"] is False
        # the envelope mirrors the handler's verdict; the error itself
        # lives in the handler's dict under "result"
        assert "5 fields" in r["result"]["error"]

    asyncio.run(go())


# -- a live fire -------------------------------------------------------------------

def test_scheduler_fires_due_automation(fresh_home, monkeypatch):
    """A due automation runs its prompt through the orchestrator (stubbed)
    and advances its next_run_at — exactly one catch-up per tick."""
    from scheduling import reset_scheduler_for_tests
    sched = reset_scheduler_for_tests()
    fired: list[dict] = []

    async def fake_run_turn(text, **kwargs):
        fired.append({"text": text, "turn_id": kwargs.get("turn_id")})
        return {"success": True, "reply": "done"}

    from core.agent.orchestrator import get_orchestrator
    monkeypatch.setattr(get_orchestrator(), "run_turn", fake_run_turn)

    row = sched.create("tick-test", "* * * * *", "hello from cron")
    # force the row due NOW (next minute is in the future by construction)
    sched._st().execute(
        "UPDATE automations SET next_run_at=? WHERE id=?;",
        (time.time() - 1, row["id"]))

    async def go():
        await sched._tick()
        for _ in range(100):
            if fired:
                break
            await asyncio.sleep(0.02)
        assert fired and fired[0]["text"] == "hello from cron"
        row2 = sched._st().query_one(
            "SELECT last_run_at, next_run_at FROM automations WHERE id=?;",
            (row["id"],))
        assert row2["last_run_at"] is not None
        assert row2["next_run_at"] is None or row2["next_run_at"] > time.time()

    asyncio.run(go())
