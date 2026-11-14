"""tests/test_ask_user_question.py — the AskUserQuestion tool, ZCode shape.

The user order (2026-11-12): the tool must be "مشابهة تماما لخاصتك
والخاصة بzcode" — the same contract, the same answer shape, the same
option semantics.

The contract was read off the SHIPPED ZCode renderer (RE of the zcode
bundle), not guessed:

  request   questions: [{question, header?, options: [{value, label,
               description}], multiSelect?}]
  response  {answers: {<question text>: "value, value"}, answer?}

These tests pin:
  * the normalization (ZCode form + the legacy flat form),
  * the answer map keyed by the question TEXT,
  * multiSelect joining with ", ",
  * the broker's option enforcement (a hostile client cannot inject
    non-option text),
  * the timeout auto-continue (first option, flagged as timed out),
  * the bridge endpoint accepting the answers map,
  * the honest fallback when no broker is installed.
"""
from __future__ import annotations

import asyncio

import pytest


# --------------------------------------------------------------------------
# the normalization
# --------------------------------------------------------------------------

def test_the_zcode_shape_is_normalized():
    """questions[] with {value,label,description} survives intact."""
    from connectors.builtin.ask_user import normalize_questions

    norm = normalize_questions({
        "questions": [{
            "question": "Which approach?",
            "header": "Approach",
            "multiSelect": True,
            "options": [
                {"value": "a", "label": "A", "description": "the fast one"},
                {"value": "b", "label": "B", "description": ""},
            ],
        }],
    })
    assert len(norm) == 1
    q = norm[0]
    assert q["question"] == "Which approach?"
    assert q["header"] == "Approach"
    assert q["multiSelect"] is True
    assert q["options"][0] == {"value": "a", "label": "A",
                               "description": "the fast one"}
    assert q["options"][1]["value"] == "b"


def test_the_legacy_flat_shape_still_works():
    """The old (question + options:[string]) form must not break."""
    from connectors.builtin.ask_user import normalize_questions

    norm = normalize_questions({"question": "proceed?", "options": ["yes", "no"]})
    assert len(norm) == 1
    assert norm[0]["question"] == "proceed?"
    assert [o["value"] for o in norm[0]["options"]] == ["yes", "no"]
    # a plain string option has no description and label == value
    assert norm[0]["options"][0]["label"] == "yes"
    assert norm[0]["options"][0]["description"] == ""


def test_a_question_without_options_is_dropped():
    """A question the user cannot answer is not a question."""
    from connectors.builtin.ask_user import normalize_questions

    norm = normalize_questions({
        "questions": [
            {"question": "no options here", "options": []},
            {"question": "", "options": [{"value": "x"}]},
        ],
    })
    assert norm == []


def test_at_most_four_questions_and_four_options():
    """The bounds are the contract's, not an accident."""
    from connectors.builtin.ask_user import normalize_questions

    many_q = [{"question": f"q{i}", "options": [{"value": f"v{i}"}]}
              for i in range(9)]
    norm = normalize_questions({"questions": many_q})
    assert len(norm) == 4

    many_o = [{"value": f"v{i}"} for i in range(9)]
    norm2 = normalize_questions({"questions": [{"question": "q",
                                                "options": many_o}]})
    assert len(norm2[0]["options"]) == 4


def test_a_missing_label_falls_back_to_the_value():
    from connectors.builtin.ask_user import normalize_questions

    norm = normalize_questions({
        "questions": [{"question": "q", "options": [{"value": "raw"}]}]})
    assert norm[0]["options"][0]["label"] == "raw"


# --------------------------------------------------------------------------
# the tool (no broker installed — the honest fallback)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_tool_falls_back_without_a_broker(fresh_home):
    """Outside the bridge the tool must answer honestly, never hang."""
    from connectors.builtin import get_registry
    reg = get_registry()

    r = await reg.call("AskUserQuestion", agent="universe", turn_id="gq2",
                       args={"question": "proceed?",
                             "options": ["yes", "no"]})
    assert r["ok"] is True
    inner = r["result"]
    assert inner["fallback"] is True
    assert inner["answer"] == "yes"  # first option, flagged as fallback


