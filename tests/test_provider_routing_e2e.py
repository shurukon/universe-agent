"""tests/test_provider_routing_e2e.py — routing, chain-error, retry and
provider-settings contracts the v18 incident exposed.

The incident (user, production): every agent + the orchestrator were routed
to OpenCode Zen, yet the error named NVIDIA's dead catalog model — the
chain's last writer won, and the emergency tail silently substituted
strangers. Every guard added since is pinned here:

  * _chain_unlocked: with a primary configured the emergency tail never
    joins; it only saves the day when routing is fully gone.
  * complete(): transient kinds retry the SAME entry (provider_retry event,
    bounded by _RETRY_MAX) while permanent kinds advance; the surfaced
    error is the PRIMARY's with the fallbacks appended "tried also" —
    never last-writer-wins.
  * 410 "end of life" → kind=model_unavailable.
  * classify-halt carries the RAW provider error.
  * cancelled goals write status='cancelled' in the durable row.
  * repeat recall: exact text collides; different text never does.
  * GET catalog answers instantly while local probes run in the background.
  * list_provider_models never 422s: probe failure / forbidden listing
    falls back to catalog ids (presets) or an empty free-text list.
  * install without a model routes to the first LIVE model; listing-forbidden
    falls back to the catalog preset's first id — never the literal default.
  * PATCH /api/agents round-trips for every specialist AND the orchestrator;
    unknown agent 404, uninstalled provider 422.
  * POST /api/providers/{name}/model re-routes all 13.
  * PUT /api/settings: response_language round-trip, unknown persisted keys
    survive, masked voice-key echoes never clobber, project_root clears.

No production module imports this file; pytest collects it directly.
Data: fresh_home temp UNIVERSE_HOME (providers, provider_keys, agent_models,
goals, planets, asteroids) + config.json desktop dict — never ~/.universe.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

import universe_desktop_bridge.app as app_mod
import providers.client as client_mod
from observability.events import reset_event_bus_for_tests
from providers.client import LLMClient, LLMError, LLMResponse
from providers.manager import get_provider_manager
from security.secrets_fallback import encrypt_secret
from storage.local import get_storage


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _resp(text: str = "ok") -> LLMResponse:
    return LLMResponse(text=text, model="m", provider="p", input_tokens=1,
                       output_tokens=1, latency_ms=1, finish_reason="stop",
                       raw={})


def _preset(name: str):
    from providers.catalog import ProviderPreset
    return ProviderPreset(name=name, base_url="http://x/v1", category="paid",
                          models=["catalog-model"])


def _seed_provider(pm, name: str, keys: int = 1) -> None:
    pm.add_provider(name, "http://127.0.0.1:9/v1")
    for k in list(pm.get_provider(name).keys):
        pm.remove_key(name, k.id)
    for i in range(keys):
        pm.add_key(name, alias=f"k{i}",
                   encrypted_secret=encrypt_secret(f"sk-{name}-{i}"))


@pytest.fixture
def bridge(monkeypatch, fresh_home):
    """TestClient bridge with a fake network probe (no real HTTP ever)."""
    async def ok_probe(base_url: str, api_key: str, timeout: float = 8):
        return {"ok": True, "models": ["live-a", "live-b"]}
    monkeypatch.setattr(app_mod, "_probe_provider", ok_probe)
    # the bridge's auth gate now generates a random token when none is
    # configured (audit 2026-09); tests opt into the documented dev mode
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", "dev")
    reset_event_bus_for_tests()
    app_mod.reset_runtime_for_tests()
    with TestClient(app_mod.app) as c:
        yield c


# --------------------------------------------------------------------------
# chain construction policy
# --------------------------------------------------------------------------

class TestChainPolicy:
    def test_primary_suppresses_emergency_tail(self, fresh_home, monkeypatch):
        pm = get_provider_manager()
        _seed_provider(pm, "Primary")
        _seed_provider(pm, "Stranger")
        from providers import manager as mgr_mod
        monkeypatch.setattr(mgr_mod, "BY_NAME",
                            {"Primary": _preset("Primary"),
                             "Stranger": _preset("Stranger")})
        pm.set_agent_model("orchestrator", "Primary", "chosen-model")
        chain = pm.chain_for("orchestrator")
        assert chain == [("Primary", "chosen-model")], \
            f"emergency tail leaked despite a configured primary: {chain}"

    def test_no_routing_at_all_uses_keyed_tail(self, fresh_home, monkeypatch):
        pm = get_provider_manager()
        _seed_provider(pm, "Stranger")
        from providers import manager as mgr_mod
        monkeypatch.setattr(mgr_mod, "BY_NAME", {"Stranger": _preset("Stranger")})
        chain = pm.chain_for("orchestrator")
        assert chain == [("Stranger", "catalog-model")], chain

    def test_fallbacks_follow_primary(self, fresh_home):
        pm = get_provider_manager()
        _seed_provider(pm, "Primary")
        _seed_provider(pm, "Backup")
        pm.set_agent_model("orchestrator", "Primary", "m1")
        pm.set_fallback_chain("orchestrator", [("Backup", "m2")])
        chain = pm.chain_for("orchestrator")
        assert chain == [("Primary", "m1"), ("Backup", "m2")]


# --------------------------------------------------------------------------
# complete(): retry schedule, error reporting, kinds
# --------------------------------------------------------------------------

class TestCompleteChain:
    def _client(self, monkeypatch) -> LLMClient:
        monkeypatch.setattr(client_mod, "_RETRY_BASE_S", 0.001)
        return LLMClient()

    def test_transient_retries_same_entry_then_succeeds(self, fresh_home,
                                                        monkeypatch):
        calls: list[str] = []
        c = self._client(monkeypatch)
        pm = get_provider_manager()
        _seed_provider(pm, "Groq")
        pm.set_agent_model("orchestrator", "Groq", "m")

        async def flaky(provider_name, model, *a, **k):
            calls.append(provider_name)
            if len(calls) < 3:
                raise LLMError("429 slow down", kind="rate_limit",
                               provider=provider_name, model=model)
            return _resp("recovered")
        monkeypatch.setattr(c, "_call_once", flaky)
        out = asyncio.run(c.complete(agent="orchestrator", messages=[]))
        assert out.text == "recovered"
        assert len(calls) == 3, "transient error gave up before the retry budget"

    def test_provider_retry_event_emitted(self, fresh_home, monkeypatch):
        captured: list[tuple[str, dict]] = []
        import observability.events as oe
        monkeypatch.setattr(oe, "emit",
                            lambda et, **kw: captured.append((et, kw)))
        c = self._client(monkeypatch)
        pm = get_provider_manager()
        _seed_provider(pm, "Groq")
        pm.set_agent_model("orchestrator", "Groq", "m")

        async def always_timeout(provider_name, model, *a, **k):
            raise LLMError("connection reset", kind="timeout",
                           provider=provider_name, model=model)
        monkeypatch.setattr(c, "_call_once", always_timeout)
        with pytest.raises(LLMError) as ei:
            asyncio.run(c.complete(agent="orchestrator", messages=[]))
        retries = [e for e in captured if e[0] == "provider_retry"]
        assert len(retries) == client_mod._RETRY_MAX, \
            f"expected {client_mod._RETRY_MAX} provider_retry events, got {len(retries)}"
        assert ei.value.kind == "timeout"

    def test_auth_does_not_retry(self, fresh_home, monkeypatch):
        c = self._client(monkeypatch)
        pm = get_provider_manager()
        _seed_provider(pm, "Groq", keys=3)
        pm.set_agent_model("orchestrator", "Groq", "m")
        calls: list[tuple[str, str]] = []

        async def deny(provider_name, model, *a, **k):
            calls.append((provider_name, model))
            raise LLMError("401 bad key", kind="auth",
                           provider=provider_name, model=model)
        monkeypatch.setattr(c, "_call_once", deny)
        with pytest.raises(LLMError) as ei:
            asyncio.run(c.complete(agent="orchestrator", messages=[]))
        assert ei.value.kind == "auth"
        assert len(calls) == 1, f"auth wrongly retried: {len(calls)} calls"

    def test_primary_error_surfaces_not_last_writers(self, fresh_home,
                                                     monkeypatch):
        c = self._client(monkeypatch)
        pm = get_provider_manager()
        _seed_provider(pm, "Primary")
        _seed_provider(pm, "Stranger")
        from providers import manager as mgr_mod
        monkeypatch.setattr(mgr_mod, "BY_NAME",
                            {"Primary": _preset("Primary"),
                             "Stranger": _preset("Stranger")})
        pm.set_agent_model("orchestrator", "Primary", "my-model")
        pm.set_fallback_chain("orchestrator", [("Stranger", "other-model")])

        async def failing(provider_name, model, *a, **k):
            raise LLMError(f"{provider_name} exploded", kind="auth",
                           provider=provider_name, model=model)
        monkeypatch.setattr(c, "_call_once", failing)
        with pytest.raises(LLMError) as ei:
            asyncio.run(c.complete(agent="orchestrator", messages=[]))
        msg = str(ei.value)
        assert msg.startswith("Primary exploded"), \
            f"the user's provider must be the reported failure: {msg}"
        assert "Stranger" in msg and "fallbacks also failed" in msg, msg

    def test_classify_halt_reason_is_raw_error(self, fresh_home):
        """run_turn: an exhausted provider ladder halts with the RAW error —
        no 'Stopped before starting: could not be reached' re-labeling."""
        from core.agent.orchestrator import Orchestrator
        raw = ("Error code: 410 - {'status': 410, 'detail': \"The model "
               "'x' has reached its end of life\"}")

        class BadLLM:
            async def complete(self, **kw):
                raise LLMError(raw, kind="model_unavailable")

        orch = Orchestrator(llm_client=BadLLM())
        summary = asyncio.run(orch.run_turn("hi there", turn_id="goal-rawerr"))
        assert summary["success"] is False and summary["halted"] is True
        assert summary["error"].startswith("Error code: 410"), summary["error"]
        row = get_storage().query_one(
            "SELECT status, final_summary FROM goals WHERE id=?;",
            ("goal-rawerr",))
        assert row["status"] == "failed"
        assert "Stopped before starting" not in row["final_summary"]

    def test_410_maps_to_model_unavailable(self, fresh_home, monkeypatch):
        """The SDK-classification seam: a 410 'end of life' APIStatusError
        must surface as kind=model_unavailable from _try_key."""
        import httpx
        import openai
        c = LLMClient()
        pm = get_provider_manager()
        _seed_provider(pm, "Groq")
        provider = pm.get_provider("Groq")
        key = provider.keys[0]

        req = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
        resp = httpx.Response(410, request=req)

        async def boom(**kwargs):
            raise openai.APIStatusError(
                "The model has reached its end of life", response=resp, body=None)

        class _Completions:
            create = staticmethod(boom)

        class _Chat:
            completions = _Completions()

        class _ClientStub:
            chat = _Chat()

        monkeypatch.setattr(client_mod.LLMClient, "_decrypt_key",
                            lambda self, blob: "secret")
        monkeypatch.setattr(c, "_client_for", lambda *a: _ClientStub())
        with pytest.raises(LLMError) as ei:
            asyncio.run(c._try_key(provider, key, "m", "code", [], 8, 0.0,
                                   None, None, None))
        assert ei.value.kind == "model_unavailable", ei.value.kind

    def test_gateway_400_provider_error_is_transient(self, fresh_home, monkeypatch):
        """Kilo/OpenRouter answer a transient 400 'Provider returned error'
        when the upstream model is busy behind them (measured live: a design
        step died on it). _try_key must classify it provider_down (retry +
        chain rotation), not malformed (immediate give-up)."""
        import httpx
        import openai
        c = LLMClient()
        pm = get_provider_manager()
        _seed_provider(pm, "Kilo Gateway")
        provider = pm.get_provider("Kilo Gateway")
        key = provider.keys[0]

        req = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
        resp = httpx.Response(400, request=req)

        async def boom(**kwargs):
            raise openai.BadRequestError(
                "Error code: 400 - {'error': {'message': 'Provider returned "
                "error', 'code': 400}}", response=resp, body=None)

        class _Completions:
            create = staticmethod(boom)

        class _Chat:
            completions = _Completions()

        class _ClientStub:
            chat = _Chat()

        monkeypatch.setattr(client_mod.LLMClient, "_decrypt_key",
                            lambda self, blob: "secret")
        monkeypatch.setattr(c, "_client_for", lambda *a: _ClientStub())
        with pytest.raises(LLMError) as ei:
            asyncio.run(c._try_key(provider, key, "m", "code", [], 8, 0.0,
                                   None, None, None))
        assert ei.value.kind == "provider_down", ei.value.kind
        # and it IS in the transient set → complete() will retry/rotate
        assert "provider_down" in client_mod.TRANSIENT_KINDS

    def test_true_malformed_stays_malformed(self, fresh_home, monkeypatch):
        """A real request-shaped 400 (invalid schema/model) must NOT be
        laundered into a retryable kind."""
        import httpx
        import openai
        c = LLMClient()
        pm = get_provider_manager()
        _seed_provider(pm, "Kilo Gateway")
        provider = pm.get_provider("Kilo Gateway")
        key = provider.keys[0]

        req = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
        resp = httpx.Response(400, request=req)

        async def boom(**kwargs):
            raise openai.BadRequestError(
                "Error code: 400 - invalid 'response_format' parameter",
                response=resp, body=None)

        class _Completions:
            create = staticmethod(boom)

        class _Chat:
            completions = _Completions()

        class _ClientStub:
            chat = _Chat()

        monkeypatch.setattr(client_mod.LLMClient, "_decrypt_key",
                            lambda self, blob: "secret")
        monkeypatch.setattr(c, "_client_for", lambda *a: _ClientStub())
        with pytest.raises(LLMError) as ei:
            asyncio.run(c._try_key(provider, key, "m", "code", [], 8, 0.0,
                                   None, None, None))
        assert ei.value.kind == "malformed", ei.value.kind


# --------------------------------------------------------------------------
# verdicts & cancel unification
# --------------------------------------------------------------------------

class TestVerdictsAndCancel:
    def test_final_step_decides_goal_success(self, fresh_home):
        """A stray mid-goal failure must not launder an accepted, finished
        goal into failure — the FINAL handoff carries the verdict."""
        from core.memory import get_memory
        mem = get_memory()
        planet = mem.l1.create_planet(
            turn_id="g-verdict", goal_text="build it",
            classification={"category": "code_generation", "domain": "python",
                            "intent": "fix", "complexity": "low"},
            language="en", owner_session_id="s")
        ast = mem.complete_task(planet=planet, handoffs=[
            {"agent": "research", "task_success": False,
             "what_was_done": "failed: web blocked", "tools_used": ["WebSearch"]},
            {"agent": "code", "task_success": True,
             "what_was_done": "wrote and verified fix", "tools_used": ["Write"]},
        ])
        assert ast.task_success is True

    def test_all_failed_goal_is_not_success(self, fresh_home):
        from core.memory import get_memory
        mem = get_memory()
        planet = mem.l1.create_planet(
            turn_id="g-bad", goal_text="do it",
            classification={"category": "general", "domain": "general",
                            "intent": "write", "complexity": "low"},
            language="en", owner_session_id="s")
        ast = mem.complete_task(planet=planet, handoffs=[
            {"agent": "write", "task_success": False,
             "what_was_done": "failed", "tools_used": []},
        ])
        assert ast.task_success is False

    def test_empty_handoffs_is_not_success(self, fresh_home):
        from core.memory import get_memory
        mem = get_memory()
        planet = mem.l1.create_planet(
            turn_id="g-empty", goal_text="do it",
            classification={"category": "general", "domain": "general",
                            "intent": "write", "complexity": "low"},
            language="en", owner_session_id="s")
        ast = mem.complete_task(planet=planet, handoffs=[])
        assert ast.task_success is False

    def test_cancelled_goal_row_reads_cancelled(self, fresh_home):
        """DB row and bridge must agree: cancel writes 'cancelled', not
        'failed' (the old disagreement flipped the row when the live entry
        TTL-evicted)."""
        from core.agent.orchestrator import Orchestrator
        get_storage().execute(
            "INSERT INTO goals(id,text,status,created_at) "
            "VALUES('goal-canc','x','running',1);")
        orch = Orchestrator()
        orch._mark_goal_cancelled("goal-canc")
        row = get_storage().query_one(
            "SELECT status, final_summary FROM goals WHERE id='goal-canc';")
        assert row["status"] == "cancelled"
        assert json.loads(row["final_summary"])["cancelled"] is True

    def test_provider_error_kind_survives_prose_stringification(self):
        """BaseAgent.run records LLMError.kind on the handoff package so the
        orchestrator's provider-guard reads STRUCTURE, not prose markers."""
        import asyncio as aio
        from core.agent.base_agent import BaseAgent, HandoffPackage

        async def _run():
            ag = BaseAgent()
            ag.name = "tester"

            async def boom(**kw):
                raise LLMError("Error code: 410", kind="model_unavailable")
            ag._execute = boom  # type: ignore[assignment]
            pkg = await ag.run(memory=None, llm_client=None, goal_text="t",
                                classification={}, handoff=None, context={},
                                capability_gate=None, turn_id="g")
            assert isinstance(pkg, HandoffPackage)
            assert pkg.context_for_memory.get(
                "provider_error_kind") == "model_unavailable"
            assert pkg.task_success is False
        aio.run(_run())


