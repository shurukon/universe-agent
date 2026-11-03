"""tests/test_channels.py — the channel manager (audit H4).

H4: TelegramChannel was complete (start/stop/configure) but NOTHING ever
called start() — a configured channel never came up. The manager in
channels/__init__.py boots configured channels with the REPL and routes
inbound messages through the one orchestrator pipeline. These tests pin:

  1. no configured channels  -> start is a no-op that returns [];
  2. a configured channel    -> start() IS called and the kind is returned;
  3. an unusable channel     -> skipped honestly (empty token), not crashed;
  4. stop_channels is idempotent and stops what started;
  5. the default route: an inbound message runs ONE orchestrator turn and
     returns its reply text.
"""
from __future__ import annotations

import asyncio

import pytest


class _FakeChannel:
    def __init__(self, token: str = "tok"):
        self.token = token
        self.started = False
        self.stopped = False
        self.handler = None

    async def start(self, on_message) -> None:
        self.started = True
        self.handler = on_message

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def _clean_started():
    from channels import stop_channels
    asyncio.run(stop_channels())
    yield
    asyncio.run(stop_channels())


def test_no_configured_channels_is_a_clean_noop(fresh_home):
    from channels import start_channels
    assert asyncio.run(start_channels()) == []


def test_configured_channel_actually_starts(fresh_home, monkeypatch):
    """H4 regression: a configured telegram row MUST be started, and the
    booted kind returned — not a silent never-starts adapter."""
    from storage.local import get_storage
    import time as _t
    get_storage().execute(
        "INSERT INTO channels(id,kind,token,allowed_user_ids,configured_at) "
        "VALUES('telegram','telegram','enc','null',?);", (_t.time(),))
    import channels.telegram as tg_mod
    fake = _FakeChannel("tok")
    monkeypatch.setattr(tg_mod, "get_telegram_channel", lambda: fake)
    from channels import start_channels, stop_channels
    booted = asyncio.run(start_channels())
    assert booted == ["telegram"]
    assert fake.started is True and fake.handler is not None
    asyncio.run(stop_channels())
    assert fake.stopped is True


def test_unusable_channel_is_skipped_honestly(fresh_home, monkeypatch):
    from storage.local import get_storage
    import time as _t
    get_storage().execute(
        "INSERT INTO channels(id,kind,token,allowed_user_ids,configured_at) "
        "VALUES('telegram','telegram','enc','null',?);", (_t.time(),))
    import channels.telegram as tg_mod
    empty = _FakeChannel("")  # stored token unreadable -> empty at boot
    monkeypatch.setattr(tg_mod, "get_telegram_channel", lambda: empty)
    from channels import start_channels
    assert asyncio.run(start_channels()) == []
    assert empty.started is False, "an empty token must not be booted"


def test_stop_channels_is_idempotent(fresh_home):
    from channels import stop_channels
    asyncio.run(stop_channels())
    asyncio.run(stop_channels())  # second call: no error


def test_default_route_runs_one_orchestrator_turn(fresh_home, monkeypatch):
    """An inbound channel message = one session turn; the reply text goes
    back out the channel."""
    import channels as ch_mod

    class _Orch:
        async def run_turn(self, text, **kw):
            return {"engine": "session", "reply": f"echo:{text}"}

    import core.agent.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "get_orchestrator", lambda: _Orch())
    out = asyncio.run(ch_mod._default_on_message("ping", 42))
    assert out == "echo:ping"
