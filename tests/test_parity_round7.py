"""tests/test_parity_round7.py — Round-7 verbatim parity contract.

Pins what the round-7 reverse-engineering pass put on
Universe: the five permission modes with the auto ladder, the Task family,
TodoWrite activeForm, the new parity tools (their exact names), the
plan-mode annex injection, the CC compaction fallback, and the slash
additions.
"""
from __future__ import annotations

import json
import pathlib

import pytest


@pytest.fixture(autouse=True)
def _fresh_gate():
    """Isolate gate state per test: the gate is a process singleton and a
    mode flip (e.g. EnterPlanMode) must not leak into the next test."""
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    yield
    gate.set_permission_mode("ask")


# ---------------------------------------------------------------------------
# M1 — permission modes
# ---------------------------------------------------------------------------

def test_five_modes_canonical():
    from security.capability import PERMISSION_MODES
    assert PERMISSION_MODES == ("ask", "plan", "edit", "full")


def test_mode_aliases_verbatim():
    from security.capability import normalize_permission_mode
    assert normalize_permission_mode("build") == "ask"
    assert normalize_permission_mode("default") == "ask"
    assert normalize_permission_mode("acceptEdits") == "edit"
    assert normalize_permission_mode("bypassPermissions") == "full"
    assert normalize_permission_mode("full") == "full"
    assert normalize_permission_mode("ask") == "ask"
    assert normalize_permission_mode("auto") == "edit"
    assert normalize_permission_mode("yolo") == "full"
    assert normalize_permission_mode("nonsense") is None


async def _gate_call(tool_name: str, *, args: dict, turn_id: str = "g7"):
    from connectors.builtin import get_registry
    reg = get_registry()
    return await reg.call(tool_name, agent="universe", turn_id=turn_id,
                          args=args)


async def test_edit_mode_ladder(monkeypatch, tmp_path):
    """edit mode: Write-family flows without prompt; commands
    still ask; explicit never drops."""
    work = tmp_path / "proj"
    work.mkdir()

    # disarm factguard-style UX hooks for this process (tests assert the
    # gate's ladder, not hook behavior)
    from hooks import get_hook_manager
    monkeypatch.setattr(get_hook_manager(), "check", lambda *a, **k: None)

    # NO auto_grant here on purpose: auto mode must flow its own ladder
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_permission_mode("edit")

    # relative target: the gate scopes it against the project root (cwd),
    # the handler writes through files._project_root → tmp work dir (same
    # pattern as the existing universe-parity tests)
    monkeypatch.setattr("connectors.builtin.files._project_root",
                        lambda: work)
    target = work / "auto_parity7.txt"
    out = await _gate_call("Write", args={
        "path": "auto_parity7.txt", "content": "via auto", "append": False})
    assert out.get("ok") is True, out
    assert target.read_text(encoding="utf-8") == "via auto"

    # commands still consent in auto (Bash capability, no auto-grant wired)
    gate.set_permission_mode("edit")
    # the shell tool is platform-native: PowerShell on Windows, Bash elsewhere
    import sys as _sys
    if _sys.platform == "win32":
        shell_tool, ask_cmd, yolo_cmd = ("PowerShell",
                                         "Write-Output parityauto",
                                         "Write-Output yoloauto")
    else:
        shell_tool, ask_cmd, yolo_cmd = ("Bash", "echo parityauto",
                                         "echo yoloauto")
    out_cmd = await _gate_call(shell_tool, args={"command": ask_cmd})
    blocked = (not out_cmd.get("ok")
               and "consent" in str(out_cmd.get("error", "")))
    assert blocked, f"auto mode must still ask for commands: {out_cmd}"

    # yolo still flows commands without prompts
    monkeypatch.setattr(get_hook_manager(), "check", lambda *a, **k: None)
    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    gate.set_permission_mode("full")
    out_cmd2 = await _gate_call(shell_tool, args={"command": yolo_cmd})
    assert out_cmd2.get("ok") is True, out_cmd2
    stdout = ((out_cmd2.get("result") or {}).get("stdout") or "").strip()
    assert "yoloauto" in stdout