# --------------------------------------------------------------------------
# repeat recall hijack
# --------------------------------------------------------------------------

class TestRepeatHijackFixed:
    def test_same_text_recalls_different_text_does_not(self, fresh_home):
        from core.memory import get_memory
        from core.memory.fingerprint import (compute_repeat_hash,
                                             find_repeat_asteroid)
        mem = get_memory()
        cls = {"category": "general", "domain": "general", "intent": "write"}
        planet = mem.l1.create_planet(
            turn_id="g-r1", goal_text="write a poem about the mountains",
            classification=cls, language="en", owner_session_id="s")
        mem.complete_task(planet=planet, handoffs=[
            {"agent": "write", "task_success": True,
             "what_was_done": "a mountain poem", "tools_used": []}],
            classification=cls)
        same = compute_repeat_hash(cls, [], "write a poem about the MOUNTAINS!")
        assert find_repeat_asteroid(same, recency_seconds=300) is not None, \
            "exact-repeat recall broke"
        other = compute_repeat_hash(cls, [], "write a poem about the sea")
        assert find_repeat_asteroid(other, recency_seconds=300) is None, \
            "different asks still collide — hijack NOT fixed"


# --------------------------------------------------------------------------
# bridge endpoints: instant catalog
# --------------------------------------------------------------------------

