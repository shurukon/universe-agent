"""Minimal stdio MCP server — newline-delimited JSON-RPC 2.0 on stdin/stdout.

Supports: initialize, notifications/initialized, tools/list, tools/call.
One tool: echo(text) -> "echo: <text>". This is the REAL protocol shape
the bridge's stdio transport speaks (connectors/mcp_client.py).
"""
import json
import sys

TOOLS = [{
    "name": "echo",
    "description": "Echo the given text back",
    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
}]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method", "")
    msg_id = msg.get("id")
    if method == "initialize":
        out = {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-stdio", "version": "1.0"}}}
    elif method == "notifications/initialized":
        continue  # notifications get no response
    elif method == "tools/list":
        out = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments", {})
        out = {"jsonrpc": "2.0", "id": msg_id, "result": {
            "content": [{"type": "text", "text": f"echo: {args.get('text', '')}"}],
            "isError": False}}
    else:
        out = {"jsonrpc": "2.0", "id": msg_id,
               "error": {"code": -32601, "message": f"unknown method {method}"}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
