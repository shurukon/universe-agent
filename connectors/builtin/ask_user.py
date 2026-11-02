"""connectors/builtin/ask_user.py — the AskUserQuestion twin.

The agent asks the human a structured question mid-goal instead of
guessing: a short prompt plus 2-4 options, resolved through the bridge's
QuestionsBroker (universe_desktop_bridge/questions.py — the ConsentManager
twin: pending future + question_required event + POST answer endpoint +
auto-continue timeout from settings).

The handler is deliberately broker-optional: outside the desktop bridge
(CLI, tests without a broker installed) it returns the honest fallback
so the agent keeps going on the first option instead of hanging.
"""
from __future__ import annotations

from typing import Any

_MAX_OPTIONS = 4
_MAX_Q_LEN = 500
_MAX_OPT_LEN = 120

# Installed by the bridge at startup (Runtime.init); None outside it.
_question_handler: Any = None


def set_question_handler(handler: Any) -> None:
    """Install the async question callback (bridge QuestionsBroker.ask)."""
    global _question_handler
    _question_handler = handler


def reset_question_handler_for_tests() -> None:
    global _question_handler
    _question_handler = None


async def _ask_user(question: str = "", options: list[str] | None = None,
                    allow_free_text: bool = True, **_: Any) -> dict[str, Any]:
    """Ask the user a structured question. Returns the picked option (or
    free text), or the honest timeout/fallback answer."""
    question = str(question or "").strip()[:_MAX_Q_LEN]
    if not question:
        return {"ok": False, "error": "question text is required"}
    opts: list[str] = []
    for o in (options or [])[:_MAX_OPTIONS]:
        s = str(o).strip()[:_MAX_OPT_LEN]
        if s:
            opts.append(s)
    if not opts:
        opts = ["Yes", "No"]  # a bare question degenerates to a yes/no
    if _question_handler is None:
        # no UI to answer — the honest fallback, never a hang
        return {"ok": True, "answer": opts[0], "fallback": True,
                "note": "no question broker installed (CLI/test) — "
                        "answered with the first option"}
    try:
        result = await _question_handler(question=question, options=opts,
                                         allow_free_text=bool(allow_free_text))
    except Exception as exc:  # broker hiccup must never kill the goal
        return {"ok": True, "answer": opts[0], "fallback": True,
                "note": f"question broker failed: {exc}"}
    return {"ok": True, **result}


def register(reg) -> None:
    from core.agent.base_agent import Tool
    reg.register(Tool(
        name="AskUserQuestion",
        capability="AskUserQuestion",
        description=(
            "Ask the user a structured question with 2-4 options instead of "
            "guessing. Use for genuine decisions (approach, scope, naming) — "
            "never for things you can determine yourself. The user's choice "
            "comes back as the answer."
        ),
        handler=_ask_user,
        consent="auto",
    ))