class TestCatalogSpeed:
    def test_catalog_returns_while_probes_run(self, bridge, monkeypatch):
        async def slow_probe(base_url, api_key, timeout=8):
            await asyncio.sleep(3)
            return {"ok": False, "error": "slow"}
        monkeypatch.setattr(app_mod, "_probe_provider", slow_probe)
        app_mod._catalog_probe_cache["value"] = None
        app_mod._catalog_probe_cache["at"] = 0.0
        started = time.monotonic()
        r = bridge.get("/api/providers/catalog")
        elapsed = time.monotonic() - started
        assert r.status_code == 200
        assert elapsed < 1.0, f"catalog blocked on probes for {elapsed:.2f}s"
        body = r.json()
        assert len(body["catalog"]) >= 30  # the expanded library


# --------------------------------------------------------------------------
# models endpoints & install routing
# --------------------------------------------------------------------------

class TestModelsEndpoints:
    def _install(self, bridge, name, key="sk-good", url="", extra=None):
        payload = {"name": name, "base_url": url, "api_key": key,
                   "set_default": True}
        if extra:
            payload.update(extra)
        return bridge.post("/api/providers", json=payload)

    def test_listing_forbidden_shows_free_text_no_catalog(self, bridge, monkeypatch):
        async def forbidden(base_url, api_key, timeout=8):
            return {"ok": True, "models": [], "listing_forbidden": True}
        monkeypatch.setattr(app_mod, "_probe_provider", forbidden)
        r = self._install(bridge, "OpenCode Zen")
        assert r.status_code == 200, r.text
        # install seeds the catalog preset's first id — NEVER the literal default
        assert r.json()["model"] and r.json()["model"] != "default"
        from providers.catalog import get_preset
        assert r.json()["model"] == get_preset("OpenCode Zen").models[0]
        # models endpoint (contract since the no-fallback decision): the LIVE
        # list only — listing forbidden → empty list + free_text, never 422
        app_mod._invalidate_models_cache("OpenCode Zen")
        m = bridge.get("/api/providers/OpenCode Zen/models")
        assert m.status_code == 200, m.text
        body = m.json()
        assert body["ok"] is True and body["models"] == []
        assert body.get("free_text") is True

    def test_probe_failure_shows_free_text_never_422(self, bridge, monkeypatch):
        # install succeeded earlier under the fixture's ok_probe:
        self._install(bridge, "Groq")
        async def deny(base_url, api_key, timeout=8):
            return {"ok": False, "error": "HTTP 503 from provider"}
        monkeypatch.setattr(app_mod, "_probe_provider", deny)
        app_mod._invalidate_models_cache("Groq")
        m = bridge.get("/api/providers/Groq/models")
        assert m.status_code == 200, m.text  # NOT 422 — dropdowns must fill
        body = m.json()
        # NO catalog fallback: only what the provider returns; free-text covers the rest
        assert body["ok"] is True and body["models"] == []
        assert body.get("free_text") is True
        assert "503" in str(body.get("probe_error", ""))

    def test_custom_with_given_model_installs(self, bridge, monkeypatch):
        async def forbidden(base_url, api_key, timeout=8):
            return {"ok": True, "models": [], "listing_forbidden": True}
        monkeypatch.setattr(app_mod, "_probe_provider", forbidden)
        r = bridge.post("/api/providers", json={
            "name": "MyBox2", "base_url": "http://127.0.0.1:8000/v1",
            "api_key": "sk-x", "model": "the-only-model"})
        assert r.status_code == 200
        assert r.json()["model"] == "the-only-model"
        app_mod._invalidate_models_cache("MyBox2")
        m = bridge.get("/api/providers/MyBox2/models").json()
        assert m["models"] == [] and m.get("free_text") is True


