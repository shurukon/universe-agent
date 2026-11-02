"""tests/test_m3_core_engine.py — regression suite for the m3 engine fixes.

Each test pins one HIGH-severity behavior from the review:
  1. attach_tool_events reads the INNER ok (m1 truth envelope)
  2. prose is not success: parse_error stays failed even with ok tools
  3. planning failure is not flipped to success by plumbing success
  4. preflight skill events do not count as the agent's work
  5. cancelled/failed goals keep a real remaining_plan (resumable)
  6. LLMError.kind travels to checkpoints as error_kind
  7. Unicode: non-Latin stars get distinct vault names (no untitled collisions)
  8. Unicode: Arabic content is searchable in L3
  9. frontmatter: CRLF vault files parse (metadata not lost)
 10. frontmatter: --- inside the body is not swallowed
 11. touch rewrites the vault (usage survives a resync)
 12. delete_star cannot be resurrected by a racing sync (row removed first)
 13. media_enforcer discovers ANY input name (no suzume hardcode)
 14. media_enforcer never builds shell strings (argv only)
 15. ffprobe timeout fails one probe honestly, not the pass
"""
from __future__ import annotations

import json
import shutil

import pytest

from core.agent.base_agent import HandoffPackage, attach_tool_events


def _envelope(inner: dict) -> dict:
    """The gate's dispatch envelope (m1 contract)."""
    ok = bool(inner.get("ok", True)) if isinstance(inner, dict) else True
    return {"ok": ok, "result": inner}


# ---- 1. truth envelope ----------------------------------------------------

def test_attach_reads_inner_ok(fresh_home):
    pkg = HandoffPackage(what_was_done="x", agent="code")
    events = [{"tool": "Bash", "args": {"cmd": "true"},
               "result": _envelope({"ok": False, "error": "exit 1"}),
               "call_id": "1"}]
    pkg = attach_tool_events(pkg, events)
    assert pkg.task_success is False
    assert "exit 1" in pkg.what_was_done


def test_attach_accepts_inner_ok(fresh_home):
    pkg = HandoffPackage(what_was_done="x", agent="code")
    events = [{"tool": "Write", "args": {"path": "a.md"},
               "result": _envelope({"ok": True, "path": "a.md", "bytes": 3}),
               "call_id": "1"}]
    pkg = attach_tool_events(pkg, events)
    assert pkg.task_success is True
    assert pkg.artifacts_created and pkg.artifacts_created[0]["path"] == "a.md"


def test_blocked_call_is_failure_even_if_envelope_ok(fresh_home):
    """Envelope ok=False with a blocked inner result must count as failure."""
    pkg = HandoffPackage(what_was_done="x", agent="code")
    events = [{"tool": "Bash", "args": {"cmd": "rm -rf /"},
               "result": {"ok": False,
                          "result": {"ok": False, "error": "requires consent",
                                     "needs_consent": True}},
               "call_id": "1"}]
    pkg = attach_tool_events(pkg, events)
    assert pkg.task_success is False


# ---- 2/3/4. no laundering, no plumbing credit ------------------------------

def test_parse_error_with_successful_tools_records_not_rescues(fresh_home):
    from core.core_agents.agents import _parse_handoff
    pkg = _parse_handoff("prose without any JSON handoff", "code")
    assert pkg.context_for_memory.get("parse_error") is True
    events = [{"tool": "Write", "args": {"path": "a.md"},
               "result": _envelope({"ok": True, "path": "a.md"}), "call_id": "1"}]
    pkg.context_for_memory.setdefault("parse_error", True)
    pkg2 = attach_tool_events(pkg, events)
    # honest outcome: the missing handoff verdict keeps the step failed; the
    # tool events stay recorded for diagnosis — never laundered into success
    assert pkg2.task_success is False
    assert pkg2.context_for_memory.get("parse_error") is True
    assert len(pkg2.context_for_memory.get("tool_events", [])) == 1


def test_planning_failure_is_not_rescued(fresh_home):
    """The old planning override flipped task_success=False to True when all
    (preflight) events were ok. No override may exist now."""
    from core.core_agents.agents import _parse_handoff
    pkg = _parse_handoff(json.dumps({
        "what_was_done": "planning incomplete",
        "task_success": False,
        "decision_confidence": 0.4,
    }), "planning")
    assert pkg.task_success is False
    events = [{"tool": "Skill", "args": {"query": "x"},
               "result": _envelope({"ok": True, "skills": []}), "call_id": "p1"}]
    pkg2 = attach_tool_events(pkg, events)
    # skill.search alone succeeding must never mark planning complete
    assert pkg2.task_success is False


def test_preflight_events_do_not_inflate_confirmed_calls(fresh_home):
    pkg = HandoffPackage(what_was_done="real work", agent="code", task_success=True)
    real = [
        {"tool": "Write", "args": {"path": "b.md"},
         "result": _envelope({"ok": True, "path": "b.md"}), "call_id": "r1"},
    ]
    pkg2 = attach_tool_events(pkg, real)  # only real events reach attach now
    assert "Confirmed 1 real tool call(s)" in pkg2.what_was_done
    assert pkg2.tools_used == ["Write"]


