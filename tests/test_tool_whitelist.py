"""tests/test_tool_whitelist.py — Phase 0.1 enforcement regression.

The codebase previously declared `tool_whitelist_names` on every Core Agent but
never checked it; any agent could call any tool. This file proves the check is
now real: an agent genuinely CANNOT call a tool outside its whitelist.
"""
import pytest


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    """file.write below uses RELATIVE paths — chdir to tmp so the test never
    drops test_file.txt into the repo root (audit 2026-09: the stray
    D:/.../test_file.txt in the tree was exactly this leak)."""
    monkeypatch.chdir(tmp_path)


@pytest.mark.asyncio
async def test_agent_cannot_call_tool_outside_its_whitelist(fresh_home):
    """Phase 0.1: the gate rejects tools outside the agent's whitelist.
    Post-S1 the registered agent is 'universe' (the union of the retired
    specialists' whitelists); TestRunner is a REAL registered tool that is
    deliberately NOT in that whitelist — the gate must reject it, not just
    decorate the agent object."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = await reg.call("TestRunner", agent="universe", turn_id="g1", args={})
    assert r["ok"] is False
    assert r["blocked_by"] == "whitelist"
    assert "universe" in r["error"]
    assert "TestRunner" in r["error"]
    assert "TestRunner" not in r.get("whitelist", [])


@pytest.mark.asyncio
async def test_agent_can_call_tool_inside_its_whitelist(fresh_home):
    """Phase 0.1 sanity: the registered agent CAN still call file.write —
    the enforcement doesn't accidentally block legitimate calls."""
    from connectors.builtin import get_registry
    reg = get_registry()
    args = {"path": "test_file.txt", "content": "ok"}
    r = await reg.call("Write", agent="universe", turn_id="g1", args=args)
    if r.get("blocked_by") == "hook":
        # Phase 1 factguard: the first write per goal is intercepted once
        # (require_facts one-shot); the retried identical call passes
        r = await reg.call("Write", agent="universe", turn_id="g1", args=args)
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_whitelist_check_uses_exact_tool_names_not_prefix(fresh_home):
    """Phase 0.1: agents historically declared 'git' / 'docker' as whitelist
    entries, but the real tools are named git.status, git.diff, docker.build,
    etc. The whitelists now declare the exact tool names. Verify a DevOps agent
    CAN call git.status (real name) and that the moon-probation test path still
    works for non-core agents."""
    from connectors.builtin import get_registry
    from core.core_agents.agents import get_agent
    reg = get_registry()
    a = get_agent("universe")
    # whitelists now contain exact tool names, not the old 'git' / 'docker' prefixes
    assert "GitStatus" in a.tool_whitelist_names
    assert "git" not in a.tool_whitelist_names
    assert "DockerBuild" in a.tool_whitelist_names
    assert "docker" not in a.tool_whitelist_names
    # the gate will reach the real git.status handler (it may fail at runtime
    # because we're not in a git repo, but it must NOT be blocked:whitelist)
    r = await reg.call("GitStatus", agent="universe", turn_id="g1", args={"repo": "."})
    assert r.get("blocked_by") != "whitelist"


@pytest.mark.asyncio
async def test_unregistered_moon_agent_is_denied_not_exempt(fresh_home):
    """m2: register_moon() is deleted — an unregistered "moon" agent must be
    DENIED (unknown_agent) rather than silently exempted from the whitelist,
    which was the old fail-open behavior."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = await reg.call("WebFetch", agent="test-moon-2", turn_id="g1",
                       args={"url": "https://api.example.com/x"})
    assert r["ok"] is False
    assert r.get("blocked_by") == "unknown_agent"


@pytest.mark.asyncio
async def test_whitelist_block_is_audited(fresh_home):
    """Phase 0.1: a blocked-by-whitelist call must produce an audit entry so
    the operator can see someone tried to escape their whitelist."""
    from connectors.builtin import get_registry
    from security.audit import tail_audit
    reg = get_registry()
    # S1: TestRunner is registered but NOT in universe's merged whitelist,
    # so the gate must block and AUDIT the attempt
    await reg.call("TestRunner", agent="universe", turn_id="g1", args={})
    class IO:
        def __init__(self): self.out = []
        def print(self, *a, **k): self.out.append(a)
    io = IO()
    tail_audit(io, 20)
    lines = " ".join(str(a) for a in io.out)
    assert "blocked:whitelist" in lines