# --------------------------------------------------------------------------
# agents endpoints
# --------------------------------------------------------------------------

class TestAgentRoutingEndpoints:
    def _seed(self, bridge):
        bridge.post("/api/providers", json={
            "name": "Groq", "base_url": "", "api_key": "sk-g",
            "set_default": False})

    def test_roster_is_subagents_only_read_only(self, bridge):
        """2026-09 model-chip redesign: GET /api/agents is the SUBAGENT
        roster — the general agent is the main loop itself, not a peer row,
        and (provider, model) is gone from every row (subagents inherit the
        one model set from the chat composer; the truth lives in the
        provider manager, not this payload)."""
        self._seed(bridge)
        rows = bridge.get("/api/agents").json()["agents"]
        names = [a["name"] for a in rows]
        assert "general" not in names
        assert len(names) == 5  # research/code/review/security/design
        for a in rows:
            assert {"name", "specialty", "tools", "tier"} <= set(a)
            assert "provider" not in a and "model" not in a

    def test_patch_roundtrip_every_agent_including_orchestrator(self, bridge):
        """The per-agent PATCH endpoint still routes every agent (the
        orchestrator included), but the round-trip is verified against the
        provider manager — the roster payload no longer mirrors it."""
        self._seed(bridge)
        from core.core_agents import ALL_AGENTS
        names = list(ALL_AGENTS)
        assert "universe" in names and len(names) == 6
        for name in names:
            r = bridge.patch(f"/api/agents/{name}/model",
                             json={"provider": "Groq", "model": "m1"})
            assert r.status_code == 200, (name, r.text)
        pm = get_provider_manager()
        for name in names:
            assert pm.get_agent_model(name) == ("Groq", "m1"), name
        # S1+v22: the orchestrator routing label retired — universe IS it
        assert pm.get_agent_model("universe") == ("Groq", "m1")

    def test_patch_rejects_unknown_and_uninstalled(self, bridge):
        r = bridge.patch("/api/agents/nope/model",
                         json={"provider": "Groq", "model": "m"})
        assert r.status_code == 404
        self._seed(bridge)
        r = bridge.patch("/api/agents/code/model",
                         json={"provider": "NotInstalled", "model": "m"})
        assert r.status_code == 422
        r = bridge.patch("/api/agents/code/model",
                         json={"provider": "Groq", "model": "   "})
        assert r.status_code == 422

    def test_apply_to_all_re_routes_all_thirteen(self, bridge):
        """One model for everything: the composer chip's POST re-routes the
        general agent AND every subagent in a single call; the roster no
        longer mirrors (provider, model), so routing is verified against
        the provider manager."""
        self._seed(bridge)
        bridge.post("/api/providers", json={
            "name": "Together.ai", "base_url": "", "api_key": "sk-t",
            "set_default": False})
        r = bridge.post("/api/providers/Groq/model", json={"model": "all-m"})
        assert r.status_code == 200
        routed = r.json()["agents_routed"]
        assert "universe" in routed and len(routed) == 6
        pm = get_provider_manager()
        for name in routed:
            assert pm.get_agent_model(name) == ("Groq", "all-m"), name


