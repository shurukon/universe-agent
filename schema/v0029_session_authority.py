"""v0029 — session authority: backfill transcript.jsonl from goals (dual-write start)."""
from __future__ import annotations
import json
from schema.migrations import register
from storage.local import LocalStorage

@register(29, "session authority: backfill sessions transcript from goals")
def upgrade(st: LocalStorage) -> None:
    from config import get_config
    home = get_config().home
    sdir_base = home / "sessions"
    sdir_base.mkdir(parents=True, exist_ok=True)
    for t in st.query_all("SELECT id, title, created_at, project_id FROM goals WHERE kind='task'"):
        prow = st.query_one("SELECT root FROM projects WHERE id=?", (t["project_id"],)) if t.get("project_id") else None
        ws_root = (prow.get("root") if prow else "") or ""
        st.execute("INSERT OR IGNORE INTO sessions(id, workspace_root, title, created_at, updated_at, project_id) VALUES(?,?,?,?,?,?);",
                   (t["id"], ws_root, t["title"] or "New session", t["created_at"], t["created_at"], t.get("project_id") or ""))
        sdir = sdir_base / t["id"]
        sdir.mkdir(parents=True, exist_ok=True)
        tfile = sdir / "transcript.jsonl"
        if tfile.exists() and tfile.stat().st_size > 0:
            continue
        rows = st.query_all("SELECT id, text, created_at, completed_at, final_summary FROM goals WHERE parent_goal_id=? AND kind='message' ORDER BY created_at;", (t["id"],))
        if not rows:
            continue
        tmp = sdir / "transcript.jsonl.tmp"
        seq = 0
        with tmp.open("w", encoding="utf-8") as fh:
            for r in rows:
                seq += 1
                fh.write(json.dumps({"seq": seq, "role": "user", "content": r["text"] or "", "ts": r["created_at"], "kind": "message", "goal_id": r["id"]}, ensure_ascii=False) + "\n")
                try:
                    fs = json.loads(r["final_summary"] or "{}")
                    reply = fs.get("reply") or ""
                except Exception:
                    reply = ""
                if reply:
                    seq += 1
                    fh.write(json.dumps({"seq": seq, "role": "assistant", "content": reply, "ts": r["completed_at"] or r["created_at"], "success": True, "goal_id": r["id"]}, ensure_ascii=False) + "\n")
        try:
            tmp.replace(tfile)
        except Exception:
            pass
        (sdir / "meta.json").write_text(json.dumps({"id": t["id"], "title": t["title"] or "New session", "created_at": t["created_at"]}, ensure_ascii=False), encoding="utf-8")
