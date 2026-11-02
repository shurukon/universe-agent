"""tests/test_mcp_client.py — tests for the rewritten connectors/mcp_client.py
and connectors/builtin/mcp_tools.py.

Live-tests against the REAL official `@modelcontextprotocol/server-everything`
reference server wherever practical (protocol handshake, tool discovery, a
real tool call, read_only enforcement against real tool names) — this is
what caught the original protocol bug (missing initialize handshake, no
handling of interleaved notifications) and the npx/registry proxy-hang bug
during development; mocking the subprocess would have hidden both.
Isolated unit tests cover the egress proxy and the read-only heuristic
directly, which don't need a real server.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
async def mcp_home(fresh_home):
    """fresh_home plus a clean MCPClient singleton and a clean Supervisor
    (crash-loop tracking now lives in the shared Supervisor, not on
    MCPServer itself — see mcp_client.py module docstring)."""
    from connectors.mcp_client import get_mcp_client, reset_mcp_client_for_tests
    from concurrency.supervisor import reset_supervisor_for_tests
    old = get_mcp_client()
    for srv in list(old.list_servers()):
        await old._stop(srv)
    reset_mcp_client_for_tests()
    reset_supervisor_for_tests()
    yield fresh_home
    new = get_mcp_client()
    for srv in list(new.list_servers()):
        await new._stop(srv)


# ---------------------------------------------------------------------------
# Live protocol tests — real subprocess, real handshake, real tool calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_handshake_discovers_real_tools(mcp_home):
    """End-to-end against the real reference server: initialize -> tools/list,
    correctly skipping the notification the server pushes first (this is
    exactly what the original one-readline-per-call code could not handle)."""
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS
    mcp = get_mcp_client()
    mcp.add(name="everything", command=KNOWN_SERVERS["everything"]["command"],
            declared_hosts=[], read_only=True)
    r = await mcp.list_tools("everything")
    assert r["ok"] is True, r
    names = {t["name"] for t in r["tools"]}
    assert "echo" in names
    assert len(names) >= 10  # the reference server exposes 13 at time of writing


@pytest.mark.asyncio
async def test_real_tool_call_returns_tagged_untrusted_result(mcp_home):
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS
    mcp = get_mcp_client()
    mcp.add(name="everything", command=KNOWN_SERVERS["everything"]["command"],
            declared_hosts=[], read_only=True)
    r = await mcp.call("everything", "echo", {"message": "hello universe"})
    assert r["ok"] is True, r
    assert r["result"].startswith("[UNTRUSTED:mcp:everything]")
    assert "hello universe" in r["result"]


@pytest.mark.asyncio
async def test_calling_undiscovered_tool_fails_with_real_tool_list(mcp_home):
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS
    mcp = get_mcp_client()
    mcp.add(name="everything", command=KNOWN_SERVERS["everything"]["command"],
            declared_hosts=[])
    r = await mcp.call("everything", "not_a_real_tool", {})
    assert r["ok"] is False
    assert "not found" in r["error"]
    assert "echo" in r["error"]  # the real discovered list is in the message


@pytest.mark.asyncio
async def test_declared_capability_allowlist_further_restricts(mcp_home):
    """Even though the server has 13 real tools, an optional
    declared_capabilities allowlist can restrict which ones Universe will
    actually invoke."""
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS
    mcp = get_mcp_client()
    mcp.add(name="everything", command=KNOWN_SERVERS["everything"]["command"],
            declared_hosts=[], capabilities=["echo"])
    ok = await mcp.call("everything", "echo", {"message": "hi"})
    assert ok["ok"] is True
    blocked = await mcp.call("everything", "get-env", {})
    assert blocked["ok"] is False
    assert "allowlist" in blocked["error"]


@pytest.mark.asyncio
async def test_unknown_server_name_fails_cleanly(mcp_home):
    from connectors.mcp_client import get_mcp_client
    r = await get_mcp_client().call("does-not-exist", "anything", {})
    assert r["ok"] is False
    assert "unknown" in r["error"]


# ---------------------------------------------------------------------------
# read_only enforcement — against REAL tool names from the live server
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_only_true_blocks_a_write_like_real_tool_name(mcp_home):
    """'toggle-simulated-logging' is a real tool on the reference server.
    No annotation is provided (verified live), so the name heuristic must
    catch it via 'toggle' — this specific case is what was missing before
    the heuristic was extended during live testing."""
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS
    mcp = get_mcp_client()
    mcp.add(name="everything", command=KNOWN_SERVERS["everything"]["command"],
            declared_hosts=[], read_only=True)
    await mcp.list_tools("everything")  # populate srv.tools
    r = await mcp.call("everything", "toggle-simulated-logging", {"enabled": True})
    assert r["ok"] is False
    assert r.get("needs_consent") is True
    assert "read_only" in r["error"]


@pytest.mark.asyncio
async def test_read_only_false_allows_the_same_tool(mcp_home):
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS
    mcp = get_mcp_client()
    mcp.add(name="everything", command=KNOWN_SERVERS["everything"]["command"],
            declared_hosts=[], read_only=False)
    r = await mcp.call("everything", "toggle-simulated-logging", {"enabled": True})
    assert r["ok"] is True, r


def test_read_only_heuristic_unit():
    from connectors.mcp_client import _tool_is_read_only
    assert _tool_is_read_only({"name": "echo"}) is True
    assert _tool_is_read_only({"name": "get-weather"}) is True
    assert _tool_is_read_only({"name": "create-issue"}) is False
    assert _tool_is_read_only({"name": "toggle-logging"}) is False
    assert _tool_is_read_only({"name": "trigger-build"}) is False
    # annotation always wins over the name heuristic, in both directions
    assert _tool_is_read_only({"name": "get-data", "annotations": {"readOnlyHint": False}}) is False
    assert _tool_is_read_only({"name": "delete-everything", "annotations": {"readOnlyHint": True}}) is True


# ---------------------------------------------------------------------------
# Egress proxy — isolated, no MCP protocol involved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_egress_proxy_allows_declared_host():
    from connectors.mcp_client import _EgressProxy
    proxy = _EgressProxy(allowed_hosts=["pypi.org"])
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: pypi.org:443\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=8)
        assert b"200" in line
        writer.close()
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_egress_proxy_denies_undeclared_host():
    from connectors.mcp_client import _EgressProxy
    proxy = _EgressProxy(allowed_hosts=[])
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example:443\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=8)
        assert b"403" in line
        writer.close()
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_egress_proxy_denies_plain_http_not_just_undeclared_hosts():
    """Plain (non-CONNECT) HTTP is deliberately not relayed at all — fail
    closed rather than silently forwarding unchecked, as documented in
    _EgressProxy's docstring."""
    from connectors.mcp_client import _EgressProxy
    proxy = _EgressProxy(allowed_hosts=["pypi.org"])
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=8)
        assert b"501" in line
        writer.close()
    finally:
        await proxy.stop()