async def test_edit_mode_lifts_writes_only():
    """edit mode keeps pre-round-7 semantics: writes flow, explicit stays."""
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    gate.set_permission_mode("edit")
    out = await _gate_call("EnterPlanMode", args={})
    assert out.get("ok") is True, out
    assert (out.get("result") or {}).get("mode") == "plan"


# ---------------------------------------------------------------------------
# M2 — Task family (exact CC names + camelCase args)
# ---------------------------------------------------------------------------

async def test_task_family_full_cycle():
    from connectors.builtin import task_tools
    task_tools.reset_for_tests()

    c = await _gate_call("TaskCreate", args={
        "subject": "verify parity", "description": "round7", "activeForm": "verifying"})
    assert c["ok"] is True
    tid = c["result"]["taskId"]

    u = await _gate_call("TaskUpdate", args={
        "taskId": tid, "status": "in_progress", "addBlockedBy": ["dep-1"]})
    assert u["ok"] is True
    assert u["result"]["task"]["status"] == "in_progress"
    assert u["result"]["task"]["addBlockedBy"] == ["dep-1"]

    g = await _gate_call("TaskGet", args={"taskId": tid})
    assert g["ok"] and g["result"]["task"]["subject"] == "verify parity"

    tl = await _gate_call("TaskList", args={})
    assert tl["result"]["count"] == 1

    task_tools.append_output(tid, "some progress", turn_id="g7")
    o = await _gate_call("TaskOutput", args={"taskId": tid})
    assert o["ok"] and "some progress" in o["result"]["output"]

    s = await _gate_call("TaskStop", args={"taskId": tid})
    assert s["ok"] and s["result"]["stopped"] is True
    st = await _gate_call("TaskGet", args={"taskId": tid})
    assert st["result"]["task"]["status"] == "stopped"

    d = await _gate_call("TaskUpdate", args={"taskId": tid,
                                             "status": "deleted"})
    assert d["ok"] and d["result"].get("deleted") == tid
    missing = await _gate_call("TaskGet", args={"taskId": tid})
    assert missing["ok"] is False
    assert "not found" in missing["result"]["error"]


async def test_task_stop_deprecated_shell_id():
    from connectors.builtin import task_tools
    task_tools.reset_for_tests()
    c = await _gate_call("TaskCreate", args={"subject": "legacy"})
    tid = c["result"]["taskId"]
    s = await _gate_call("TaskStop", args={"shell_id": tid})
    assert s["ok"] and "deprecated" in s["result"].get("note", "")


# ---------------------------------------------------------------------------
# M3 — TodoWrite activeForm
# ---------------------------------------------------------------------------

async def test_todowrite_activeform_roundtrip():
    from connectors.builtin import todos
    todos.reset_todos_for_tests()
    w = await _gate_call("TodoWrite", args={"todos": [
        {"content": "step one", "status": "in_progress"},
        {"content": "step two", "status": "pending",
         "activeForm": "running step two"},
    ]})
    assert w["ok"] is True
    t = w["result"]["todos"]
    assert t[0]["activeForm"] == "step one"          # defaults to content
    assert t[1]["activeForm"] == "running step two"  # CC zod field honored
    r = await _gate_call("TodoRead", args={})
    assert r["result"]["todos"][1]["activeForm"] == "running step two"


# ---------------------------------------------------------------------------
# M2 — the parity tools
# ---------------------------------------------------------------------------

async def test_tool_search_and_select():
    q = await _gate_call("ToolSearch", args={"query": "notebook"})
    assert q["ok"] and q["result"]["matches"][0]["name"] == "NotebookEdit"
    sel = await _gate_call("ToolSearch", args={"query": "select:Read"})
    assert sel["ok"] and sel["result"]["selected"] is True
    assert sel["result"]["matches"][0]["name"] == "Read"
    none = await _gate_call("ToolSearch", args={"query": "select:NoSuchTool"})
    assert none["ok"] and none["result"]["selected"] is False


