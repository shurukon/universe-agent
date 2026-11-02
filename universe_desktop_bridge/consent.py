"""universe_desktop_bridge/consent.py — interactive consent for the desktop.

The CapabilityGate asks one question: "may this tool run?" (security/
capability.py:_ask_consent). The CLI answers via a terminal prompt; the
desktop answers via this manager:

  gate handler  ->  pending request created, consent_required event emitted
  UI modal      ->  POST /api/consent/{id} {approve}
  timeout 60s   ->  auto-deny (never blocks the goal forever)

Every decision is emitted on the bus (consent_decided) so the UI can
dismiss its modal even if the HTTP response raced.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from observability.events import emit
from security.secret_filter import redact_dict

CONSENT_TIMEOUT_S = 60.0


def _summarize(args: dict[str, Any]) -> str:
    """One-line, secret-free summary of what the tool wants to do."""
    try:
        clean = redact_dict(args)
        parts = [str(clean.get(k)) for k in ("path", "cmd", "url", "skill_id", "query")
                 if clean.get(k)]
        if not parts:
            parts = [f"{k}={v}" for k, v in list(clean.items())[:4]
                     if isinstance(v, (str, int, float, bool))]
        return " · ".join(parts)[:300]
    except Exception:
        return "unavailable"


def _full_args(args: dict[str, Any], cap: int = 4000) -> str:
    """Complete secret-free JSON of the arguments. The 300-char summary was
    rubber-stamp material: a docker.build whose `image` carried injected
    shell text looked identical to a clean one. The user approves the FULL
    argument vector, not a preview of it (audit 2026-09)."""
    import json
    try:
        return json.dumps(redact_dict(args), ensure_ascii=False, default=str)[:cap]
    except Exception:
        return "unavailable"


class ConsentManager:
    """Pending consent requests, resolved by the UI or auto-denied."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._n = 0

    # -- gate side ----------------------------------------------------------

    async def handler(self, tool: Any, agent: str, turn_id: str,
                      args: dict[str, Any]) -> bool:
        """Installed via gate.set_consent_handler() at bridge startup."""
        pending_id = f"consent-{int(time.time() * 1000)}-{self._n}"
        self._n += 1
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[pending_id] = fut
        self._meta[pending_id] = {
            "tool": getattr(tool, "name", str(tool)),
            "capability": getattr(tool, "capability", ""),
            "agent": agent,
            "turn_id": turn_id,
            "args_summary": _summarize(args),
            "args_full": _full_args(args),
            "created_at": round(time.time(), 3),
        }
        emit("consent_required", turn_id=turn_id or None, pending_id=pending_id,
             tool=self._meta[pending_id]["tool"], agent=agent,
             args_summary=self._meta[pending_id]["args_summary"],
             args_full=self._meta[pending_id]["args_full"],
             timeout_s=CONSENT_TIMEOUT_S)
        try:
            return await asyncio.wait_for(fut, CONSENT_TIMEOUT_S)
        except asyncio.TimeoutError:
            emit("consent_decided", turn_id=turn_id or None, pending_id=pending_id,
                 decision="denied", reason="timeout")
            return False
        finally:
            self._pending.pop(pending_id, None)
            self._meta.pop(pending_id, None)

    # -- UI side ------------------------------------------------------------

    def decide(self, pending_id: str, approve: bool) -> bool:
        """Resolve a pending request. False when unknown/already-resolved.
        Thread-safe: resolves on the future's own loop, wherever decide()
        is called from."""
        fut = self._pending.get(pending_id)
        if fut is None or fut.done():
            return False
        meta = self._meta.get(pending_id, {})
        try:
            fut.get_loop().call_soon_threadsafe(fut.set_result, approve)
        except RuntimeError:
            fut.set_result(approve)
        emit("consent_decided", turn_id=meta.get("turn_id") or None,
             pending_id=pending_id, decision="approved" if approve else "denied")
        return True

    def pending(self) -> list[dict[str, Any]]:
        return [dict(meta, pending_id=pid) for pid, meta in self._meta.items()]
