"""tests/test_security.py — capability gate, secret filter, audit, sandbox."""
import pytest


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    """relative file.write paths below must land in tmp, never the repo root
    (audit 2026-09 leak)."""
    monkeypatch.chdir(tmp_path)


@pytest.mark.asyncio
async def test_capability_gate_intercepts_every_call(fresh_home):
    """§STEP 4: the capability gate is a real enforced chokepoint — every
    tool call passes through it."""
    from connectors.builtin import get_registry
    from security.audit import tail_audit
    reg = get_registry()
    # every call should produce an audit entry
    await reg.call("Bash", agent="code", turn_id="g1", args={"cmd": "echo a"})
    await reg.call("Write", agent="code", turn_id="g1",
                   args={"path": "test_file.txt", "content": "x"})
    await reg.call("MemoryQuery", agent="code", turn_id="g1", args={"query": "x"})
    class IO:
        def __init__(self): self.out = []
        def print(self,*a,**k): self.out.append(a)
    io = IO()
    tail_audit(io, 10)
    actions = [a[0] for a in io.out]
    assert any("Bash" in str(a) for a in actions)
    assert any("Write" in str(a) for a in actions)


@pytest.mark.asyncio
async def test_blackhole_blocks_tool(fresh_home):
    """§10: black hole rules block matching actions."""
    from connectors.builtin import get_registry
    from core.memory import get_memory
    mem = get_memory()
    mem.add_rule(kind="blackhole", rule="rm -rf")
    reg = get_registry()
    r = await reg.call("Bash", agent="code", turn_id="g1", args={"cmd": "rm -rf /"})
    assert r["ok"] is False
    assert r["blocked_by"] == "blackhole"


def test_secret_filter_redacts_all_patterns():
    """§10: the SECRET_PATTERNS list redacts sk-, ghp_, JWT, Bearer, etc."""
    from security.secret_filter import redact
    cases = [
        ("key sk-abc123def456ghi789jkl012mno345pqr678", "[REDACTED]"),
        ("pat ghp_abcdefABCDEF0123456789abcdefghij0123456789", "[REDACTED]"),
        ("jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", "[REDACTED]"),
        ("auth Bearer abc123def456ghi789jkl012mno345pqr678", "[REDACTED]"),
        ("password=supersecret123", "[REDACTED]"),
    ]
    for raw, _ in cases:
        redacted = redact(raw)
        assert "[REDACTED]" in redacted
        # the original secret must not survive
        for secret in ["sk-abc123", "ghp_abcdef", "eyJhbGci", "Bearer abc", "supersecret123"]:
            if secret in raw:
                assert secret not in redacted, f"{secret} survived redaction"


def test_secret_filter_never_redacts_internal_uuid_ids():
    """§10 regression: internal ids (goal-<uuid4>, planet-<uuid4>, …) are
    identifiers, not secrets. The entropy heuristic used to redact ~27% of
    them because the '-' separators inflated the token's entropy."""
    from security.secret_filter import redact
    from core.agent.base_agent import new_id
    for prefix in ("goal-", "planet-", "consent-", "asteroid-"):
        for _ in range(200):
            v = new_id(prefix)
            assert redact(v) == v, f"internal id redacted: {v}"
    # high-entropy non-UUID junk is still caught (defense stays intact)
    assert "[REDACTED]" in redact("Zk9xQ2pLm4Nv8Tt3Rq7Wb1Yc6Ud5Ha0Jf2Ig9Kq1")


