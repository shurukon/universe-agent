# Adapted to the session-engine default (2026-11): a posted turn no longer
# inserts a kind='message' child row into the goals table — the message and
# its reply are appended to the SESSION TRANSCRIPT (core/session/store.py)
# under session_id = task_id, while the bridge still owns the kind='task'
# container row. These tests mirror that reality: thread children are read
# from the transcript, and the session-authority detail endpoint
# (/api/session/{id}) is the thread reader.
"""tests/test_task_threading.py — v17 desktop model: tasks are containers,
messages are transcript turns.

Covers the threading fixes end to end at the bridge API level (no provider
keys: the orchestrator's LLM is replaced with a deterministic fake):
  * POST /api/turn without turn_id auto-creates a 'task' container titled
    "New task" (never the message text) and appends the message to the
    task's session transcript
  * POST /api/turn with turn_id continues the SAME task (one container,
    two transcript user messages; the task list never grows per message)
  * GET /api/sessions returns only task rows (message turns are invisible)
  * GET /api/session/{task} returns the full thread (transcript messages)
  * POST /api/tasks/draft creates an empty container titled "New task"
  * client_msg_id idempotency: a duplicate send returns duplicate=true and
    does NOT start a second goal
  * unified event seq: the refill stream is strictly monotonic, no dups
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import universe_desktop_bridge.app as bridge_app
from universe_desktop_bridge.consent import ConsentManager
from providers.client import LLMResponse
from security.capability import get_gate
from storage.local import get_storage


class FakeLLM:
    """Deterministic LLMClient stand-in: chat replies echo, classification
    returns a minimal valid UNIVERSE_META block so the orchestrator takes the
    chat branch (no agent ladder, no tools)."""

    async def complete(self, *, agent, messages, max_tokens=1024,
                       temperature=0.0, response_format=None, tools=None,
                       tool_choice=None, tier_hint=None) -> LLMResponse:
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content", "")
        if "universe_meta" in str(messages[:1]):
            pass
        sys = str(messages[0].get("content", "")) if messages else ""
        if "UNIVERSE_META" in sys or "capability strategist" in sys:
            text = ("```universe_meta\n{\"mode\": \"chat\", "
                    "\"classification\": {\"category\": \"general\", "
                    "\"domain\": \"general\", \"intent\": \"chat\", "
                    "\"complexity\": \"low\"}, \"plan_summary\": \"\", "
                    "\"needs_clarification\": false, "
                    "\"required_capabilities\": [], "
                    "\"acceptance_summary\": []}\n```")
        else:
            text = f"echo: {user[:200]}"
        return LLMResponse(text=text, model="fake", provider="fake",
                           input_tokens=1, output_tokens=1, latency_ms=1,
                           finish_reason="stop", raw={})

    def call_count(self) -> int:
        return 1

    def call_log(self) -> list:
        return []


@pytest.fixture
def thread_client(monkeypatch, fresh_home):
    from config import get_config
    get_config().set("project_root", str(fresh_home))
    from universe_desktop_bridge import app as app_mod
    from observability.events import reset_event_bus_for_tests
    from providers.manager import reset_provider_manager_for_tests
    reset_event_bus_for_tests()
    reset_provider_manager_for_tests()
    get_gate().set_consent_handler(ConsentManager().handler)
    bridge_app.reset_runtime_for_tests()
    # install the fake LLM into the orchestrator singleton
    from core.agent.orchestrator import get_orchestrator
    orch = get_orchestrator()
    monkeypatch.setattr(orch, "llm", FakeLLM(), raising=True)
    # satisfy the bridge's no_provider preflight
    get_config()
    from providers.manager import get_provider_manager
    pm = get_provider_manager()

    class _FakePM:
        def best_available(self, agent):
            return ("fake", "fake-model")

        def get_agent_model(self, agent):
            return ("fake", "fake-model")

        def get_fallback_chain(self, agent):
            return []

    monkeypatch.setattr(pm, "best_available", _FakePM().best_available)
    with TestClient(app_mod.app) as c:
        yield c


def _msg_rows(task_id: str) -> list[dict[str, Any]]:
    """The thread children: user messages appended to the task's session
    transcript (kind='message') in order — the session-engine model."""
    from core.session.store import get_session_store
    return [e for e in get_session_store().transcript(task_id)
            if e.get("kind") == "message" and e.get("role") == "user"]


def _wait_task_row(task_id: str) -> dict[str, Any] | None:
    for _ in range(60):
        row = get_storage().query_one(
            "SELECT id, kind, title, status FROM goals WHERE id=?;",
            (task_id,))
        if row is not None:
            return row
        time.sleep(0.05)
    return None


def _wait_msg_count(task_id: str, n: int) -> list[dict[str, Any]]:
    """run_turn appends the message turn asynchronously — poll until the
    transcript holds n user messages (or time out with whatever exists)."""
    for _ in range(60):
        msgs = _msg_rows(task_id)
        if len(msgs) >= n:
            return msgs
        time.sleep(0.05)
    return _msg_rows(task_id)


class TestTaskThreading:
    def test_first_message_creates_task_titled_new_task(self, thread_client):
        r = thread_client.post("/api/turn", json={"text": "build me a rocket engine"})
        assert r.status_code == 200, r.text
        body = r.json()
        task_id = body["task_id"]
        row = _wait_task_row(task_id)
        assert row is not None
        assert row["kind"] == "task"
        # THE core rule: the title is NEVER the message text
        assert row["title"] == "New task"
        # and a message turn was appended to the task's transcript
        msgs = _wait_msg_count(task_id, 1)
        assert len(msgs) == 1
        assert msgs[0]["kind"] == "message"
        assert msgs[0]["content"].startswith("build me")

    def test_continuation_reuses_task_no_new_row(self, thread_client):
        r1 = thread_client.post("/api/turn", json={"text": "first message here"})
        task_id = r1.json()["task_id"]
        _wait_task_row(task_id)
        r2 = thread_client.post("/api/turn", json={
            "text": "second message in the same thread", "turn_id": task_id})
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["task_id"] == task_id  # SAME container
        assert body2["turn_id"] != body2["task_id"]  # new message goal
        # exactly one task row, two transcript user messages
        tasks = get_storage().query_all("SELECT id FROM goals WHERE kind='task';")
        assert len(tasks) == 1
        msgs = _wait_msg_count(task_id, 2)
        assert len(msgs) == 2
        assert msgs[1]["content"].startswith("second message")

    def test_goals_list_hides_message_children(self, thread_client):
        r1 = thread_client.post("/api/turn", json={"text": "thread starter"})
        task_id = r1.json()["task_id"]
        thread_client.post("/api/turn", json={
            "text": "follow up", "turn_id": task_id})
        _wait_msg_count(task_id, 2)
        rows = thread_client.get("/api/sessions").json()["goals"]
        ids = [g["turn_id"] for g in rows]
        assert task_id in ids
        msg_turn_ids = {m.get("turn_id") for m in _msg_rows(task_id)}
        assert not (set(ids) & msg_turn_ids), "message turns leaked into the task list"

    def test_goal_detail_returns_thread(self, thread_client):
        r1 = thread_client.post("/api/turn", json={"text": "hello thread"})
        task_id = r1.json()["task_id"]
        _wait_task_row(task_id)
        _wait_msg_count(task_id, 1)
        # session authority: the thread lives in the transcript
        detail = thread_client.get(f"/api/session/{task_id}").json()
        assert detail.get("kind") == "task"
        thread = [m for m in (detail.get("messages") or [])
                  if m.get("text")]
        assert thread, "task detail returned no thread"
        assert any(m.get("role") == "user"
                   and str(m.get("text", "")).startswith("hello thread")
                   for m in thread), thread

    def test_draft_task_endpoint(self, thread_client):
        r = thread_client.post("/api/tasks/draft?project_id=general")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["task"]["title"] == "New task"
        assert body["task"]["kind"] == "task"
        row = get_storage().query_one(
            "SELECT kind, title, text, status FROM goals WHERE id=?;",
            (body["task"]["turn_id"],))
        assert row["kind"] == "task"
        assert row["title"] == "New task"
        assert row["text"] == ""  # no message text involved

    def test_duplicate_client_msg_id_is_ignored(self, thread_client):
        payload = {"text": "only once please", "client_msg_id": "cmid-123"}
        r1 = thread_client.post("/api/turn", json=payload)
        assert r1.status_code == 200
        first = r1.json()
        r2 = thread_client.post("/api/turn", json=payload)
        assert r2.status_code == 200
        second = r2.json()
        assert second.get("duplicate") is True
        assert second["turn_id"] == first["turn_id"]
        # only ONE message turn was appended
        msgs = _wait_msg_count(first["task_id"], 1)
        assert len(msgs) == 1

    def test_continuation_on_missing_task_404(self, thread_client):
        r = thread_client.post("/api/turn", json={
            "text": "ghost continuation", "turn_id": "task-doesnotexist"})
        assert r.status_code == 404

    def test_seq_single_source_strictly_monotonic(self, thread_client):
        """The unified seq: the ring (refill) events share ONE numbering —
        strictly monotonic, no duplicates."""
        r = thread_client.post("/api/turn", json={"text": "seq check"})
        gid = r.json()["turn_id"]
        for _ in range(60):
            row = get_storage().query_one(
                "SELECT status FROM goals WHERE id=?;", (gid,))
            if row is not None and row["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        events = thread_client.get(
            f"/api/turn/{gid}/events?after_seq=0").json()["events"]
        seqs = [e["seq"] for e in events]
        assert seqs, "no events stamped"
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs)), f"duplicate seqs: {seqs}"

    def test_task_title_never_the_message_text(self, thread_client):
        text = "an unmistakable unique message string xyzzy"
        r = thread_client.post("/api/turn", json={"text": text})
        task_id = r.json()["task_id"]
        _wait_task_row(task_id)
        task = get_storage().query_one(
            "SELECT title FROM goals WHERE id=?;", (task_id,))
        assert task["title"] == "New task"
        # archived task unarchives when a continuation message arrives
        from universe_desktop_bridge.app import get_storage as _gs  # noqa: F401
        get_storage().execute(
            "UPDATE goals SET archived=1 WHERE id=?;", (task_id,))
        r2 = thread_client.post("/api/turn", json={
            "text": "revive", "turn_id": task_id})
        assert r2.status_code == 200
        row = get_storage().query_one(
            "SELECT archived FROM goals WHERE id=?;", (task_id,))
        assert row["archived"] == 0
