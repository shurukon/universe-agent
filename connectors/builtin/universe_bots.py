"""connectors/builtin/universe_bots.py — the Universe's tools for the
universe bots link.

universe bots (OpenMausBot, renamed) is a second agent app living inside
this one. Its bots each have an inbox; the Universe hands them work there
and their results come back later, as a notification.

The three tools here are the whole link:

    UniverseBotsList      who can be handed work
    UniverseBotsDelegate  hand a task over (it lands in that bot's inbox)
    UniverseBotsResult    read what came back

The wire is the harness's own peer protocol (universe_desktop_bridge/
universe_bots.py): a standing bearer capability bound to one conversation,
which the harness reads from its grants file.
"""
from __future__ import annotations

from typing import Any

from core.agent.base_agent import Tool
from universe_desktop_bridge import universe_bots


def _bots_text(payload: dict[str, Any]) -> str:
    bots = payload.get("bots")
    if not isinstance(bots, list) or not bots:
        return ("No other bots yet in Universe Bots. A person makes them in the "
                "Bots surface; once one exists it can be handed work here.")
    lines = []
    for bot in bots:
        if not isinstance(bot, dict):
            continue
        name = str(bot.get("name") or bot.get("id") or "?")
        title = str(bot.get("title") or "")
        state = str(bot.get("statusText") or "")
        if state == "available":
            state = ""
        about = f" — {title}" if title else ""
        busy = f", {state}" if state else ""
        lines.append(f"- {name}{about} [id: {bot.get('id')}"
                     f", model: {bot.get('model')}{busy}]")
    return ("Bots in Universe Bots that can be handed work:\n"
            + "\n".join(lines)
            + "\n\nUse UniverseBotsDelegate with bot_ids and a message. "
              "The task lands in that bot's inbox; its result arrives later "
              "as a notification.")


async def bots(turn_id: str = "") -> dict[str, Any]:
    """List the bots in Universe Bots that the Universe can hand work to."""
    payload = await universe_bots.list_agents()
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error", "universe bots is not reachable")}
    return {"ok": True, "text": _bots_text(payload),
            "bots": payload.get("bots", [])}


async def delegate(bot_ids: Any = "", message: str = "",
                   title: str = "", reason: str = "",
                   consult: bool = False, done_when: str = "",
                   turn_id: str = "") -> dict[str, Any]:
    """Hand a task to one or more bots in Universe Bots.

    bot_ids: the ids from UniverseBotsList (one or more, up to four).
    message: what the bot should do, in plain words - it is the whole brief.
    title: a short name for the task, one line.
    reason: why this work is being handed over.
    consult: true asks for advice instead of work.
    done_when: what "finished" means for this task.

    The task lands in that bot's inbox and the call returns at once. The
    result comes back later, as a notification - do not poll for it in this
    turn.
    """
    if isinstance(bot_ids, str):
        ids = [part.strip() for part in bot_ids.split(",") if part.strip()]
    elif isinstance(bot_ids, (list, tuple)):
        ids = [str(part).strip() for part in bot_ids if str(part).strip()]
    else:
        ids = []
    if not ids:
        return {"ok": False,
                "error": "bot_ids is empty - call UniverseBotsList first and "
                         "pass the id(s) of the bot(s) to hand work to"}
    if len(ids) > 4:
        return {"ok": False, "error": "at most four bots at a time"}
    brief = str(message or "").strip()
    if not brief:
        return {"ok": False, "error": "message is empty - say what the bot should do"}
    payload = await universe_bots.delegate(
        ids, brief,
        title=str(title or "").strip() or None,
        reason=str(reason or "").strip() or None,
        consult=bool(consult),
        done_when=str(done_when or "").strip() or None,
    )
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error", "the bots refused the task")}
    receipts = payload.get("receipts") or []
    names = ", ".join(str(r.get("botName") or r.get("botId") or "?")
                      for r in receipts if isinstance(r, dict))
    root_id = str(payload.get("rootId") or "")
    universe_bots.remember(root_id, names, turn_id)
    return {"ok": True,
            "assignment_id": root_id,
            "text": (f"Handed to {names or 'the bot'}. It runs in that bot's "
                     f"inbox; the result comes back later as a notification "
                     f"(assignment {root_id}). Do not wait for it in this turn.")}


