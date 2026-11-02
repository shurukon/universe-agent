"""scripts/migrate_goals_to_sessions.py — P5 legacy migration.

Backfills session transcripts from the legacy goals tables: every
kind='task' goal becomes a session (id = the goal id) whose transcript
replays the task text, its message-turn children (kind='message' rows
whose owner_session_id points at the task) and the final summary reply.

Idempotent: a session that already has transcript entries is skipped, so
the script can run at every startup and never duplicates history.
"""
from __future__ import annotations


def _reply_of(row) -> str:
    import json
    fs = row.get("final_summary")
    if not fs:
        return ""
    try:
        data = json.loads(fs)
    except Exception:
        return ""
    if isinstance(data, dict):
        reply = str(data.get("reply") or "")
        if reply:
            return reply
        steps = data.get("steps") or []
        if isinstance(steps, list):
            return "; ".join(str(s.get("what", "")) for s in steps
                             if isinstance(s, dict)
                             and str(s.get("what", "")).strip())
    return ""


def main(argv=None) -> int:
    from storage.local import get_storage
    from core.session.store import get_session_store
    st = get_storage()
    store = get_session_store()
    tasks = st.query_all(
        "SELECT id, text, final_summary, created_at FROM goals "
        "WHERE kind='task' AND deleted=0 ORDER BY created_at ASC;")
    migrated = 0
    for t in tasks:
        sid = str(t["id"])
        if store.transcript(sid):
            continue                       # idempotent: already backfilled
        try:
            store.create(id=sid, title=str(t.get("text") or "Migrated task")[:120])
        except Exception:
            pass                           # session row may already exist
        store.append(sid, {"kind": "message", "role": "user",
                           "content": str(t.get("text") or "")})
        msgs = st.query_all(
            "SELECT id, text, final_summary, created_at FROM goals "
            "WHERE kind='message' AND owner_session_id=? "
            "ORDER BY created_at ASC;", (sid,))
        for m in msgs:
            store.append(sid, {"kind": "message", "role": "user",
                               "content": str(m.get("text") or "")})
            reply = _reply_of(m)
            if reply:
                store.append(sid, {"kind": "message", "role": "assistant",
                                   "content": reply})
        reply = _reply_of(t)
        if reply:
            store.append(sid, {"kind": "message", "role": "assistant",
                               "content": reply})
        migrated += 1
    print(f"migrate: {migrated} goal(s) backfilled into session transcripts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
