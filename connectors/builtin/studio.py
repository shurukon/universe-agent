"""connectors/builtin/studio.py — agent tools for the Universe Studio
services. These are the REAL surfaces the Studio tool cards route to:

  StudioAVE      — AVE, the user's AI video editor. Its zero-dependency MCP
                   server (JSON-RPC 2.0 over HTTP, 127.0.0.1:19789/mcp)
                   exposes ~41 tools (timeline CRUD, clips, keyframes,
                   color/effects, export). This tool lists the live catalog
                   and executes one tool call — no simulation: an offline
                   AVE returns an honest unreachable error.
  StudioDesign   — the open-design daemon (127.0.0.1:7456), the
                   websites/presentations engine. Generic authenticated
                   /api/* passthrough so the agent can create artifacts and
                   read the daemon's own responses. Loopback only.
  StudioBlender  — Blender via the blender-mcp addon socket
                   (tcp://127.0.0.1:9876). Speaks the addon's real wire
                   protocol: one JSON object {"type": <command>, "params":
                   {...}} per request, response {"status", "result",
                   "message"}; chunked receive with JSON accumulation,
                   exactly like the official MCP server client.

All three go through the same Capability Gate as every other tool.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from connectors.builtin import ToolRegistry
from core.agent.base_agent import Tool

BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876
DESIGN_BASE = "http://127.0.0.1:7456"


def _make(name: str, capability: str, desc: str, fn, consent: str = "per_turn",
          resources=None):
    return Tool(name=name, capability=capability, description=desc, handler=fn,
                consent=consent, resources=resources or [])


# ---- StudioAVE ---------------------------------------------------------------

async def studio_ave(action: str = "list", tool: str = "",
                     arguments_json: str = "{}") -> dict:
    """List AVE's live MCP tool catalog, or execute ONE AVE tool call."""
    if action not in ("list", "call"):
        return {"ok": False, "error": "action must be 'list' or 'call'"}
    if action == "list":
        try:
            from universe_desktop_bridge import studio_mcp
            tools = await studio_mcp.list_tools()
        except Exception as exc:  # noqa: BLE001 — connection refused etc.
            return {"ok": False, "unreachable": True,
                    "error": f"AVE MCP unreachable (is AVE running?): {exc}"}
        return {"ok": True, "count": len(tools), "tools": tools}
    if not tool:
        return {"ok": False, "error": "tool is required for action='call'"}
    try:
        arguments = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"arguments_json is not valid JSON: {exc}"}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "arguments_json must decode to an object"}
    try:
        from universe_desktop_bridge import studio_mcp
        result = await studio_mcp.call_tool(tool, arguments)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "unreachable": True,
                "error": f"AVE MCP unreachable (is AVE running?): {exc}"}
    return {"ok": not result.get("isError"), "tool": tool,
            "arguments": arguments, "result": result}


# ---- StudioDesign ------------------------------------------------------------

async def studio_design(action: str = "health", method: str = "GET",
                        path: str = "/api/health",
                        body_json: str = "") -> dict:
    """Talk to the open-design daemon (127.0.0.1:7456): health probe, or a
    real GET/POST against one of its /api/* endpoints. The daemon's own
    responses come back verbatim — success AND errors."""
    import httpx
    if action == "health":
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(DESIGN_BASE + "/")
            return {"ok": resp.status_code < 500,
                    "status_code": resp.status_code,
                    "detail": "open-design daemon is reachable" 
                              if resp.status_code < 500 else "daemon unhealthy"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "unreachable": True,
                    "error": f"open-design daemon unreachable on {DESIGN_BASE} "
                             f"(start it from the Studio Design card): {exc}"}
    if action != "request":
        return {"ok": False, "error": "action must be 'health' or 'request'"}
    clean_path = (path or "").strip()
    if not clean_path.startswith("/api/"):
        return {"ok": False,
                "error": "path must start with /api/ (the daemon's API surface)"}
    method_u = method.upper()
    if method_u not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"ok": False, "error": f"unsupported method {method_u}"}
    body: Any = None
    if body_json.strip():
        try:
            body = json.loads(body_json)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"body_json is not valid JSON: {exc}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(method_u, DESIGN_BASE + clean_path,
                                        json=body if method_u != "GET" else None)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "unreachable": True,
                "error": f"open-design daemon unreachable on {DESIGN_BASE}: {exc}"}
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON body (HTML export etc.)
        payload = resp.text[:4000]
    return {"ok": resp.status_code < 400, "status_code": resp.status_code,
            "path": clean_path, "response": payload}