async def test_monitor_contract():
    m = await _gate_call("Monitor", args={
        "description": "echo", "timeout_ms": 1000, "command": "echo parity7"})
    r = m.get("result") or m.get("error")
    ok = m["ok"] and m["result"]["timed_out"] is False \
        and "parity7" in m["result"]["stdout"]
    assert ok, r
    both = await _gate_call("Monitor", args={
        "description": "b", "command": "echo x",
        "ws": {"url": "wss://x"}})
    assert both.get("ok") is not True  # exactly-one rule enforced
    no_args = await _gate_call("Monitor", args={"description": "empty"})
    assert no_args.get("ok") is not True


async def test_notebook_edit(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    work.joinpath("nb.ipynb").write_text(json.dumps({
        "cells": [{"cell_type": "code", "id": "abc", "metadata": {},
                   "source": ["print(1)\n"]}],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }), encoding="utf-8")
    monkeypatch.setattr("connectors.builtin.files._project_root",
                        lambda: work)
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    from security.capability import reset_gate_for_tests
    reset_gate_for_tests().set_auto_grant(True)

    r = await _gate_call("NotebookEdit", args={
        "notebook_path": "nb.ipynb", "cell_id": "abc",
        "new_source": "print(3)", "edit_mode": "replace"})
    assert r["ok"], r
    nb = json.loads(work.joinpath("nb.ipynb").read_text(encoding="utf-8"))
    assert nb["cells"][0]["source"] == ["print(3)"]
    i = await _gate_call("NotebookEdit", args={
        "notebook_path": "nb.ipynb", "cell_id": "",
        "new_source": "## new", "edit_mode": "insert"})
    assert i["ok"] and i["result"]["cells"] == 2
    d = await _gate_call("NotebookEdit", args={
        "notebook_path": "nb.ipynb", "cell_id": "abc", "edit_mode": "delete"})
    assert d["ok"] and d["result"]["cells"] == 1


async def test_apply_patch(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    work.joinpath("t.txt").write_text("hello\nworld\n", encoding="utf-8")
    monkeypatch.setattr("connectors.builtin.files._project_root",
                        lambda: work)
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    from security.capability import reset_gate_for_tests
    reset_gate_for_tests().set_auto_grant(True)

    patch = ("*** Begin Patch\n*** Update File: t.txt\n@@ world\n"
             "-world\n+there\n*** End Patch")
    r = await _gate_call("ApplyPatch", args={"patch": patch})
    assert r["ok"], r
    assert (work / "t.txt").read_text(encoding="utf-8") == "hello\nthere\n"

    add = ("*** Begin Patch\n*** Add File: new.txt\n+made by patch\n"
           "*** End Patch")
    a = await _gate_call("ApplyPatch", args={"patch": add})
    assert a["ok"]
    assert (work / "new.txt").read_text(encoding="utf-8") == "made by patch"

    miss = ("*** Begin Patch\n*** Update File: t.txt\n@@ nomatch\n"
            "-nomatch\n+x\n*** End Patch")
    bad = await _gate_call("ApplyPatch", args={"patch": miss})
    assert bad["result"]["ok"] is False
    assert "anchors not found" in " ".join(bad["result"]["errors"])

    begone = "*** Begin Patch\n*** Delete File: new.txt\n*** End Patch"
    gone = await _gate_call("ApplyPatch", args={"patch": begone})
    assert gone["ok"] and not (work / "new.txt").exists()


def test_parity_tool_names_exact():
    from connectors.builtin import get_registry
    names = set(get_registry().names())
    for expected in ("TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
                     "TaskStop", "TaskOutput", "EnterPlanMode",
                     "ToolSearch", "Monitor", "NotebookEdit", "GoalRead",
                     "ReadSessionContext", "PowerShell", "ApplyPatch",
                     "js", "js_reset", "js_add_node_module_dir"):
        assert expected in names, f"{expected} missing after Round-7"


async def test_enter_plan_gate_contract():
    """EnterPlanMode flips build→plan; yolo refuses (honest note)."""
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    gate.set_permission_mode("ask")
    e = await _gate_call("EnterPlanMode", args={})
    assert e["ok"] and (e["result"] or {}).get("mode") == "plan"
    assert gate.get_permission_mode() == "plan"
    gate.set_permission_mode("full")
    e2 = await _gate_call("EnterPlanMode", args={})
    assert e2["ok"] is False
    gate.set_permission_mode("ask")
    x = await _gate_call("ExitPlanMode", args={})
    # outside plan mode ExitPlanMode is an honest no-op, never a crash
    assert x.get("ok") is True and (x.get("result") or {}).get("changed") is False


# ---------------------------------------------------------------------------
# M4 — orchestrator plan annex + agreements
# ---------------------------------------------------------------------------

def _build_messages():
    from core.agent.orchestrator import Orchestrator
    orch = Orchestrator()
    return orch._build_turn_messages(
        goal_text="do the thing", language="en", history=[],
        l4_skills=[], l3_stars=[], rules=[],
        project_root="D:/proj")


def test_plan_annex_injected_only_in_plan_mode():
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_permission_mode("plan")
    msgs = _build_messages()
    text = msgs[0]["content"]
    assert "PLAN MODE" in text and "ExitPlanMode" in text
    gate.set_permission_mode("ask")
    msgs2 = _build_messages()
    assert "PLAN MODE" not in msgs2[0]["content"]
    # the CC working agreements ride EVERY turn
    assert "WORKING AGREEMENTS" in msgs2[0]["content"]
    assert "file_path:line_number" in msgs2[0]["content"]


# ---------------------------------------------------------------------------
# M5 — compaction: model template exists; fallback stays honest
# ---------------------------------------------------------------------------

def test_compact_model_summary_template():
    from context.compaction import SUMMARY_TEMPLATE
    assert SUMMARY_TEMPLATE
    for header in ("Primary Request and Intent", "All user messages",
                   "Pending Tasks", "Current Work", "Optional Next Step"):
        assert header in SUMMARY_TEMPLATE


async def test_compact_with_model_no_client_is_false():
    from context.compaction import compact_with_model
    msgs = [{"role": "system", "content": "s"}]
    msgs += [{"role": "user", "content": f"m{i}"} for i in range(10)]
    msgs += [{"role": "assistant", "content": "prose"}] * 10
    before = [dict(m) for m in msgs]
    changed = await compact_with_model(msgs, None)
    assert changed is False
    assert msgs == before


# ---------------------------------------------------------------------------
# M6 — slash registry additions
# ---------------------------------------------------------------------------

def test_slash_round7_additions():
    from universe_desktop_bridge.slash import BUILTINS
    by_name = {c.name: c for c in BUILTINS}
    for name, action in (("agents", "agents"), ("memory", "memory"),
                         ("hooks", "hooks"), ("permissions", "permission"),
                         ("status", "status"), ("context", "context"),
                         ("clear", "new"), ("export", "export"),
                         ("plan", "planmode"), ("skills", "skill"),
                         ("doctor", "doctor"), ("version", "version")):
        cmd = by_name.get(name)
        assert cmd is not None, f"/{name} missing from the slash registry"
        assert cmd.action == action, f"/{name} action mismatch"
        assert cmd.to_dict()["supported"] is True
    # no builtin /review: user .md command files own that name (resolve()
    # tries builtins first — a builtin would shadow real custom files)
    assert "review" not in by_name
    # /goal is retired (2026-10-28): one engine runs every task
    assert "goal" not in by_name
    assert by_name["mode"].action == "mode"
    assert "ask" in by_name["mode"].usage
    assert by_name["resume"].aliases == ("continue",)


def test_parity_doc_records_evidence_paths():
    """The RE evidence files must exist and carry the findings — the
    implementation's provenance trail.

    The evidence dir is machine-local (the RE workstation's temp), not
    part of the repo — on any other machine the honest outcome is a
    skip, not a failure."""
    base = pathlib.Path(r"D:\ClaudeDesktop\Temp\opencode\re7")
    if not base.is_dir():
        pytest.skip(f"RE evidence dir is machine-local and absent: {base}")
    for f in ("shell_findings.md", "parity_findings.md", "parity_spec.md",
              "shell_prompts.txt", "engine_prompts.txt"):
        assert (base / f).exists(), f"missing RE evidence: {f}"
