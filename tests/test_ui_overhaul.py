"""tests/test_ui_overhaul.py — W1 backend coverage: projects, chat
lifecycle (archive/delete/branch), MCP transports (stdio/http/sse with a
REAL local HTTP MCP server), the setup gate, and the agents endpoint.

No API key needed anywhere: goal running is not exercised here.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from universe_desktop_bridge.app import reset_runtime_for_tests


@pytest.fixture
def client(fresh_home):
    from config import get_config
    get_config().set("project_root", str(fresh_home))
    from universe_desktop_bridge import app as app_mod
    from observability.events import reset_event_bus_for_tests
    reset_event_bus_for_tests()
    reset_runtime_for_tests()
    with TestClient(app_mod.app) as c:
        yield c
    # stop any MCP subprocesses/sessions this file's tests registered so
    # later suites sharing the process get a clean event loop
    import asyncio as _asyncio
    from connectors.mcp_client import (get_mcp_client,
                                       reset_mcp_client_for_tests)
    from concurrency.supervisor import reset_supervisor_for_tests
    try:
        _asyncio.run(_stop_all_mcp_servers(get_mcp_client()))
    except RuntimeError:
        pass  # a running loop owns them; the loop owner tears them down
    reset_mcp_client_for_tests()
    reset_supervisor_for_tests()


async def _stop_all_mcp_servers(mcp) -> None:
    for srv in list(mcp.list_servers()):
        try:
            await mcp.remove(srv.name)
        except Exception:
            pass


# ---- setup gate ---------------------------------------------------------------

def test_setup_gate_reports_first_run(client):
    s = client.get("/api/setup").json()
    assert s == {"setup_complete": False, "has_provider": False,
                 "assistant_name": "Universe"}


def test_setup_complete_persists_name(client):
    r = client.post("/api/setup/complete", json={"assistant_name": "Nova"})
    assert r.json()["assistant_name"] == "Nova"
    s = client.get("/api/setup").json()
    assert s["setup_complete"] is True and s["assistant_name"] == "Nova"


def test_setup_complete_defaults_name(client):
    r = client.post("/api/setup/complete", json={})
    assert r.json()["assistant_name"] == "Universe"


# ---- projects ------------------------------------------------------------------

def test_project_crud_and_goal_count(client):
    from storage.local import get_storage
    pid = client.post("/api/projects", json={"name": "Apollo"}).json()["project"]["id"]
    st = get_storage()
    with st.transaction() as conn:
        for i in range(3):
            conn.execute("INSERT INTO goals(id,text,status,created_at,project_id) "
                         "VALUES(?,?,?, ?, ?);",
                         (f"goal-p{i}", f"task {i}", "completed", 1000 + i, pid))
    projects = client.get("/api/projects").json()["projects"]
    apollo = next(p for p in projects if p["id"] == pid)
    assert apollo["goal_count"] == 3
    # rename
    client.patch(f"/api/projects/{pid}", json={"name": "Apollo 2"})
    apollo = next(p for p in client.get("/api/projects").json()["projects"]
                  if p["id"] == pid)
    assert apollo["name"] == "Apollo 2"
    # soft delete archives; goals fall back only on hard
    client.delete(f"/api/projects/{pid}")
    apollo = next(p for p in client.get("/api/projects").json()["projects"]
                  if p["id"] == pid)
    assert apollo["archived"] == 1
    # hard delete with chats -> 409
    r = client.delete(f"/api/projects/{pid}?hard=true")
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_not_empty"


def test_general_project_is_retired_not_protected(client):
    """v22: the synthetic 'general' project row no longer exists — the
    endpoint answers honestly that the project is unknown."""
    r = client.delete("/api/projects/general")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"
    listed = client.get("/api/projects").json()["projects"]
    assert not any(p["id"] == "general" for p in listed)


# ---- chat lifecycle ---------------------------------------------------------------

def _seed_goal(turn_id: str, text: str = "write a thing",
               project_id: str | None = None) -> None:
    from storage.local import get_storage
    with get_storage().transaction() as conn:
        conn.execute(
            "INSERT INTO goals(id,text,status,created_at,project_id,title) "
            "VALUES(?,?,?,?,?,?);",
            (turn_id, text, "completed", 1000.0, project_id, text[:80]))


def test_goal_archive_unarchive_pin_rename(client):
    _seed_goal("goal-x1", "original text")
    # archive hides
    client.patch("/api/turn/goal-x1", json={"archived": True})
    assert all(g["turn_id"] != "goal-x1"
               for g in client.get("/api/sessions").json()["goals"])
    got = next(g for g in client.get("/api/sessions?include_archived=true").json()["goals"]
               if g["turn_id"] == "goal-x1")
    assert got["archived"] == 1
    # unarchive restores + undeletes
    client.patch("/api/turn/goal-x1", json={"archived": False, "title": "Renamed",
                                              "pinned": True})
    got = next(g for g in client.get("/api/sessions").json()["goals"]
               if g["turn_id"] == "goal-x1")
    assert got["title"] == "Renamed" and got["pinned"] == 1 and got["deleted"] == 0


def test_goal_soft_and_hard_delete(client, fresh_home):
    _seed_goal("goal-d1", "to delete softly")
    client.delete("/api/turn/goal-d1")
    assert all(g["turn_id"] != "goal-d1"
               for g in client.get("/api/sessions?include_archived=true").json()["goals"])
    # hard delete removes the row + planet + timeline
    _seed_goal("goal-d2", "to delete hard")
    from storage.local import get_storage
    with get_storage().transaction() as conn:
        conn.execute("INSERT INTO planets(id,turn_id,goal_text,status,created_at,updated_at) "
                     "VALUES('p1','goal-d2','to delete hard','completed',1.0,1.0);")
    tl = fresh_home / "timelines" / "goal-d2.jsonl"
    tl.parent.mkdir(parents=True, exist_ok=True)
    tl.write_text('{"t":1}\n', encoding="utf-8")
    client.delete("/api/turn/goal-d2?hard=true")
    assert get_storage().query_one(
        "SELECT id FROM goals WHERE id='goal-d2';") is None
    assert get_storage().query_one(
        "SELECT id FROM planets WHERE turn_id='goal-d2';") is None
    assert not tl.exists()


def test_goal_branch_carries_parent_and_project(client):
    _seed_goal("goal-b1", "branch me", project_id="")
    r = client.post("/api/turn/goal-b1/branch").json()
    assert r["session_id"] == "goal-b1" and r["text"] == "branch me"
    branched = next(g for g in client.get("/api/sessions").json()["goals"]
                    if g["turn_id"] == r["turn_id"])
    assert branched["session_id"] == "goal-b1"
    assert branched["project_id"] == ""
    assert branched["title"].startswith("⑂")


def test_goals_search_filter(client):
    _seed_goal("goal-q1", "write a fibonacci function")
    _seed_goal("goal-q2", "research quantum computing")
    hits = client.get("/api/sessions?q=fibonacci").json()["goals"]
    assert [g["turn_id"] for g in hits] == ["goal-q1"]


def test_mcp_invalid_server_rejected(client):
    r = client.post("/api/mcp/servers", json={"name": "bad",
                                               "transport": "websocket"})
    assert r.status_code == 422
    r = client.post("/api/mcp/servers", json={"name": "nourl", "transport": "sse"})
    assert r.status_code == 422


# ---- MCP remote transports (REAL local HTTP server) ---------------------------

class _MiniMcpHttpServer:
    """A real Streamable-HTTP-ish MCP server: JSON POST in, JSON or SSE out.
    Implements initialize + tools/list + tools/call — enough to prove the
    client's remote transport end-to-end over a socket."""

    def __init__(self, mode: str = "json") -> None:
        from fastapi import FastAPI
        import uvicorn
        self.mode = mode  # json | sse (SSE wraps the same payload)
        self.app = FastAPI()
        self.session_id = "sess-test-1"
        self.initialize_seen = False
        self.session_header_seen = False

        @self.app.post("/mcp")
        async def mcp(req: dict):
            from starlette.responses import JSONResponse, Response
            headers = {"mcp-session-id": self.session_id}
            if req.get("method") == "initialize":
                self.initialize_seen = True
                body = {"jsonrpc": "2.0", "id": req["id"], "result": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "serverInfo": {"name": "mini", "version": "0"}}}
            elif req.get("method") == "tools/list":
                body = {"jsonrpc": "2.0", "id": req["id"], "result": {"tools": [
                    {"name": "echo", "description": "echo a message",
                     "inputSchema": {"type": "object",
                                     "properties": {"message": {"type": "string"}}}}]}}
            elif req.get("method") == "tools/call":
                args = (req.get("params") or {}).get("arguments") or {}
                body = {"jsonrpc": "2.0", "id": req["id"], "result": {
                    "content": [{"type": "text",
                                 "text": f"echo:{args.get('message','')}]"}]}}
            elif req.get("method") == "notifications/initialized":
                # notification: 202 Accepted with no body (per MCP spec)
                return Response(status_code=202, headers=headers)
            else:
                body = {"jsonrpc": "2.0", "id": req.get("id"),
                        "error": {"code": -32601, "message": "unknown"}}
            if self.mode == "sse":
                payload = "data: " + json.dumps(body) + "\n\n"
                return Response(payload, media_type="text/event-stream",
                                headers=headers)
            return JSONResponse(body, headers=headers)

        self.config = uvicorn.Config(self.app, host="127.0.0.1", port=0,
                                     log_level="error")
        self.server = uvicorn.Server(self.config)

    async def start(self) -> str:
        task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.05)
        self._task = task
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/mcp"

    async def stop(self) -> None:
        self.server.should_exit = True
        await self._task