def test_bootstrap_hosts_recognizes_package_managers():
    from connectors.mcp_client import _bootstrap_hosts_for
    assert "registry.npmjs.org" in _bootstrap_hosts_for("npx -y @scope/server")
    assert "pypi.org" in _bootstrap_hosts_for("uvx mcp-server-git --repository /x")
    assert _bootstrap_hosts_for("node /already/resolved/server.js") == []


@pytest.mark.asyncio
async def test_npx_server_actually_starts_with_egress_enforced(mcp_home):
    """Regression test for the real bug found during development: npx checks
    registry.npmjs.org for version metadata even on a cached package, and
    npm treats a 403 there as fatal rather than falling back to cache — so
    without _bootstrap_hosts_for, EVERY npx-based server would fail to start
    the moment egress restriction is enabled with an empty declared_hosts."""
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS
    mcp = get_mcp_client()
    mcp.add(name="everything", command=KNOWN_SERVERS["everything"]["command"],
            declared_hosts=[])  # deliberately empty — must still work
    r = await mcp.list_tools("everything")
    assert r["ok"] is True, (
        f"npx-based server failed to start with egress enforced: {r.get('error')}")


# ---------------------------------------------------------------------------
# mcp_tools.py — the agent-callable layer, through the real CapabilityGate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_add_server_requires_explicit_consent(mcp_home):
    from security.capability import get_gate
    from connectors.builtin import get_registry
    get_gate().set_auto_grant(False)
    r = await get_registry().call("McpAddServer", agent="universe", turn_id="g1",
                                  args={"name": "x", "server_key": "memory"})
    assert r["ok"] is False
    assert r.get("needs_consent") is True


@pytest.mark.asyncio
async def test_mcp_add_server_curated_directory_end_to_end(mcp_home):
    from connectors.builtin import get_registry
    r = await get_registry().call("McpAddServer", agent="universe", turn_id="g1",
                                  args={"name": "mem1", "server_key": "memory"})
    assert r["ok"] is True
    inner = r["result"]
    assert inner["ok"] is True, inner
    assert len(inner["tools_discovered"]) > 0


