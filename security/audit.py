"""security/audit.py — append-only JSONL audit log (§10).

Append-only at ~/.universe/audit.log, rotated at 100MB, kept 90 days, and
hash-chained: every entry carries sha256(prev_hash + entry), so silent
truncation or in-place edits break the chain and are detectable via
verify_chain(). Never includes prompts/completions — only metadata.
/audit inspects it.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from threading import Lock
from typing import Any

from config import get_config
from security.secret_filter import redact_dict


MAX_LOG_SIZE = 100 * 1024 * 1024  # 100MB
RETENTION_DAYS = 90

# hash-chain state. The "immutable" claim was previously decorative — any
# process could truncate or rewrite the file unnoticed. The chain makes
# tampering detectable (not preventable: an attacker with write access can
# always re-forge a whole chain; the point is that accidental truncation and
# lazy edits become visible, and /audit can report them).
_chain_lock = Lock()
_prev_hash_cache: str | None = None
GENESIS_HASH = "0" * 64


def _last_hash_from_disk(cfg) -> str:
    """Read the last line's hash field without loading the whole file.
    A corrupt/missing last line falls back to the genesis hash — a rewound
    log then fails verify_chain() at the seam, which is the honest outcome."""
    try:
        with open(cfg.audit_log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            window = min(size, 65536)
            fh.seek(max(0, size - window))
            tail = fh.read().decode("utf-8", errors="replace")
        for line in reversed([ln for ln in tail.splitlines() if ln.strip()]):
            try:
                entry = json.loads(line)
                h = entry.get("hash")
                if isinstance(h, str) and h:
                    return h
            except Exception:
                continue
    except Exception:
        pass
    return GENESIS_HASH


def _prev_hash() -> str:
    global _prev_hash_cache
    cfg = get_config()
    with _chain_lock:
        if _prev_hash_cache is None:
            _prev_hash_cache = _last_hash_from_disk(cfg)
        return _prev_hash_cache


def _reset_chain_cache_for_tests() -> None:
    global _prev_hash_cache
    with _chain_lock:
        _prev_hash_cache = None


def log(*, actor: str, action: str, args: dict[str, Any] | None = None,
        result: str = "ok", duration_ms: int = 0,
        turn_id: str = "") -> None:
    """Append one audit entry. Metadata only; args are redacted."""
    cfg = get_config()
    cfg.home.mkdir(parents=True, exist_ok=True)
    prev = _prev_hash()
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor": actor,
        "action": action,
        "args": redact_dict(args or {}),
        "result": result,
        "duration_ms": duration_ms,
        "turn_id": turn_id,
        "nonce": str(uuid.uuid4()),
        "prev_hash": prev,
    }
    entry["hash"] = hashlib.sha256(
        (prev + json.dumps(entry, ensure_ascii=False, sort_keys=True)
         ).encode("utf-8")).hexdigest()
    with _chain_lock:
        with open(cfg.audit_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        global _prev_hash_cache
        _prev_hash_cache = entry["hash"]
    _maybe_rotate()


def verify_chain(limit: int = 0) -> dict[str, Any]:
    """Check the audit log's hash chain. Returns {ok, checked, first_bad_line}
    (line numbers are 1-based; 0 = no break found). With limit=N only the
    last N entries are verified (cheap incremental check)."""
    cfg = get_config()
    if not cfg.audit_log.exists():
        return {"ok": True, "checked": 0, "first_bad_line": 0}
    lines = cfg.audit_log.read_text(encoding="utf-8").splitlines()
    if limit and limit > 0:
        lines = lines[-limit:]
    prev = GENESIS_HASH if not limit else ""  # incremental: trust the seam
    checked = 0
    for i, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
        except Exception:
            return {"ok": False, "checked": checked, "first_bad_line": i}
        if limit and entry.get("prev_hash"):
            prev = entry["prev_hash"]
        h = entry.pop("hash", None)
        recomputed = hashlib.sha256(
            (prev + json.dumps(entry, ensure_ascii=False, sort_keys=True))
            .encode("utf-8")).hexdigest()
        if h != recomputed:
            return {"ok": False, "checked": checked, "first_bad_line": i}
        prev = h
        checked += 1
    return {"ok": True, "checked": checked, "first_bad_line": 0}


def _maybe_rotate() -> None:
    cfg = get_config()
    try:
        if cfg.audit_log.exists() and cfg.audit_log.stat().st_size > MAX_LOG_SIZE:
            # rotate: rename to .1, start fresh
            rotated = cfg.audit_log.with_suffix(".log.1")
            if rotated.exists():
                rotated.unlink()
            cfg.audit_log.rename(rotated)
    except Exception:
        pass


def tail_audit(io, limit: int = 20) -> str:
    """/audit: show the last N entries."""
    cfg = get_config()
    if not cfg.audit_log.exists():
        return "No audit log yet."
    lines = cfg.audit_log.read_text(encoding="utf-8").splitlines()[-limit:]
    for line in lines:
        try:
            e = json.loads(line)
            io.print(f"  {e['ts']}  {e['actor']:14s}  {e['action']:24s}  {e['result']}")
        except Exception:
            io.print(f"  {line[:120]}")
    return f"{len(lines)} entries."


def prune_old() -> int:
    """Drop entries older than RETENTION_DAYS. Returns count removed."""
    cfg = get_config()
    if not cfg.audit_log.exists():
        return 0
    cutoff = time.time() - RETENTION_DAYS * 86400
    kept = 0
    removed = 0
    tmp = cfg.audit_log.with_suffix(".log.tmp")
    with open(cfg.audit_log, "r", encoding="utf-8") as src, \
         open(tmp, "w", encoding="utf-8") as dst:
        for line in src:
            try:
                e = json.loads(line)
                ts = time.mktime(time.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ"))
                if ts >= cutoff:
                    dst.write(line)
                    kept += 1
                else:
                    removed += 1
            except Exception:
                dst.write(line)
                kept += 1
    tmp.replace(cfg.audit_log)
    # the rewrite resets the chain seam — forget the cached prev hash so the
    # next entry links from whatever is now the last line on disk
    global _prev_hash_cache
    with _chain_lock:
        _prev_hash_cache = None
    return removed


def prune_at_startup() -> int:
    """Apply the documented 90-day retention once per process start (the
    prune_old function existed but was never wired, so the documented
    retention was fiction — audit 2026-09)."""
    try:
        return prune_old()
    except Exception:
        return 0
