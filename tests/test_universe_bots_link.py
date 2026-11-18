"""tests/test_universe_bots_link.py — the Universe's side of the universe
bots link.

universe bots exposes three peer routes to a bot whose engine lives
outside its harness. They answer only to a standing bearer capability the
harness reads from ``<data dir>/external-runtimes.json``, re-reading that
file on every request. These tests pin the part the Universe owns: the
grant file's shape and its write discipline, the binding's permanence, and
the three tools' argument handling.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors.builtin import get_registry  # noqa: E402
from connectors.builtin import universe_bots as tools  # noqa: E402
from universe_desktop_bridge import universe_bots  # noqa: E402


@pytest.fixture
def bots_dir(tmp_path, monkeypatch):
    """A data dir of its own, so no test touches the user's world."""
    monkeypatch.setenv("UNIVERSE_BOTS_DATA_DIR", str(tmp_path / "bots"))
    monkeypatch.setenv("UNIVERSE_BOTS_ORIGIN", "http://127.0.0.1:8799")
    return tmp_path / "bots"


@pytest.fixture(autouse=True)
def _clear_watches():
    """The watcher's memory is process-wide, so an assignment a test already
    reported must not make the next test's identical id look handled."""
    universe_bots._watches.clear()
    universe_bots._reported.clear()
    yield
    universe_bots._watches.clear()
    universe_bots._reported.clear()


# ---- the grants file ---------------------------------------------------------


def test_a_grant_round_trips_and_never_joins_a_token_with_a_bom(bots_dir):
    universe_bots.write_grants({
        "bot-1": {"token": "t" * 40, "threadId": "thread-1"},
    })
    raw = universe_bots.runtimes_path().read_bytes()
    # The harness parses this file with JSON.parse: a UTF-8 BOM makes every
    # grant invisible, and the whole link then reads as unauthenticated.
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert universe_bots.read_grants() == {
        "bot-1": {"token": "t" * 40, "threadId": "thread-1"},
    }


def test_a_grant_of_another_bot_survives_a_write(bots_dir):
    """The harness reads the whole file, so a bot's grants may not collide."""
    universe_bots.write_grants({
        "bot-a": {"token": "t" * 40, "threadId": "thread-a"},
    })
    grants = universe_bots.read_grants()
    grants["bot-b"] = {"token": "u" * 40, "threadId": "thread-b"}
    universe_bots.write_grants(grants)
    assert set(universe_bots.read_grants()) == {"bot-a", "bot-b"}


def test_a_broken_grants_file_reads_as_no_grants(bots_dir):
    """The harness and the Universe must not disagree about the file."""
    universe_bots.runtimes_path().parent.mkdir(parents=True, exist_ok=True)
    universe_bots.runtimes_path().write_text("{ not json", encoding="utf-8")
    assert universe_bots.read_grants() == {}


def test_a_grant_with_a_blank_thread_is_dropped(bots_dir):
    """The binding is to a conversation; a bare token selects nothing."""
    universe_bots.write_grants({
        "bot-1": {"token": "t" * 40, "threadId": "thread-1"},
    })
    file = universe_bots.runtimes_path()
    file.write_text(json.dumps({"bot-1": {"token": "t" * 40}}),
                    encoding="utf-8")
    assert universe_bots.read_grants() == {}


# ---- the result watcher ------------------------------------------------------


def test_a_settled_delegation_reports_its_children(bots_dir, monkeypatch):
    import asyncio

    from observability.events import get_event_bus

    assignment = {
        "id": "a-1",
        "botId": "chief",
        "botName": "Universe Bots",
        "status": "completed",
        "outcome": "done",
        "result": "",
    }
    child = {
        "id": "a-1-child",
        "botId": "scout",
        "botName": "Scout",
        "status": "failed",
        "outcome": "provider_error",
        "result": "the engine was unreachable",
    }

    async def fake_status(ident, wait_ms=0, **_):
        return {"ok": True, "assignment": assignment, "children": [child]}

    async def fake_outstanding():
        return [dict(assignment)]

    monkeypatch.setattr(universe_bots, "assignment_status", fake_status)
    monkeypatch.setattr(universe_bots, "outstanding", fake_outstanding)

    bus = get_event_bus()
    sub = bus.subscribe()
    seen: list[dict] = []

    async def listen():
        while True:
            event = await sub.next_event(timeout=5)
            if event is None:
                return
            if event.get("type") == "universe_bots_result":
                seen.append(event)

    async def run():
        listener = asyncio.create_task(listen())
        watcher = universe_bots.ResultWatcher(interval=0.01)
        watcher.start()
        await asyncio.sleep(0.2)
        await watcher.stop()
        listener.cancel()

    universe_bots.remember("a-1", "Scout", "turn-1")
    asyncio.run(run())

    assert len(seen) == 1
    event = seen[0]
    # The requester's own row says nothing; the child carries the work and
    # its outcome, so the report is the child's.
    assert event["bot_name"] == "Scout"
    assert event["bots_status"] == "failed"
    assert event["bots_result"] == "the engine was unreachable"
    assert event["turn_id"] == "turn-1"
    assert "the engine was unreachable" in event["text"]


