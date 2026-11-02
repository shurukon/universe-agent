"""connectors/builtin/mcp_tools.py — agent-callable tools wrapping
connectors/mcp_client.py, wiring MCP server calls through the SAME
CapabilityGate every other tool goes through (the old mcp_client.py was
only reachable from the CLI wizard's add/list flow; no agent could
actually call an MCP tool during real goal execution before this file).

Design choice: one generic `mcp_call_tool(server, tool, params_json)`
dispatcher, not N dynamically-registered per-tool Tool objects. The
finer-grained enforcement (does the tool exist? is it within an optional
declared allowlist? does it look like a write op the server disallows?)
already lives inside MCPClient.call() itself and is exercised regardless of
which design wraps it. Per-tool CapabilityGate-level consent tracking
(matching the native connectors in thirdparty.py) is a reasonable future
enhancement once a runtime-whitelist-mutation mechanism exists — not
implemented here, flagged rather than implied.
"""
from __future__ import annotations

import json

from connectors.builtin import ToolRegistry
from core.agent.base_agent import Tool


def _make(name: str, capability: str, desc: str, fn, consent: str = "per_turn",
          resources=None):
    return Tool(name=name, capability=capability, description=desc, handler=fn,
                consent=consent, resources=resources or [])


async def mcp_add_server(name: str, server_key: str = "", command: str = "",
                         path: str = "", connection_string: str = "",
                         declared_hosts: str = "", read_only: bool = True) -> dict:
    """Connect an MCP server, then immediately discover its real tool list.
    Two ways to specify what to run:
      - server_key: a short name from the curated directory (see
        connectors.mcp_client.KNOWN_SERVERS) — 'everything', 'filesystem',
        'memory', 'postgres', 'git'. `path`/`connection_string` fill in
        whichever placeholder that entry needs.
      - command: a raw launch command for any other MCP server (e.g. a
        community server from registry.modelcontextprotocol.io) not in the
        curated list yet.
    declared_hosts (comma-separated) is the egress allowlist for the
    server's OWN runtime calls — package-manager bootstrap traffic (npm/
    PyPI registries) is handled separately and doesn't need to be listed.
    """
    from connectors.mcp_client import get_mcp_client, KNOWN_SERVERS, _quote_template_value
    from pathlib import Path
    mcp = get_mcp_client()
    if server_key:
        if server_key not in KNOWN_SERVERS:
            return {"ok": False,
                    "error": f"{server_key!r} not in the curated directory. "
                             f"Known: {sorted(KNOWN_SERVERS)}. Pass `command` "
                             f"directly for any other MCP server."}
        entry = KNOWN_SERVERS[server_key]
        cmd = entry["command"]
        if "{path}" in cmd:
            if not path:
                return {"ok": False, "error": f"{server_key!r} requires a `path` argument"}
            if not Path(path).expanduser().is_absolute():
                return {"ok": False,
                        "error": f"`path` must be an absolute directory, got {path!r}"}
            try:
                cmd = cmd.replace("{path}", _quote_template_value(path))
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        if "{connection_string}" in cmd:
            if not connection_string:
                return {"ok": False, "error": f"{server_key!r} requires `connection_string`"}
            try:
                cmd = cmd.replace("{connection_string}",
                                  _quote_template_value(connection_string,
                                                        allow_connection_string=True))
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        hosts = list(entry.get("declared_hosts", []))
    elif command:
        cmd = command
        hosts = [h.strip() for h in declared_hosts.split(",") if h.strip()]
    else:
        return {"ok": False, "error": "provide either server_key (curated directory) "
                                      "or command (custom server)"}

    from observability.tool_lifecycle import record_tool_lifecycle, register_catalog_entry
    record_tool_lifecycle(name=name, phase="proposed", status="requested", source="mcp",
                          details={"command": cmd, "declared_hosts": hosts, "read_only": read_only})
    mcp.add(name=name, command=cmd, declared_hosts=hosts, read_only=read_only)
    record_tool_lifecycle(name=name, phase="installed", status="connected", source="mcp")
    discovery = await mcp.list_tools(name)
    if not discovery["ok"]:
        record_tool_lifecycle(name=name, phase="failed", status="discovery_failed", source="mcp",
                              details={"error": discovery.get("error", "")})
        return {"ok": False,
                "error": f"server added but failed to start/discover tools: {discovery['error']}"}
    discovered_names = [t["name"] for t in discovery["tools"]]
    record_tool_lifecycle(name=name, phase="discovered", status="success", source="mcp",
                          details={"tools": discovered_names})
    for tool_info in discovery["tools"]:
        tool_name = str(tool_info.get("name", ""))
        if tool_name:
            register_catalog_entry(name=f"mcp:{name}:{tool_name}", kind="mcp",
                                   description=str(tool_info.get("description", "")),
                                   source=name, status="registered", details=tool_info)
            record_tool_lifecycle(name=f"mcp:{name}:{tool_name}", phase="registered",
                                  status="discovered", source="mcp", details=tool_info)
    return {"ok": True, "name": name, "read_only": read_only,
            "egress_limited_to": hosts or ["localhost only"],
            "tools_discovered": discovered_names,
            "note": ("write-like tools are blocked while read_only=True; "
                     "pass read_only=False when adding to allow them")}