# --------------------------------------------------------------------------
# context compaction protocol safety
# --------------------------------------------------------------------------

class TestCompactionSafety:
    def test_none_content_tool_call_turn_does_not_crash(self):
        """Production crash: an assistant tool-call turn has content=None and
        the key EXISTS — m.get('content','') still returns None, so the old
        compaction died on None.lower() mid-goal ('failed: 'NoneType' object
        has no attribute 'lower'' in a live research step)."""
        from context.compaction import compact_messages
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "goal " + "x" * 100}]
        for i in range(10):
            msgs.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"c{i}", "function":
                                         {"name": "WebSearch",
                                          "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": "r" * 20000})
        out = compact_messages(msgs, keep_recent=6)
        assert out  # no raise

    def test_tool_call_atoms_survive_compaction_pairing(self):
        """Every surviving tool_call must keep its matching tool response
        (OpenAI endpoints 400 on a broken pairing)."""
        from context.compaction import compact_messages
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "goal"}]
        for i in range(12):
            msgs.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"c{i}", "function":
                                         {"name": "t", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": "x" * 9000})
            msgs.append({"role": "user", "content": "filler prose"})
        out = compact_messages(msgs, keep_recent=4)
        assistant_ids = {tc["id"] for m in out if m.get("tool_calls")
                         for tc in m["tool_calls"]}
        tool_ids = {m.get("tool_call_id") for m in out
                    if m.get("role") == "tool"}
        assert assistant_ids == tool_ids, \
            f"pairing broke: {assistant_ids ^ tool_ids}"
        # each surviving call id appears before its tool response in order
        for i, m in enumerate(out):
            for tc in (m.get("tool_calls") or []):
                following = [x.get("tool_call_id") for x in out[i + 1:i + 8]
                             if x.get("role") == "tool"]
                assert tc["id"] in following, \
                    f"tool_call {tc['id']} lost its response (pairing)"


# --------------------------------------------------------------------------
# tool-loop circuit breaker
# --------------------------------------------------------------------------

class TestToolLoopCircuitBreaker:
    def test_consecutive_tool_failures_end_the_loop(self, fresh_home,
                                                    monkeypatch):
        """Live finding: a free model re-issued failing tool calls
        (whitelist-blocked file.write + DNS errors) for 26-36 rounds inside
        ONE agent while burning the whole goal deadline. The loop must now
        break after the consecutive-failure cap with one final no-tools
        handoff turn — and emit tool_loop_throttled."""
        from core.agent.base_agent import BaseAgent

        class FakeResp:
            def __init__(self, with_tools: bool, rounds: int):
                self.rounds = rounds
                if with_tools:
                    self.raw = {"tool_calls": [{
                        "id": f"c{rounds}", "type": "function",
                        "function": {"name": "Write",
                                     "arguments": json.dumps(
                                         {"path": f"p{rounds}.txt",
                                          "content": "x"})}}]}
                    self.text = ""
                else:
                    self.raw = {}
                    self.text = ('{"what_was_done":"stopped honestly",'
                                 '"task_success":false}')
                self.latency_ms = 1

        async def fake_registry_call(name, agent, turn_id, args):
            return {"ok": False,
                    "error": f"tool {name!r} not in agent whitelist",
                    "blocked_by": "whitelist"}

        ag = BaseAgent()
        ag.name = "general"
        ag.tool_whitelist_names = ["Read"]
        # no real registry/gate needed: patch the registry lookup + gate
        from connectors.builtin import get_registry
        reg = get_registry()
        monkeypatch.setattr(reg, "call",
                            lambda name, agent, turn_id, args: _fail_async())
        captured: list[tuple[str, dict]] = []
        import observability.events as oe
        monkeypatch.setattr(oe, "emit",
                            lambda et, **kw: captured.append((et, kw)))
        calls = {"n": 0}

        class LLM:
            async def complete(self, **kw):
                calls["n"] += 1
                # the breaker's final forcing turn disables tools
                return FakeResp(with_tools=kw.get("tools") is not None,
                                rounds=calls["n"])

        from core.agent.turn_engine import run_turn
        resp, events, out = asyncio.run(run_turn(
            llm_client=LLM(),
            messages=[{"role": "system", "content": "s"},
                      {"role": "user", "content": "go"}],
            agent="universe", turn_id="g-break", capability_gate=None,
            response_format={"type": "json_object"},
        ))
        assert resp.text.startswith('{"what_was_done"'), resp.text
        # capped far below any budget (5 failures then the forcing turn)
        tool_rounds = [c for c in captured if c[0] == "llm_round"]
        assert len(tool_rounds) <= 8, f"breaker late: {len(tool_rounds)}"
        assert any(c[0] == "tool_loop_throttled" for c in captured), captured
        assert out.stop_reason == "breaker"


def _fail_async():
    fut = asyncio.get_event_loop().create_future()
    fut.set_result({"ok": False, "error": "blocked", "blocked_by": "whitelist"})
    return fut


# --------------------------------------------------------------------------
# settings contracts
# --------------------------------------------------------------------------

class TestSettingsContracts:
    def test_response_language_roundtrip(self, bridge):
        r = bridge.put("/api/settings", json={"response_language": "ar"})
        assert r.status_code == 200
        assert "response_language" in r.json()["changed"]
        got = bridge.get("/api/settings").json()["settings"]
        assert got["response_language"] == "ar"

    def test_unknown_desktop_keys_survive_a_write(self, bridge):
        from config import get_config
        cfg = get_config()
        desktop = dict(cfg.get("desktop", {}))
        desktop["some_future_key"] = {"a": 1}
        cfg.set("desktop", desktop)
        bridge.put("/api/settings", json={"assistant_name": "UniverseX"})
        after = get_config().get("desktop", {})
        assert after.get("some_future_key") == {"a": 1}, \
            "PUT /api/settings dropped an unknown persisted key"
        assert after["assistant_name"] == "UniverseX"

    def test_masked_voice_key_echo_never_clobbers(self, bridge):
        from config import get_config
        from security.secrets_fallback import decrypt_secret
        r = bridge.put("/api/settings", json={"stt_api_key": "sk-supersecret-key"})
        assert r.status_code == 200
        stored = get_config().get("desktop", {})["stt_api_key"]
        # voice keys are AES blobs at rest now (audit 2026-09); the plaintext
        # never sits in config.json again
        assert stored != "sk-supersecret-key"
        assert decrypt_secret(stored) == "sk-supersecret-key"
        # GET returns the mask; echoing the mask back must be a no-op
        mask = bridge.get("/api/settings").json()["settings"]["stt_api_key"]
        assert mask != "sk-supersecret-key"
        bridge.put("/api/settings", json={"stt_api_key": mask})
        assert decrypt_secret(
            get_config().get("desktop", {})["stt_api_key"]) == "sk-supersecret-key"

    def test_project_root_can_be_cleared(self, bridge):
        from config import get_config
        bridge.put("/api/settings", json={"project_root": "D:/work/a"})
        assert get_config().get("project_root") == "D:/work/a"
        r = bridge.put("/api/settings", json={"project_root": ""})
        assert r.status_code == 200
        assert get_config().get("project_root") in ("", None)

    def test_empty_wake_word_is_ignored(self, bridge):
        # effective wake word comes from the DESKTOP_DEFAULTS projection
        before = bridge.get("/api/settings").json()["settings"]["wake_word"]
        r = bridge.put("/api/settings", json={"wake_word": "   "})
        assert r.status_code == 200
        assert "wake_word" not in r.json()["changed"]
        assert r.json()["settings"]["wake_word"] == before