def test_a_delegation_is_reported_once(bots_dir, monkeypatch):
    import asyncio

    from observability.events import get_event_bus

    assignment = {
        "id": "a-2", "botName": "Scout", "status": "completed",
        "outcome": "done", "result": "done",
    }

    async def fake_status(ident, wait_ms=0, **_):
        return {"ok": True, "assignment": assignment, "children": []}

    async def fake_outstanding():
        return []

    monkeypatch.setattr(universe_bots, "assignment_status", fake_status)
    monkeypatch.setattr(universe_bots, "outstanding", fake_outstanding)
    bus = get_event_bus()
    sub = bus.subscribe()
    seen: list[dict] = []

    async def listen():
        while True:
            event = await sub.next_event(timeout=5)
            if event is None:
                return
            if event.get("type") == "universe_bots_result":
                seen.append(event)

    async def run():
        listener = asyncio.create_task(listen())
        watcher = universe_bots.ResultWatcher(interval=0.01)
        watcher.start()
        await asyncio.sleep(0.3)
        await watcher.stop()
        listener.cancel()

    universe_bots.remember("a-2", "Scout", "")
    asyncio.run(run())
    assert len(seen) == 1


# ---- the tools ---------------------------------------------------------------


def test_the_tools_are_registered():
    names = get_registry().names()
    for tool in ("UniverseBotsList", "UniverseBotsDelegate",
                 "UniverseBotsResult", "UniverseBotsCreate"):
        assert tool in names, tool


def test_a_delegation_needs_a_brief():
    out = _run(tools.delegate(bot_ids="b1", message=""))
    assert out["ok"] is False
    assert "message" in out["error"]


def test_a_delegation_needs_bots():
    out = _run(tools.delegate(bot_ids="", message="do it"))
    assert out["ok"] is False
    assert "bot_ids" in out["error"]


def test_at_most_four_bots_at_a_time():
    out = _run(tools.delegate(bot_ids=["a", "b", "c", "d", "e"],
                              message="do it"))
    assert out["ok"] is False
    assert "four" in out["error"]


def test_a_bot_needs_a_name():
    out = _run(tools.create(name=""))
    assert out["ok"] is False
    assert "name" in out["error"]


def test_a_misspelled_assignment_id_is_refused():
    out = _run(tools.result(assignment_id="not an id"))
    assert out["ok"] is False
    assert "assignment" in out["error"]


def test_a_tool_reports_an_unreachable_harness_honestly(bots_dir):
    """A data dir with no grant and no harness answering: the tool says so
    rather than claiming the work went out."""
    universe_bots.runtimes_path().parent.mkdir(parents=True, exist_ok=True)
    universe_bots.runtimes_path().write_text("[]", encoding="utf-8")
    assert universe_bots.read_grants() == {}
    out = _run(tools.delegate(bot_ids=["b1"], message="do it"))
    assert out["ok"] is False


def _run(coroutine):
    import asyncio

    return asyncio.run(_with_env(coroutine))


async def _with_env(coroutine):
    return await coroutine


def test_settled_names_are_the_ones_the_watcher_waits_for():
    assert universe_bots.settled("completed")
    assert universe_bots.settled("failed")
    assert universe_bots.settled("cancelled")
    assert not universe_bots.settled("running")
    assert not universe_bots.settled("waiting")
    assert not universe_bots.settled("queued")


def test_the_data_dir_is_under_the_users_home(bots_dir):
    assert universe_bots.data_dir() == bots_dir