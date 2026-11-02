"""Capability discovery tools exposed through the normal registry and gate."""
from __future__ import annotations

from connectors.builtin import ToolRegistry
from core.agent.base_agent import Tool


def capability_catalog() -> dict:
    """Return the currently registered tool catalog and connected MCP tools."""
    from connectors.builtin import get_registry
    from observability.tool_lifecycle import catalog_snapshot
    registry = get_registry()
    payload = {
        "ok": True,
        "registered_tools": registry.names(),
        "catalog": catalog_snapshot(),
    }
    try:
        from connectors.mcp_client import get_mcp_client
        payload["mcp_servers"] = [
            {"name": server.name, "read_only": server.read_only,
             "declared_hosts": server.declared_hosts,
             "tools": sorted(server.tools) if server.tools else []}
            for server in get_mcp_client().list_servers()
        ]
    except Exception as exc:
        payload["mcp_servers"] = []
        payload["mcp_error"] = str(exc)
    return payload


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="CapabilityCatalog",
        capability="memory.read",
        description="Inspect the registered Universe tool catalog and connected MCP capabilities before choosing an approach.",
        handler=capability_catalog,
        consent="auto",
        resources=["universe:capabilities"],
    ))