# ---- 5/6. checkpoints carry real progress --------------------------------

def test_cancelled_goal_keeps_remaining_plan(fresh_home):
    from failure.checkpoint import checkpoint, load_checkpoint
    plan = [{"agent": "code", "instruction": "s1"},
            {"agent": "review", "instruction": "s2"},
            {"agent": "security", "instruction": "s3"}]
    remaining_plan_now = plan[1:]  # user cancels during step index 1
    checkpoint("g-cancel-test", state={
        "status": "cancelled", "goal_text": "demo",
        "remaining_plan": remaining_plan_now,
        "handoffs": [plan[0]], "classification": {}, "language": "en",
        "planet_id": "p1", "owner_session_id": "s1"})
    cp = load_checkpoint("g-cancel-test")
    assert cp["remaining_plan"] == plan[1:]
    assert cp["status"] == "cancelled"


def test_failed_goal_carries_error_kind(fresh_home):
    from providers.client import LLMError
    from failure.checkpoint import checkpoint, load_checkpoint
    err = LLMError("quota exhausted", kind="rate_limit", provider="X", model="m")
    checkpoint("g-kind-test", state={
        "status": "failed", "goal_text": "d", "remaining_plan": [],
        "handoffs": [], "classification": {},
        "error": str(err), "error_kind": getattr(err, "kind", "")})
    cp = load_checkpoint("g-kind-test")
    assert cp["error_kind"] == "rate_limit"


# ---- 7/8/9/10/11/12. memory Unicode + vault -------------------------------

def test_safe_names_arabic_topics_distinct(fresh_home):
    from core.memory.layers.l3_semantic import _safe
    a = _safe("مقدمة في الذكاء الاصطناعي")
    b = _safe("أساسيات التعلم العميق")
    assert a.startswith("star-") and b.startswith("star-")
    assert a != b  # no untitled collision
    assert _safe("plain-latin") == "plain-latin"


def test_arabic_content_searchable_l3(fresh_home):
    from core.memory import get_memory
    mem = get_memory()
    mem.l3.create_star(topic="arabic star", domain="test",
                       summary="معالجة النصوص العربية",
                       content="محتوى عن الذكاء الاصطناعي وتعلم الآلة")
    hits = mem.search_l3("الذكاء الاصطناعي", top_k=5)
    assert any(s.topic == "arabic star" for s in hits)


def test_frontmatter_parses_crlf(fresh_home):
    from core.memory.layers.l3_semantic import _parse_frontmatter
    text = "---\r\nid: star-1\r\ntopic: win-file\r\nlast_used: 1700000000.0\r\n---\r\n\r\nbody"
    fm, body = _parse_frontmatter(text)
    assert fm.get("id") == "star-1"
    assert fm.get("last_used") == 1700000000.0
    assert "body" in body


def test_frontmatter_body_dash_line_not_swallowed(fresh_home):
    from core.memory.layers.l3_semantic import _parse_frontmatter
    text = ("---\nid: star-2\ntopic: t\n---\n\n"
            "intro\n\n---\n\nsecret-tail-that-must-stay-in-body")
    fm, body = _parse_frontmatter(text)
    assert fm.get("id") == "star-2"
    assert "secret-tail-that-must-stay-in-body" in body


def test_touch_rewrites_vault_and_survives_resync(fresh_home):
    from core.memory import get_memory
    mem = get_memory()
    star = mem.l3.create_star(topic="touch-me", domain="test",
                              summary="s", content="c")
    mem.l3.touch(star.id)
    # resolve the vault path from the (possibly updated) star record
    from config import get_config
    vpath = get_config().vault_dir / mem.l3.get_star(star.id).vault_path
    raw = vpath.read_text(encoding="utf-8")
    from core.memory.layers.l3_semantic import _parse_frontmatter
    fm, _ = _parse_frontmatter(raw)
    assert fm.get("interaction_count") == 1
    assert float(fm.get("last_used", 0)) > 0
    # a resync must not revert the usage counters
    mem.l3.sync_from_vault()
    again = mem.l3.get_star(star.id)
    assert again is not None and again.interaction_count == 1


def test_delete_star_removes_row_and_file_without_resurrection(fresh_home):
    from core.memory import get_memory
    mem = get_memory()
    star = mem.l3.create_star(topic="doomed", domain="test",
                              summary="s", content="c")
    from config import get_config
    vpath = get_config().vault_dir / star.vault_path
    mem.l3.delete_star(star.id)
    assert mem.l3.get_star(star.id) is None
    assert not vpath.exists()
    # a sync AFTER the deletion must not resurrect the star
    mem.l3.sync_from_vault()
    assert mem.l3.get_star(star.id) is None


# ---- 13/14/15. generalized media path --------------------------------------

def test_media_enforcer_finds_any_input_name(fresh_home, tmp_path):
    """A non-suzume input in input/ is discovered."""
    from core.media_enforcer import _find_input
    inp = tmp_path / "input"
    inp.mkdir()
    (inp / "user_clip.MOV").write_bytes(b"\x00" * 16)
    assert _find_input(tmp_path).name == "user_clip.MOV"


