"""tests/test_apply_patch_v4a.py — V4A update hunks that CHANGE only by
insertion (context + plus lines, no minus lines) must insert at the anchor,
and unanchored plus-only hunks must refuse — never silently succeed (the
2026-10 matrix audit caught `_apply_patch` writing the file unchanged and
reporting ok=true for our fixture patch)."""
from __future__ import annotations

import pytest


async def _call(tool_name: str, *, args: dict):
    from connectors.builtin import get_registry
    reg = get_registry()
    return await reg.call(tool_name, agent="universe", turn_id="g-v4a",
                          args=args)


@pytest.fixture()
def _patch_env(tmp_path, monkeypatch):
    """Isolated project root + auto grant + disarm UX hooks (parity: same
    recipe as test_parity_round7.test_apply_patch)."""
    work = tmp_path / "proj"
    work.mkdir()
    work.joinpath("t.txt").write_text("line one\nline two\nline three\n",
                                      encoding="utf-8")
    monkeypatch.setattr("connectors.builtin.files._project_root",
                        lambda: work)
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    # disarm the built-in factguard entirely: pre_write now covers
    # ApplyPatch too (hook domain widening), so a plain reset is not enough
    # for a test that exercises the TOOL, not the hook
    from hooks import get_hook_manager
    monkeypatch.setattr(get_hook_manager(), "check", lambda *a, **k: None)
    from security.capability import reset_gate_for_tests
    reset_gate_for_tests().set_auto_grant(True)
    return work


async def test_plus_only_insertion_after_context(_patch_env):
    patch = ("*** Begin Patch\n*** Update File: t.txt\n@@\n"
             " line two\n+line two MATRIX-PATCHED\n*** End Patch")
    r = await _call("ApplyPatch", args={"patch": patch})
    assert r["ok"], r
    assert (r["result"]["applied"][0]["op"] == "update")
    assert (_patch_env / "t.txt").read_text(encoding="utf-8") == \
        "line one\nline two\nline two MATRIX-PATCHED\nline three\n"


async def test_plus_only_insertion_after_hunk_anchor(_patch_env):
    patch = ("*** Begin Patch\n*** Update File: t.txt\n@@ line two\n"
             "+inserted-at-anchor\n*** End Patch")
    r = await _call("ApplyPatch", args={"patch": patch})
    assert r["ok"], r
    assert (_patch_env / "t.txt").read_text(encoding="utf-8") == \
        "line one\nline two\ninserted-at-anchor\nline three\n"


async def test_plus_only_without_anchor_refuses(_patch_env):
    patch = ("*** Begin Patch\n*** Update File: t.txt\n@@\n"
             "+orphan insertion\n*** End Patch")
    r = await _call("ApplyPatch", args={"patch": patch})
    assert r["ok"] is False or r["result"]["ok"] is False
    assert "anchors not found" in " ".join(r["result"]["errors"])
    # the file is untouched
    assert (_patch_env / "t.txt").read_text(encoding="utf-8") == \
        "line one\nline two\nline three\n"
