"""tests/test_studio_mcp.py — Universe drives AVE over MCP: the JSON-RPC
wire contract (pinned against mcp-server/server.js source) and the smart
drive() path (LLM picks one tool from the LIVE catalog)."""
from __future__ import annotations

import asyncio

import pytest

from universe_desktop_bridge import studio_mcp


class _RecordingClient:
    requests: list[dict] = []
    _resp: object = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kw):  # noqa: A002 — httpx signature
        _RecordingClient.requests.append(
            {"method": "POST", "url": url, "json": json, **kw})
        return self._resp

    async def get(self, url, **kw):
        _RecordingClient.requests.append({"method": "GET", "url": url, **kw})
        return self._resp


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def recording(monkeypatch):
    _RecordingClient.requests = []
    _RecordingClient._resp = None
    monkeypatch.setattr(studio_mcp.httpx, "AsyncClient", _RecordingClient)

    def install(payload, status=200):
        _RecordingClient._resp = _Resp(payload, status)

    return install


def _rpc_result(result):
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def test_tools_list_wire_contract(recording, fresh_home):
    recording(_rpc_result({"tools": [
        {"name": "add_clip", "description": "Add a clip to the timeline",
         "inputSchema": {"type": "object"}},
    ]}))
    tools = asyncio.run(studio_mcp.list_tools())
    req = _RecordingClient.requests[-1]
    assert req["url"] == "http://127.0.0.1:19789/mcp"
    body = req["json"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/list"
    assert tools[0]["name"] == "add_clip"


def test_call_tool_wire_contract(recording, fresh_home):
    recording(_rpc_result({"content": [{"type": "text",
                                        "text": "{\"ok\": true}"}]}))
    out = asyncio.run(studio_mcp.call_tool("add_clip", {"path": "a.mp4"}))
    req = _RecordingClient.requests[-1]
    assert req["json"]["method"] == "tools/call"
    assert req["json"]["params"] == {"name": "add_clip",
                                     "arguments": {"path": "a.mp4"}}
    assert out["data"] == {"ok": True}


def test_unreachable_is_honest(recording, fresh_home, monkeypatch):
    def _boom(*a, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(studio_mcp.httpx, "AsyncClient", _boom)
    with pytest.raises(studio_mcp.AveMcpError) as exc:
        asyncio.run(studio_mcp.list_tools())
    assert exc.value.reachable is False


def test_drive_picks_one_tool_from_live_catalog(recording, fresh_home,
                                                monkeypatch):
    tools = [{"name": "add_clip", "description": "Add a clip",
              "input_schema": {}},
             {"name": "export_project", "description": "Export",
              "input_schema": {}}]
    calls: list[tuple[str, dict]] = []

    async def fake_list_tools():
        return tools

    async def fake_call_tool(name, arguments):
        calls.append((name, arguments))
        return {"isError": False, "data": {"ok": True}}

    class _FakeLLM:
        async def complete(self, *, agent, messages):
            assert agent == "studio"
            assert "add_clip" in messages[0]["content"]
            return ("Here you go: "
                    '{"tool": "add_clip", "arguments": {"path": "intro.mp4"}}')

    monkeypatch.setattr(studio_mcp, "list_tools", fake_list_tools)
    monkeypatch.setattr(studio_mcp, "call_tool", fake_call_tool)
    monkeypatch.setattr("providers.client.get_llm_client", lambda: _FakeLLM())
    result = asyncio.run(studio_mcp.drive("add intro.mp4 to the timeline"))
    assert result["tool"] == "add_clip"
    assert calls == [("add_clip", {"path": "intro.mp4"})]


def test_drive_rejects_unknown_tool(recording, fresh_home, monkeypatch):
    async def fake_list_tools():
        return [{"name": "add_clip", "description": "Add a clip",
                 "input_schema": {}}]

    class _FakeLLM:
        async def complete(self, *, agent, messages):
            return '{"tool": "delete_everything", "arguments": {}}'

    monkeypatch.setattr(studio_mcp, "list_tools", fake_list_tools)
    monkeypatch.setattr("providers.client.get_llm_client", lambda: _FakeLLM())
    result = asyncio.run(studio_mcp.drive("nuke it"))
    assert result["tool"] is None


def test_ping_honest_when_offline(fresh_home):
    # no AVE MCP server runs in the test env — ping must say so, not throw
    out = asyncio.run(studio_mcp.ping())
    assert out["reachable"] is False and out["info"] == {}
