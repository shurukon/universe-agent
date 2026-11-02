"""tests/test_engine_fix_infra.py — engine-fix round: observability,
context and provider-layer fixes.

Covers:
  * compaction reads LLMResponse.text (the resp.choices sniff was dead)
  * compact_tool_output re-truncates the ASSEMBLED head+tail
  * the event bus hard-caps a single event + prunes the seq table
  * timeline detail is secret-redacted before the JSONL write
  * file_select survives an unrankable BM25 corpus
  * codebase_map skips scanning oversized files but keeps them mapped;
    load_map rebuilds when the cheap staleness scan drifts
  * ProviderKey.to_dict never leaks the ciphertext blob
  * provider disabled predicate / derived status / client cache eviction
  * a mid-stream APIConnectionError is classified provider_down and never
    re-sent as a second full request
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# compaction (fix 1 + 11r)
# ---------------------------------------------------------------------------

def test_compaction_uses_response_text(fresh_home):
    """LLMResponse exposes the completion as .text — the old
    resp.choices/resp.content sniff always produced '' and every
    compaction fell back to the keyword heuristic."""
    from context.compaction import compact_with_model

    class _FakeLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(text="## 1. Primary Request\nUser wanted X")

    llm = _FakeLLM()
    messages = [{"role": "system", "content": "sys"}]
    for i in range(12):
        messages.append({"role": "user", "content": f"message {i}"})
        messages.append({"role": "assistant", "content": f"answer {i}"})
    ok = asyncio.run(compact_with_model(messages, llm, agent="test"))
    assert ok is True
    assert llm.calls == 1
    joined = json.dumps(messages, ensure_ascii=False)
    assert "Primary Request" in joined  # the real summary landed
    # atoms + recent kept, assistant prose collapsed
    assert not any(m.get("role") == "assistant" and m.get("content") == "answer 0"
                   for m in messages)


def test_compact_tool_output_retruncates_assembled_head_tail():
    """Five head + five tail LINES can still exceed max_chars when each
    line is huge — the advertised cap must hold on the assembled result."""
    from context.compaction import compact_tool_output
    big = "\n".join("x" * 1000 for _ in range(30))
    capped = compact_tool_output(big, max_chars=8000)
    assert len(capped) <= 8000 + 16  # cap + the truncation marker
    assert capped.endswith("...[truncated]")
    # the audit pin (20000-char flat input at 8000) still holds
    flat = "y" * 20_000
    assert len(compact_tool_output(flat, max_chars=8000)) < 9000


# ---------------------------------------------------------------------------
# event bus (fix 9)
# ---------------------------------------------------------------------------

def test_event_bus_hard_caps_single_event():
    from observability.events import reset_event_bus_for_tests
    bus = reset_event_bus_for_tests()
    sub = bus.subscribe()
    try:
        # ~76 KB payload slips through _cap (per-level budgets never
        # enforced on the assembled result) — _drop_to_fit must now catch it
        blob = {f"k{i:04d}": "x" * 30 for i in range(2000)}
        bus.emit("tool_running", turn_id="g-cap", blob=blob)
        event = asyncio.run(sub.next_event(1))
        assert event is not None
        assert event["type"] == "tool_running"
        assert event["turn_id"] == "g-cap"
        # the skeleton survives; the heavy field is honestly marked
        assert event["blob"] == "(omitted: size cap)"
        assert len(json.dumps(event, ensure_ascii=False)) <= 32_768
    finally:
        sub.stop()


def test_goal_seq_table_is_pruned():
    from observability.events import reset_event_bus_for_tests
    bus = reset_event_bus_for_tests()
    for i in range(1100):
        bus.goal_seq(f"g{i}")
    assert len(bus._goal_seq) <= 1000
    assert "g0" not in bus._goal_seq          # oldest inserted evicted
    assert bus._goal_seq["g1099"] == 1        # newest intact


# ---------------------------------------------------------------------------
# timeline (11t)
# ---------------------------------------------------------------------------

def test_timeline_redacts_detail_before_jsonl(fresh_home):
    from config import get_config
    from observability.timeline import record
    secret = "token=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    record("g-redact", agent="universe", event="agent_complete", detail=secret)
    path = get_config().timeline_dir / "g-redact.jsonl"
    line = path.read_text("utf-8")
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in line
    assert "agent_complete" in line  # the entry itself still landed


# ---------------------------------------------------------------------------
# file_select (11s)
# ---------------------------------------------------------------------------

def test_select_files_survives_unrankable_corpus():
    from context.file_select import select_files
    # CJK-only paths produce an empty token corpus → BM25Okapi used to
    # zero-divide inside rank_bm25 and crash the tool call
    files = [{"path": "日本語ファイル", "symbols": [], "mtime": 0.0,
              "interaction_count": 0},
             {"path": "real_file.py", "symbols": ["foo"], "mtime": 0.0,
              "interaction_count": 10}]
    out = select_files("search me", files, top_k=2)
    assert out  # nothing raised, both files still ranked
    assert {f["path"] for f in out} <= {f["path"] for f in files}


def test_select_files_normal_ranking_still_works():
    from context.file_select import select_files
    files = [{"path": "src/parser.py", "symbols": ["parse"], "mtime": 0.0,
              "interaction_count": 3},
             {"path": "docs/readme.md", "symbols": [], "mtime": 0.0,
              "interaction_count": 0}]
    out = select_files("parser", files, top_k=2)
    assert out[0]["path"] == "src/parser.py"


# ---------------------------------------------------------------------------
# codebase_map (11q)
# ---------------------------------------------------------------------------

def test_build_map_maps_but_skips_scanning_oversized_files(fresh_home, tmp_path):
    from context.codebase_map import build_map
    small = tmp_path / "small.py"
    small.write_text("def alpha_fn():\n    pass\n", encoding="utf-8")
    big = tmp_path / "vendored_big.py"
    big.write_text("# padding\n" * 120_000, encoding="utf-8")  # > 1 MB
    cm = build_map(tmp_path)
    paths = {f.path: f for f in cm.files}
    assert "small.py" in paths and "alpha_fn" in cm.symbol_index
    assert "vendored_big.py" in paths          # still mapped (tree/index)
    assert paths["vendored_big.py"].symbols == []  # but never scanned


def test_load_map_rebuilds_when_stale(fresh_home, tmp_path):
    from context.codebase_map import build_map, load_map
    small = tmp_path / "small.py"
    small.write_text("def alpha_fn():\n    pass\n", encoding="utf-8")
    build_map(tmp_path)
    # edit AFTER the map was built
    small.write_text("def alpha_fn():\n    pass\n\n\ndef beta_fn():\n    pass\n",
                     encoding="utf-8")
    cm = load_map(tmp_path)
    assert cm is not None
    assert "beta_fn" in cm.symbol_index  # served map was rebuilt, not stale


# ---------------------------------------------------------------------------
# providers (fix 5 + 6)
# ---------------------------------------------------------------------------

def test_provider_key_to_dict_never_exposes_secret(fresh_home):
    from providers.manager import get_provider_manager
    from security.secrets_fallback import encrypt_secret
    pm = get_provider_manager()
    pm.add_provider("SecTest", "https://example.test/v1")
    pm.add_key("SecTest", alias="k", encrypted_secret=encrypt_secret("super-secret"))
    kdict = pm.get_provider("SecTest").to_dict()["keys"][0]
    assert "encrypted_secret" not in kdict
    top = pm.to_dict()
    assert "encrypted_secret" not in top["providers"]["SecTest"]["keys"][0]
    # the real secret is still reachable attribute-side for decryption
    assert pm.get_provider("SecTest").keys[0].encrypted_secret != ""


def test_is_disabled_and_effective_status_single_source():
    from providers.manager import ProviderKey, _effective_status, _is_disabled
    k = ProviderKey(id=1, alias="t", status="high",
                    request_count_5min=[1.0] * 5, error_count_5min=[1.0] * 4)
    assert _is_disabled(k) is True
    assert _effective_status(k) == "low"
    k2 = ProviderKey(id=2, alias="t2", status="high",
                     request_count_5min=[1.0] * 5, error_count_5min=[1.0])
    assert _is_disabled(k2) is False
    assert _effective_status(k2) == "medium"
    k3 = ProviderKey(id=3, alias="t3", status="high",
                     request_count_5min=[1.0] * 5, error_count_5min=[])
    assert _effective_status(k3) == "high"
    # too few samples: the STORED status is the baseline, never "disabled"
    k4 = ProviderKey(id=4, alias="t4", status="high",
                     request_count_5min=[1.0, 2.0], error_count_5min=[1.0, 2.0])
    assert _is_disabled(k4) is False
    assert _effective_status(k4) == "high"


def test_record_key_error_success_write_derived_status(fresh_home):
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    pm.add_provider("StatusTest", "https://example.test/v1")
    pm.add_key("StatusTest", alias="s", encrypted_secret="x")
    key = pm.get_provider("StatusTest").keys[0]
    for _ in range(5):
        pm.record_key_error("StatusTest", key.id)
    assert key.status == "low"
    # a new key climbs back to high on a clean window
    pm.add_key("StatusTest", alias="s2", encrypted_secret="x")
    key2 = pm.get_provider("StatusTest").keys[1]
    for _ in range(5):
        pm.record_key_success("StatusTest", key2.id, 10)
    assert key2.status == "high"


def test_forget_provider_evicts_all_matching_clients():
    from providers.client import LLMClient
    c = LLMClient()
    c._clients[("http://x/v1", "a")] = object()
    c._clients[("http://x/v1", "b")] = object()
    c._clients[("http://other/v1", "c")] = object()
    c.forget_provider("http://x/v1")
    assert ("http://x/v1", "a") not in c._clients
    assert ("http://x/v1", "b") not in c._clients
    assert ("http://other/v1", "c") in c._clients


def test_remove_provider_forgets_cached_clients(fresh_home):
    from providers.client import get_llm_client
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    pm.add_provider("Evict", "http://evict.test/v1")
    pm.add_key("Evict", alias="e", encrypted_secret="x")
    c = get_llm_client()
    c._clients[("http://evict.test/v1", "e")] = object()
    assert pm.remove_provider("Evict") is True
    assert ("http://evict.test/v1", "e") not in c._clients


def test_midstream_connection_error_classified_provider_down(fresh_home, monkeypatch):
    """A connection that dies DURING streaming is a real provider failure:
    classified provider_down, recorded against the key, and the request is
    never silently re-sent as a second full non-streamed call."""
    import httpx
    from openai import APIConnectionError
    import providers.client as pc
    from providers.client import LLMError, get_llm_client
    from providers.manager import get_provider_manager
    from security.secrets_fallback import encrypt_secret

    pm = get_provider_manager()
    pm.add_provider("StreamDead", "http://stream-dead.test/v1")
    pm.add_key("StreamDead", alias="sd", encrypted_secret=encrypt_secret("sk-x"))
    pm.set_agent_model("orchestrator", "StreamDead", "m")

    calls = {"stream": 0}

    async def _dead_stream(client, kwargs, on_delta):
        calls["stream"] += 1
        # openai 2.x: APIConnectionError is keyword-only
        raise APIConnectionError(
            request=httpx.Request("POST", "http://stream-dead.test/v1/c"))

    def _forbidden(*_a, **_k):
        raise AssertionError("non-stream re-send must not happen after a "
                             "mid-stream connection failure")

    class _Completions:
        create = staticmethod(_forbidden)

    class _Chat:
        completions = _Completions()

    class _StubClient:
        def __init__(self, *a, **k):
            self.chat = _Chat()

        async def close(self):
            pass

    async def _nosleep(_):
        pass  # collapse the retry backoff

    monkeypatch.setattr(pc, "_stream_completion", _dead_stream)
    monkeypatch.setattr("openai.AsyncOpenAI", _StubClient)
    monkeypatch.setattr(pc.asyncio, "sleep", _nosleep)

    c = get_llm_client()
    with pytest.raises(LLMError) as ei:
        asyncio.run(c.complete(
            agent="orchestrator",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8, on_delta=lambda s: None))
    assert ei.value.kind == "provider_down"
    # 1 try + 4 retries, ALL via the stream path — the ladder rotates, it
    # does not double-request a dying link non-streamed
    assert calls["stream"] == 1 + pc._RETRY_MAX
    # the failure was recorded against the key (5-minute window)
    snap = pm.health_snapshot()
    assert snap and snap[0]["errors_5m"] >= 1
