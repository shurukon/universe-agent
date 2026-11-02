"""universe_desktop_bridge/questions.py — structured user questions.

The AskUserQuestion twin's bridge half, mirroring ConsentManager:

  question.ask handler  ->  pending question created, question_required
                            event emitted
  UI picker             ->  POST /api/question/{id} {answer}
  timeout (settings)    ->  auto-continue with the first option (never
                            blocks the goal forever)

A question differs from a consent prompt: it returns the PICKED TEXT,
not a yes/no. Timeout answers with the first option (the agent proposed
it first for a reason) and marks the answer as timed-out so the reply is
honest about not being the user's actual choice.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from observability.events import emit

DEFAULT_QUESTION_TIMEOUT_S = 300.0


class QuestionsBroker:
    """Pending structured questions, resolved by the UI or auto-continued."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._n = 0

    def _timeout_s(self) -> float:
        """Auto-continue timeout from desktop settings; falls back to the
        default when the setting is missing/malformed (never raises)."""
        try:
            from config import get_config
            v = get_config().get("desktop", {}).get("auto_continue_questions_s")
            v = float(v)
            return v if 5.0 <= v <= 3600.0 else DEFAULT_QUESTION_TIMEOUT_S
        except Exception:
            return DEFAULT_QUESTION_TIMEOUT_S

    # -- tool side ----------------------------------------------------------

    async def ask(self, *, question: str, options: list[str],
                  allow_free_text: bool) -> dict[str, Any]:
        """Installed via ask_user.set_question_handler() at bridge startup."""
        pending_id = f"question-{int(time.time() * 1000)}-{self._n}"
        self._n += 1
        fut: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future())
        self._pending[pending_id] = fut
        self._meta[pending_id] = {
            "question": question,
            "options": list(options),
            "allow_free_text": bool(allow_free_text),
            "created_at": round(time.time(), 3),
        }
        timeout = self._timeout_s()
        emit("question_required", pending_id=pending_id, question=question,
             options=list(options), allow_free_text=bool(allow_free_text),
             timeout_s=timeout)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            answer = {"answer": options[0] if options else "",
                      "timed_out": True}
            emit("question_decided", pending_id=pending_id,
                 answer=answer["answer"][:200], reason="timeout")
            return answer
        finally:
            self._pending.pop(pending_id, None)
            self._meta.pop(pending_id, None)

    # -- UI side ------------------------------------------------------------

    def answer(self, pending_id: str, answer: str) -> bool:
        """Resolve a pending question. False when unknown/already-resolved,
        and False when the answer is not one of the declared options while
        free text is disallowed — the broker, not the UI, enforces the
        option contract (a buggy or hostile client must not inject
        non-option text into the model's conversation)."""
        fut = self._pending.get(pending_id)
        if fut is None or fut.done():
            return False
        meta = self._meta.get(pending_id, {})
        answer = str(answer)[:500]
        if not meta.get("allow_free_text", True):
            options = meta.get("options") or []
            if answer not in options:
                return False  # rejected: not a declared option
        result = {"answer": answer, "timed_out": False}

        def _resolve() -> None:
            # runs ON the future's loop: if wait_for's timeout won the race
            # and cancelled the future, set_result raises InvalidStateError —
            # then NO question_decided is emitted (the timeout path owns the
            # event) and the asker keeps its honest timed_out answer
            try:
                fut.set_result(result)
            except asyncio.InvalidStateError:
                return
            emit("question_decided", pending_id=pending_id,
                 answer=result["answer"][:200], reason="answered")

        try:
            fut.get_loop().call_soon_threadsafe(_resolve)
        except RuntimeError:
            # loop closed: nobody is awaiting anymore — nothing to do
            return False
        return True

    def pending(self) -> list[dict[str, Any]]:
        return [dict(meta, pending_id=pid) for pid, meta in self._meta.items()]


_broker: QuestionsBroker | None = None


def get_questions_broker() -> QuestionsBroker:
    global _broker
    if _broker is None:
        _broker = QuestionsBroker()
    return _broker


def reset_questions_broker_for_tests() -> QuestionsBroker:
    global _broker
    _broker = QuestionsBroker()
    return _broker
