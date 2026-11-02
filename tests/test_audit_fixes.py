"""Regression tests for the 2026-09 audit fixes.

Each test pins one verified audit finding so the fix cannot silently
regress:
  1. v0018 (and any future migration) can never again be silently left out
     of the _load_all() import list — the suite auto-discovers schema/v*.py.
  2. Tool-spawned subprocesses never see UNIVERSE_BRIDGE_TOKEN / passphrase.
  3. docker/k8s argv construction cannot inject host shell metacharacters.
  4. The capability gate fails closed: path args with no declared path
     resource are denied; file:// never matches a url: resource.
  5. The audit hash chain detects tampering.
  6. Tool results bound for the LLM are secret-redacted and size-capped.
"""
from __future__ import annotations

from pathlib import Path

import json

import pytest


# ---- 1. migration discovery --------------------------------------------------

def test_every_migration_file_is_loaded():
    """schema/v*.py files on disk must all appear in MIGRATIONS after
    _load_all() — v0018_swarm_registry was written but never imported, so
    the live DB froze at v17 and the general agent lost its routing."""
    from pathlib import Path
    from schema import migrations
    migrations._load_all()
    root = Path(migrations.__file__).parent
    on_disk = sorted(
        int(p.stem[1:5]) for p in root.glob("v*.py")
        if p.stem[1:5].isdigit() and p.stem != "__init__"
    )
    loaded = sorted(v for v, _, _ in migrations.MIGRATIONS)
    assert loaded == on_disk, (
        f"migration files on disk {on_disk} != loaded {loaded} — "
        f"missing: {set(on_disk) - set(loaded)}")


def test_latest_migration_is_at_least_v18():
    from schema import migrations
    migrations._load_all()
    assert max(v for v, _, _ in migrations.MIGRATIONS) >= 18


# ---- 2. child env sanitization ----------------------------------------------

def test_sanitized_child_env_strips_universe_secrets(monkeypatch):
    from security.child_env import sanitize_env, sanitized_child_env
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", "tok-123")
    monkeypatch.setenv("UNIVERSE_PASSPHRASE", "pass-456")
    monkeypatch.setenv("SOMEONE_API_KEY", "sk-abc")
    monkeypatch.setenv("PATH", "C:/windows")
    env = sanitized_child_env()
    assert "UNIVERSE_BRIDGE_TOKEN" not in env
    assert "UNIVERSE_PASSPHRASE" not in env
    assert "SOMEONE_API_KEY" not in env
    assert env["PATH"] == "C:/windows"
    # caller overrides can never smuggle a denied name back in
    env2 = sanitize_env({"UNIVERSE_BRIDGE_TOKEN": "evil", "MY_VAR": "1"})
    assert "UNIVERSE_BRIDGE_TOKEN" not in env2
    assert env2["MY_VAR"] == "1"


def test_shell_run_does_not_leak_bridge_token(monkeypatch, tmp_path):
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", "tok-123")
    from connectors.builtin.shell import _run
    out = _run("echo %UNIVERSE_BRIDGE_TOKEN%", cwd=str(tmp_path), timeout=15)
    assert out["ok"] is True
    assert "tok-123" not in out["stdout"]


# ---- 3. docker / k8s argv injection ------------------------------------------

def test_docker_run_image_injection_is_inert(monkeypatch):
    """`image="alpine & calc.exe"` used to execute on the host shell."""
    from connectors.builtin import docker
    captured: dict = {}

    def fake_run_argv(argv, cwd=".", timeout=0, env=None):
        captured["argv"] = argv
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": "",
                "elapsed_ms": 0, "cmd": " ".join(argv)}

    monkeypatch.setattr(docker, "_run_argv", fake_run_argv)
    res = docker.docker_run(image="alpine & calc.exe", command="ls")
    assert res["ok"] is True
    argv = captured["argv"]
    # the image is ONE argv element followed by the explicit in-container
    # shell — nothing is ever parsed by a host shell (shell=False)
    assert "alpine & calc.exe" in argv
    assert argv[argv.index("alpine & calc.exe") + 1:] == ["sh", "-c", "ls"]


def test_k8s_name_injection_is_inert(monkeypatch):
    from connectors.builtin import devops
    captured: dict = {}

    def fake_run_argv(argv, cwd=".", timeout=0, env=None):
        captured["argv"] = argv
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": "",
                "elapsed_ms": 0, "cmd": " ".join(argv)}

    monkeypatch.setattr(devops, "_run_argv", fake_run_argv)
    res = devops.k8s(action="get", resource="pods", name="a&calc.exe",
                     confirm=False)
    assert res["ok"] is True
    assert "a&calc.exe" in captured["argv"]  # single argv element, no shell


# ---- 4. gate fail-closed resource checks -------------------------------------

