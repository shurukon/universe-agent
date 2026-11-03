"""tests/test_bots.py — Universe Bots: store, risk gating, Muse event
digestion (against the REAL fixture captured from `muse exec --json`),
TASK→SKILL→ROUTINE pieces, and scheduler due-logic."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bots import runtime, scheduler, store

FIXTURE = Path(__file__).parent / "fixtures" / "muse_echo_run.jsonl"


@pytest.fixture(autouse=True)
def _clean_bots(fresh_home):
    store.reset_for_tests()


def test_bot_crud_and_public_shape():
    bot = store.create_bot("Blog Pulse", "Prepare a daily review of the site")
    assert bot["id"].startswith("bot-")
    assert bot["status"] == "idle"
    assert bot["schedule"] == {"kind": "none"}
    assert bot["approvals_pending"] == 0
    assert bot["workspace"]
    assert Path(bot["workspace"]).exists()

    store.append_event(bot["id"], "user", "run the daily review")
    store.append_event(bot["id"], "bot", "review ready: 3 posts analyzed")
    got = store.get_bot(bot["id"])
    assert got is not None
    roles = [e["role"] for e in got["transcript"]]
    assert roles == ["user", "bot"]
    assert got["last_result"] is None

    assert len(store.list_bots()) == 1
    assert store.delete_bot(bot["id"]) is True
    assert store.get_bot(bot["id"]) is None
    assert store.delete_bot(bot["id"]) is False


def test_transcript_cap():
    bot = store.create_bot("Cap", "cap test")
    for i in range(store.TRANSCRIPT_CAP + 30):
        store.append_event(bot["id"], "system", f"e{i}")
    got = store.get_bot(bot["id"])
    assert got is not None
    assert len(got["transcript"]) == store.TRANSCRIPT_CAP
    assert got["transcript"][-1]["text"] == f"e{store.TRANSCRIPT_CAP + 29}"


def test_skill_save_task_to_skill():
    bot = store.create_bot("Pulse", "daily site review")
    store.append_event(bot["id"], "user", "review the site")
    store.append_event(bot["id"], "bot", "done: found 2 broken links")
    skill = store.add_skill(bot["id"], "daily-blog-pulse", "# method body")
    assert skill == "/daily-blog-pulse"
    got = store.get_bot(bot["id"])
    assert got is not None
    assert got["skill"] == "/daily-blog-pulse"
    assert "/daily-blog-pulse" in got["skills"]
    method_file = Path(got["workspace"]) / "skills" / "daily-blog-pulse.md"
    assert method_file.exists()
    assert "method body" in method_file.read_text(encoding="utf-8")


def test_risky_task_requires_approval():
    bot = store.create_bot("Mailer", "send the weekly email digest")
    task = "send the digest to subscribers"
    assert runtime.is_risky(task) is True
    result = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        runtime.request_run(bot["id"], task))
    assert result.get("approval_required") is True
    fresh = store.get_bot(bot["id"])
    assert fresh is not None
    assert fresh["status"] == "waiting_approval"
    assert fresh["approvals_pending"] == 1
    approvals = store.list_approvals(bot["id"])
    assert approvals[0]["kind"] == "risky_step"
    # deny drains the queue and returns the bot to idle
    store.resolve_approval(bot["id"], approvals[0]["id"], "deny")
    after = store.get_bot(bot["id"])
    assert after is not None
    assert after["status"] == "idle"
    assert after["approvals_pending"] == 0


def test_safe_task_does_not_require_approval():
    bot = store.create_bot("Researcher", "summarize the repo state")
    assert runtime.is_risky("summarize the repo state") is False


def test_muse_event_digest_real_fixture():
    """The parser must survive the REAL `muse exec --json` wire stream —
    the fixture is a genuine capture (echo provider, task-level failure)."""
    lines = [l for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 20
    error = None
    saw_start = False
    for line in lines:
        evt = json.loads(line)
        text, err, approval = runtime._digest_muse_event(evt)
        if text and text[0] == "system" and "started" in text[1]:
            saw_start = True
        if err:
            error = err
    assert saw_start, "run.lifecycle.started must surface as a system event"
    assert error, "the fixture run failed — its reason must be extracted"
    assert "provider" in error.lower() or "instructions" in error.lower()


def test_muse_event_digest_success_and_text():
    completed = {
        "schema_version": 1, "record_type": "event",
        "payload_type": "run.lifecycle.completed",
        "payload": {"text": "The daily review found 2 broken links.",
                    "kind": "run_completed"},
    }
    text, err, approval = runtime._digest_muse_event(completed)
    assert text is not None and text[0] == "bot"
    assert "broken links" in text[1]
    assert err is None and approval is None

    approval_evt = {
        "record_type": "status",
        "payload_type": "approval.requested",
        "payload": {"title": "push to the repository", "detail": "git push"},
    }
    text, err, approval = runtime._digest_muse_event(approval_evt)
    assert approval is not None
    assert approval[0] == "approval.requested"
    assert "push" in approval[1]


def test_scheduler_interval_due():
    bot = {"id": "b", "status": "idle",
           "schedule": {"kind": "interval", "expr": "30m"},
           "last_run_at_ts": 0.0}
    now = 1_000_000.0
    assert scheduler._due(bot, now) is True
    bot["last_run_at_ts"] = now - 10 * 60
    assert scheduler._due(bot, now) is False
    bot["last_run_at_ts"] = now - 31 * 60
    assert scheduler._due(bot, now) is True
    bot["status"] = "running"
    assert scheduler._due(bot, now) is False


def test_scheduler_daily_due():
    from datetime import datetime
    zinfo = scheduler._tz("UTC")
    now_dt = datetime.now(zinfo)
    hhmm = now_dt.strftime("%H:%M")
    bot = {"id": "b", "status": "idle",
           "schedule": {"kind": "daily", "time": hhmm, "tz": "UTC",
                        "last_fired_date": ""}}
    now = now_dt.timestamp()
    assert scheduler._due(bot, now) is True
    bot["schedule"]["last_fired_date"] = now_dt.strftime("%Y-%m-%d")
    assert scheduler._due(bot, now) is False


def test_schedule_validation_rejects_unknown_kind():
    bot = store.create_bot("S", "s")
    got = store.get_bot(bot["id"])
    assert got is not None
    with pytest.raises(ValueError):
        store.set_status(bot["id"], "bogus")
