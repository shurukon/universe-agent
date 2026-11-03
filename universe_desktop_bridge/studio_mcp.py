"""universe_desktop_bridge/studio_mcp.py — Universe drives AVE over MCP.

AVE (the user's AI video editor) ships a zero-dependency MCP server speaking
JSON-RPC 2.0 over HTTP on 127.0.0.1:19789/mcp with 41 tools (timeline CRUD,
clips, keyframes, color/effects, export, project settings). This module is
the bridge between the chat and that server:

  * list_tools()  — the live tool catalog straight from AVE
  * call_tool()   — one real JSON-RPC tools/call
  * drive()       — the smart path: the configured LLM (the provider chain,
    exactly like chat) reads the live tool catalog and picks ONE tool +
    arguments for a natural-language instruction, we validate the choice and
    execute it. No simulation: if AVE is not running, callers get an honest
    "unreachable" result.

`mcp-server/server.js` handles tools/list and tools/call statelessly, so a
plain POST per call is the official wire shape (see its source).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from loguru import logger

DEFAULT_AVE_MCP = "http://127.0.0.1:19789/mcp"
DEFAULT_AVE_HEALTH = "http://127.0.0.1:19789/"


def mcp_url() -> str:
    return os.environ.get("UNIVERSE_AVE_MCP_URL", DEFAULT_AVE_MCP).rstrip("/")


def _rpc_body(method: str, params: dict[str, Any] | None = None,
              rpc_id: int = 1) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


class AveMcpError(Exception):
    def __init__(self, message: str, *, reachable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.reachable = reachable


async def _rpc(method: str, params: dict[str, Any] | None = None,
               timeout: float = 30.0) -> Any:
    payload = _rpc_body(method, params)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(mcp_url(), json=payload)
    except Exception as exc:  # noqa: BLE001 — connection refused etc.
        raise AveMcpError(f"AVE MCP unreachable at {mcp_url()}: {exc}",
                          reachable=False)
    if resp.status_code != 200:
        raise AveMcpError(f"AVE MCP HTTP {resp.status_code}", reachable=True)
    body = resp.json()
    if body.get("error"):
        raise AveMcpError(f"AVE MCP error: {body['error'].get('message')}",
                          reachable=True)
    return body.get("result")


async def ping() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(DEFAULT_AVE_HEALTH)
        if resp.status_code < 500:
            info = {}
            try:
                info = resp.json()
            except Exception:
                pass
            return {"reachable": True, "info": info}
    except Exception:
        pass
    return {"reachable": False, "info": {}}


async def list_tools() -> list[dict[str, Any]]:
    result = await _rpc("tools/list")
    tools = (result or {}).get("tools") or []
    return [{"name": t.get("name"),
             "description": (t.get("description") or "")[:400],
             "input_schema": t.get("inputSchema") or {}} for t in tools]


async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    result = await _rpc("tools/call", {"name": name, "arguments": arguments},
                        timeout=120.0)
    if result is None:
        return {"ok": False, "error": "empty result"}
    content = result.get("content") or []
    texts = [c.get("text") for c in content
             if isinstance(c, dict) and c.get("type") == "text"]
    out: dict[str, Any] = {"isError": bool(result.get("isError"))}
    if texts:
        joined = "\n".join(str(t) for t in texts)
        try:
            out["data"] = json.loads(joined)
        except Exception:
            out["data"] = joined[:6000]
    if result.get("structuredContent") is not None:
        out["structured"] = result["structuredContent"]
    return out


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def drive(instruction: str) -> dict[str, Any]:
    """Natural-language → ONE AVE tool call, using the live catalog."""
    instruction = (instruction or "").strip()
    if not instruction:
        raise AveMcpError("empty instruction", reachable=True)
    tools = await list_tools()
    if not tools:
        raise AveMcpError("AVE MCP returned no tools", reachable=True)
    catalog = "\n".join(f"- {t['name']}: {t['description']}"
                        for t in tools[:48])
    from providers.client import get_llm_client
    res = await get_llm_client().complete(
        agent="studio",
        messages=[
            {"role": "system",
             "content": (
                 "You operate AVE, a video editor, through its MCP tools. "
                 "Choose the ONE best tool for the user's instruction and "
                 "fill its arguments. Reply with STRICT JSON only — no prose, "
                 "no code fences:\n"
                 '{"tool": "<tool name>", "arguments": { ... }}\n'
                 "If no tool fits, reply exactly: "
                 '{"tool": null, "reason": "<why>"}\n\nTool catalog:\n'
                 + catalog)},
            {"role": "user", "content": instruction},
        ])
    text = ""
    if isinstance(res, dict):
        text = str(res.get("content") or res.get("text") or "")
    elif isinstance(res, str):
        text = res
    match = _JSON_RE.search(text)
    if not match:
        return {"tool": None,
                "reason": "the model did not return a tool choice",
                "raw": text[:500]}
    try:
        choice = json.loads(match.group(0))
    except Exception:
        return {"tool": None, "reason": "unparseable tool choice",
                "raw": text[:500]}
    tool = choice.get("tool")
    if not tool or tool not in {t["name"] for t in tools}:
        return {"tool": None,
                "reason": str(choice.get("reason") or "no suitable tool"),
                "raw": text[:300]}
    arguments = choice.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    result = await call_tool(str(tool), arguments)
    logger.info(f"AVE drive: {tool} ok")
    return {"tool": tool, "arguments": arguments, "result": result}
