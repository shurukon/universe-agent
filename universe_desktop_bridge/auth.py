"""universe_desktop_bridge/auth.py — the bridge's bearer-token gate.

Every HTTP request and WebSocket upgrade must carry the token that the
Electron main process generated and passed via UNIVERSE_BRIDGE_TOKEN. This
closes the class of attacks where any local process — or any website via
DNS rebinding / direct localhost fetch — could drive the unauthenticated
bridge (approve consents, spawn MCP subprocesses, start goals, read the
live event stream).

Threat model (see docs/SECURITY.md):
  * Malicious local process: must NOT be able to call the API. It can
    still read env vars of ITS OWN process, not ours — the token lives in
    the Electron main process env only.
  * Malicious webpage: has no token, and the Origin check below rejects
    it before routing.
  * Dev/tests: run with UNIVERSE_BRIDGE_TOKEN=dev to disable the gate
    explicitly (loopback-only by design). WITHOUT that opt-out a bare
    `python -m universe_desktop_bridge` now generates a random token and
    prints it — the gate is never silently off (audit 2026-09).

Failure discipline: fail CLOSED. A configured token that is not
presented correctly → reject everything.
"""
from __future__ import annotations

import os
import secrets
import sys

from starlette.types import ASGIApp, Receive, Scope, Send

# The explicit opt-out for pytest, e2e scripts and a deliberate dev run.
# "dev" disables the token gate (loopback binding remains the boundary).
DEV_TOKEN = "dev"


def get_bridge_token() -> str:
    """Resolve the token the bridge will require.

    Precedence:
      1. UNIVERSE_BRIDGE_TOKEN env (set by Electron main / the operator /
         the explicit dev opt-out)
      2. a freshly generated random token, printed once to stderr — a
         bare `python -m universe_desktop_bridge` used to silently accept
         the literal "dev" token, i.e. NO gate at all while any local
         process could approve consents and start goals (audit 2026-09)
    """
    configured = os.environ.get("UNIVERSE_BRIDGE_TOKEN")
    if configured:
        return configured
    generated = secrets.token_urlsafe(32)
    print("universe-bridge: UNIVERSE_BRIDGE_TOKEN not set — generated a random "
          f"token for this run: {generated}\n(set UNIVERSE_BRIDGE_TOKEN=dev to "
          "explicitly disable the gate for local development)",
          file=sys.stderr)
    os.environ["UNIVERSE_BRIDGE_TOKEN"] = generated
    return generated


def generate_token() -> str:
    """A fresh 256-bit token; Electron main calls this once per launch."""
    return secrets.token_urlsafe(32)


def _token_ok(provided: str | None, expected: str) -> bool:
    if expected == DEV_TOKEN:
        return True  # explicit dev opt-out: gate off (loopback is the boundary)
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


def _origin_ok(headers: dict) -> bool:
    """Origin discipline for cross-site protection.

    The bridge serves exactly two browser origins: the packaged Electron
    app (app://universe) and localhost dev servers. A missing Origin
    (non-browser clients: Electron main health poll, python tests, curl)
    is allowed — the bearer token is what authorizes those.

    Exact origin match only: the old prefix check accepted
    http://localhost.evil.com-style origins (audit 2026-09).
    """
    raw = headers.get(b"origin")
    if not raw:
        return True
    origin = raw.decode("latin-1", "replace").strip().lower()
    if not origin:
        return True
    if origin == "app://universe":
        return True
    try:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
        return False
    if parsed.path not in ("", "/") or parsed.query or parsed.username:
        return False
    port = parsed.port
    return port is None or (isinstance(port, int) and 0 < port < 65536)


class BridgeAuthMiddleware:
    """ASGI middleware enforcing the token on every HTTP + WS route.

    Rejected requests never reach routing: 401 with the bridge's uniform
    error shape. WebSocket upgrades are rejected with policy code 1008.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        expected = get_bridge_token()
        token: str | None = None
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization")
        if raw:
            parts = raw.decode("latin-1", "replace").split(" ", 1)
            token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None
        if not token:
            # the renderer may also send it as a plain header (WS + fetch)
            raw2 = headers.get(b"x-universe-token")
            if raw2:
                token = raw2.decode("latin-1", "replace").strip()
        if not token and (scope["type"] == "websocket" or
                          (scope["type"] == "http" and scope.get("method") == "GET")):
            # browsers cannot set custom headers on WebSocket, and media
            # elements (<img>/<audio>/<video>/<iframe>) cannot set them on
            # GET either: the renderer passes the token as a query param
            # instead (needed by /api/file previews)
            qs = scope.get("query_string") or b""
            for pair in qs.split(b"&"):
                if pair.startswith(b"token="):
                    token = pair[6:].decode("latin-1", "replace")
                    break

        if not _token_ok(token, expected):
            if scope["type"] == "http":
                await _json_error(scope, receive, send, 401, "unauthorized",
                                  "missing or invalid bridge token")
            else:
                await _ws_reject(scope, receive, send)
            return
        if not _origin_ok(headers):
            if scope["type"] == "http":
                await _json_error(scope, receive, send, 403, "bad_origin",
                                  "origin not allowed for the Universe bridge")
            else:
                await _ws_reject(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _json_error(scope: Scope, receive: Receive, send: Send,
                      status: int, code: str, message: str) -> None:
    from starlette.responses import JSONResponse

    resp = JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})
    await resp(scope, receive, send)


async def _ws_reject(scope: Scope, receive: Receive, send: Send) -> None:
    # per ASGI: closing before accept rejects the upgrade handshake.
    # 1008 = policy violation (unauthorized / bad origin)
    await send({"type": "websocket.close", "code": 1008,
                "reason": "unauthorized or disallowed origin"})


# ---- test helper ---------------------------------------------------------------

def reset_auth_for_tests(token: str = "dev") -> None:
    """Point the gate at a known token (or dev mode) for the test suite."""
    os.environ["UNIVERSE_BRIDGE_TOKEN"] = token