def mcp_list_servers() -> dict:
    """List connected MCP servers and their discovered tools. Pure local
    read (no subprocess launch, no network) — safe to call freely."""
    from connectors.mcp_client import get_mcp_client
    from concurrency.supervisor import get_supervisor
    mcp = get_mcp_client()
    sup = get_supervisor()
    return {"ok": True, "servers": [
        {"name": s.name, "read_only": s.read_only, "paused": sup.is_paused(s.name),
         "declared_hosts": s.declared_hosts,
         "tools": sorted(s.tools) if s.tools else "[not yet started]"}
        for s in mcp.list_servers()
    ]}


async def mcp_remove_server(name: str) -> dict:
    """Disconnect and remove an MCP server (terminates its subprocess and
    egress proxy, deletes its stored config)."""
    from connectors.mcp_client import get_mcp_client
    ok = await get_mcp_client().remove(name)
    return {"ok": ok, "removed": name if ok else None,
            "error": None if ok else f"no server named {name!r}"}


async def mcp_call_tool(server: str, tool: str, params_json: str = "{}") -> dict:
    """Call a tool on a connected MCP server. `params_json` is a JSON object
    string of arguments for that tool (use '{}' for tools with no args).
    Enforcement happens inside MCPClient.call(): the tool must be in the
    server's real discovered list, within its optional declared capability
    allowlist if one was set, and — if the server is read_only=True (the
    default) — must look like a read operation (via MCP annotations or a
    name heuristic) or the call is refused."""
    from connectors.mcp_client import get_mcp_client
    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"params_json is not valid JSON: {e}"}
    return await get_mcp_client().call(server, tool, params)


def register(reg: ToolRegistry) -> None:
    reg.register(_make(
        "McpAddServer", "connector.run",
        "Connect an MCP server (curated: everything/filesystem/memory/postgres/git, "
        "or any custom launch command) and discover its tools. Runs third-party "
        "subprocess code — requires explicit consent.",
        mcp_add_server, "explicit", ["path:*"]))
    reg.register(_make(
        "McpListServers", "connector.run",
        "List connected MCP servers and their discovered tools. Local read only.",
        mcp_list_servers, "auto", []))
    reg.register(_make(
        "McpRemoveServer", "connector.run",
        "Disconnect and remove an MCP server.",
        mcp_remove_server, "explicit", []))
    reg.register(_make(
        "McpCallTool", "connector.run",
        "Call a tool on a connected MCP server (server's read_only setting "
        "and declared capabilities are enforced internally).",
        mcp_call_tool, "per_turn", []))
