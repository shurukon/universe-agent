"""tests/test_zcode_parity.py — the ZCode-parity surfaces (Phase 2-4).

Covers the three new tools, the renamed registry names, the four permission
modes, and the slash registry — the parts of the reverse-engineering pass
that other suites don't already assert.
"""
from __future__ import annotations


def test_registry_uses_zcode_names():
    from connectors.builtin import get_registry
    names = set(get_registry().names())
    for expected in ("Read", "Write", "Edit", "LS", "Bash", "Grep", "Glob",
                     "TodoWrite", "TodoRead", "Agent", "Skill", "ExitPlanMode",
                     "AskUserQuestion", "MemoryQuery", "WebSearch", "WebFetch"):
        assert expected in names, f"{expected} missing from the registry"
    for retired in ("file.read", "file.write", "shell.exec", "skill.search",
                    "spawn", "todo.write", "plan.submit", "question.ask"):
        assert retired not in names, f"{retired} should be gone"


async def test_new_tools_run(fresh_home, tmp_path, monkeypatch):
    # fresh_home: this test touches storage-backed singletons (hooks read the
    # rules table) — without an isolated migrated home the file only passes
    # when some earlier suite file happens to leave a migrated DB behind.
    from connectors.builtin import get_registry
    reg = get_registry()
    work = tmp_path / "proj"
    work.mkdir()
    (work / "a.txt").write_text("alpha" + chr(10) + "beta target" + chr(10),
                                encoding="utf-8")
    (work / "b.md").write_text("# doc" + chr(10), encoding="utf-8")
    monkeypatch.setattr("connectors.builtin.search_tools.get_config",
                        lambda: {"project_root": str(work)})
    monkeypatch.setattr("connectors.builtin.files._project_root",
                        lambda: work)

    # disarm the built-in factguard: the hook demands facts before a goal's
    # first file write, which is a UX contract — this test exercises the
    # TOOL, not the hook
    from hooks import get_hook_manager
    monkeypatch.setattr(get_hook_manager(), "check", lambda *a, **k: None)
    # pin the gate state: earlier suite files may leave the mode in
    # edit/yolo or the auto-grant bypass on — the per_turn ladder (build,
    # no bypass) is what this test asserts
    from security.capability import get_gate
    gate = get_gate()
    gate.set_permission_mode("ask")
    gate.set_auto_grant(False)

    hit = await reg.call("Grep", agent="universe", turn_id="g1",
                         args={"pattern": "target"})
    assert hit["ok"] and hit["result"]["count"] == 1, hit
    globbed = await reg.call("Glob", agent="universe", turn_id="g1",
                             args={"pattern": "*.md"})
    assert globbed["ok"] and globbed["result"]["files"] == ["b.md"], globbed

    write = await reg.call("Write", agent="universe", turn_id="g1",
                           args={"path": "a.txt", "content": "x"})
    # Write is per_turn consent: yolo lifts it (this also exercises the
    # mode ladder) — build mode would ask through the consent banner
    from security.capability import get_gate
    assert write["needs_consent"] is True, write
    get_gate().set_permission_mode("full")
    write2 = await reg.call("Write", agent="universe", turn_id="g1",
                            args={"path": "a.txt", "content": "x"})
    assert write2["ok"] and (work / "a.txt").read_text(encoding="utf-8") == "x", write2
    get_gate().set_permission_mode("ask")
    bounced = await reg.call("SendMessage", agent="universe", turn_id="g1",
                             args={"to": "research", "message": "hi"})
    assert bounced["result"]["ok"] is False and "nothing delivered" in bounced["result"]["error"], bounced


def test_permission_modes_are_zcodes_five():
    """ZCode verbatim 4 modes: ask before changes, plan, edit automatically, full access."""
    from security.capability import (PERMISSION_MODES, get_gate,
                                     normalize_permission_mode)
    assert PERMISSION_MODES == ("ask", "plan", "edit", "full")
    assert normalize_permission_mode("acceptEdits") == "edit"
    assert normalize_permission_mode("bypassPermissions") == "full"
    assert normalize_permission_mode("full") == "full"
    assert normalize_permission_mode("auto") == "edit"   # alias to edit
    assert normalize_permission_mode("build") == "ask"   # legacy alias
    assert normalize_permission_mode("yolo") == "full"   # legacy alias
    assert normalize_permission_mode("nonsense") is None
    gate = get_gate()
    assert gate.set_permission_mode("default") == "ask"
    assert gate.set_permission_mode("yolo") == "full"
    assert gate.set_permission_mode("auto") == "edit"
    assert gate.get_permission_mode() == "edit"
    gate.set_permission_mode("ask")


def test_slash_registry_matches_zcode():
    from universe_desktop_bridge.slash import BUILTINS, resolve
    by_name = {c.name: c for c in BUILTINS}
    for zcode_name in ("help", "login", "logout", "compact", "init", "expert",
                       "effort", "workflow", "workflows", "fork", "locale",
                       "mcp", "plugins", "mode", "model", "new", "resume",
                       "sessions", "rewind", "skill"):
        # /goal deliberately absent — the goal era is over (2026-10-28)
        assert zcode_name in by_name, f"/{zcode_name} missing from BUILTINS"
    assert by_name["mode"].usage == "/mode [ask|plan|edit|full]"
    assert resolve("/continue")["command"]["name"] == "resume"
    # /sessions — the fourth session command (new/fork/compact/sessions),
    # parity with the CLI registry (cli/slash_commands.py §Sessions)
    sessions = resolve("/sessions")
    assert sessions is not None and sessions["kind"] == "builtin"
    assert sessions["command"]["action"] == "sessions"
    assert sessions["command"]["supported"] is True
    assert resolve("/settings") is not None
    assert resolve("C:/some/path") is None
    assert resolve("/definitely-not-a-command") is None


def test_slash_custom_commands_expand_arguments(tmp_path, monkeypatch):
    from universe_desktop_bridge import slash
    cmd_dir = tmp_path / ".zcode" / "commands"
    cmd_dir.mkdir(parents=True)
    fm = ("---" + chr(10) + "description: Review the code" + chr(10)
          + "argument-hint: [path]" + chr(10) + "allowed-tools: Grep, Read"
          + chr(10) + "---" + chr(10) + "Review $ARGUMENTS carefully."
          + chr(10))
    (cmd_dir / "review.md").write_text(fm, encoding="utf-8")
    monkeypatch.setattr(slash, "_command_roots", lambda: [cmd_dir])

    found = {c["name"]: c for c in slash.list_commands()}
    assert "review" in found
    resolved = slash.resolve("/review src/auth.ts")
    assert resolved["kind"] == "custom"
    assert resolved["prompt"] == "Review src/auth.ts carefully."
