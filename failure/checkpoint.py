"""Durable goal checkpoints and real resume integration."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from config import get_config


def checkpoint(turn_id: str, *, state: dict[str, Any]) -> Path:
    """Atomically persist the complete resumable goal state."""
    if not turn_id:
        raise ValueError("turn_id is required")
    cfg = get_config()
    cfg.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.checkpoints_dir / f"{turn_id}.json"
    payload = dict(state)
    payload["turn_id"] = turn_id
    payload["checkpointed_at"] = time.time()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{turn_id}.", suffix=".tmp", dir=cfg.checkpoints_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


def load_checkpoint(turn_id: str) -> dict[str, Any] | None:
    cfg = get_config()
    path = cfg.checkpoints_dir / f"{turn_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_checkpoints() -> list[dict[str, Any]]:
    cfg = get_config()
    out: list[dict[str, Any]] = []
    if not cfg.checkpoints_dir.exists():
        return out
    for path in sorted(cfg.checkpoints_dir.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        data = load_checkpoint(path.stem)
        if data:
            out.append({"turn_id": path.stem,
                        "checkpointed_at": data.get("checkpointed_at", 0),
                        "status": data.get("status", "unknown"),
                        "goal_text": str(data.get("goal_text", ""))[:60]})
    return out


async def resume_from(turn_id: str, io) -> str:
    """Resume through the same Orchestrator path used for new goals."""
    cp = load_checkpoint(turn_id)
    if not cp:
        return f"No checkpoint for {turn_id}."
    io.print(f"Resuming goal: {str(cp.get('goal_text', ''))[:60]}")
    io.print(f"  handoffs so far: {len(cp.get('handoffs', []))}")
    io.print(f"  checkpointed at: {cp.get('checkpointed_at')}")
    # `remaining_plan` is never populated in the single-agent architecture —
    # the old empty-plan refusal made every checkpoint permanently
    # unresumable. Resume is a fresh continuation run now (audit 2026-09);
    # only a genuinely completed goal is refused.
    if cp.get("status") == "completed":
        return "Goal was already complete; nothing to resume."
    from core.agent.orchestrator import get_orchestrator
    from security.capability import get_gate
    gate = get_gate()
    async def on_step(event: dict[str, Any]) -> None:
        io.print(f"  [{event.get('phase')}] {event.get('agent', '')} {event.get('step', '')}")
    summary = await get_orchestrator().resume_turn(
        turn_id, on_step=on_step, capability_gate=gate)
    io.print(f"Resumed goal complete: success={summary.get('success')} gravity={summary.get('gravity_score', 0.0):.2f}")
    return f"Resumed {turn_id}: success={summary.get('success')}."