async def create(name: str = "", description: str = "", title: str = "",
                 turn_id: str = "") -> dict[str, Any]:
    """Make a bot in Universe Bots.

    name: what the bot is called.
    description: what it does, in plain words - this is its job definition.
    title: the short role under its name ("Research", "Inbox triage").

    The person asked for this bot in a chat. Confirm the name and the job
    with them before you make it, then call this once.
    """
    bot_name = str(name or "").strip()
    if not bot_name:
        return {"ok": False, "error": "the bot needs a name"}
    payload = await universe_bots.create_bot(
        bot_name, str(description or "").strip(), str(title or "").strip())
    if not payload.get("ok"):
        return {"ok": False,
                "error": payload.get("error", "universe bots refused the new bot")}
    return {"ok": True,
            "id": payload.get("id", ""),
            "name": payload.get("name", bot_name),
            "text": (f"{payload.get('name', bot_name)} joined the roster in "
                     f"Universe Bots.")}


async def result(assignment_id: str = "", wait_ms: int = 0,
                 turn_id: str = "") -> dict[str, Any]:
    """Read what a delegation came back with.

    assignment_id: the id the delegation returned. Leave it empty to list
        every delegation of this conversation that is still open.
    wait_ms: how long to wait for it to settle, 0 to 240000.

    A result usually arrives as a notification on its own; read it here when
    the person asks for it.
    """
    ident = str(assignment_id or "").strip()
    if ident and not ident.replace("-", "").replace("_", "").isalnum():
        return {"ok": False, "error": "that is not an assignment id"}
    payload = await universe_bots.assignment_status(ident, int(wait_ms or 0))
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error", "could not read the delegation")}
    assignment = payload.get("assignment")
    children = payload.get("children")
    outstanding = payload.get("outstanding")
    if ident:
        if not isinstance(assignment, dict):
            return {"ok": False, "error": f"no assignment {ident} is known here"}
        return {"ok": True, "text": universe_bots.describe(assignment),
                "assignment": assignment,
                "children": children if isinstance(children, list) else []}
    rows = outstanding if isinstance(outstanding, list) else []
    if not rows:
        return {"ok": True, "text": "Nothing outstanding: no delegation of this conversation is still open."}
    lines = [f"- {universe_bots.describe(row)}" for row in rows if isinstance(row, dict)]
    return {"ok": True,
            "text": f"Outstanding delegations ({len(lines)}):\n" + "\n".join(lines),
            "outstanding": rows}


def register(reg) -> None:
    reg.register(Tool(
        name="UniverseBotsList",
        capability="UniverseBotsList",
        description=(
            "List the bots in Universe Bots (the second agent app inside this "
            "one) that work can be handed to. Their ids are what "
            "UniverseBotsDelegate needs."
        ),
        handler=bots,
        consent="auto",
        allow_in_plan=True))
    reg.register(Tool(
        name="UniverseBotsDelegate",
        capability="UniverseBotsDelegate",
        description=(
            "Hand a task to one or more bots in Universe Bots. The task lands "
            "in that bot's inbox and the call returns at once; the result "
            "arrives later as a notification, so do not wait for it in this "
            "turn."
        ),
        handler=delegate,
        consent="auto",
        allow_in_plan=True))
    reg.register(Tool(
        name="UniverseBotsResult",
        capability="UniverseBotsResult",
        description=(
            "Read what a delegation to Universe Bots came back with. Give the "
            "assignment id, or leave it empty to list the delegations that "
            "are still open."
        ),
        handler=result,
        consent="auto",
        allow_in_plan=True))
    reg.register(Tool(
        name="UniverseBotsCreate",
        capability="UniverseBotsCreate",
        description=(
            "Make a bot in Universe Bots. Use it when the person asks for a "
            "new teammate: agree the name and the job with them first, then "
            "call this once."
        ),
        handler=create,
        consent="per_turn"))
