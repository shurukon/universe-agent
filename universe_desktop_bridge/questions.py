"""universe_desktop_bridge/questions.py — structured user questions.

The AskUserQuestion twin's bridge half, mirroring ConsentManager:

  question.ask handler  ->  pending question created, question_required
                            event emitted
  UI picker             ->  POST /api/question/{id} {answers}
  timeout (settings)    ->  auto-continue with the first option (never
                            blocks the turn forever)

A question differs from a consent prompt: it returns the PICKED VALUES,
not a yes/no. Timeout answers with the first option (the agent proposed
it first for a reason) and marks the answer as timed-out so the reply is
honest about not being the user's actual choice.

THE SHAPE IS ZCODE'S (2026-11-12, user order: "مشابهة تماما ... zcode").
A pending question is a LIST of questions, each:

    {question, header, options: [{value, label, description}], multiSelect}

and the answer is a MAP keyed by the question text:

    {"<question text>": "value" | "value, value"}

That is exactly what the ZCode renderer submits (its `answers` object)
and what its renderer normalizes the request into.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from observability.events import emit

DEFAULT_QUESTION_TIMEOUT_S = 300.0
_MAX_QUESTIONS = 4
_MAX_ANSWER_LEN = 500


def _new_pending_id() -> str:
    """A pending-question id that survives the event sanitizer.

    The sanitizer redacts long high-entropy tokens (they look like
    credentials). A UUID is explicitly exempt, so "question-<uuid>" is
    both unique and safe to carry over the wire.
    """
    try:
        from core.agent.base_agent import new_id
        return new_id("question-")
    except Exception:
        import uuid
        return f"question-{uuid.uuid4()}"


class QuestionsBroker:
    """Pending structured questions, resolved by the UI or auto-continued."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._n = 0

    def _timeout_s(self) -> float | None:
        """The auto-continue timeout from the desktop settings.

        None = NEVER auto-continue: the question waits for the human (the
        Settings toggle "Automatically continue questions" off writes 0).
        The old clamp folded 0 back into the 300 s default, so the toggle
        did nothing at all - the question kept auto-continuing itself
        (audit 2026-11-12).

        A missing or malformed setting falls back to the default; a value
        outside 5..3600 s is clamped into that band.
        """
        try:
            from config import get_config
            raw = get_config().get("desktop", {}).get(
                "auto_continue_questions_s")
            v = float(raw)
            if v <= 0:
                return None          # never auto-continue
            return v if 5.0 <= v <= 3600.0 else DEFAULT_QUESTION_TIMEOUT_S
        except (TypeError, ValueError):
            return DEFAULT_QUESTION_TIMEOUT_S
        except Exception:
            return DEFAULT_QUESTION_TIMEOUT_S

    # -- tool side ----------------------------------------------------------

    async def ask(self, *, questions: list[dict[str, Any]] | None = None,
                  allow_free_text: bool = True,
                  question: str = "",
                  options: list[str] | None = None,
                  ) -> dict[str, Any]:
        """Installed via ask_user.set_question_handler() at bridge startup.

        Accepts the ZCode shape (`questions=[...]`) and the legacy flat
        shape (`question=` + `options=[...]`).
        """
        norm = _normalize(questions, question, options)
        if not norm:
            return {"answers": {}, "error": "no usable question"}

        # The pending id MUST survive the secret filter: the bridge's event
        # sanitizer entropy-redacts any 24+ char token that looks like
        # credential material, and the old "question-<millis>-<n>" id was
        # exactly that shape - the client received "[REDACTED]" and its
        # answer POST 404'd (the consent id escaped only by being one
        # character shorter). The UUID form the filter explicitly exempts
        # is the safe id.
        pending_id = _new_pending_id()
        fut: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future())
        self._pending[pending_id] = fut
        self._meta[pending_id] = {
            "questions": norm,
            "allow_free_text": bool(allow_free_text),
            "created_at": round(time.time(), 3),
        }
        timeout = self._timeout_s()
        emit("question_required", pending_id=pending_id,
             questions=norm,
             # the legacy flat fields stay for the old UI shape
             question=norm[0]["question"],
             options=[o["label"] for o in norm[0]["options"]],
             allow_free_text=bool(allow_free_text),
             timeout_s=timeout)
        try:
            # timeout=None = never auto-continue: the question waits for the
            # human (the Settings toggle off). The TOOL's own backstop
            # (Tool.timeout_s) is what eventually ends such a turn.
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            answers = {q["question"]: q["options"][0]["value"]
                       for q in norm if q.get("options")}
            result: dict[str, Any] = {"answers": answers, "timed_out": True}
            if len(norm) == 1 and answers:
                result["answer"] = next(iter(answers.values()))
            emit("question_decided", pending_id=pending_id,
                 answer=json_answers(answers)[:200], reason="timeout")
            return result
        finally:
            self._pending.pop(pending_id, None)
            self._meta.pop(pending_id, None)

    # -- UI side ------------------------------------------------------------

    def answer(self, pending_id: str, answers: Any) -> bool:
        """Resolve a pending question with the user's picks.

        `answers` is either the ZCode map ({"<question text>": "value"})
        or a plain string (the legacy single-answer form). Every value
        must be one of the DECLARED options — the broker, not the UI,
        enforces the option contract (a buggy or hostile client must not
        inject non-option text into the model's conversation). Free text
        is allowed only when the question allows it.
        """
        fut = self._pending.get(pending_id)
        if fut is None or fut.done():
            return False
        meta = self._meta.get(pending_id, {})
        norm = meta.get("questions") or []

        if isinstance(answers, str):
            picks = {norm[0]["question"]: answers} if norm else {}
        elif isinstance(answers, dict):
            picks = {str(k): v for k, v in answers.items()}
        else:
            return False

        cleaned = self._validate(picks, norm,
                                 bool(meta.get("allow_free_text", True)))
        if cleaned is None:
            return False  # rejected: not a declared option

        result: dict[str, Any] = {"answers": cleaned, "timed_out": False}
        if len(norm) == 1 and cleaned:
            result["answer"] = next(iter(cleaned.values()))

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
                 answer=json_answers(cleaned)[:200], reason="answered")

        try:
            fut.get_loop().call_soon_threadsafe(_resolve)
        except RuntimeError:
            # loop closed: nobody is awaiting anymore — nothing to do
            return False
        return True

    def _validate(self, picks: dict[str, Any],
                  questions: list[dict[str, Any]],
                  allow_free_text: bool) -> dict[str, str] | None:
        """The picks -> {question text: "value, value"} or None (rejected).

        A question the user did not answer is SKIPPED (not a rejection):
        the agent gets what it was given, and the honest gap is visible.
        A value that is neither a declared option nor free text (when
        allowed) rejects the WHOLE answer.
        """
        by_text = {q["question"]: q for q in questions}
        out: dict[str, str] = {}
        for qtext, raw in picks.items():
            q = by_text.get(qtext)
            if q is None:
                # unknown question key: ignore it (the client may send the
                # whole map back with extra keys)
                continue
            values = raw if isinstance(raw, list) else [raw]
            declared = {o["value"] for o in q.get("options", [])}
            picked: list[str] = []
            for v in values:
                v = str(v).strip()[:_MAX_ANSWER_LEN]
                if not v:
                    continue
                if v in declared:
                    if not q.get("multiSelect") and picked:
                        # single-select: the FIRST declared pick wins
                        break
                    if v not in picked:
                        picked.append(v)
                elif allow_free_text:
                    if v not in picked:
                        picked.append(v)
                else:
                    return None  # not a declared option and free text is off
            if picked:
                out[qtext] = ", ".join(picked)
        return out

    def pending(self) -> list[dict[str, Any]]:
        """The pending questions, in the shape the UI reads."""
        out: list[dict[str, Any]] = []
        for pid, meta in self._meta.items():
            norm = meta.get("questions") or []
            if not norm:
                continue
            out.append({
                "pending_id": pid,
                "questions": norm,
                # the legacy flat fields stay for the old UI shape
                "question": norm[0]["question"],
                "options": [o["label"] for o in norm[0]["options"]],
                "allow_free_text": bool(meta.get("allow_free_text", True)),
                "created_at": meta.get("created_at"),
            })
        return out


def json_answers(answers: dict[str, str]) -> str:
    """A compact one-line rendering of the answers map (for the event)."""
    return "; ".join(f"{k} = {v}" for k, v in answers.items())


def _normalize(questions: list[dict[str, Any]] | None,
               question: str = "",
               options: list[str] | None = None,
               ) -> list[dict[str, Any]]:
    """The requested questions -> the normalized list (ZCode shape)."""
    from connectors.builtin.ask_user import normalize_questions
    args: dict[str, Any] = {"questions": questions}
    if question:
        args["question"] = question
    if options:
        args["options"] = options
    norm = normalize_questions(args)
    return norm[:_MAX_QUESTIONS]


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
