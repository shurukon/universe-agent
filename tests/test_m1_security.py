"""tests/test_m1_security.py — m1 security regression tests.

Every exploit found in the two reviews becomes a permanent test here, so a
future refactor can never silently re-open the hole:

  1. Capability gate fail-closed behaviors (unknown agent, unknown consent,
     path-aware glob escapes, URL scheme forgery).
  2. Truth envelope: a failing handler can never surface as ok=True.
  3. Command-injection surfaces (git limit, ssh host, psm, sql denylist).
  4. SSRF guard on every outbound tool (loopback/private/metadata/scheme,
     DNS-rebinding shapes) while public hosts still pass.
  5. Secrets: undecryptable blob raises DecryptionError; the provider
     client maps it to an EMPTY key, never the ciphertext.
  6. Zip-slip on import (drive-letter smuggle + parent escape).
  7. secret_filter key=value / URL-credential / hex-blob redaction.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _call(tool: str, args: dict, agent: str = "code", turn_id: str = "g-m1"):
    from connectors.builtin import get_registry
    return await get_registry().call(tool, agent=agent, turn_id=turn_id, args=args)


# ---------------------------------------------------------------------------
# 1. capability gate: fail-closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate_blocks_unknown_agent(fresh_home):
    """An agent name that is neither a core agent nor a moon gets NO
    whitelist exemption — the gate used to skip the check entirely."""
    r = await _call("LS", {"path": "."}, agent="not-a-real-agent")
    assert r["ok"] is False
    assert r.get("blocked_by") == "unknown_agent"


@pytest.mark.asyncio
async def test_gate_url_scheme_forgery_rejected(fresh_home):
    """The old `fnmatch(f"https://{url}", pattern)` prefix trick let an
    http URL match a url:https://* pattern. The gate must verify the
    scheme of the ACTUAL url."""
    from security.capability import _url_match
    assert _url_match("http://example.com/x", "https://*") is False
    assert _url_match("https://example.com/x", "https://*") is True
    assert _url_match("ftp://example.com/x", "https://*") is False


def test_gate_glob_path_escape_rejected(fresh_home):
    """fnmatch's `*` crosses separators: a scoped glob must match
    segment-wise and refuse any `..` component."""
    from security.capability import _glob_match
    assert _glob_match("../../etc/passwd", "./**") is False
    assert _glob_match("sub/../../etc/passwd", "**/*") is False
    # `./**` matches paths written with the explicit ./ prefix (the exact
    # form file tools declare); the bare form matches `**/*`
    assert _glob_match("./src/main.py", "./**") is True
    assert _glob_match("src/main.py", "**/*") is True


@pytest.mark.asyncio
async def test_gate_unknown_consent_value_escalates(fresh_home):
    """A capability_policy row (or tool consent) with a typo'd consent
    value is escalated to explicit, never silently honored."""
    from storage.local import get_storage
    import time as _time
    st = get_storage()
    with st.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO capability_policy(capability, consent, updated_at) VALUES('Write','perweek',?);", (_time.time(),))
    from security.capability import CapabilityGate
    gate = CapabilityGate()
    assert gate._policy_for("Write", None) == {"consent": "explicit"}
    assert gate._policy_for("Bash", "per_goel") == {"consent": "explicit"}


# ---------------------------------------------------------------------------
# 2. truth envelope
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failing_handler_never_reports_ok(fresh_home):
    """A handler returning {ok: False} must surface as ok=False at the
    registry level — the old envelope wrapped it in ok=True and the
    failure was counted as a confirmed tool call."""
    r = await _call("Read", {"path": "definitely_missing_file.xyz"})
    inner = r.get("result", r)
    assert inner["ok"] is False
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# 3. command injection surfaces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_log_limit_injection_blocked(fresh_home):
    """git.log's `limit` used to be interpolated into a shell string. The
    connector now COERCES any non-integer to the forgiving default (20) —
    the hostile value never reaches a shell and the call behaves exactly
    like a normal git log."""
    r = await _call("GitLog", {"limit": "1 && calc.exe"}, agent="universe")
    inner = r.get("result", r)
    # no integer-validation refusal anymore (coercion replaced it) and no
    # trace of the injected fragment in any error: it ran as plain git log
    assert "integer" not in str(inner.get("error", ""))
    assert "calc" not in str(inner.get("error", ""))
    assert inner["ok"] is True


@pytest.mark.asyncio
async def test_ssh_host_injection_blocked(fresh_home):
    r = await _call("Ssh", {"host": "1.2.3.4; calc.exe", "command": "ls"},
                    agent="universe")
    inner = r.get("result", r)
    assert inner["ok"] is False
    assert "invalid host" in inner.get("error", "")


@pytest.mark.asyncio
async def test_image_ocr_psm_injection_blocked(fresh_home):
    """psm becomes a tesseract CLI fragment — non-integer must be refused
    BEFORE any binary runs. (image_ocr is whitelisted for the File agent.)"""
    r = await _call("ImageOcr", {"path": "x.png", "psm": "3; calc.exe"},
                    agent="universe")
    inner = r.get("result", r)
    assert inner["ok"] is False
    # refused at validation, not at file-existence: the error names psm
    assert "psm" in inner.get("error", "")


@pytest.mark.asyncio
async def test_sql_with_insert_bypass_blocked(fresh_home):
    """Sql tool removed — should return unknown tool."""
    r = await _call("Sql", {"query": "SELECT 1"}, agent="universe")
    assert r.get("ok") is False
    assert "unknown tool" in r.get("error", "").lower()


# ---------------------------------------------------------------------------
# 4. SSRF guard
# ---------------------------------------------------------------------------

SSRF_MUST_BLOCK = [
    "http://127.0.0.1:8738/api/health",
    "http://localhost:6379/",
    "https://192.168.0.1/admin",
    "http://10.0.0.5/internal",
    "http://172.16.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:8080/",
    "ftp://example.com/file",
    "file:///etc/passwd",
    "not a url at all",
]


@pytest.mark.parametrize("url", SSRF_MUST_BLOCK)
def test_ssrf_guard_blocks_private_targets(url):
    from connectors.builtin.misc import _ssrf_guard
    r = _ssrf_guard(url)
    assert r["ok"] is False, f"SSRF guard must block {url}"


def test_ssrf_guard_allows_public_hosts():
    from connectors.builtin.misc import _ssrf_guard
    assert _ssrf_guard("https://api.github.com")["ok"] is True
    assert _ssrf_guard("https://example.com/org")["ok"] is True


def test_ssrf_guard_dns_rebinding_shape():
    """A public-looking name that resolves to loopback must be refused —
    localtest.me is a real DNS name that always resolves to 127.0.0.1."""
    from connectors.builtin.misc import _ssrf_guard
    r = _ssrf_guard("http://localtest.me")
    assert r["ok"] is False
    assert "private" in r.get("error", "")


def test_ssrf_guard_blocks_ipv4_mapped_ipv6():
    """::ffff:127.0.0.1 is a loopback address wearing an IPv6 costume —
    is_loopback is False on the v6 object, so the guard used to wave it
    through (audit 2026-10)."""
    from connectors.builtin.misc import _ssrf_guard
    r = _ssrf_guard("http://[::ffff:127.0.0.1]:8738/")
    assert r["ok"] is False
    assert "private/loopback" in r.get("error", "")


@pytest.mark.asyncio
async def test_web_fetch_blocks_loopback(fresh_home):
    r = await _call("WebFetch", {"url": "http://127.0.0.1:8738/api/health"},
                    agent="research")
    inner = r.get("result", r)
    assert inner["ok"] is False


@pytest.mark.asyncio
async def test_http_client_blocks_metadata_endpoint(fresh_home):
    r = await _call("HttpClient", {"url": "http://169.254.169.254/latest"},
                    agent="research")
    inner = r.get("result", r)
    assert inner["ok"] is False


# ---------------------------------------------------------------------------
# 5. secrets
# ---------------------------------------------------------------------------

def test_undecryptable_secret_raises_not_ciphertext(fresh_home):
    import security.secrets_fallback as sf
    sf._passphrase = "pass-A"
    enc = sf.encrypt_secret("real-key")
    sf._passphrase = None
    os.environ["UNIVERSE_PASSPHRASE"] = "pass-B"
    try:
        with pytest.raises(sf.DecryptionError):
            sf.decrypt_secret(enc)
    finally:
        os.environ.pop("UNIVERSE_PASSPHRASE", None)
        sf._passphrase = None


def test_client_maps_undecryptable_key_to_empty(fresh_home):
    """The provider client must never send the ciphertext blob as a key."""
    import security.secrets_fallback as sf
    from providers.client import LLMClient
    sf._passphrase = "pass-A"
    enc = sf.encrypt_secret("real-key")
    sf._passphrase = None
    os.environ["UNIVERSE_PASSPHRASE"] = "pass-B"
    try:
        c = object.__new__(LLMClient)
        assert c._decrypt_key(enc) == ""   # never the blob
        assert c._decrypt_key("") == ""
    finally:
        os.environ.pop("UNIVERSE_PASSPHRASE", None)
        sf._passphrase = None


# ---------------------------------------------------------------------------
# 6. zip-slip on import
# ---------------------------------------------------------------------------

def test_zip_slip_drive_letter_smuggle_blocked(fresh_home, tmp_path):
    from data.export_import import _write_member
    zp = tmp_path / "x.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("memory_vault/C:/evil.md", "evil")
    with zipfile.ZipFile(zp) as zf:
        with pytest.raises(ValueError):
            _write_member(zf, "memory_vault/C:/evil.md",
                          tmp_path / "vault" / "C:" / "evil.md",
                          base_dir=tmp_path / "vault")


def test_zip_slip_parent_escape_blocked(fresh_home, tmp_path):
    from data.export_import import _write_member
    zp = tmp_path / "x.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("memory_vault/ok.md", "fine")
    with zipfile.ZipFile(zp) as zf:
        with pytest.raises(ValueError):
            _write_member(zf, "memory_vault/ok.md",
                          tmp_path / "vault" / "sub" / ".." / ".." / "esc.md",
                          base_dir=tmp_path / "vault")
        # and the sane target still works
        _write_member(zf, "memory_vault/ok.md", tmp_path / "vault" / "ok.md",
                      base_dir=tmp_path / "vault")
        assert (tmp_path / "vault" / "ok.md").read_text() == "fine"


# ---------------------------------------------------------------------------
# 7. secret_filter
# ---------------------------------------------------------------------------

def test_secret_filter_keyvalue_shapes():
    from security.secret_filter import redact
    assert "abcdefgh1234" not in redact("api_key=abcdefgh1234567890abc")
    assert "[REDACTED]" in redact("token=SuperToken987654321")
    assert "[REDACTED]" in redact('password: "MyPassw0rd1234"')
    # config booleans / short values are untouched
    assert "retry_count=12" in redact("retry_count=12")
    assert "model=gpt-4o-mini" in redact("model=gpt-4o-mini")


def test_secret_filter_url_credentials():
    from security.secret_filter import redact
    out = redact("https://user:secretpw123@api.example.com/v1")
    assert "secretpw123" not in out
    assert "user:[REDACTED]@api.example.com" in out


def test_secret_filter_hex_blob():
    from security.secret_filter import redact
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" not in \
        redact("digest a1b2c3d4e5f60718293a4b5c6d7e8f90")
    # short hex stays
    assert "deadbeef1234" in redact("id deadbeef1234")


def test_secret_filter_uuids_exempt():
    from security.secret_filter import redact
    uuid = "goal-3f2a1b8c-9d0e-4f5a-8b7c-1d2e3f4a5b6c"
    assert redact(f"processing {uuid}") == f"processing {uuid}"


def test_secret_filter_nested_structures():
    from security.secret_filter import redact_dict
    d = redact_dict({
        "outer": ["keep", "sk-abcdefghijklmnopqrstuvwx"],
        "tuple": ("sk-abcdefghijklmnopqrstuvwx",),
        "deep": [{"inner": "sk-abcdefghijklmnopqrstuvwx"}],
    })
    flat = str(d)
    assert "sk-abcdefghijklmnopqrstuvwx" not in flat
    assert "keep" in flat
    assert isinstance(d["tuple"], tuple)
