"""core/session/store.py — Universe file-backed SessionStore."""
from __future__ import annotations
import json, time, uuid
from pathlib import Path
from typing import Any
from config import get_config
from storage.local import get_storage
def new_session_id() -> str:
    return "sess_" + uuid.uuid4().hex[:16]
def _sessions_dir() -> Path:
    return get_config().home / "sessions"
class SessionStore:
    def create(self, *, workspace_root: str = "", title: str = "New session", id: str | None = None, parent_id: str | None = None) -> dict[str, Any]:
        sid = id or new_session_id(); now = time.time()
        st = get_storage()
        with st.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO sessions(id, workspace_root, title, created_at, updated_at) VALUES(?,?,?,?,?);", (sid, workspace_root or str(get_config().get("project_root","") or ""), title, now, now))
        sdir = _sessions_dir() / sid; sdir.mkdir(parents=True, exist_ok=True)
        tf = sdir / "transcript.jsonl"
        if not tf.exists():
            tf.write_text("", encoding="utf-8")
        (sdir / "meta.json").write_text(json.dumps({"id": sid, "title": title, "created_at": now, "parent_id": parent_id}, indent=2), encoding="utf-8")
        return {"id": sid, "title": title, "created_at": now, "workspace_root": workspace_root, "parent_id": parent_id}
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
        tfile = sdir / "transcript.jsonl"; seq = 0
        if tfile.exists():
            try: seq = sum(1 for _ in tfile.read_text(encoding="utf-8").splitlines() if _.strip())
            except Exception: seq = 0
        seq += 1
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
        # Compaction semantics (Codex parity): entries BEFORE the last
        # synthetic compaction marker left the replay — the marker's
        # summary IS the prior context now.
        last_marker = -1
        for i, obj in enumerate(out):
            if obj.get("kind") == "synthetic" and "preCompactTokenCount" in obj:
                last_marker = i
        if last_marker > 0:
            out = out[last_marker:]
        return out
    def history_messages(self, session_id: str, *, exclude_seq: int | None = None) -> list[dict[str, Any]]:
        entries = self.transcript(session_id); msgs: list[dict[str, Any]] = []
        for e in entries:
            if exclude_seq and e.get("seq") == exclude_seq: continue
            kind = e.get("kind", "message")
            if kind in ("turn_start", "turn_end", "reflected"): continue  # bookkeeping metadata — never replayed
            role = e.get("role", "user")
            if kind == "synthetic": msgs.append({"role": "system", "content": e.get("content",""), "kind": "synthetic", "preCompactTokenCount": e.get("preCompactTokenCount"), "postCompactTokenCount": e.get("postCompactTokenCount")})
            elif role == "tool": msgs.append({"role": "tool", "tool_call_id": e.get("tool_call_id",""), "content": e.get("content","")})
            elif role == "assistant" and e.get("tool_calls"): msgs.append({"role": "assistant", "content": e.get("content") or None, "tool_calls": e["tool_calls"]})
            else: msgs.append({"role": str(role), "content": str(e.get("content",""))})
        return msgs
    def fork(self, source_id: str) -> dict[str, Any] | None:
        src = self.get(source_id)
        if not src: return None
        new = self.create(workspace_root=src.get("workspace_root",""), title=(src.get("title","") or "Fork") + " (fork)", parent_id=source_id)
        src_file = _sessions_dir() / source_id / "transcript.jsonl"; dst_file = _sessions_dir() / new["id"] / "transcript.jsonl"
        if src_file.exists(): dst_file.write_text(src_file.read_text(encoding="utf-8"), encoding="utf-8")
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