@pytest.fixture()
def gate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVERSE_HOME", str(tmp_path))
    from config import get_config
    get_config().set("project_root", str(tmp_path))
    return tmp_path


def test_tool_with_url_resources_must_check_path_args(gate_env):
    """browser_screenshot declared only url: resources; the old gate
    returned True when the call had no URL arg, so its `path` argument
    could write any absolute path at consent=auto."""
    from core.agent.base_agent import Tool
    from security.capability import CapabilityGate

    async def handler(path=None, url=""):
        return {"ok": True}

    tool = Tool(name="t", capability="network.req", description="",
                handler=handler, resources=["url:https://*"])
    gate = CapabilityGate()
    assert gate._resource_allowed(tool, {"path": str(gate_env / "ok.png")}) is False
    assert gate._resource_allowed(tool, {"path": "C:/Windows/evil.png"}) is False
    assert gate._resource_allowed(tool, {"url": "https://x.example"}) is True


def test_tool_with_no_resources_fails_closed_on_path_args(gate_env):
    """charting registered resources=[] and read any local file."""
    from core.agent.base_agent import Tool
    from security.capability import CapabilityGate

    def handler(path=""):
        return {"ok": True}

    tool = Tool(name="t", capability="Read", description="",
                handler=handler, resources=[])
    gate = CapabilityGate()
    assert gate._resource_allowed(tool, {"path": str(gate_env / "a.csv")}) is False
    # a purely internal call still passes
    assert gate._resource_allowed(tool, {"data": [1, 2]}) is True


def test_config_singleton_resolves_universe_home_lazily(monkeypatch, tmp_path):
    """The class docstring promises paths resolve lazily from UNIVERSE_HOME,
    but __new__ memoized the first construction — later fixtures' setenv
    was ignored and cfg.set("project_root", tmp) persisted into the REAL
    user home (the 2026-10-25 pytest-108 poisoning: a deleted pytest temp
    dir became project_root and every shell cwd died WinError 267)."""
    import json
    import os

    from config import get_config

    home_a = tmp_path / "homeA"
    home_b = tmp_path / "homeB"
    os.environ["UNIVERSE_HOME"] = str(home_a)
    cfg_a = get_config()
    assert cfg_a.home == home_a
    cfg_a.set("project_root", str(home_a))
    assert json.loads((home_a / "config.json").read_text("utf-8"))[
        "project_root"] == str(home_a)

    # a fixture retargeting the env must retarget the singleton too —
    # "project_root" land in home_b, NOT home_a, NOT the real ~/.universe
    os.environ["UNIVERSE_HOME"] = str(home_b)
    cfg_b = get_config()
    assert cfg_b.home == home_b
    cfg_b.set("project_root", str(home_b))
    assert json.loads((home_b / "config.json").read_text("utf-8"))[
        "project_root"] == str(home_b)
    assert json.loads((home_a / "config.json").read_text("utf-8"))[
        "project_root"] == str(home_a)


def test_attachments_inbox_is_always_in_scope(gate_env, monkeypatch):
    """Pasted-image attachments live under %TMP%/universe-attachments
    (electron main.ts universe:paste-clipboard-media). They are user-attached
    content — reading them must pass the resource gate even though the
    inbox is outside the project root (2026-10-25 live failure: every
    'Read <tmp>/universe-attachments/clipboard-*.png' bounced)."""
    import tempfile

    from core.agent.base_agent import Tool
    from security.capability import CapabilityGate

    inbox = Path(tempfile.gettempdir()) / "universe-attachments" / "clip.png"
    monkeypatch.setattr("security.capability._attachments_inbox_roots",
                        lambda: [inbox.parent])

    async def handler(path=None, url=""):
        return {"ok": True}

    # declared-resource tool scoped to the project: the inbox is allowed
    tool = Tool(name="t", capability="Read", description="",
                handler=handler, resources=["cwd"])
    gate = CapabilityGate()
    assert gate._resource_allowed(
        tool, {"path": str(gate_env / "ok.png")}) is True
    assert gate._resource_allowed(
        tool, {"path": str(inbox)}) is True
    # a sibling directory in temp is NOT the inbox — still denied
    assert gate._resource_allowed(
        tool, {"path": str(inbox.parent.parent / "other" / "x.png")}) is False
    # and a tool with NO declared resources can still read the inbox
    bare = Tool(name="b", capability="Read", description="",
                handler=handler, resources=[])
    assert gate._resource_allowed(bare, {"path": str(inbox)}) is True
    assert gate._resource_allowed(
        bare, {"path": str(gate_env / "a.csv")}) is False


def test_file_urls_never_match_url_resources(gate_env):
    from security.capability import _url_match
    assert _url_match("file:///C:/Users/x/.ssh/id_rsa", "file://*") is False
    assert _url_match("file:///etc/passwd", "*") is False


