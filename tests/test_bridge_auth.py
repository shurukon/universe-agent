"""tests/test_bridge_auth.py — regression tests for the bridge auth gate.

The bridge used to be fully unauthenticated: any local process or any
webpage (via localhost fetch or DNS rebinding) could approve consents,
spawn MCP subprocesses, and read the live event stream. The bearer-token
middleware (universe_desktop_bridge/auth.py) closes that. These tests pin
the strict-mode contract:

  * a configured token is REQUIRED (401 without, 401 with wrong token)
  * the token works via Authorization header, X-Universe-Token, and the
    ?token= query param (WebSocket path)
  * a disallowed Origin is rejected (403) even with a valid token
  * WebSocket upgrades are rejected the same way
  * dev mode (no env token) stays open for the loopback test suite
"""
import pytest
from fastapi.testclient import TestClient

import universe_desktop_bridge.auth as auth_mod


@pytest.fixture
def strict_app(monkeypatch, fresh_home):
    """The bridge with a real (non-dev) token configured."""
    token = "test-token-abc123"
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", token)
    import universe_desktop_bridge.app as app_mod
    yield app_mod.app, token
    # restore dev mode for the rest of the suite
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", auth_mod.DEV_TOKEN)


def test_no_token_is_rejected(strict_app):
    app, _ = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health")
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "unauthorized"


def test_wrong_token_is_rejected(strict_app):
    app, _ = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health",
                    headers={"Authorization": "Bearer wrong-token"})
        assert res.status_code == 401


def test_bearer_header_accepted(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health",
                    headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["ok"] is True


def test_x_universe_token_header_accepted(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health", headers={"X-Universe-Token": token})
        assert res.status_code == 200


def test_malformed_authorization_rejected(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        # not "Bearer <token>" scheme
        res = c.get("/api/health", headers={"Authorization": token})
        assert res.status_code == 401


def test_bad_origin_rejected_even_with_token(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health", headers={
            "Authorization": f"Bearer {token}",
            "Origin": "http://evil.example",
        })
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "bad_origin"


def test_app_origin_allowed(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health", headers={
            "Authorization": f"Bearer {token}",
            "Origin": "app://universe",
        })
        assert res.status_code == 200


def test_spoofed_origin_prefix_rejected(strict_app):
    """app://universe.evil must NOT pass the app://universe check."""
    app, token = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health", headers={
            "Authorization": f"Bearer {token}",
            "Origin": "app://universe.evil",
        })
        assert res.status_code == 403


def test_localhost_origin_allowed(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health", headers={
            "Authorization": f"Bearer {token}",
            "Origin": "http://localhost:5199",
        })
        assert res.status_code == 200


def test_websocket_rejected_without_token(strict_app):
    app, _ = strict_app
    with TestClient(app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/api/events"):
                pass  # must never get this far


def test_websocket_accepted_with_query_token(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        with c.websocket_connect(f"/api/events?token={token}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "bridge_hello"


def test_websocket_rejected_with_bad_origin(strict_app):
    app, token = strict_app
    with TestClient(app) as c:
        # TestClient sets no Origin by default; emulate one via headers
        with pytest.raises(Exception):
            with c.websocket_connect(
                    f"/api/events?token={token}",
                    headers={"Origin": "http://evil.example"}):
                pass


def test_localhost_prefix_origin_is_rejected(strict_app):
    """http://localhost.evil.com used to pass the prefix-match origin check
    (audit 2026-09); exact-origin matching rejects it."""
    app, token = strict_app
    with TestClient(app) as c:
        res = c.get("/api/health", headers={
            "Authorization": f"Bearer {token}",
            "Origin": "http://localhost.evil.com"})
        assert res.status_code == 403
        res2 = c.get("/api/health", headers={
            "Authorization": f"Bearer {token}",
            "Origin": "http://127.0.0.1:5199"})
        assert res2.status_code == 200


def test_no_token_generates_a_random_one_not_dev(fresh_home, monkeypatch):
    """No UNIVERSE_BRIDGE_TOKEN configured → the bridge must NOT silently run
    with the gate off (audit 2026-09): a random token is generated, printed,
    and enforced — a bare request without it is rejected."""
    monkeypatch.delenv("UNIVERSE_BRIDGE_TOKEN", raising=False)
    import universe_desktop_bridge.app as app_mod
    import universe_desktop_bridge.auth as auth_mod
    with TestClient(app_mod.app) as c:
        res = c.get("/api/health")
        assert res.status_code == 401
        # the generated token does work
        tok = auth_mod.get_bridge_token()
        res2 = c.get("/api/health", headers={"Authorization": f"Bearer {tok}"})
        assert res2.status_code == 200


def test_dev_mode_stays_open(fresh_home, monkeypatch):
    """EXPLICIT UNIVERSE_BRIDGE_TOKEN=dev → loopback stays open. This is the
    documented dev contract (opt-out, not a default)."""
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", "dev")
    import universe_desktop_bridge.app as app_mod
    with TestClient(app_mod.app) as c:
        res = c.get("/api/health")
        assert res.status_code == 200


def test_consent_endpoints_are_gated(strict_app):
    """The rubber-stamp attack: POST /api/consent without a token must be
    rejected — this was the CRITICAL finding."""
    app, _ = strict_app
    with TestClient(app) as c:
        res = c.post("/api/consent/consent-1-1", json={"approve": True})
        assert res.status_code == 401
        res = c.get("/api/consent/pending")
        assert res.status_code == 401


def test_mcp_spawn_is_gated(strict_app):
    """POST /api/mcp/servers (arbitrary subprocess spawn) must be gated."""
    app, _ = strict_app
    with TestClient(app) as c:
        res = c.post("/api/mcp/servers", json={
            "name": "evil", "transport": "stdio", "command": "calc.exe"})
        assert res.status_code == 401
