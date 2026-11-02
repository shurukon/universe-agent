"""tests/test_security_hardening_2026_10.py — regression pins for the
2026-10 full-audit fix batch (security cluster).

Every test here pins a fix that was verified live during the audit:
glob depth semantics, ApplyPatch scope locks, single-use "once" consent,
runtime consent escalators (CDP browser + docker quarantine downgrade),
the edit-mode lift restriction, the gate's per-call watchdog, the pandas
query code-execution defense, and the Monitor timeout cap.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fresh_gate():
    """Isolate gate state per test (same contract as test_parity_round7)."""
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    yield
    gate.set_permission_mode("ask")


async def _call(tool_name: str, args: dict, *, agent: str = "universe",
                turn_id: str = "g-harden"):
    from connectors.builtin import get_registry
    return await get_registry().call(tool_name, agent=agent,
                                     turn_id=turn_id, args=args)


# ---- glob / path scope -----------------------------------------------------

def test_path_scope_matches_root_and_every_depth():
    """The old segment-wise matcher only matched depth-2 paths: root-level
    and depth-3+ candidates were silently denied even when in-scope, which
    made several declared scopes vacuous or over-restrictive."""
    from security.capability import CapabilityGate
    root = (Path(tempfile.gettempdir()) / "gh-scope-root").resolve()
    root.mkdir(parents=True, exist_ok=True)
    pm = CapabilityGate._path_matches
    assert pm(root / "x.py", "path:glob:**/*", root) is True
    assert pm(root / "x.py", "path:glob:./**", root) is True
    assert pm(root / "a" / "b" / "c" / "deep.py", "path:glob:**/*", root) is True
    assert pm(root / "a" / "b" / "c" / "deep.py", "path:glob:./**", root) is True
    outside = root.parent / "outside.txt"  # sibling of root: guaranteed out
    assert pm(outside, "path:glob:**/*", root) is False
    assert pm(outside, "path:glob:./**", root) is False


# ---- ApplyPatch scope locks ------------------------------------------------

async def test_apply_patch_rejects_absolute_and_traversal(fresh_home, tmp_path,
                                                          monkeypatch):
    """ApplyPatch used to honor absolute paths + `~` inside the patch text —
    one per_turn approval silently covered writes/deletes anywhere on disk."""
    work = tmp_path / "proj"
    work.mkdir()
    (work / "t.txt").write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr("connectors.builtin.files._project_root", lambda: work)
    # disarm the built-in factguard: pre_write covers ApplyPatch now (hook
    # domain widening) and this test pins the TOOL's scope locks
    from hooks import get_hook_manager
    monkeypatch.setattr(get_hook_manager(), "check", lambda *a, **k: None)

    evil_abs = ("*** Begin Patch\n"
                f"*** Update File: {work.parent / 'evil.txt'}\n"
                "@@ hello\n-hello\n+evil\n*** End Patch")
    r = await _call("ApplyPatch", {"patch": evil_abs})
    assert r["ok"] is False, r
    assert not (work.parent / "evil.txt").exists()

    evil_rel = ("*** Begin Patch\n*** Update File: ../evil.txt\n"
                "@@ x\n-x\n+y\n*** End Patch")
    r2 = await _call("ApplyPatch", {"patch": evil_rel})
    assert r2["ok"] is False, r2
    assert not (work.parent / "evil.txt").exists()

    # the honest in-root patch still works
    ok_patch = ("*** Begin Patch\n*** Update File: t.txt\n"
                "@@ hello\n-hello\n+there\n*** End Patch")
    r3 = await _call("ApplyPatch", {"patch": ok_patch})
    assert r3["ok"] is True, r3
    assert (work / "t.txt").read_text(encoding="utf-8") == "there\n"


# ---- single-use "once" consent ----------------------------------------------

async def test_once_consent_is_single_use(fresh_home, monkeypatch):
    """A "once" grant used to persist forever keyed by tool name — the first
    approval silently became a permanent one. Now consumed on use."""
    from connectors.builtin import get_registry
    from core.agent.base_agent import Tool
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(False)  # force the grant path, not the auto bypass
    reg = get_registry()
    calls: list[int] = []
    # probe tool is intentionally not a registered name — patch it into the
    # universe agent's whitelist so the identity/whitelist phases pass
    from core.core_agents import get_agent
    ga = get_agent("universe")
    monkeypatch.setattr(ga, "tool_whitelist_names",
                        [*ga.tool_whitelist_names, "__OnceProbe__"])
    reg.register(Tool(name="__OnceProbe__", capability="Read",
                      description="once-consent probe",
                      handler=lambda: calls.append(1) or {"ok": True},
                      consent="once", resources=[], path_args=None))
    # simulate a previously-approved once grant (as the consent UI would leave)
    gate._once_granted.add("__OnceProbe__")
    gate._persist_grant("", "__OnceProbe__", "once")
    r = await reg.call("__OnceProbe__", agent="universe", turn_id="g1", args={})
    assert r["ok"] is True and calls == [1]
    # the grant is GONE after this use — memory and disk
    assert "__OnceProbe__" not in gate._once_granted
    row = gate._st.query_one(
        "SELECT COUNT(*) AS c FROM capability_grants WHERE tool='__OnceProbe__'")
    assert row["c"] == 0


# ---- runtime consent escalators ---------------------------------------------

def test_docker_unsafe_flags_escalate_to_per_goal():
    """network='host'/readonly=False downgrade the advertised quarantine —
    they re-ask per goal instead of riding the tool-level consent."""
    from connectors.builtin import get_registry
    get_registry()  # runs every module's register() — escalators wired here
    from security.capability import _escalate
    assert _escalate("DockerRun", {"image": "x", "network": "host"}) is True
    assert _escalate("DockerRun", {"image": "x", "readonly": False}) is True
    assert _escalate("DockerRun", {"image": "x"}) is False
    assert _escalate("DockerRun", {"image": "x", "network": "bridge"}) is False


def test_browser_escalators_private_url_and_cdp(monkeypatch):
    from connectors.builtin import get_registry
    get_registry()
    from security.capability import _escalate
    # literal private/loopback targets ask per goal
    assert _escalate("BrowserNavigate", {"url": "http://127.0.0.1:8738/"}) is True
    assert _escalate("BrowserNavigate", {"url": "http://10.1.2.3/"}) is True
    # a literal public IP does not (no network dependency in this test)
    assert _escalate("BrowserNavigate", {"url": "https://93.184.216.34/"}) is False
    # with the user's real browser attached, everything interactive asks
    import connectors.builtin.browser as b
    monkeypatch.setattr(b, "_cdp_browser", object())
    assert _escalate("BrowserClick", {}) is True
    assert _escalate("BrowserFill", {}) is True
    assert _escalate("BrowserSnapshot", {}) is True
    assert _escalate("BrowserNavigate", {"url": "https://93.184.216.34/"}) is True
    assert _escalate("BrowserTabs", {"url": "https://93.184.216.34/"}) is True


# ---- edit-mode lift restriction ----------------------------------------------

async def test_edit_mode_lifts_writes_not_commands(fresh_home, tmp_path):
    """edit mode = "writes run without the per-goal gate; commands still ask".
    The old lift raised EVERY per_turn tool (Bash, BrowserConsole, GitPush)."""
    from config import get_config
    get_config().set("project_root", str(tmp_path))
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(False)
    gate.set_permission_mode("edit")
    p = tmp_path / "w.txt"
    # the builtin fact-guard blocks the FIRST write of a goal by design —
    # assert that contract, then the retry proves the consent lift itself
    r1 = await _call("Write", {"path": str(p), "content": "lifted"})
    if not r1["ok"] and r1.get("blocked_by") == "hook":
        pass  # fact-guard consumed its one block for this goal
    else:
        assert r1["ok"] is True, r1
    rw = await _call("Write", {"path": str(p), "content": "lifted"})
    assert rw["ok"] is True, rw
    assert p.read_text(encoding="utf-8") == "lifted"
    # a command in edit mode still needs consent. The builtin fact-guard
    # blocks the first SHELL command of a goal by design; after that one
    # block, the gate's consent phase is what blocks (no handler installed)
    rb = await _call("Bash", {"cmd": "echo never-ran"})
    if rb.get("ok") is False and rb.get("blocked_by") == "hook":
        rb = await _call("Bash", {"cmd": "echo never-ran"})
    assert rb.get("ok") is False and rb.get("needs_consent") is True, rb


# ---- gate watchdog ------------------------------------------------------------

async def test_gate_timeout_kills_hung_handler(fresh_home, monkeypatch):
    from config import get_config
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    get_config().set("tool_timeout_s", 1)
    from connectors.builtin import get_registry
    from core.agent.base_agent import Tool
    reg = get_registry()

    async def slow():
        await asyncio.sleep(6)
        return {"ok": True}

    from core.core_agents import get_agent
    ga = get_agent("universe")
    monkeypatch.setattr(ga, "tool_whitelist_names",
                        [*ga.tool_whitelist_names, "__SlowProbe__"])
    reg.register(Tool(name="__SlowProbe__", capability="Read",
                      description="watchdog probe", handler=slow,
                      consent="auto", resources=[], path_args=None))
    r = await reg.call("__SlowProbe__", agent="universe", turn_id="g1", args={})
    assert r["ok"] is False, r
    assert "timed out" in str(r.get("error", "")), r


# ---- pandas query code-execution defense ---------------------------------------

async def test_pandas_query_rejects_mro_code_exec(fresh_home, tmp_path):
    """df.__class__.mro()… executed ARBITRARY code through pandas' eval
    fallback (verified live pre-fix). The deny regex must reject it."""
    from config import get_config
    get_config().set("project_root", str(tmp_path))
    csv = tmp_path / "d.csv"
    csv.write_text("a\n1\n2\n", encoding="utf-8")
    r = await _call("PandasQuery", {"path": str(csv), "op": "query",
                                    "expr": "df.__class__.mro()"})
    assert r["ok"] is False, r
    # the honest query still runs (numexpr engine)
    ok = await _call("PandasQuery", {"path": str(csv), "op": "query",
                                     "expr": "a > 1"})
    assert ok["ok"] is True, ok


# ---- Monitor timeout cap ---------------------------------------------------------

def test_monitor_rejects_over_5min():
    """Monitor allowed 900s while Bash was capped at 300 — one ladder, one cap."""
    from connectors.builtin.parity_tools import _monitor
    r = _monitor(description="probe", command="echo hi", timeout_ms=901000)
    assert r["ok"] is False
    assert "300000" in r["error"]


@pytest.mark.skipif(shutil.which("powershell.exe") is None,
                    reason="powershell.exe not on PATH (non-Windows CI)")
def test_powershell_runs_via_encoded_command():
    """The -EncodedCommand form must carry the script intact — no quoting
    layer can be injected through base64(UTF-16LE)."""
    from connectors.builtin.parity_tools import _powershell
    r = _powershell(command="Write-Output pq-ok-42")
    assert r["ok"] is True, r
    assert "pq-ok-42" in str(r.get("stdout", ""))
