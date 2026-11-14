"""connectors/builtin/ask_user.py — the AskUserQuestion tool.

The agent asks the human a structured question mid-turn instead of
guessing: a short prompt plus 2-4 options, resolved through the bridge's
QuestionsBroker (universe_desktop_bridge/questions.py — the ConsentManager
twin: pending future + question_required event + POST answer endpoint +
auto-continue timeout from settings).

THE CONTRACT IS ZCODE'S (2026-11-12, user order: "مشابهة تماما لخاصتك
والخاصة بzcode"). Read off the real ZCode renderer (RE of the shipped
zcode bundle) — NOT guessed:

  input (the model -> tool call):
    questions: [
      {
        question: string,          # the prompt itself
        header?: string,           # a SHORT category chip above it
        options: [                 # 2-4 choices
          { value: string,         # the stable answer value
            label?: string,        # what the user reads (defaults to value)
            description?: string } # one line of extra context
        ],
        multiSelect?: boolean      # pick several
      }, ...
    ]

  output (the tool -> the model):
    { answers: { <question>: "value, value" },  # one entry per question
      answer?: string,                          # single-question shortcut
      timed_out?: boolean }

ZCode's own renderer normalizes exactly this way: a question without
`questions[]` becomes a single-question list, options are normalized to
{value, label, description}, and the answers map is keyed by the question
TEXT with multiSelect answers joined by ", ".

BACKWARD COMPATIBILITY (do not break the old callers): the flat shape
(`question` + `options: [string]`) is still accepted and is converted
into the one-question form. The broker and the UI read the normalized
form only.

The handler is deliberately broker-optional: outside the desktop bridge
(CLI, tests without a broker installed) it returns the honest fallback
so the agent keeps going on the first option instead of hanging.
"""
from __future__ import annotations

from typing import Any

_MAX_QUESTIONS = 4
_MAX_OPTIONS = 4
_MAX_Q_LEN = 500
_MAX_HEADER_LEN = 60
_MAX_LABEL_LEN = 120
_MAX_DESC_LEN = 300

# Installed by the bridge at startup (Runtime.init); None outside it.
_question_handler: Any = None


def set_question_handler(handler: Any) -> None:
    """Install the async question callback (bridge QuestionsBroker.ask)."""
    global _question_handler
    _question_handler = handler


def reset_question_handler_for_tests() -> None:
    global _question_handler
    _question_handler = None


def _normalize_option(raw: Any) -> dict[str, str] | None:
    """One option -> {value, label, description}.

    Accepts the ZCode object form ({"value", "label", "description"}) and
    the legacy plain-string form ("Yes"). A missing label falls back to
    the value; a missing value falls back to the label (the option text
    IS the answer).
    """
    if isinstance(raw, dict):
        value = str(raw.get("value") or "").strip()
        label = str(raw.get("label") or "").strip()
        desc = str(raw.get("description") or "").strip()
        if not value and not label:
            return None
        value = (value or label)[:_MAX_LABEL_LEN]
        label = (label or value)[:_MAX_LABEL_LEN]
        return {"value": value, "label": label, "description": desc[:_MAX_DESC_LEN]}
    text = str(raw or "").strip()
    if not text:
        return None
    text = text[:_MAX_LABEL_LEN]
    return {"value": text, "label": text, "description": ""}


def normalize_questions(args: dict[str, Any]) -> list[dict[str, Any]]:
    """The model's arguments -> the normalized question list.

    ZCode's shape is `questions: [...]`; the legacy flat shape
    (`question` + `options`) is converted to a one-question list. Every
    question is {question, header, options: [{value,label,description}],
    multiSelect}. A question with no usable option is DROPPED (a question
    the user cannot answer is not a question).
    """
    raw_questions = args.get("questions")
    out: list[dict[str, Any]] = []

    if isinstance(raw_questions, list) and raw_questions:
        for raw in raw_questions[:_MAX_QUESTIONS]:
            if not isinstance(raw, dict):
                continue
            qtext = str(raw.get("question") or "").strip()[:_MAX_Q_LEN]
            if not qtext:
                continue
            opts = []
            for o in (raw.get("options") or [])[:_MAX_OPTIONS]:
                norm = _normalize_option(o)
                if norm:
                    opts.append(norm)
            if not opts:
                continue
            header = str(raw.get("header") or "").strip()[:_MAX_HEADER_LEN]
            out.append({
                "question": qtext,
                "header": header,
                "options": opts,
                "multiSelect": bool(raw.get("multiSelect") or raw.get("multi_select")),
            })
    else:
        # the legacy flat shape
        qtext = str(args.get("question") or "").strip()[:_MAX_Q_LEN]
        opts = []
        for o in (args.get("options") or [])[:_MAX_OPTIONS]:
            norm = _normalize_option(o)
            if norm:
                opts.append(norm)
        if qtext and opts:
            out.append({
                "question": qtext,
                "header": str(args.get("header") or "").strip()[:_MAX_HEADER_LEN],
                "options": opts,
                "multiSelect": bool(args.get("multiSelect")
                                    or args.get("multi_select")),
            })

    if not out:
        return []
    # a bare question with no options degenerates to Yes/No (the old
    # behaviour) rather than dying: the agent asked SOMETHING.
    return out


