"""bots/store.py — JSON-file persistence for bots (config.home/bots.json).

Desktop-scale single-user store: one JSON document, atomic writes
(temp file + os.replace), one RLock. A bot record:
  id, name, description, status, schedule{kind,time,expr,tz}, skill, skills[],
  created_at, last_run_at, last_result, workspace, transcript[], approvals[]
Transcripts are capped (TRANSCRIPT_CAP) so long-running bots can't grow the
file without bound. to_public() strips internal fields and computes
approvals_pending — the exact wire shape the UI contract expects.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from config import get_config

TRANSCRIPT_CAP = 400

_STATUSES = ("idle", "running", "waiting_approval", "error", "paused")


def _bots_file() -> Path:
    return get_config().home / "bots.json"


_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    path = _bots_file()
    if path.exists():
        try:
            _cache = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _cache = None
    if not isinstance(_cache, dict) or "bots" not in _cache:
        _cache = {"bots": []}
    return _cache


def _save() -> None:
    assert _cache is not None
    path = _bots_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(ts))


def list_bots() -> list[dict[str, Any]]:
    with _lock:
        bots = _load()["bots"]
        return [to_public(b) for b in
                sorted(bots, key=lambda b: b.get("created_at", 0))]


def get_bot(bot_id: str) -> dict[str, Any] | None:
    with _lock:
        for b in _load()["bots"]:
            if b["id"] == bot_id:
                return to_public(b, include_transcript=True)
        return None


def _raw(bot_id: str) -> dict[str, Any] | None:
    for b in _load()["bots"]:
        if b["id"] == bot_id:
            return b
    return None


def create_bot(name: str, description: str) -> dict[str, Any]:
    with _lock:
        bot_id = "bot-" + uuid.uuid4().hex[:12]
        workspace = get_config().home / "bot-workspaces" / bot_id
        workspace.mkdir(parents=True, exist_ok=True)
        bot: dict[str, Any] = {
            "id": bot_id,
            "name": name.strip()[:80] or "Untitled bot",
            "description": description.strip()[:2000],
            "status": "idle",
            "schedule": {"kind": "none"},
            "skill": None,
            "skills": [],
            "created_at": _now(),
            "last_run_at": None,
            "last_result": None,
            "workspace": str(workspace),
            "transcript": [],
            "approvals": [],
        }
        _load()["bots"].append(bot)
        _save()
        return to_public(bot, include_transcript=True)


def delete_bot(bot_id: str) -> bool:
    with _lock:
        bots = _load()["bots"]
        keep = [b for b in bots if b["id"] != bot_id]
        if len(keep) == len(bots):
            return False
        _load()["bots"] = keep
        _save()
        return True


def update_bot(bot_id: str, **fields: Any) -> dict[str, Any] | None:
    with _lock:
        bot = _raw(bot_id)
        if bot is None:
            return None
        for k, v in fields.items():
            if k in ("schedule", "skill", "last_result", "status"):
                bot[k] = v
        _save()
        return to_public(bot, include_transcript=True)


def append_event(bot_id: str, role: str, text: str) -> None:
    with _lock:
        bot = _raw(bot_id)
        if bot is None:
            return
        bot["transcript"].append({"ts": _iso(_now()), "role": role,
                                  "text": (text or "")[:6000]})
        if len(bot["transcript"]) > TRANSCRIPT_CAP:
            bot["transcript"] = bot["transcript"][-TRANSCRIPT_CAP:]
        _save()


def set_status(bot_id: str, status: str, result: str | None = None) -> None:
    if status not in _STATUSES:
        raise ValueError(f"bad bot status {status!r}")
    with _lock:
        bot = _raw(bot_id)
        if bot is None:
            return
        bot["status"] = status
        if status == "running":
            bot["last_run_at"] = _now()
        if result is not None:
            bot["last_result"] = result[:500]
        _save()


def add_skill(bot_id: str, name: str, method: str) -> str:
    """Save the bot's current method as a reusable skill (Orbits)."""
    clean = name.strip().lstrip("/").replace(" ", "-")[:60] or "routine"
    skill_name = "/" + clean
    with _lock:
        bot = _raw(bot_id)
        if bot is None:
            raise KeyError(bot_id)
        ws = Path(bot["workspace"])
        skills_dir = ws / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / f"{clean}.md").write_text(method, encoding="utf-8")
        if skill_name not in bot["skills"]:
            bot["skills"].append(skill_name)
        bot["skill"] = skill_name
        _save()
    return skill_name


def add_approval(bot_id: str, kind: str, summary: str, detail: str) -> str:
    with _lock:
        bot = _raw(bot_id)
        if bot is None:
            raise KeyError(bot_id)
        aid = "apr-" + uuid.uuid4().hex[:10]
        bot["approvals"].append({"id": aid, "ts": _iso(_now()), "kind": kind,
                                 "summary": summary[:300], "detail": detail[:2000]})
        if bot["status"] not in ("error",):
            bot["status"] = "waiting_approval"
        _save()
        return aid


def list_approvals(bot_id: str) -> list[dict[str, Any]]:
    with _lock:
        bot = _raw(bot_id)
        if bot is None:
            return []
        return list(bot.get("approvals") or [])


def resolve_approval(bot_id: str, approval_id: str,
                     decision: str) -> dict[str, Any] | None:
    """Remove the approval from the queue. deny records the refusal; the
    queue draining unblocks the bot (status returns to idle)."""
    with _lock:
        bot = _raw(bot_id)
        if bot is None:
            return None
        before = len(bot["approvals"])
        bot["approvals"] = [a for a in bot["approvals"] if a["id"] != approval_id]
        removed = len(bot["approvals"]) < before
        if removed:
            if bot["status"] == "waiting_approval":
                bot["status"] = "idle"
            if decision == "deny":
                bot["last_result"] = "a step was denied by the user"
            # decision is recorded in the transcript by the caller (route)
        _save()
        return {"ok": removed}


def shared_dir() -> Path:
    """The bots' SHARED COMPUTER (Grok-Bot parity): one directory every bot
    can read and write — the files substrate bots collaborate through (one
    bot writes a file, another reads it)."""
    d = get_config().home / "bot-shared"
    d.mkdir(parents=True, exist_ok=True)
    return d


def shared_files() -> list[dict[str, Any]]:
    root = shared_dir()
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                st = p.stat()
                out.append({"name": str(p.relative_to(root)),
                            "size": st.st_size,
                            "mtime": _iso(st.st_mtime)})
            except OSError:
                continue
    return out[:200]


def to_public(bot: dict[str, Any], *, include_transcript: bool = False) -> dict[str, Any]:
    out = {
        "id": bot["id"],
        "name": bot["name"],
        "description": bot["description"],
        "status": bot.get("status", "idle"),
        "schedule": bot.get("schedule") or {"kind": "none"},
        "skill": bot.get("skill"),
        "skills": list(bot.get("skills") or []),
        "created_at": _iso(bot.get("created_at", 0)),
        "last_run_at": _iso(bot.get("last_run_at")),
        "last_result": bot.get("last_result"),
        "approvals_pending": len(bot.get("approvals") or []),
        "workspace": bot.get("workspace", ""),
    }
    if include_transcript:
        out["transcript"] = list(bot.get("transcript") or [])
    return out


def reset_for_tests() -> None:
    global _cache
    with _lock:
        _cache = {"bots": []}
        _save()