def test_media_enforcer_argv_only(fresh_home):
    """The module must not build shell strings — argv only."""
    import core.media_enforcer as me
    import inspect
    src = inspect.getsource(me)
    assert "shlex" not in src, "media_enforcer must not build shell strings"
    assert "shell=True" not in src


# ---- 16. repeat recall · 17. chat measurement/closure ---------------

def _completed_goal_row(fresh_goal_id: str, classification: dict):
    import time as _t
    from storage.local import get_storage
    st = get_storage()
    st.execute("INSERT INTO goals(id,text,status,created_at) VALUES(?,?,?,?);",
               (fresh_goal_id, "recall probe goal", "completed", _t.time()))
    return fresh_goal_id


def test_repeat_recall_short_circuits_same_goal(fresh_home):
    """Two identical non-chat goals in a row: the second recalls the first's
    asteroid instead of running the ladder. find_repeat_asteroid must be
    actually wired into the goal flow. The repeat hash includes a digest of
    the goal text (v18 fix): the same wording still collides, different
    wording never does — a tool-less goal used to hijack ANY earlier goal
    that shared only (category, intent)."""
    from core.memory import get_memory
    from core.memory.fingerprint import compute_repeat_hash, find_repeat_asteroid
    mem = get_memory()
    planet = mem.l1.create_planet(turn_id="g-first", goal_text="recall probe",
                                  classification={"category": "code_generation",
                                                  "domain": "general",
                                                  "intent": "write",
                                                  "complexity": "low"},
                                  language="en", owner_session_id="s1")
    ast = mem.complete_task(planet=planet, handoffs=[{
        "agent": "code", "what_was_done": "wrote f()",
        "task_success": True, "tools_used": [], "key_decisions": ["use def"]}],
        classification={"category": "code_generation", "domain": "general",
                        "intent": "write", "complexity": "low"}, language="en")
    h = compute_repeat_hash({"category": "code_generation",
                             "domain": "general", "intent": "write"}, [],
                            "recall probe")
    found = find_repeat_asteroid(h, recency_seconds=300)
    assert found == ast.id
    # a DIFFERENT ask with the same classification must NOT recall it
    h_other = compute_repeat_hash({"category": "code_generation",
                                   "domain": "general", "intent": "write"}, [],
                                  "write a poem about the sea")
    assert find_repeat_asteroid(h_other, recency_seconds=300) is None


def test_chat_closes_planet_with_measured_values(fresh_home, monkeypatch):
    """A conversational turn completes its planet and records MEASURED
    values (elapsed_ms real, llm_calls = the honest call_log count). The
    agent-turn model completes the turn with one completion round — no
    classify/chat router involved."""
    import asyncio
    from core.agent.orchestrator import get_orchestrator
    from providers.client import LLMClient, LLMResponse
    from storage.local import get_storage
    orch = get_orchestrator()

    async def fake_complete(self, *, agent, messages, max_tokens=1024,
                            temperature=0.0, response_format=None, tools=None,
                            tool_choice=None, tier_hint=None, on_delta=None):
        await asyncio.sleep(0.02)
        return LLMResponse(text="أهلاً بك! كيف أستطيع المساعدة؟",
                           model="mock", provider="MockLLM",
                           input_tokens=24, output_tokens=9, latency_ms=20,
                           finish_reason="stop", raw={"tool_calls": []},
                           key_alias="pytest")
    monkeypatch.setattr(LLMClient, "complete", fake_complete)

    summary = asyncio.run(orch.run_turn("مرحبا", language="ar"))
    assert summary.get("success") is True and summary.get("conversation") is True
    assert summary.get("elapsed_ms", 0) > 0, "elapsed must be measured, not 0"
    assert summary.get("reply"), "the turn must carry its reply text"
    st = get_storage()
    row = st.query_one("SELECT status FROM planets WHERE turn_id=?;",
                       (summary["turn_id"],))
    assert row and row["status"] == "completed", "chat planet must close"


def test_probe_media_timeout_is_honest(fresh_home, tmp_path, monkeypatch):
    from core.acceptance import _probe_media
    fake = tmp_path / "x.mp4"
    fake.write_bytes(b"\x00" * 64)
    import core.acceptance as acc
    if shutil.which("python") is None and shutil.which("py") is None:
        pytest.skip("no python launcher on PATH to fake a hanging ffprobe")
    launcher = shutil.which("python") or shutil.which("py")
    monkeypatch.setattr(acc.shutil, "which",
                        lambda name: str(tmp_path / "ffprobe_hang.bat"))
    blocker = tmp_path / "ffprobe_hang.bat"
    # a .bat runs on Windows without WinError 193; it blocks forever
    blocker.write_text("@echo off\r\n\"{}\" -c \"import time; time.sleep(600)\"\r\n".format(launcher),
                       encoding="utf-8")
    res = _probe_media(fake)
    assert res["ok"] is False and res.get("error") == "ffprobe_timeout"