def test_repo_and_webhook_url_args_are_now_scope_checked(gate_env):
    from core.agent.base_agent import Tool
    from security.capability import CapabilityGate

    def git_handler(repo=".", cwd="", path=""):
        return {"ok": True}

    # git's `repo` IS a filesystem path — declared via path_args. (An
    # ambiguous name alone is NOT a path: GitHub's owner/repo `repo`
    # string must not be scope-checked as one.)
    tool = Tool(name="t", capability="git.read", description="",
                handler=git_handler, resources=["cwd"],
                path_args=["repo", "cwd", "path"])
    gate = CapabilityGate()
    assert gate._resource_allowed(tool, {"repo": str(gate_env)}) is True
    assert gate._resource_allowed(tool, {"repo": "C:/Windows/System32"}) is False

    # and a tool that does NOT declare repo as a path passes it through —
    # the thirdparty regression that motivated explicit declarations
    def github_handler(owner="", repo="", title=""):
        return {"ok": True}

    gh_tool = Tool(name="gh", capability="network.req", description="",
                   handler=github_handler, resources=["url:https://api.github.com/*"])
    assert gate._resource_allowed(
        gh_tool, {"owner": "o", "repo": "r", "title": "t"}) is True

    def discord_handler(content="", webhook_url=""):
        return {"ok": True}

    tool2 = Tool(name="d", capability="network.req", description="",
                handler=discord_handler,
                resources=["url:https://discord.com/*"])
    assert gate._resource_allowed(tool2, {"webhook_url": "https://discord.com/api/x"}) is True
    assert gate._resource_allowed(tool2, {"webhook_url": "https://evil.example"}) is False


# ---- 5. audit hash chain ------------------------------------------------------

def test_audit_hash_chain_detects_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVERSE_HOME", str(tmp_path))
    from config import get_config
    get_config().set("project_root", str(tmp_path))
    from security import audit
    audit._reset_chain_cache_for_tests()
    audit.log(actor="t", action="a1")
    audit.log(actor="t", action="a2")
    assert audit.verify_chain()["ok"] is True
    # rewrite one line in place -> chain must break
    cfg = get_config()
    lines = cfg.audit_log.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["args"] = {"tampered": True}
    lines[0] = json.dumps(entry, ensure_ascii=False)
    cfg.audit_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = audit.verify_chain()
    assert result["ok"] is False
    assert result["first_bad_line"] >= 1


# ---- 6. LLM-bound redaction + cap ---------------------------------------------

def test_tool_result_redacted_and_capped_for_llm_context():
    from context.compaction import compact_tool_output
    from security.secret_filter import redact_dict
    secret = {"ok": True, "content": "token=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"}
    clean = redact_dict(secret)
    assert "sk-ABCDEF" not in json.dumps(clean)
    big = "x" * 20_000
    capped = compact_tool_output(big, max_chars=8000)
    assert len(capped) < 9000


# ---- 7. cancel-before-planet stays 'cancelled' ---------------------------------

@pytest.mark.asyncio
async def test_cancel_before_planet_records_cancelled_not_failed(tmp_path,
                                                                 monkeypatch):
    """A cancel landing during classify (planet still None) used to hit
    planet.id inside the CancelledError handler, raise AttributeError FROM
    the except block, and swallow the cancellation — the goal read
    'failed: NoneType has no attribute id' instead of 'cancelled'."""
    monkeypatch.setenv("UNIVERSE_HOME", str(tmp_path))
    from config import get_config
    get_config().set("project_root", str(tmp_path))
    import asyncio
    from core.agent.orchestrator import Orchestrator

    class CancelledLLM:
        async def complete(self, **kwargs):
            await asyncio.sleep(3600)  # cancelled from outside

    orch = Orchestrator(llm_client=CancelledLLM())
    task = asyncio.get_running_loop().create_task(
        orch.run_turn("will be cancelled", turn_id="goal-cancel-test"))
    await asyncio.sleep(0.05)  # let classify() reach its await
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    from storage.local import get_storage
    row = get_storage().query_one(
        "SELECT status FROM goals WHERE id='goal-cancel-test';")
    assert row is not None
    assert row["status"] == "cancelled"
    # the checkpoint written by the handler must not have crashed
    from failure.checkpoint import load_checkpoint
    cp = load_checkpoint("goal-cancel-test")
    assert cp is not None and cp.get("status") == "cancelled"
    # agent-turn model: the planet is created at TURN START (before the
    # first LLM await), so a cancel may legitimately carry its id — but it
    # must be a planet id, never a goal id, and the handler must not crash
    pid = str(cp.get("planet_id", "") or "")
    assert pid == "" or pid.startswith("planet-"), f"honest planet id, got {pid!r}"