@pytest.mark.asyncio
async def test_mcp_add_server_unknown_curated_key_fails_clearly(mcp_home):
    from connectors.builtin import get_registry
    r = await get_registry().call("McpAddServer", agent="universe", turn_id="g1",
                                  args={"name": "x", "server_key": "not-a-real-server"})
    assert r["result"]["ok"] is False
    assert "curated directory" in r["result"]["error"]


@pytest.mark.asyncio
async def test_mcp_add_server_missing_required_path_arg(mcp_home):
    from connectors.builtin import get_registry
    r = await get_registry().call("McpAddServer", agent="universe", turn_id="g1",
                                  args={"name": "fs1", "server_key": "filesystem"})
    assert r["result"]["ok"] is False
    assert "path" in r["result"]["error"]


@pytest.mark.asyncio
async def test_mcp_list_servers_is_auto_consent_and_local_only(mcp_home):
    """mcp_list_servers must not require any consent (auto) since it never
    launches a subprocess or touches the network."""
    from connectors.builtin import get_registry
    from security.capability import get_gate
    get_gate().set_auto_grant(False)
    r = await get_registry().call("McpListServers", agent="universe", turn_id="g1", args={})
    assert r["ok"] is True
    assert r["result"]["ok"] is True


@pytest.mark.asyncio
async def test_mcp_call_tool_dispatches_through_gate(mcp_home):
    from connectors.builtin import get_registry
    reg = get_registry()
    await reg.call("McpAddServer", agent="universe", turn_id="g1",
                   args={"name": "everything", "server_key": "everything"})
    r = await reg.call("McpCallTool", agent="universe", turn_id="g1",
                       args={"server": "everything", "tool": "echo",
                             "params_json": '{"message": "via dispatcher"}'})
    assert r["ok"] is True
    assert r["result"]["ok"] is True
    assert "via dispatcher" in r["result"]["result"]


@pytest.mark.asyncio
async def test_mcp_call_tool_rejects_invalid_json_params(mcp_home):
    from connectors.builtin import get_registry
    r = await get_registry().call("McpCallTool", agent="universe", turn_id="g1",
                                  args={"server": "x", "tool": "y",
                                        "params_json": "{not valid json"})
    assert r["result"]["ok"] is False
    assert "JSON" in r["result"]["error"]


@pytest.mark.asyncio
async def test_mcp_remove_server_requires_explicit_and_actually_stops_it(mcp_home):
    from connectors.builtin import get_registry
    reg = get_registry()
    await reg.call("McpAddServer", agent="universe", turn_id="g1",
                   args={"name": "mem2", "server_key": "memory"})
    from security.capability import get_gate
    get_gate().set_auto_grant(False)
    blocked = await reg.call("McpRemoveServer", agent="universe", turn_id="g1", args={"name": "mem2"})
    assert blocked.get("needs_consent") is True
    get_gate().set_auto_grant(True)
    removed = await reg.call("McpRemoveServer", agent="universe", turn_id="g1", args={"name": "mem2"})
    assert removed["result"]["ok"] is True
    from connectors.mcp_client import get_mcp_client
    assert get_mcp_client().get("mem2") is None


@pytest.mark.asyncio
async def test_mcp_tools_not_callable_by_unregistered_agent(mcp_home):
    """Phase 0.1 enforcement must still apply to the new MCP tools exactly
    like every other tool. Post-S1 the registered agent is 'universe' (the
    MCP tools ARE in its merged whitelist), so the enforced boundary is the
    fail-closed unknown-agent denial — an unregistered name gets NO
    whitelist exemption, for MCP tools like any other."""
    from connectors.builtin import get_registry
    r = await get_registry().call("McpAddServer", agent="code", turn_id="g1",
                                  args={"name": "x", "server_key": "memory"})
    assert r["ok"] is False
    assert r["blocked_by"] == "unknown_agent"


# ---------------------------------------------------------------------------
# Crash supervision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crash_loop_pauses_after_three_failures(mcp_home):
    """Crash-loop tracking now delegates to concurrency/supervisor.py's
    shared Supervisor (see mcp_client.py module docstring) rather than
    tracking state independently — this asserts against that shared source
    of truth, not a field on MCPServer."""
    from connectors.mcp_client import get_mcp_client
    from concurrency.supervisor import get_supervisor
    mcp = get_mcp_client()
    mcp.add(name="broken", command="false", declared_hosts=[])  # 'false' exits 1 immediately
    for _ in range(3):
        r = await mcp.call("broken", "anything", {})
        assert r["ok"] is False
    assert get_supervisor().is_paused("broken") is True
    final = await mcp.call("broken", "anything", {})
    assert "paused" in final["error"]