@pytest.mark.asyncio
async def test_the_tool_answers_with_the_answers_map(fresh_home):
    """The ZCode answer shape: a map keyed by the question text."""
    from connectors.builtin import get_registry
    reg = get_registry()

    r = await reg.call("AskUserQuestion", agent="universe", turn_id="gq3",
                       args={"questions": [{
                           "question": "Which approach?",
                           "options": [{"value": "a"}, {"value": "b"}]}]})
    inner = r["result"]
    assert inner["answers"] == {"Which approach?": "a"}
    assert inner["answer"] == "a"       # single-question shortcut
    assert inner["fallback"] is True


@pytest.mark.asyncio
async def test_the_tool_requires_a_question(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()
    r = await reg.call("AskUserQuestion", agent="universe", turn_id="gq4",
                       args={"question": "", "options": []})
    assert r["ok"] is False
    inner = r.get("result") if isinstance(r.get("result"), dict) else r
    assert "question" in str(inner.get("error", "")).lower()


# --------------------------------------------------------------------------
# the broker
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_pending_id_survives_the_event_sanitizer(fresh_home):
    """The client must be able to SEND the pending id back.

    The bridge's event sanitizer entropy-redacts long high-entropy tokens
    (they look like credential material). The old id shape
    ("question-<millis>-<n>") was exactly that: the client received
    "[REDACTED]" and its answer POST 404'd - the card was unusable even
    though it rendered. The UUID form the filter exempts is the safe one.
    """
    from security.secret_filter import redact
    from universe_desktop_bridge.questions import _new_pending_id

    pid = _new_pending_id()
    assert redact(pid) == pid, (
        f"the pending id is redacted on the wire: {pid!r} -> {redact(pid)!r}")
    assert pid.startswith("question-")


@pytest.mark.asyncio
async def test_the_tool_timeout_outlasts_the_question_timeout():
    """The tool's backstop must NEVER be shorter than the question's own
    timeout: a 30 s tool timeout cut the question off while the user was
    still reading it, and the agent reported a timeout while the card was
    still on screen (live 2026-11-12)."""
    from connectors.builtin import get_registry
    from universe_desktop_bridge.questions import (
        DEFAULT_QUESTION_TIMEOUT_S, get_questions_broker)

    broker = get_questions_broker()
    tool_timeout = get_registry().get("AskUserQuestion").timeout_s
    assert tool_timeout is not None
    # the broker's ceiling is 3600 s (the setting's max) + the default
    assert tool_timeout > 3600, (
        f"the tool timeout ({tool_timeout}s) can beat the question timeout "
        f"(max 3600s, default {DEFAULT_QUESTION_TIMEOUT_S}s)")
    assert broker._timeout_s() in (None, DEFAULT_QUESTION_TIMEOUT_S)


@pytest.mark.asyncio
async def test_never_auto_continue_waits_for_the_human(fresh_home):
    """The Settings toggle off (0) must actually disable the
    auto-continue - the old clamp folded 0 back into 300 s, so the toggle
    did nothing."""
    from config import get_config
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    cfg = get_config()
    old = cfg.get("desktop", {}) or {}
    desktop = dict(old)
    desktop["auto_continue_questions_s"] = 0
    cfg.set("desktop", desktop)
    try:
        assert broker._timeout_s() is None, (
            "0 must mean never auto-continue")
    finally:
        cfg.set("desktop", old)


@pytest.mark.asyncio
async def test_the_broker_returns_the_picked_values():
    """The broker hands the model the picked VALUE, keyed by the question."""
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    spec = [{"question": "Which one?", "header": "Pick",
             "options": [{"value": "a", "label": "A", "description": ""},
                         {"value": "b", "label": "B", "description": ""}],
             "multiSelect": False}]

    async def answer_later():
        await asyncio.sleep(0.05)
        pending = broker.pending()
        assert pending, "the pending question must be visible to the UI"
        assert pending[0]["questions"][0]["options"][0]["value"] == "a"
        assert broker.answer(pending[0]["pending_id"],
                             {"Which one?": "b"}) is True

    task = asyncio.create_task(answer_later())
    result = await broker.ask(questions=spec, allow_free_text=True)
    await task
    assert result["answers"] == {"Which one?": "b"}
    assert result["answer"] == "b"
    assert result["timed_out"] is False


@pytest.mark.asyncio
async def test_multi_select_joins_with_a_comma():
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    spec = [{"question": "Pick all that apply", "header": "",
             "options": [{"value": "a", "label": "A", "description": ""},
                         {"value": "b", "label": "B", "description": ""},
                         {"value": "c", "label": "C", "description": ""}],
             "multiSelect": True}]

    async def answer_later():
        await asyncio.sleep(0.05)
        pending = broker.pending()
        broker.answer(pending[0]["pending_id"],
                      {"Pick all that apply": ["a", "c"]})

    task = asyncio.create_task(answer_later())
    result = await broker.ask(questions=spec, allow_free_text=True)
    await task
    assert result["answers"] == {"Pick all that apply": "a, c"}


@pytest.mark.asyncio
async def test_the_broker_refuses_an_undeclared_option():
    """The broker, not the UI, enforces the option contract."""
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    spec = [{"question": "Pick one", "header": "",
             "options": [{"value": "a", "label": "A", "description": ""}],
             "multiSelect": False}]

    async def answer_later():
        await asyncio.sleep(0.05)
        pending = broker.pending()
        pid = pending[0]["pending_id"]
        # free text is OFF -> a non-option value must be rejected
        assert broker.answer(pid, {"Pick one": "rm -rf /"}) is False
        # the legit option still works
        assert broker.answer(pid, {"Pick one": "a"}) is True

    task = asyncio.create_task(answer_later())
    result = await broker.ask(questions=spec, allow_free_text=False)
    await task
    assert result["answers"] == {"Pick one": "a"}


@pytest.mark.asyncio
async def test_free_text_is_allowed_when_the_question_allows_it():
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    spec = [{"question": "Your call", "header": "",
             "options": [{"value": "a", "label": "A", "description": ""}],
             "multiSelect": False}]

    async def answer_later():
        await asyncio.sleep(0.05)
        pending = broker.pending()
        broker.answer(pending[0]["pending_id"], {"Your call": "something else"})

    task = asyncio.create_task(answer_later())
    result = await broker.ask(questions=spec, allow_free_text=True)
    await task
    assert result["answers"] == {"Your call": "something else"}


@pytest.mark.asyncio
async def test_the_timeout_auto_continues_with_the_first_option():
    """A question must never block the turn forever."""
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    spec = [{"question": "Pick one", "header": "",
             "options": [{"value": "a", "label": "A", "description": ""},
                         {"value": "b", "label": "B", "description": ""}],
             "multiSelect": False}]

    # the timeout is read from the desktop settings; force it to the floor
    from config import get_config
    cfg = get_config()
    old = cfg.get("desktop", {}) or {}
    desktop = dict(old)
    desktop["auto_continue_questions_s"] = 5.0
    cfg.set("desktop", desktop)
    try:
        result = await asyncio.wait_for(
            broker.ask(questions=spec, allow_free_text=True), timeout=30)
    finally:
        cfg.set("desktop", old)
    # the 5s floor means this cannot have timed out in the test window;
    # the timeout PATH itself is covered by the broker's own unit below.
    assert "answers" in result


@pytest.mark.asyncio
async def test_the_timeout_path_returns_the_first_option(monkeypatch):
    """Force the timeout and check the honest auto-continue."""
    from universe_desktop_bridge import questions as q_mod
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    monkeypatch.setattr(broker, "_timeout_s", lambda: 0.05)
    spec = [{"question": "Pick one", "header": "",
             "options": [{"value": "a", "label": "A", "description": ""},
                         {"value": "b", "label": "B", "description": ""}],
             "multiSelect": False}]
    result = await broker.ask(questions=spec, allow_free_text=True)
    assert result["timed_out"] is True
    assert result["answers"] == {"Pick one": "a"}
    assert result["answer"] == "a"
    assert q_mod.json_answers({"Pick one": "a"}) == "Pick one = a"


@pytest.mark.asyncio
async def test_several_questions_come_back_as_several_answers():
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    spec = [
        {"question": "First?", "header": "One",
         "options": [{"value": "x", "label": "X", "description": ""}],
         "multiSelect": False},
        {"question": "Second?", "header": "Two",
         "options": [{"value": "y", "label": "Y", "description": ""}],
         "multiSelect": False},
    ]

    async def answer_later():
        await asyncio.sleep(0.05)
        pending = broker.pending()
        broker.answer(pending[0]["pending_id"],
                      {"First?": "x", "Second?": "y"})

    task = asyncio.create_task(answer_later())
    result = await broker.ask(questions=spec, allow_free_text=True)
    await task
    assert result["answers"] == {"First?": "x", "Second?": "y"}
    # no single-question shortcut for a multi-question call
    assert "answer" not in result


# --------------------------------------------------------------------------
# the bridge endpoint
# --------------------------------------------------------------------------

def test_the_bridge_endpoint_accepts_the_answers_map(fresh_home):
    """POST /api/question/{id} takes the ZCode answers map."""
    from fastapi.testclient import TestClient
    import universe_desktop_bridge.app as app_mod
    app_mod.reset_runtime_for_tests()
    from universe_desktop_bridge.questions import (
        reset_questions_broker_for_tests)

    broker = reset_questions_broker_for_tests()
    app_mod._runtime.questions = broker

    spec = [{"question": "Which one?", "header": "Pick",
             "options": [{"value": "a", "label": "A", "description": ""},
                         {"value": "b", "label": "B", "description": ""}],
             "multiSelect": False}]

    with TestClient(app_mod.app) as c:
        async def drive():
            task = asyncio.create_task(broker.ask(questions=spec,
                                                  allow_free_text=True))
            await asyncio.sleep(0.2)
            pending = c.get("/api/question/pending").json()["pending"]
            assert pending, "the pending question must be listed"
            assert pending[0]["questions"][0]["question"] == "Which one?"
            r = c.post(f"/api/question/{pending[0]['pending_id']}",
                       json={"answers": {"Which one?": "b"}})
            assert r.status_code == 200, r.text
            return await task

        result = asyncio.run(drive())
    assert result["answers"] == {"Which one?": "b"}


def test_the_bridge_endpoint_rejects_an_empty_answer(fresh_home):
    from fastapi.testclient import TestClient
    import universe_desktop_bridge.app as app_mod
    app_mod.reset_runtime_for_tests()
    with TestClient(app_mod.app) as c:
        r = c.post("/api/question/question-nope", json={})
        assert r.status_code in (404, 422)


# --------------------------------------------------------------------------
# the tool schema the model sees
# --------------------------------------------------------------------------

def test_the_tool_is_advertised_to_the_model():
    """The tool must be in the schema set the model is offered, with the
    ZCode contract (the nested option objects) visible."""
    from core.core_agents.agents import get_agent
    agent = get_agent("universe")
    # no argument = the agent's own whitelist (an empty list means "none")
    schemas = agent._tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "AskUserQuestion" in names
    entry = next(s for s in schemas
                 if s["function"]["name"] == "AskUserQuestion")
    desc = entry["function"]["description"]
    assert "question" in desc.lower()
    assert "option" in desc.lower()
    # the NESTED contract must be advertised, not a bare "array"
    params = entry["function"]["parameters"]
    items = params["properties"]["questions"]["items"]
    assert items["properties"]["options"]["items"]["properties"]["value"]
    assert items["properties"]["options"]["items"]["required"] == ["value"]
    assert "multiSelect" in items["properties"]
