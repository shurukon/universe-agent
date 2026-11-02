"""tests/test_m4_mcp_hardening.py — regression suite for the m4 MCP fixes.

Pure unit tests (no subprocess, no network):
  1. _parse_connect_target: IPv6 bracket notation parses; garbage fails closed
  2. _quote_template_value: spaces/quotes stay one argv element; control
     chars rejected
  3. SSE id matching: a notification without our id is never attributed
     to our call
  4. void result: a response with neither result nor error is a failure,
     not ok=True with result "null"
  5. _WRITE_HINTS covers the m4 verbs (edit/move/save/commit/append/rename/
     init/grant/install/launch)
  6. line cap: constant exists and a >16MB line aborts the read
"""
from __future__ import annotations


def test_connect_target_ipv4_and_hostname():
    from connectors.mcp_client import _EgressProxy as P
    assert P._parse_connect_target("pypi.org:443") == ("pypi.org", 443)
    assert P._parse_connect_target("pypi.org") == ("pypi.org", 443)
    assert P._parse_connect_target("127.0.0.1:8080") == ("127.0.0.1", 8080)


def test_connect_target_ipv6_brackets():
    from connectors.mcp_client import _EgressProxy as P
    assert P._parse_connect_target("[::1]:443") == ("::1", 443)
    assert P._parse_connect_target("[::1]") == ("::1", 443)
    assert P._parse_connect_target("[2001:db8::1]:8443") == ("2001:db8::1", 8443)


def test_connect_target_garbage_fails_closed():
    from connectors.mcp_client import _EgressProxy as P
    assert P._parse_connect_target("[::1") == (None, 0)
    assert P._parse_connect_target("") == (None, 0)
    assert P._parse_connect_target("host:notaport") == (None, 0)
    assert P._parse_connect_target("host:99999") == (None, 0)
    assert P._parse_connect_target("[::1]x:443") == (None, 0)


def test_quote_template_value_keeps_spaces_together():
    from connectors.mcp_client import _quote_template_value, _split_command
    quoted = _quote_template_value(r"C:\My Projects\repo dir")
    argv = _split_command(f"uvx mcp-server-git --repository {quoted}")
    assert argv[-1] == r"C:\My Projects\repo dir", argv


def test_quote_template_value_rejects_quotes_and_controls():
    import pytest
    from connectors.mcp_client import _quote_template_value
    with pytest.raises(ValueError):
        _quote_template_value('evil" --repository /x')
    with pytest.raises(ValueError):
        _quote_template_value("line1\nline2")
    with pytest.raises(ValueError):
        _quote_template_value("nul\x00byte")


def test_sse_notification_not_attributed_to_call():
    """A server-sent notification arriving in an SSE stream must not be
    returned as the tools/call response just because it came first."""
    import asyncio
    from connectors.mcp_client import MCPClient, MCPServer

    async def go():
        client = MCPClient.__new__(MCPClient)
        srv = MCPServer(id="t", name="t", command="", transport="http",
                        url="http://127.0.0.1:1/mcp")
        body = (
            'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/ping"}\n\n'
            'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"ok":1}}\n\n'
        )

        class FakeResp:
            status_code = 200
            headers = {"content-type": "text/event-stream"}
            text = body
            def raise_for_status(self):
                pass

        class FakeHttp:
            async def post(self, url, json=None, headers=None):
                return FakeResp()

        srv._http = FakeHttp()
        return await client._http_rpc(
            srv, {"jsonrpc": "2.0", "id": 7,
                  "method": "tools/call", "params": {}})

    out = asyncio.new_event_loop().run_until_complete(go())
    assert out == {"jsonrpc": "2.0", "id": 7, "result": {"ok": 1}}


def test_sse_wrong_id_returns_none_not_foreign_payload():
    import asyncio
    from connectors.mcp_client import MCPClient, MCPServer

    async def go():
        client = MCPClient.__new__(MCPClient)
        srv = MCPServer(id="t", name="t", command="", transport="http",
                        url="http://127.0.0.1:1/mcp")
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":999,"result":{"ok":1}}\n\n'

        class FakeResp:
            status_code = 200
            headers = {"content-type": "text/event-stream"}
            text = body
            def raise_for_status(self):
                pass

        class FakeHttp:
            async def post(self, url, json=None, headers=None):
                return FakeResp()

        srv._http = FakeHttp()
        return await client._http_rpc(
            srv, {"jsonrpc": "2.0", "id": 7,
                  "method": "tools/call", "params": {}})

    out = asyncio.new_event_loop().run_until_complete(go())
    assert out is None


