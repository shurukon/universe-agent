"""core/session/store.py — ZCode file-backed SessionStore."""
from __future__ import annotations
import json, threading, time, uuid
from pathlib import Path
from typing import Any
from config import get_config
from storage.local import get_storage

# per-transcript append locks + cached seq counters (audit L4: counting
# lines by re-reading the whole file was O(n) per append AND racy under
# concurrent appends — duplicate seq numbers). One lock + one counter per
# file; a miss falls back to a single count-then-cache.
_seq_locks: dict[str, threading.Lock] = {}
_seq_counts: dict[str, int] = {}

def _seq_lock(path: Path) -> threading.Lock:
    return _seq_locks.setdefault(str(path), threading.Lock())

def _count_entries(text: str) -> int:
    return sum(1 for l in text.splitlines() if l.strip())

def new_session_id() -> str:
    return "sess_" + uuid.uuid4().hex[:16]
def _sessions_dir() -> Path:
    return get_config().home / "sessions"
class SessionStore:
    def create(self, *, workspace_root: str = "", title: str = "New session", id: str | None = None) -> dict[str, Any]:
        sid = id or new_session_id(); now = time.time()
        st = get_storage()
        with st.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO sessions(id, workspace_root, title, created_at, updated_at) VALUES(?,?,?,?,?);", (sid, workspace_root or str(get_config().get("project_root","") or ""), title, now, now))
        sdir = _sessions_dir() / sid; sdir.mkdir(parents=True, exist_ok=True)
        tf = sdir / "transcript.jsonl"
        if not tf.exists():
            tf.write_text("", encoding="utf-8")
            _seq_counts[str(tf)] = 0
        (sdir / "meta.json").write_text(json.dumps({"id": sid, "title": title, "created_at": now}, indent=2), encoding="utf-8")
        return {"id": sid, "title": title, "created_at": now, "workspace_root": workspace_root}
    def get(self, session_id: str) -> dict[str, Any] | None:
        st = get_storage()
        row = st.query_one("SELECT * FROM sessions WHERE id=?;", (session_id,))
        if row: return dict(row)
        meta = _sessions_dir() / session_id / "meta.json"
        if meta.exists():
            try: return json.loads(meta.read_text(encoding="utf-8"))
            except Exception: pass
        return None
    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        st = get_storage()
        try:
            rows = st.query_all("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?;", (limit,))
            return [dict(r) for r in rows]
        except Exception: return []
    def append(self, session_id: str, entry: dict[str, Any]) -> int:
        sdir = _sessions_dir() / session_id; sdir.mkdir(parents=True, exist_ok=True)
        tfile = sdir / "transcript.jsonl"
        with _seq_lock(tfile):
            key = str(tfile)
            seq = _seq_counts.get(key)
            if seq is None:
                if tfile.exists():
                    try: seq = _count_entries(tfile.read_text(encoding="utf-8"))
                    except Exception: seq = 0
                else:
                    seq = 0
            seq += 1
            _seq_counts[key] = seq
            entry = {**entry, "seq": seq, "ts": entry.get("ts") or time.time()}
            with tfile.open("a", encoding="utf-8") as fh: fh.write(json.dumps(entry, ensure_ascii=False) + chr(10))
        try:
            st = get_storage()
            with st.transaction() as conn: conn.execute("UPDATE sessions SET updated_at=? WHERE id=?;", (time.time(), session_id))
        except Exception: pass
        return seq
    def transcript(self, session_id: str, *, after_seq: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        tfile = _sessions_dir() / session_id / "transcript.jsonl"
        if not tfile.exists(): return []
        out: list[dict[str, Any]] = []
        for line in tfile.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try: obj = json.loads(line)
            except Exception: continue
            if obj.get("seq", 0) > after_seq:
                out.append(obj)
                if limit and len(out) >= limit: break
        return out
    def history_messages(self, session_id: str, *, exclude_seq: int | None = None) -> list[dict[str, Any]]:
        entries = self.transcript(session_id); msgs: list[dict[str, Any]] = []
        # compact-aware replay: entries BEFORE the last compaction marker
        # are already summarized by that marker — replaying them verbatim
        # defeats the compaction (the summary message is the replacement)
        cut = None
        for i, e in enumerate(entries):
            if e.get("kind") == "synthetic" and "preCompactTokenCount" in e:
                cut = i
        if cut is not None:
            entries = entries[cut:]
        # replay pairing guard: a role:tool entry without its preceding
        # assistant tool_calls message is not API-replayable — skip it
        seen_call_ids: set[str] = set()
        for e in entries:
            if exclude_seq and e.get("seq") == exclude_seq: continue
            kind = e.get("kind", "message"); role = e.get("role", "user")
            if role == "tool":
                if str(e.get("tool_call_id", "")) not in seen_call_ids:
                    continue
                msgs.append({"role": "tool", "tool_call_id": e.get("tool_call_id",""), "content": e.get("content","")})
            elif role == "assistant" and e.get("tool_calls"):
                for _tc in e.get("tool_calls") or []:
                    try: seen_call_ids.add(str((_tc or {}).get("id", "")))
                    except Exception: pass
                msgs.append({"role": "assistant", "content": e.get("content") or None, "tool_calls": e["tool_calls"]})
            elif kind == "synthetic": msgs.append({"role": "system", "content": e.get("content",""), "kind": "synthetic", "preCompactTokenCount": e.get("preCompactTokenCount"), "postCompactTokenCount": e.get("postCompactTokenCount")})
            else: msgs.append({"role": str(role), "content": str(e.get("content",""))})
        return msgs
    def fork(self, source_id: str) -> dict[str, Any] | None:
        src = self.get(source_id)
        if not src: return None
        new = self.create(workspace_root=src.get("workspace_root",""), title=(src.get("title","") or "Fork") + " (fork)")
        src_file = _sessions_dir() / source_id / "transcript.jsonl"; dst_file = _sessions_dir() / new["id"] / "transcript.jsonl"
        if src_file.exists():
            data = src_file.read_text(encoding="utf-8")
            dst_file.write_text(data, encoding="utf-8")
            _seq_counts[str(dst_file)] = _count_entries(data)  # O(1) appends after the copy
        # record lineage: the fork is a new session whose transcript is a
        # copy — resume/replay semantics stay identical (Claude Code / Codex fork)
        new["parent_id"] = source_id
        try:
            mp = _sessions_dir() / new["id"] / "meta.json"
            d = json.loads(mp.read_text(encoding="utf-8")); d["parent_id"] = source_id
            mp.write_text(json.dumps(d, indent=2), encoding="utf-8")
        except Exception: pass
        return new
    def rewind(self, session_id: str, target_seq: int) -> bool:
        tfile = _sessions_dir() / session_id / "transcript.jsonl"
        if not tfile.exists(): return False
        lines = [l for l in tfile.read_text(encoding="utf-8").splitlines() if l.strip()]; kept: list[str] = []
        for l in lines:
            try:
                if json.loads(l).get("seq", 0) <= target_seq: kept.append(l)
            except Exception: kept.append(l)
        if len(kept) == len(lines): return False
        tfile.write_text(chr(10).join(kept) + (chr(10) if kept else ""), encoding="utf-8")
        _seq_counts.pop(str(tfile), None)  # invalidate the cached counter
        try:
            st = get_storage()
            with st.transaction() as conn: conn.execute("UPDATE sessions SET updated_at=? WHERE id=?;", (time.time(), session_id))
        except Exception: pass
        return True
    def compact_separator(self, session_id: str, pre_count: int, post_count: int, summary: str = "") -> int:
        return self.append(session_id, {"role": "system", "kind": "synthetic", "content": summary or f"[context compacted: {pre_count} -> {post_count} tokens]", "preCompactTokenCount": pre_count, "postCompactTokenCount": post_count})
    def set_title(self, session_id: str, title: str) -> None:
        try:
            st = get_storage()
            with st.transaction() as conn: conn.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?;", (title[:200], time.time(), session_id))
        except Exception: pass
        meta = _sessions_dir() / session_id / "meta.json"
        if meta.exists():
            try: d=json.loads(meta.read_text(encoding="utf-8")); d["title"]=title[:200]; meta.write_text(json.dumps(d, indent=2), encoding="utf-8")
            except Exception: pass
_store: SessionStore | None = None
def get_session_store() -> SessionStore:
    global _store
    if _store is None: _store = SessionStore()
    return _store
