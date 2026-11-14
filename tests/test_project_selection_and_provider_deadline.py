"""tests/test_project_selection_and_provider_deadline.py

Two agent-logic gaps found on the running app (2026-11-12):

1. PROJECT SELECTION — adding the same folder twice created two project
   rows with the same root (the sidebar showed the folder twice), and a
   project with an empty root was accepted even though it scopes nothing.
2. A HANGING PROVIDER — the OpenAI SDK's default read timeout is 600 s,
   so a provider that accepts the connection and never answers held the
   turn hostage for ten minutes per attempt (five attempts = over half an
   hour of "working"). The ladder now has a per-attempt deadline and a
   chain budget, and it ends with an honest `timeout` error.
"""
from __future__ import annotations

import asyncio

import pytest


# --------------------------------------------------------------------------
# 1. project selection
# --------------------------------------------------------------------------

def _client():
    from fastapi.testclient import TestClient
    import universe_desktop_bridge.app as app_mod
    app_mod.reset_runtime_for_tests()
    return TestClient(app_mod.app)


def test_the_same_folder_is_one_project(fresh_home, tmp_path):
    """Adding the same folder twice returns the SAME project (the sidebar
    must not show the folder twice)."""
    root = tmp_path / "work"
    root.mkdir()
    with _client() as c:
        r1 = c.post("/api/projects", json={"name": "work", "root": str(root)})
        assert r1.status_code == 200, r1.text
        id1 = r1.json()["project"]["id"]
        r2 = c.post("/api/projects", json={"name": "work", "root": str(root)})
        assert r2.status_code == 200, r2.text
        id2 = r2.json()["project"]["id"]
        assert id1 == id2, "the same folder must not create a second project"
        listed = c.get("/api/projects").json()["projects"]
        roots = [p["root"] for p in listed]
        assert roots.count(str(root)) == 1, roots


def test_a_project_without_a_root_is_refused(fresh_home, tmp_path):
    """A project that scopes nothing is refused — it would silently fall
    back to the global root."""
    with _client() as c:
        r = c.post("/api/projects", json={"name": "ghost", "root": ""})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "project_root_required"


def test_the_project_name_defaults_to_the_folder_name(fresh_home, tmp_path):
    root = tmp_path / "MyWork"
    root.mkdir()
    with _client() as c:
        r = c.post("/api/projects", json={"name": "", "root": str(root)})
        assert r.status_code == 200, r.text
        assert r.json()["project"]["name"] == "MyWork"


# --------------------------------------------------------------------------
# 2. the provider deadline
# --------------------------------------------------------------------------

def test_the_provider_attempt_has_a_deadline():
    """There IS a deadline — the SDK's own default (600 s) is what let a
    hung provider hold a turn for ten minutes."""
    from providers.client import _attempt_timeout_s, _chain_timeout_s
    assert 5.0 <= _attempt_timeout_s() <= 300.0
    assert 10.0 <= _chain_timeout_s() <= 1800.0


@pytest.mark.asyncio
async def test_a_hanging_provider_fails_instead_of_hanging(monkeypatch):
    """A provider that never answers must END the call with a timeout
    error — not hold the turn hostage."""
    from providers import client as client_mod

    monkeypatch.setenv("UNIVERSE_PROVIDER_TIMEOUT_S", "1")
    monkeypatch.setenv("UNIVERSE_PROVIDER_CHAIN_TIMEOUT_S", "3")

    llm = client_mod.LLMClient.__new__(client_mod.LLMClient)
    llm._pm = client_mod.get_provider_manager()
    llm._clients = {}
    llm._call_log = []
    llm._MAX_CALL_LOG = 100

    async def _hang(*a, **kw):
        await asyncio.sleep(3600)      # a provider that never answers

    monkeypatch.setattr(llm, "_call_once", _hang)

    with pytest.raises(client_mod.LLMError) as ei:
        await asyncio.wait_for(
            llm.complete(agent="universe",
                         messages=[{"role": "user", "content": "hi"}],
                         max_tokens=8),
            timeout=20)
    assert ei.value.kind in ("timeout", "no_provider"), ei.value.kind
    assert "did not answer" in str(ei.value) or ei.value.kind == "no_provider"


@pytest.mark.asyncio
async def test_the_chain_budget_is_enforced(monkeypatch):
    """The whole ladder gets a wall-clock budget: a dead primary plus a
    dead fallback must not mean half an hour of "working"."""
    from providers import client as client_mod

    monkeypatch.setenv("UNIVERSE_PROVIDER_TIMEOUT_S", "2")
    monkeypatch.setenv("UNIVERSE_PROVIDER_CHAIN_TIMEOUT_S", "4")

    llm = client_mod.LLMClient.__new__(client_mod.LLMClient)
    llm._pm = client_mod.get_provider_manager()
    llm._clients = {}
    llm._call_log = []
    llm._MAX_CALL_LOG = 100

    async def _hang(*a, **kw):
        await asyncio.sleep(3600)

    monkeypatch.setattr(llm, "_call_once", _hang)

    import time
    started = time.monotonic()
    with pytest.raises(client_mod.LLMError):
        await llm.complete(agent="universe",
                           messages=[{"role": "user", "content": "hi"}],
                           max_tokens=8)
    elapsed = time.monotonic() - started
    assert elapsed < 30, f"the ladder ran for {elapsed:.1f}s (budget 4s)"