def test_void_result_is_failure_not_success():
    """A JSON-RPC reply with neither result nor error must surface as an
    error dict, never ok=True."""
    import asyncio
    from connectors.mcp_client import MCPClient, MCPServer, reset_mcp_client_for_tests
    reset_mcp_client_for_tests()

    async def go():
        client = MCPClient.__new__(MCPClient)
        client._servers = {}
        srv = MCPServer(id="v", name="voidsrv", command="", transport="stdio")
        client._servers["voidsrv"] = srv
        srv.tools = {"t": {"name": "t", "annotations": {"readOnlyHint": True}}}

        async def fake_started(s):
            s.handshake_ok = True
        client._ensure_started = fake_started  # type: ignore[method-assign]

        async def fake_send(s, msg):
            pass
        async def fake_recv(s, expected_id, timeout=20.0):
            return {"jsonrpc": "2.0", "id": expected_id}  # neither result nor error
        client._send = fake_send  # type: ignore[method-assign]
        client._recv_response_for = fake_recv  # type: ignore[method-assign]
        from concurrency.supervisor import reset_supervisor_for_tests
        reset_supervisor_for_tests()
        return await client.call("voidsrv", "t", {})

    out = asyncio.new_event_loop().run_until_complete(go())
    assert out["ok"] is False
    assert "neither result nor error" in out["error"]
    reset_mcp_client_for_tests()


def test_write_hints_cover_m4_verbs():
    from connectors.mcp_client import _tool_is_read_only
    for verb in ("edit", "move", "save", "commit", "append", "rename",
                 "init", "grant", "install", "launch"):
        assert _tool_is_read_only({"name": f"file-{verb}"}) is False, verb
    assert _tool_is_read_only({"name": "echo"}) is True


def test_line_cap_constant_and_abort():
    import asyncio
    import pytest
    from connectors.mcp_client import MCPClient, MCPServer, _MAX_MCP_LINE_BYTES

    assert _MAX_MCP_LINE_BYTES == 16 * 1024 * 1024

    class FakeStdout:
        async def readline(self):
            return b"{" + b"x" * (_MAX_MCP_LINE_BYTES + 1) + b"}\n"

    class FakeProc:
        stdout = FakeStdout()

    async def go():
        client = MCPClient.__new__(MCPClient)
        srv = MCPServer(id="t", name="t", command="")
        srv.proc = FakeProc()
        return await client._recv_response_for(srv, 1, timeout=5.0)

    with pytest.raises(RuntimeError, match="over .* bytes"):
        asyncio.new_event_loop().run_until_complete(go())


# ---------------------------------------------------------------------------
# m4-close additions: accounting columns, 404 retry, timeout≠crash,
# client eviction, tab id uniqueness
# ---------------------------------------------------------------------------

def test_v16_migration_adds_key_alias_and_error_kind(fresh_home):
    from schema.migrations import run
    run()
    from storage.local import get_storage
    cols = [r["name"] for r in
            get_storage().query_all("PRAGMA table_info(llm_calls);")]
    assert "key_alias" in cols
    assert "error_kind" in cols


def test_record_inserts_key_alias_and_empty_error_kind(fresh_home):
    import asyncio
    from providers.client import LLMClient, LLMResponse
    c = LLMClient()
    # _record is async now (the JSONL/SQLite writes hop to a worker thread);
    # the durable row must still land when awaited
    asyncio.run(c._record("orchestrator", LLMResponse(
        text="hi", model="m", provider="p", input_tokens=3, output_tokens=2,
        latency_ms=9, finish_reason="stop", key_alias="desk", raw={}), []))
    from storage.local import get_storage
    row = get_storage().query_one(
        "SELECT key_alias, error_kind FROM llm_calls ORDER BY id DESC LIMIT 1;")
    assert row["key_alias"] == "desk"
    assert row["error_kind"] == ""


