"""channels — external input adapters (§7, §25 Phase 5).

A configured channel must actually START (audit H4: TelegramChannel was
complete but nothing ever called start() — the channel never came up).
This manager boots every configured channel inside the running event loop
(the CLI REPL owns it) and routes inbound messages through the ONE
orchestrator pipeline — no separate Telegram logic, the same session turn
a typed message would take.

Start is honest: a channel that fails to boot (library missing, bad
token, network down) is logged and skipped — never silently swallowed,
and the process keeps running without it.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from loguru import logger

_started: list[Any] = []


def configured_channels() -> list[dict[str, Any]]:
    """The channel rows the user actually configured (empty-safe: the
    channels table may not exist on very old homes)."""
    try:
        from storage.local import get_storage
        return [dict(r) for r in get_storage().query_all(
            "SELECT kind FROM channels;")]
    except Exception:
        return []


async def _default_on_message(text: str, user_id: int) -> str:
    """The production route: an inbound channel message is one session
    turn — the reply text goes back out the same channel."""
    from core.agent.orchestrator import get_orchestrator
    summary = await get_orchestrator().run_turn(str(text))
    return str(summary.get("reply") or "")


async def start_channels(
    on_message: Callable[[str, int], Any] | None = None,
) -> list[str]:
    """Start every configured channel. Returns the kinds that actually
    booted; failures are logged loudly and skipped."""
    started: list[str] = []
    handler = on_message or _default_on_message
    kinds = {c.get("kind") for c in configured_channels()}
    if "telegram" in kinds:
        try:
            from channels.telegram import get_telegram_channel
            ch = get_telegram_channel()
            if not ch.token:
                logger.warning("Telegram channel configured but the stored "
                               "token is empty/unreadable — not starting")
            else:
                await ch.start(handler)
                _started.append(ch)
                started.append("telegram")
                logger.info("Telegram channel started")
        except Exception as e:
            logger.warning(f"Telegram channel failed to start: {e}")
    return started


async def stop_channels() -> None:
    """Stop everything start_channels() booted (best-effort, idempotent)."""
    for ch in _started:
        try:
            await ch.stop()
        except Exception:
            pass
    _started.clear()
