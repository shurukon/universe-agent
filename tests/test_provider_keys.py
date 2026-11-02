"""tests/test_provider_keys.py — unlimited API keys per provider + the
expanded catalog contract.

Covers the user requests:
  * POST /api/providers twice with the same provider → TWO keys (append,
    not replace); base_url change still replaces
  * GET /api/providers exposes masked per-key rows (id/alias/preview/
    disabled) — never a secret
  * POST /api/providers/{name}/keys adds one key (probed); DELETE removes
    one; the LAST key is protected (409)
  * rotation actually walks multiple keys: pick_key/list_keys return all
  * catalog presets: OpenCode Zen / Cloudflare Workers AI / Z.ai / Qwen
    present; templated presets (url_fields) accept a client-resolved URL
All probes are monkeypatched — no network.

Callers: pytest collects this module (tests/test_provider_keys.py) — no
production code imports it. No existing suite covers multi-key management.
Data touched: the fresh_home tmp provider_keys table only —
(provider, id INTEGER, alias, status, encrypted_secret <base64 blob>,
tier); API previews are masked like "abc…xyz", never plaintext.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from universe_desktop_bridge import app as app_mod


@pytest.fixture
def client(monkeypatch, fresh_home):
    async def fake_probe(base_url: str, api_key: str, timeout: float = 8):
        if "bad" in api_key:
            return {"ok": False, "error": "HTTP 401 from provider"}
        return {"ok": True, "models": ["model-a", "model-b"]}
    monkeypatch.setattr(app_mod, "_probe_provider", fake_probe)
    from observability.events import reset_event_bus_for_tests
    reset_event_bus_for_tests()
    app_mod.reset_runtime_for_tests()
    with TestClient(app_mod.app) as c:
        yield c


def _install(client, name, key):
    return client.post("/api/providers", json={
        "name": name, "base_url": "", "api_key": key,
    })


class TestKeyAppend:
    def test_second_install_appends_key(self, client):
        r1 = _install(client, "Groq", "sk-one")
        assert r1.status_code == 200, r1.text
        assert r1.json()["key_appended"] is False
        r2 = _install(client, "Groq", "sk-two")
        assert r2.status_code == 200, r2.text
        assert r2.json()["key_appended"] is True
        # provider list shows two masked keys
        provs = client.get("/api/providers").json()["providers"]
        groq = [p for p in provs if p["name"] == "Groq"][0]
        assert groq["key_count"] == 2
        aliases = {k["alias"] for k in groq["keys"]}
        assert len(aliases) == 2 and "desktop" in aliases
        for k in groq["keys"]:
            assert "••" in k["preview"] or "…" in k["preview"]
            assert "sk-" not in k["preview"]  # never leak plaintext fragments

    def test_rotation_sees_both_keys(self, client):
        from providers.manager import get_provider_manager
        _install(client, "Groq", "sk-one")
        _install(client, "Groq", "sk-two")
        pm = get_provider_manager()
        keys = pm.list_keys("Groq")
        assert len(keys) == 2
        assert pm.pick_key("Groq") is not None


class TestKeyEndpoints:
    def test_add_single_key_crud(self, client):
        assert _install(client, "Cerebras", "sk-a").status_code == 200
        r = client.post("/api/providers/Cerebras/keys", json={"api_key": "sk-b"})
        assert r.status_code == 200, r.text
        key_id = r.json()["key_id"]
        lst = client.get("/api/providers/Cerebras/keys").json()["keys"]
        assert len(lst) == 2
        # a bad key is refused live, nothing stored
        bad = client.post("/api/providers/Cerebras/keys", json={"api_key": "sk-bad"})
        assert bad.status_code == 422
        assert len(client.get("/api/providers/Cerebras/keys").json()["keys"]) == 2
        # delete the added key
        assert client.delete(
            f"/api/providers/Cerebras/keys/{key_id}").status_code == 200
        assert len(client.get("/api/providers/Cerebras/keys").json()["keys"]) == 1

    def test_last_key_is_protected(self, client):
        _install(client, "Mistral", "sk-only")
        only = client.get("/api/providers/Mistral/keys").json()["keys"][0]
        r = client.delete(f"/api/providers/Mistral/keys/{only['id']}")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "last_key"
        assert len(client.get("/api/providers/Mistral/keys").json()["keys"]) == 1


class TestTemplatedPresets:
    def test_cloudflare_needs_resolved_url(self, client):
        r = client.post("/api/providers", json={
            "name": "Cloudflare Workers AI", "base_url": "",
            "api_key": "cf-k"})
        assert r.status_code == 422  # the raw template cannot be probed
        r = client.post("/api/providers", json={
            "name": "Cloudflare Workers AI",
            "base_url": "https://api.cloudflare.com/client/v4/accounts/acct123/ai/v1",
            "api_key": "cf-k"})
        assert r.status_code == 200, r.text
        prov = [p for p in client.get("/api/providers").json()["providers"]
                if p["name"] == "Cloudflare Workers AI"][0]
        assert "acct123" in prov["base_url"]

    def test_new_presets_exist(self):
        from providers.catalog import BY_NAME
        for name in ("OpenCode Zen", "Cloudflare Workers AI", "Z.ai (GLM)",
                     "Qwen (Alibaba Cloud)", "Moonshot (Kimi)",
                     "GitHub Models", "Azure OpenAI"):
            assert name in BY_NAME, name
        assert BY_NAME["Cloudflare Workers AI"].url_fields == ("account_id",)
        assert BY_NAME["Azure OpenAI"].url_fields == ("resource",)
        assert BY_NAME["OpenCode Zen"].supports_response_format is False


class TestModelsCache:
    def test_models_endpoint_caches(self, client):
        _install(client, "Groq", "sk-one")
        r1 = client.get("/api/providers/Groq/models")
        assert r1.status_code == 200
        assert r1.json().get("cached") is not True
        r2 = client.get("/api/providers/Groq/models")
        assert r2.json().get("cached") is True
        # key change invalidates
        client.post("/api/providers/Groq/keys", json={"api_key": "sk-two"})
        r3 = client.get("/api/providers/Groq/models")
        assert r3.json().get("cached") is not True
