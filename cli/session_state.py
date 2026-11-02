"""cli/session_state.py — process-local current-session holder.

The REPL/CLI speaks session semantics: chat messages land in the CURRENT
session; /new rotates it, /fork replaces it with a copy, /sessions use
switches it. The holder is intentionally process-local (like Claude
Code's in-terminal session) — persistence lives in SessionStore.
"""
from __future__ import annotations

_current: dict = {"id": None}


def current_session_id(create: bool = True) -> str | None:
    """The active session id; creates one lazily on first chat."""
    if _current["id"] is None and create:
        from core.session.store import get_session_store
        _current["id"] = get_session_store().create()["id"]
    return _current["id"]


def set_current_session(sid: str | None) -> None:
    _current["id"] = sid
