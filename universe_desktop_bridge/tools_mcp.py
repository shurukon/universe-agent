"""universe_desktop_bridge/tools_mcp.py — Universe's tools as an MCP server.

The harness can mount an MCP server over streamable HTTP on a driver turn
(`integrations.custom`). This module exposes the same tool registry the
Universe agent uses, so an engine running in universe bots can reach those
tools without a second implementation.

The schemas come from the registry itself — the very ones the model sees —
so a tool advertised here is a tool the gate can actually execute.
"""
from __future__ import annotations

import inspect
import json
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from connectors.builtin import get_registry
from core.agent.base_agent import _schema_for_handler


def _annotation(kind: str) -> type:
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(kind, str)


def build_server() -> FastMCP:
    """A server with one MCP tool per registered Universe tool."""
    # The bridge mounts this app inside its own FastAPI lifespan, so the
    # sub-app's lifespan never runs. Stateless mode needs no task group.
    server = FastMCP("universe-tools", stateless_http=True)
    registry = get_registry()

    for name in registry.names():
        tool = registry.get(name)
        if tool is None:
            continue
        schema = tool.schema or _schema_for_handler(tool.handler)
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])

        parameters = []
        for key, spec in properties.items():
            if not isinstance(spec, dict) or key in ("turn_id", "agent"):
                continue
            kind = str(spec.get("type") or "string")
            parameters.append(inspect.Parameter(
                key,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if key in required else None,
                annotation=_annotation(kind),
            ))

        def make_handler(tool_name: str, kwargs_names: tuple[str, ...]):
            async def invoke(**kwargs: Any) -> str:
                args = {key: value for key, value in kwargs.items()
                        if key in kwargs_names and value is not None}
                result = await get_registry().call(
                    tool_name,
                    agent="universe",
                    turn_id=f"mcp-{tool_name}",
                    args=args,
                )
                return json.dumps(result, ensure_ascii=False, default=str)
            return invoke

        handler = make_handler(name, tuple(p.name for p in parameters))
        handler.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
        server.add_tool(handler, name=name, description=tool.description)

    return server


_server: FastMCP | None = None


def get_server() -> FastMCP:
    """The MCP server, created once for the process."""
    global _server
    if _server is None:
        _server = build_server()
    return _server


def mcp_app():
    """The ASGI app mounted by the bridge."""
    return get_server().streamable_http_app()


@asynccontextmanager
async def mcp_lifespan():
    """The streamable-HTTP transport needs its task group running.

    The bridge mounts this app inside its own FastAPI app, so the sub-app's
    lifespan is not called for us. The bridge must enter it instead.
    """
    async with get_server().session_manager.run():
        yield
