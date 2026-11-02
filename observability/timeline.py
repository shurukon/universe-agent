"""observability/timeline.py — agent timeline + /trace (§11).

Every agent run produces a timeline, visible live via [S] and replayable via
/trace <turn_id>. Timeline entries are written to ~/.universe/timelines/<turn_id>.jsonl.
"""
from __future__ import annotations

import json
import time

from config import get_config
from security.secret_filter import redact


def record(turn_id: str, *, agent: str, event: str, detail: str = "") -> None:
    """Append a timeline entry for a goal — and mirror it to the live event
    bus so the desktop UI sees the same stream it can later replay here."""
    cfg = get_config()
    cfg.timeline_dir.mkdir(parents=True, exist_ok=True)
    # secrets never reach the JSONL: the timeline used to be the one
    # observability sink that wrote detail verbatim (the event bus redacts,
    # the log redacts — /trace replayed prompts with credentials in them)
    detail = redact(detail)
    entry = {"t": round(time.time(), 3), "agent": agent, "event": event, "detail": detail}
    with open(cfg.timeline_dir / f"{turn_id}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        from observability.events import emit
        # events that already have a RICHER direct bus emission upstream —
        # mirroring them duplicated the stream (chat_reply arrived twice:
        # once with the full reply, once as a thin detail; provider_halt
        # likewise arrived with reason/error AND as a mirror detail)
        if event in ("chat_reply", "provider_halt"):
            return
        # timeline event name -> wire event name (plan §3.3 taxonomy;
        # goal_* names are legacy rows kept for old mirrors)
        _WIRE = {
            "classified": "turn_classified",
            "agent_start": "agent_started",
            "agent_complete": "agent_complete",
            "obstacle_detected": "recovery_added",
            "media_enforcer": "media_enforcer",
            "acceptance_checked": "acceptance_checked",
            "goal_complete": "turn_completed",
            "turn_failed": "turn_failed",
            "turn_complete": "turn_completed",
            "turn_failed": "turn_failed",
        }
        emit(_WIRE.get(event, event), turn_id=turn_id, agent=agent, detail=detail)
    except Exception:
        pass  # observability must never break the observed work


def replay_goal(turn_id: str, io) -> str:
    """/trace <turn_id>: replay the agent timeline."""
    cfg = get_config()
    path = cfg.timeline_dir / f"{turn_id}.jsonl"
    if not path.exists():
        return f"No timeline for {turn_id}."
    lines = path.read_text("utf-8").splitlines()
    if not lines:
        return f"Empty timeline for {turn_id}."
    start_t = json.loads(lines[0])["t"]
    io.print(f"Timeline for {turn_id}:")
    for line in lines:
        try:
            e = json.loads(line)
            elapsed = e["t"] - start_t
            io.print(f"  T+{elapsed:6.1f}s  {e['agent']:14s}  {e['event']}"
                     + (f"  {e['detail']}" if e.get('detail') else ''))
        except Exception:
            continue
    return f"{len(lines)} events."