@pytest.mark.parametrize("mode", ["json", "sse"])
@pytest.mark.asyncio
async def test_mcp_remote_transport_handshake_and_call(fresh_home, mode):
    """http + sse transports: initialize → tools/list → tools/call over a
    REAL socket, including the Mcp-Session-Id echo."""
    srv = _MiniMcpHttpServer(mode=mode)
    url = await srv.start()
    try:
        from connectors.mcp_client import get_mcp_client
        mcp = get_mcp_client()
        mcp.add(name=f"remote-{mode}", transport="http" if mode == "json" else "sse",
                url=url)
        listed = await mcp.list_tools(f"remote-{mode}")
        assert listed["ok"], listed
        assert [t["name"] for t in listed["tools"]] == ["echo"]
        assert srv.initialize_seen
        called = await mcp.call(f"remote-{mode}", "echo", {"message": "hello"})
        assert called["ok"], called
        assert "echo:hello" in called["result"]
        # session header echoed on subsequent calls (set during initialize)
        assert srv.session_id == "sess-test-1"
        remote = mcp.get(f"remote-{mode}")
        assert remote._session_id == "sess-test-1"
        # disabled server refuses to start
        mcp.set_enabled(f"remote-{mode}", False)
        denied = await mcp.list_tools(f"remote-{mode}")
        assert not denied["ok"] and "disabled" in denied["error"]
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_mcp_http_error_shapes(fresh_home):
    """A URL that answers garbage fails the handshake honestly."""
    from connectors.mcp_client import get_mcp_client
    mcp = get_mcp_client()
    mcp.add(name="dead-remote", transport="http",
            url="http://127.0.0.1:1/mcp")  # nothing listens here
    res = await mcp.list_tools("dead-remote")
    assert not res["ok"]
    assert "failed to start/handshake" in res["error"]