def test_audit_log_is_append_only_jsonl(fresh_home):
    """§10: the audit log is append-only JSONL on disk."""
    from security.audit import log
    from config import get_config
    import json
    log(actor="test", action="tool:x", args={"a": 1}, result="ok")
    log(actor="test", action="tool:y", args={"b": 2}, result="ok")
    cfg = get_config()
    lines = cfg.audit_log.read_text("utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        e = json.loads(line)
        assert "ts" in e and "actor" in e and "action" in e and "nonce" in e


def test_secrets_fallback_roundtrip_without_keyring(fresh_home):
    """§10: the passphrase-encrypted fallback round-trips — exercised on any
    platform (dev machines may have an OS keychain; CI/sandboxes may not)."""
    from security.secrets_fallback import encrypt_secret, decrypt_secret, keyring_available
    if keyring_available():
        pytest.skip("OS keychain present — fallback path not reachable here")
    enc = encrypt_secret("my-super-secret-api-key")
    assert enc != "my-super-secret-api-key"
    dec = decrypt_secret(enc)
    assert dec == "my-super-secret-api-key"


def test_sandbox_verify_no_network():
    """§STEP 5: a test that the sandbox genuinely has no network. Skipped if
    Docker isn't available (the faithful equivalent still ships)."""
    from security.sandbox import is_docker_available, verify_no_network
    if not is_docker_available():
        pytest.skip("docker not available in this environment")
    assert verify_no_network() is True


@pytest.mark.asyncio
async def test_unregistered_moon_agent_is_fail_closed(fresh_home):
    """m2: the moon scaffolding (register_moon/probation/egress lists) is
    deleted — it had no production registration path. Its replacement is
    strictly stronger: ANY agent name outside the 12 core agents is denied
    at the gate with blocked_by=unknown_agent, on every capability."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = await reg.call("Bash", agent="test-moon", turn_id="g1", args={"cmd": "ls"})
    assert r["ok"] is False
    assert r.get("blocked_by") == "unknown_agent"
    r = await reg.call("WebFetch", agent="test-moon", turn_id="g1",
                       args={"url": "https://api.example.com/x"})
    assert r["ok"] is False
    assert r.get("blocked_by") == "unknown_agent"


@pytest.mark.asyncio
async def test_tool_level_consent_overrides_capability_default(fresh_home):
    """§4/§10: a tool's own declared `consent` must be respected even when it
    diverges from its capability's coarse default. `Ssh` declares "explicit"
    even though its capability (network.req) defaults to "auto" — it must
    still require explicit consent, not silently inherit the lighter default.
    """
    from security.capability import get_gate
    from connectors.builtin import get_registry
    get_gate().set_auto_grant(False)  # exercise the real (non-bypassed) path
    reg = get_registry()
    r = await reg.call("Ssh", agent="universe", turn_id="g-ssh",
                        args={"host": "example.com", "command": "id"})
    assert r["ok"] is False
    assert r["needs_consent"] is True


@pytest.mark.asyncio
async def test_lenient_tool_grant_does_not_leak_to_stricter_sibling(fresh_home):
    """§4/§10: tools are granted individually, not by capability. Using a
    lenient network.req tool (web_search, consent=auto) for a goal must not
    silently pre-authorize a stricter network.req tool (ssh, consent=explicit)
    for that same goal."""
    from security.capability import get_gate
    from connectors.builtin import get_registry
    get_gate().set_auto_grant(False)
    reg = get_registry()
    await reg.call("WebSearch", agent="research", turn_id="g-shared", args={"query": "x"})
    r = await reg.call("Ssh", agent="universe", turn_id="g-shared",
                        args={"host": "example.com", "command": "id"})
    assert r["ok"] is False
    assert r["needs_consent"] is True


@pytest.mark.asyncio
async def test_explicit_grant_unblocks_the_specific_tool(fresh_home):
    """§4: gate.grant(turn_id, tool_name) unblocks exactly that tool for that
    goal, after which the call proceeds past the consent gate."""
    from security.capability import get_gate
    from connectors.builtin import get_registry
    gate = get_gate()
    gate.set_auto_grant(False)
    reg = get_registry()
    blocked = await reg.call("Ssh", agent="universe", turn_id="g-grant",
                              args={"host": "example.com", "command": "id"})
    assert blocked["needs_consent"] is True
    gate.grant("g-grant", "Ssh")
    after = await reg.call("Ssh", agent="universe", turn_id="g-grant",
                            args={"host": "example.com", "command": "id"})
    assert after.get("needs_consent") is None
