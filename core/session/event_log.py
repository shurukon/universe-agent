"""core/session/event_log.py — the DURABLE per-session event log.

Why this exists (audit 2026-11-11, user order):

The desktop timeline was rebuilt from two sources: the live bus ring
(`~/.universe/rings/<turn_id>.jsonl`, keyed by TURN) and the `goals` table
(the message children of a task). Both broke the moment the goal engine was
deleted:

* a session (task container) has no ring of its own — only its turns do;
* the message children no longer exist as `goals` rows, so the "merge every
  message ring" path found nothing;
* the fallback rebuilt a SYNTHETIC timeline from the final summary's
  tool_history — the thinking, the plan, the subagent rows and the artifacts
  were simply gone. "The events are lost and do not show."

So the authority is now the SESSION itself:

    <project_root>/sessions/<session_id>/events.jsonl

One append-only JSONL file per session, written in the PROJECT the session
belongs to (the user asked for the sessions to live inside the project),
holding EVERY event the bus ever carried for that session: thinking
deltas, tool calls and their results, plan updates, subagent events,
artifacts, the terminal event — verbatim, in order.

Properties that matter:

* **Durable** — a restart, a crash or a new bridge process replays the same
  stream; nothing is rebuilt, nothing is synthesised.
* **Fast** — one sequential append per event (line buffered), one sequential
  read on reopen. A 10k-event session reads in well under a second, which
  is what the "reopen must feel instant (< 2s)" requirement needs.
* **Session-scoped** — no goal, no task container, no turn table. The
  session id IS the key.

The write path is best-effort and never raises: observability must not
break the work it observes.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# One writer lock per process: the bridge's bus consumer, the CLI and the
# tools can all append to the same session file.
_LOCK = threading.RLock()
# session_id -> open append handle (line buffered)
_HANDLES: dict[str, Any] = {}
# session_id -> the resolved directory (so a read and a write agree)
_DIRS: dict[str, str] = {}
# path -> the last session_seq written (so a restart continues the
# numbering instead of restarting at 1 and confusing an after_seq refill)
_SEQ: dict[str, int] = {}


def _home() -> Path:
    try:
        from config import get_config
        return Path(get_config().home)
    except Exception:
        return Path.home() / ".universe"


def project_root_for(session_id: str = "") -> Path:
    """The directory the session's `sessions/` folder lives in.

    Order (the first that exists wins):
      1. the session's own project root (the `projects` table),
      2. the global project_root from the config,
      3. the Universe home.

    A session therefore lands INSIDE the project the user is working in
    (their explicit request), and never outside the workspace when the
    project root is unknown.
    """
    if session_id:
        try:
            from storage.local import get_storage
            row = get_storage().query_one(
                "SELECT p.root AS root FROM sessions s "
                "LEFT JOIN projects p ON p.id = s.project_id "
                "WHERE s.id=?;", (session_id,))
            if row and row.get("root"):
                return Path(str(row["root"]))
        except Exception:
            pass
    try:
        from config import get_config
        cfg = get_config()
        root = str(cfg.get("project_root", "") or "").strip()
        if root:
            return Path(root)
    except Exception:
        pass
    return _home()


def sessions_dir(session_id: str = "", root: Path | str | None = None) -> Path:
    """`<root>/sessions` — the directory that holds every session folder."""
    base = Path(root) if root else project_root_for(session_id)
    return base / "sessions"


def session_events_path(session_id: str,
                        root: Path | str | None = None) -> Path:
    """`<root>/sessions/<session_id>/events.jsonl`.

    The resolved directory is CACHED per session: the lookup can hit the
    database (the session's project), and this is called for EVERY event —
    an uncached lookup would run a query per event and the append would
    stop being cheap. The cache is keyed by the CONFIGURED project root as
    well, so switching projects (or a test moving the workspace) resolves
    the new directory instead of writing into the old one."""
    if root is not None:
        return Path(root) / "sessions" / str(session_id) / "events.jsonl"
    configured = ""
    try:
        from config import get_config
        configured = str(get_config().get("project_root", "") or "")
    except Exception:
        configured = ""
    key = f"{session_id}\x00{configured}"
    cached = _DIRS.get(key)
    if cached:
        # the cached value IS the sessions directory
        return Path(cached) / str(session_id) / "events.jsonl"
    base = project_root_for(session_id)
    sessions = base / "sessions"
    _DIRS[key] = str(sessions)
    return sessions / str(session_id) / "events.jsonl"


def _handle_for(path: Path):
    fh = _HANDLES.get(str(path))
    if fh is not None:
        try:
            if not fh.closed:
                return fh
        except Exception:
            pass
        _HANDLES.pop(str(path), None)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a", encoding="utf-8", newline="\n")
    _HANDLES[str(path)] = fh
    return fh


def _next_session_seq(path: Path) -> int:
    """The next SESSION-level sequence number for this log.

    The bus stamps a per-TURN seq (the UI dedups on it); the session log
    needs a numbering of its own that spans every turn of the session, so
    an incremental refill (after_seq) can ask for "everything after N" and
    get exactly that. The counter is recovered from the file's last row, so
    a restarted process continues the numbering."""
    key = str(path)
    current = _SEQ.get(key)
    if current is not None:
        return current + 1
    best = 0
    try:
        if path.exists():
            with open(path, "rb") as fh:
                try:
                    fh.seek(-4096, os.SEEK_END)
                except OSError:
                    fh.seek(0)
                tail = fh.read().decode("utf-8", "replace")
            for line in reversed(tail.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                try:
                    best = int(row.get("session_seq") or 0)
                except (TypeError, ValueError):
                    best = 0
                break
    except Exception:
        best = 0
    _SEQ[key] = best
    return best + 1


def append_event(session_id: str, event: dict[str, Any],
                 root: Path | str | None = None) -> bool:
    """Append ONE event to the session's durable log.

    Best-effort: a failure here must never break the turn that produced the
    event. Returns True when the row reached the disk."""
    if not session_id or not isinstance(event, dict):
        return False
    try:
        path = session_events_path(session_id, root)
        row = dict(event)
        row.setdefault("ts", time.time())
        row.setdefault("session_id", session_id)
        line = json.dumps(row, ensure_ascii=False, default=str)
        with _LOCK:
            fh = _handle_for(path)
            row["session_seq"] = _next_session_seq(path)
            _SEQ[str(path)] = int(row["session_seq"])
            line = json.dumps(row, ensure_ascii=False, default=str)
            fh.write(line + "\n")
            fh.flush()          # the event is durable NOW, not at close
        return True
    except Exception:
        return False


def append_events(session_id: str, events: list[dict[str, Any]],
                  root: Path | str | None = None) -> int:
    """Append a batch (one write, one flush) — used when a session is
    back-filled from a live ring."""
    if not session_id or not events:
        return 0
    written = 0
    try:
        path = session_events_path(session_id, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            fh = _handle_for(path)
            for e in events:
                if not isinstance(e, dict):
                    continue
                row = dict(e)
                row.setdefault("ts", time.time())
                row.setdefault("session_id", session_id)
                fh.write(json.dumps(row, ensure_ascii=False,
                                    default=str) + "\n")
                written += 1
            fh.flush()
        return written
    except Exception:
        return written


def read_events(session_id: str, after_seq: int = 0,
                root: Path | str | None = None,
                limit: int | None = None) -> list[dict[str, Any]]:
    """Read the session's durable events, in order.

    `after_seq` filters on the SESSION-level counter (`session_seq`), so
    "give me everything after N" is exact across turns. A row without a
    `session_seq` (written by an older build) is kept when after_seq == 0
    and skipped otherwise — its position in the file is still its order.

    A truncated last line (a crash mid-write) is skipped, not fatal."""
    if not session_id:
        return []
    try:
        path = session_events_path(session_id, root)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue        # a partial tail line: ignore it
                if not isinstance(e, dict):
                    continue
                if after_seq:
                    try:
                        sseq = int(e.get("session_seq") or 0)
                    except (TypeError, ValueError):
                        sseq = 0
                    if sseq <= after_seq:
                        continue
                out.append(e)
                if limit and len(out) >= limit:
                    break
        return out
    except Exception:
        return []


def latest_seq(session_id: str, root: Path | str | None = None) -> int:
    """The highest seq stored for the session (0 when empty)."""
    events = read_events(session_id, 0, root)
    best = 0
    for e in events:
        try:
            seq = int(e.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if seq > best:
            best = seq
    return best


def event_count(session_id: str, root: Path | str | None = None) -> int:
    try:
        path = session_events_path(session_id, root)
        if not path.exists():
            return 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for line in fh if line.strip())
    except Exception:
        return 0


def close_handles() -> None:
    """Close every open append handle (shutdown, tests)."""
    with _LOCK:
        for fh in list(_HANDLES.values()):
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
        _HANDLES.clear()
        _SEQ.clear()
        _DIRS.clear()


def list_sessions(root: Path | str | None = None) -> list[str]:
    """Every session id that has a durable log under `<root>/sessions`."""
    try:
        base = sessions_dir("", root)
        if not base.exists():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())
    except Exception:
        return []