def test_settings_stt_language_roundtrip(client):
    """PUT /api/settings persists stt_language (was silently dropped:
    SettingsUpdate lacked the field while DESKTOP_DEFAULTS + voice reader
    both support it)."""
    r = client.put("/api/settings", json={"stt_language": "ar"})
    assert r.status_code == 200
    assert "stt_language" in r.json()["changed"]
    s = client.get("/api/settings").json()["settings"]
    assert s["stt_language"] == "ar"


def test_mcp_patch_reconfigures_url_and_headers(client):
    """PATCH /api/mcp/servers/{name} edits url/headers/read_only in place
    (beyond the enabled-only toggle), with create-time validation."""
    r = client.post("/api/mcp/servers", json={
        "name": "edit-me", "transport": "http",
        "url": "http://127.0.0.1:9/mcp"})
    assert r.status_code == 200, r.text
    # invalid url is rejected, server untouched
    bad = client.patch("/api/mcp/servers/edit-me",
                       json={"url": "not-a-url", "verify": False})
    assert bad.status_code == 422
    # valid edit applies + persists to the row
    ok = client.patch("/api/mcp/servers/edit-me", json={
        "url": "http://127.0.0.1:10/mcp",
        "headers": {"Authorization": "Bearer EXAMPLE-TOKEN"},
        "read_only": False, "verify": False})
    assert ok.status_code == 200, ok.text
    srv = ok.json()["server"]
    assert srv["url"] == "http://127.0.0.1:10/mcp"
    assert srv["read_only"] is False
    assert srv["handshake_ok"] is False  # stale handshake cleared
    listed = client.get("/api/mcp/servers").json()["servers"]
    assert next(s for s in listed if s["name"] == "edit-me")["url"] == \
        "http://127.0.0.1:10/mcp"
    # unknown server still 404s
    missing = client.patch("/api/mcp/servers/nope", json={"enabled": False})
    assert missing.status_code == 404
