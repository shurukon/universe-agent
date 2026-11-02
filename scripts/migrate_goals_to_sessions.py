#!/usr/bin/env python3
"""migrate_goals_to_sessions.py — complete the v0029 "session authority" move.

Turns legacy goals rows (the goal-era state machine) into session-store
transcripts, so sessions become the single source of truth:

* every goal row with kind='task' (or a task container) becomes a session;
  its kind='message' children (and its own turn text) become transcript
  entries in creation order;
* every orphan kind='message' row (owner_session_id empty) becomes its own
  single-turn session;
* idempotent: a session whose id already exists with a non-empty transcript
  is skipped, so the script can run repeatedly / after partial runs.

Usage:  python scripts/migrate_goals_to_sessions.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.environ.setdefault("HOME", os.path.expanduser("~"))


def main() -> int:
    dry = "--dry-run" in sys.argv
    from storage.local import get_storage
    from core.session.store import get_session_store
    st = get_storage()
    store = get_session_store()

    rows = st.query_all(
        "SELECT id, text, status, created_at, completed_at, final_summary, "
        "owner_session_id, kind, title FROM goals ORDER BY created_at ASC;")
    if not rows:
        print("no goals rows — nothing to migrate")
        return 0

    tasks: dict[str, dict] = {}
    orphans: list[dict] = []
    for r in rows:
        kind = (r.get("kind") or "task")
        if kind == "message" and r.get("owner_session_id"):
            tasks.setdefault(r["owner_session_id"], {"id": r["owner_session_id"],
                            "title": "", "entries": []})["entries"].append(r)
        else:
            (tasks if kind != "message" else orphans)
            if kind != "message":
                tasks.setdefault(r["id"], {"id": r["id"], "title": r.get("title") or "",
                                           "entries": [], "self": r})
                tasks[r["id"]]["entries"].append(r)
            else:
                orphans.append(r)

    def summary_reply(row: dict) -> str:
        try:
            fs = json.loads(row.get("final_summary") or "{}")
            if isinstance(fs, dict):
                return str(fs.get("reply") or "")
        except Exception:
            pass
        return ""

    # workloads: task containers (with their message children) + orphan
    # message rows (each becomes its own single-turn session)
    workloads = [
        {"id": tid, "title": str((b.get("self") or {}).get("title") or "") or "Migrated task",
         "entries": b["entries"]}
        for tid, b in tasks.items()]
    workloads += [
        {"id": "sess_" + o["id"].replace("turn-", "").replace("goal-", "")[:16],
         "title": str(o.get("text", ""))[:40] or "Migrated message",
         "entries": [o]}
        for o in orphans]

    created = skipped = 0
    for bucket in workloads:
        sid = bucket["id"]
        existing = store.transcript(sid)
        if existing:
            skipped += 1
            continue
        if dry:
            print(f"[dry-run] would create {sid} ({len(bucket['entries'])} entries)")
            created += 1
            continue
        meta = store.create(id=sid, title=bucket.get("title") or "Migrated session")
        for r in bucket["entries"]:
            store.append(sid, {"kind": "message", "role": "user",
                               "content": str(r.get("text", "")),
                               "turn_id": r["id"],
                               "migrated_from": "goals"})
            reply = summary_reply(r)
            if reply:
                store.append(sid, {"kind": "message", "role": "assistant",
                                   "content": reply, "turn_id": r["id"],
                                   "migrated_from": "goals"})
        created += 1
        print(f"migrated -> {meta['id']} ({len(bucket['entries'])} entries)")

    print(f"done: {created} session(s) created, {skipped} already present (skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