def _fallback_answer(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """The honest no-broker answer: the first option of every question."""
    answers = {q["question"]: q["options"][0]["value"] for q in questions
               if q.get("options")}
    result: dict[str, Any] = {"answers": answers}
    if len(questions) == 1 and answers:
        result["answer"] = next(iter(answers.values()))
    result["fallback"] = True
    return result


async def _ask_user(questions: Any = None, question: str = "",
                    options: Any = None, header: str = "",
                    multiSelect: bool = False, multi_select: bool = False,
                    allow_free_text: bool = True, **_: Any) -> dict[str, Any]:
    """Ask the user one or more structured questions.

    Args:
        questions: The questions to ask. Each one is an object with
            `question` (the prompt), `header` (a short category chip),
            `options` (2-4 choices, each {value, label, description}),
            and `multiSelect` (allow picking several).
        question: A single question, used instead of `questions`.
        options: The options for that single question — either plain
            strings or {value, label, description} objects.
        header: A short category chip for the single-question form.
        multiSelect: Allow picking several options (single-question form).
        allow_free_text: Let the user type their own answer instead of
            picking an option.
    """
    args: dict[str, Any] = {
        "questions": questions,
        "question": question,
        "options": options,
        "header": header,
        "multiSelect": multiSelect or multi_select,
        "allow_free_text": allow_free_text,
    }
    norm = normalize_questions(args)
    if not norm:
        return {"ok": False, "error": "a question needs text and 2-4 options"}

    if _question_handler is None:
        # no UI to answer — the honest fallback, never a hang
        return {"ok": True, **_fallback_answer(norm),
                "note": "no question broker installed (CLI/test) — "
                        "answered with the first option"}

    try:
        result = await _question_handler(questions=norm,
                                         allow_free_text=bool(allow_free_text))
    except TypeError:
        # an older broker that only speaks the flat shape
        try:
            first = norm[0]
            result = await _question_handler(
                question=first["question"],
                options=[o["label"] for o in first["options"]],
                allow_free_text=bool(allow_free_text))
        except Exception as exc:  # broker hiccup must never kill the turn
            return {"ok": True, **_fallback_answer(norm),
                    "note": f"question broker failed: {exc}"}
    except Exception as exc:  # broker hiccup must never kill the turn
        return {"ok": True, **_fallback_answer(norm),
                "note": f"question broker failed: {exc}"}
    return {"ok": True, **result}


# The ZCode schema, advertised to the model verbatim (Tool.schema). The
# signature-derived schema cannot express a nested contract - it would say
# "questions: array" and leave the model to guess the item fields, which is
# exactly how the model ends up sending {"options": ["a", "b"]} instead of
# {"options": [{"value": "a", "label": "A"}]}.
ASK_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "description": (
                "The questions to ask (1-4). Ask them all in ONE call so the "
                "user answers in one pass."),
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question itself - what you need "
                                       "the user to decide.",
                    },
                    "header": {
                        "type": "string",
                        "description": "A SHORT category chip shown above "
                                       "the question (a few words).",
                    },
                    "multiSelect": {
                        "type": "boolean",
                        "description": "True when several answers may apply.",
                    },
                    "options": {
                        "type": "array",
                        "description": "The 2-4 choices the user picks from.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "value": {
                                    "type": "string",
                                    "description": "The value that comes "
                                                   "back as the answer.",
                                },
                                "label": {
                                    "type": "string",
                                    "description": "What the user reads "
                                                   "(defaults to the value).",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "One short line saying "
                                                   "what this option means.",
                                },
                            },
                            "required": ["value"],
                        },
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
    "required": ["questions"],
    "additionalProperties": True,
}


def register(reg) -> None:
    from core.agent.base_agent import Tool
    reg.register(Tool(
        name="AskUserQuestion",
        capability="AskUserQuestion",
        description=(
            "Ask the user one or more structured questions instead of guessing. "
            "Each question carries a short `question`, an optional short "
            "`header` (a category chip), and 2-4 `options`; every option has a "
            "stable `value`, a `label` to read and an optional one-line "
            "`description`. Set `multiSelect` when several answers may apply. "
            "Use it for genuine decisions (approach, scope, naming, which of "
            "these to do) - never for things you can determine yourself, and "
            "never for approvals (those go through ExitPlanMode). The user's "
            "choice comes back as `answers`, keyed by the question text."
        ),
        handler=_ask_user,
        consent="auto",
        schema=ASK_USER_SCHEMA,
    ))