def test_complete_terminal_failure_is_accounted(fresh_home):
    import asyncio
    from providers.client import LLMClient
    from providers.manager import get_provider_manager
    from security.secrets_fallback import encrypt_secret
    pm = get_provider_manager()
    pm.add_provider("Dead", "https://127.0.0.1:9/v1")
    pm.add_key("Dead", alias="d0",
               encrypted_secret=encrypt_secret("local-no-key"))
    pm.set_agent_model("orchestrator", "Dead", "none")
    c = LLMClient()
    from providers.client import LLMError
    try:
        asyncio.new_event_loop().run_until_complete(
            c.complete(agent="orchestrator",
                       messages=[{"role": "user", "content": "hi"}],
                       max_tokens=8))
        raise AssertionError("should have raised")
    except LLMError as e:
        # structured failure: provider name on .provider, never
        # connection-refused prose disguised as success
        assert e.provider == "Dead"
        assert e.kind == "provider_down"
    from storage.local import get_storage
    row = get_storage().query_one(
        "SELECT finish_reason, error_kind, provider FROM llm_calls "
        "ORDER BY id DESC LIMIT 1;")
    assert row["finish_reason"] == "error"
    assert row["provider"] == "Dead"
    assert row["error_kind"] == "provider_down"


def test_timeout_is_not_a_crash():
    import asyncio
    from connectors.mcp_client import MCPClient
    assert MCPClient._is_timeout_error(asyncio.TimeoutError()) is True
    assert MCPClient._is_timeout_error(TimeoutError("t")) is True
    assert MCPClient._is_timeout_error(ValueError("x")) is False


def test_404_session_expiry_retries_once_with_fresh_handshake():
    import asyncio
    from connectors.mcp_client import (
        MCPClient, MCPServer, reset_mcp_client_for_tests)
    from concurrency.supervisor import reset_supervisor_for_tests

    async def go():
        client = MCPClient.__new__(MCPClient)
        client._servers = {}
        srv = MCPServer(id="s", name="flaky", command="",
                        transport="http", url="http://127.0.0.1:1/mcp")
        srv.tools = {"t": {"name": "t"}}
        srv.handshake_ok = True
        calls = {"n": 0}

        async def fake_rpc(s, msg, timeout=20.0):
            calls["n"] += 1
            if calls["n"] == 1:
                s._session_id = ""
                s.handshake_ok = False
                raise RuntimeError(
                    "MCP session expired; retry will re-initialize")
            return {"jsonrpc": "2.0", "id": msg["id"], "result": {"v": 1}}

        async def fake_start(s):
            s.handshake_ok = True
        client._http_rpc = fake_rpc  # type: ignore[method-assign]
        client._remote_start = fake_start  # type: ignore[method-assign]
        client._ensure_started = (  # type: ignore[method-assign]
            lambda s: asyncio.sleep(0))
        client._servers["flaky"] = srv
        reset_supervisor_for_tests()
        out = await client.call("flaky", "t", {})
        return out, calls["n"]

    out, n = asyncio.new_event_loop().run_until_complete(go())
    assert out["ok"] is True, out
    assert n == 2  # expired attempt + exactly one retried call
    reset_mcp_client_for_tests()


def test_remove_key_evicts_cached_client(fresh_home):
    from providers.manager import get_provider_manager
    from providers.client import get_llm_client
    from security.secrets_fallback import encrypt_secret
    pm = get_provider_manager()
    pm.add_provider("Ev", "https://example.invalid/v1")
    k = pm.add_key("Ev", alias="k0",
                   encrypted_secret=encrypt_secret("x"))
    c = get_llm_client()
    c._clients[("https://example.invalid/v1", "k0")] = object()
    assert pm.remove_key("Ev", k.id) is True
    assert ("https://example.invalid/v1", "k0") not in c._clients


def test_tab_open_ids_never_collide_after_close():
    import asyncio
    import connectors.builtin.browser as B

    class FakePage:
        def __init__(self):
            self.url = "about:blank"
        async def goto(self, url):
            self.url = url
        async def close(self):
            pass

    class FakeBrowser:
        def is_connected(self):
            return True
        async def new_page(self):
            return FakePage()

    async def go():
        B._browser = FakeBrowser()
        import asyncio as _aio
        B._loop_at_create = _aio.get_running_loop()
        B._pages.clear()
        B._cdp_pages.clear()
        r1 = await B.browser_tabs(action="open")
        r2 = await B.browser_tabs(action="open")
        assert r1["tab"] != r2["tab"]
        await B.browser_tabs(action="close", tab=r1["tab"])
        # len(_pages) is back to 1 — the old tab{len} scheme would now
        # re-mint tab2 and collide with the live tab
        r3 = await B.browser_tabs(action="open")
        assert r3["tab"] != r2["tab"], (r2, r3)
        B._browser = None
        B._pages.clear()
        B._loop_at_create = None
        return True

    assert asyncio.new_event_loop().run_until_complete(go()) is True