# ---- StudioBlender -----------------------------------------------------------

async def _blender_command(command_type: str, params: dict[str, Any] | None,
                           timeout: float) -> dict:
    """One real command over the blender-mcp addon socket — the same wire
    protocol the official MCP server speaks ({"type","params"} out, one JSON
    object back, chunked receive with accumulation)."""
    def _connect() -> tuple[Any, Any]:
        import socket
        sock = socket.create_connection((BLENDER_HOST, BLENDER_PORT),
                                        timeout=10.0)
        sock.settimeout(timeout)
        return sock, sock.makefile("rb")

    def _roundtrip() -> dict:
        import socket as _s
        sock, _ = _connect()
        try:
            payload = json.dumps({"type": command_type,
                                  "params": params or {}}).encode("utf-8")
            sock.sendall(payload)
            buf = b""
            while True:
                try:
                    chunk = sock.recv(8192)
                except _s.timeout as exc:
                    raise TimeoutError(
                        f"Blender addon did not answer within {timeout:.0f}s"
                    ) from exc
                if not chunk:
                    break
                buf += chunk
                try:
                    return json.loads(buf.decode("utf-8"))
                except json.JSONDecodeError:
                    continue  # partial JSON — keep accumulating
            raise ConnectionError("Blender addon closed the socket before a "
                                  "complete JSON response")
        finally:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass

    return await asyncio.to_thread(_roundtrip)


async def studio_blender(command: str = "get_scene_info",
                         params_json: str = "{}",
                         timeout_s: float = 180.0) -> dict:
    """Execute one command in Blender through the MCP addon socket. Read the
    scene first (get_scene_info), then build with execute_code or the addon's
    command types. The addon's own success/error envelope comes back real."""
    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"params_json is not valid JSON: {exc}"}
    if not isinstance(params, dict):
        return {"ok": False, "error": "params_json must decode to an object"}
    if not command or "/" in command:
        return {"ok": False, "error": "command must be an addon command type "
                                      "(e.g. get_scene_info, execute_code)"}
    try:
        timeout = max(5.0, min(float(timeout_s), 600.0))
    except (TypeError, ValueError):
        timeout = 180.0
    try:
        resp = await _blender_command(command, params, timeout)
    except (ConnectionError, OSError, TimeoutError) as exc:
        return {"ok": False, "unreachable": True,
                "error": f"Blender addon unreachable on {BLENDER_HOST}:{BLENDER_PORT} "
                         f"(open Blender and enable the blender-mcp addon): {exc}"}
    status = str(resp.get("status", ""))
    return {"ok": status == "success", "status": status,
            "result": resp.get("result"),
            "message": resp.get("message", "")}


# ---- registration ------------------------------------------------------------

def register(reg: ToolRegistry) -> None:
    reg.register(_make(
        "StudioAVE", "connector.run",
        "Drive AVE (video editor) through its MCP server on 127.0.0.1:19789. "
        "action='list' returns the live tool catalog; action='call' executes "
        "one AVE tool (tool= name, arguments_json= JSON object). Universe "
        "Studio routes video tasks here first.",
        studio_ave, "per_turn", []))
    reg.register(_make(
        "StudioDesign", "connector.run",
        "Drive the open-design daemon (websites, pages, presentations) on "
        "127.0.0.1:7456. action='health' probes the daemon; action='request' "
        "sends a real GET/POST to one of its /api/* endpoints "
        "(method/path/body_json). The daemon's responses return verbatim.",
        studio_design, "per_turn", []))
    reg.register(_make(
        "StudioBlender", "connector.run",
        "Execute one command in Blender through the blender-mcp addon socket "
        "(127.0.0.1:9876): command= get_scene_info / execute_code / "
        "get_viewport_screenshot / search_polyhaven_assets ... with "
        "params_json. Start 3D tasks with get_scene_info.",
        studio_blender, "per_turn", []))
