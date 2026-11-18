"""universe_desktop_bridge/universe_bots.py — the Universe's side of the
universe bots link.

universe bots (OpenMausBot, renamed) runs its own harness on a loopback
port. It offers three peer routes to a bot whose engine lives OUTSIDE that
harness:

    GET  /api/internal/agents
    POST /api/internal/coordinate-bots
    GET  /api/internal/assignment-status

They answer only to a standing bearer capability, which the harness reads
from ``<data dir>/external-runtimes.json``::

    { "<botId>": { "token": "<secret>", "threadId": "<conversation>" } }

The harness re-reads that file on every request, so it is written atomically
and the grants of other bots are preserved. The binding is permanent: the
same bot keeps the same conversation for as long as both live, and the
harness refuses the capability the moment the bot or the conversation goes
away.

The Universe speaks through the place's own Universe Bots — its chief of
staff, whose job is exactly this: "I can manage your other bots. Task me
with anything. I'll make sure it gets to the right bot." Its grant pins one
conversation there, and every delegation goes into the addressed bot's
inbox as a new task.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

#: The harness's owner capability. The desktop mints it per launch and
#: hands it to the harness over the parent message port; the harness then
#: trusts only requests carrying it. The bridge is the desktop's own
#: process, so it carries the same capability for the routes that change
#: something.
OWNER_HEADER = "X-UB-Desktop-Owner"

#: A grant's token must be 32-4096 characters without whitespace: the
#: harness rejects anything shorter as "a token another user could read".
_TOKEN_BYTES = 32

#: How long a delegation may hold the caller while it waits for a result.
_MAX_WAIT_MS = 240_000


def origin() -> str:
    """Where the universe bots harness listens."""
    return os.environ.get(
        "UNIVERSE_BOTS_ORIGIN", "http://127.0.0.1:8799").rstrip("/")


def data_dir() -> Path:
    """The universe bots data dir — everything it keeps lives under it."""
    configured = os.environ.get("UNIVERSE_BOTS_DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path.home() / ".universe" / "bots"


def runtimes_path() -> Path:
    """The grants file the harness reads on every request."""
    return data_dir() / "external-runtimes.json"


def read_grants() -> dict[str, dict[str, str]]:
    """Every grant on this machine, keyed by bot id.

    A file that cannot be read is an empty set of grants: the harness
    treats it the same way, so the two never disagree.
    """
    try:
        raw = runtimes_path().read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    grants: dict[str, dict[str, str]] = {}
    for bot_id, value in parsed.items():
        if not isinstance(bot_id, str) or not isinstance(value, dict):
            continue
        token = value.get("token")
        thread_id = value.get("threadId")
        if isinstance(token, str) and isinstance(thread_id, str):
            grants[bot_id] = {"token": token, "threadId": thread_id}
    return grants


def write_grants(grants: dict[str, dict[str, str]]) -> None:
    """Write the grants file atomically.

    The harness reads it while it serves a request, so a half-written file
    would take the whole link down for a moment.
    """
    path = runtimes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(grants, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


async def _call(method: str, path: str, *, body: Any = None,
                token: str | None = None,
                timeout: float = 20.0) -> tuple[int, Any]:
    """One request to the harness. Returns (status, decoded body)."""
    headers = {"content-type": "application/json"}
    owner = os.environ.get("UNIVERSE_BOTS_OWNER_TOKEN", "").strip()
    if owner:
        headers[OWNER_HEADER] = owner
    if token:
        headers["authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, f"{origin()}{path}",
                                        headers=headers, content=payload)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"error": response.text[:400]}


def _error(status: int, body: Any, fallback: str) -> dict[str, Any]:
    message = ""
    if isinstance(body, dict):
        message = str(body.get("error") or "")
    return {"ok": False, "status": status, "error": message or fallback}


async def _bots() -> list[dict[str, Any]]:
    """Every bot this place has, as the harness reports them."""
    try:
        status, body = await _call("GET", "/api/bots", timeout=10.0)
    except httpx.HTTPError:
        return []
    if status != 200 or not isinstance(body, dict):
        return []
    bots = body.get("bots")
    if not isinstance(bots, list):
        return []
    return [bot for bot in bots if isinstance(bot, dict)]


def _speaker(bots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The bot the Universe speaks through: the place's own Universe Bots
    (its chief of staff) when it is there, else any bot a person can see."""
    for bot in bots:
        if bot.get("hidden"):
            continue
        if bot.get("defaultBot") or bot.get("chiefOfStaff"):
            return bot
    for bot in bots:
        if not bot.get("hidden"):
            return bot
    return None


def _thread_ids(bot: dict[str, Any]) -> list[str]:
    tasks = bot.get("tasks")
    if not isinstance(tasks, list):
        return []
    return [str(task.get("threadId")) for task in tasks
            if isinstance(task, dict) and task.get("threadId")]


async def gateway() -> dict[str, Any]:
    """The Universe's identity inside universe bots and its capability for
    one conversation there.

    An existing grant is kept: the binding is permanent, and minting a new
    token would revoke the one other tools may already be using.
    """
    bots = await _bots()
    if not bots:
        return {"ok": False,
                "error": "universe bots has no bots yet — open the Bots surface once"}
    speaker = _speaker(bots)
    if speaker is None:
        return {"ok": False, "error": "universe bots has no bot to speak through"}
    bot_id = str(speaker.get("id") or "")
    if not bot_id:
        return {"ok": False, "error": "universe bots reported a bot with no id"}
    grants = read_grants()
    grant = grants.get(bot_id)
    threads = _thread_ids(speaker)
    if grant and grant["threadId"] in threads:
        return {"ok": True, "botId": bot_id,
                "botName": str(speaker.get("name") or ""),
                "threadId": grant["threadId"], "token": grant["token"]}
    thread_id = str(speaker.get("threadId") or (threads[0] if threads else ""))
    if not thread_id:
        return {"ok": False,
                "error": "that bot has no conversation to bind to"}
    token = "ubrt-" + secrets.token_urlsafe(_TOKEN_BYTES)
    grants[bot_id] = {"token": token, "threadId": thread_id}
    write_grants(grants)
    return {"ok": True, "botId": bot_id,
            "botName": str(speaker.get("name") or ""),
            "threadId": thread_id, "token": token}


async def list_agents() -> dict[str, Any]:
    """The bots the Universe may hand work to."""
    grant = await gateway()
    if not grant.get("ok"):
        return grant
    status, body = await _call("GET", "/api/internal/agents",
                               token=grant["token"])
    if status != 200:
        return _error(status, body, "could not read the bot roster")
    bots = body.get("bots") if isinstance(body, dict) else None
    return {"ok": True, "bots": bots if isinstance(bots, list) else [],
            "speaker": grant.get("botName", "")}


async def delegate(bot_ids: list[str], message: str, *,
                   title: str | None = None, reason: str | None = None,
                   consult: bool = False, done_when: str | None = None,
                   thread: str = "standing",
                   reply: str = "resume") -> dict[str, Any]:
    """Hand a task to one or more bots. Each one takes it into its inbox.

    The call returns as soon as the work is queued; the result comes back
    later, as a notification.
    """
    ids = [str(bot_id).strip() for bot_id in bot_ids if str(bot_id).strip()]
    if not ids:
        return {"ok": False, "error": "name at least one bot"}
    text = str(message or "").strip()
    if not text:
        return {"ok": False, "error": "say what the bot should do"}
    grant = await gateway()
    if not grant.get("ok"):
        return grant
    body: dict[str, Any] = {
        "botIds": ids,
        "message": text[:4000],
        "thread": thread,
        "reply": reply,
        "consult": consult,
    }
    if title:
        body["title"] = title[:80]
    if reason:
        body["reason"] = reason[:200]
    if done_when:
        body["done_when"] = done_when[:1000]
    status, payload = await _call("POST", "/api/internal/coordinate-bots",
                                  body=body, token=grant["token"],
                                  timeout=30.0)
    if status != 200:
        return _error(status, payload, "the bots refused the task")
    receipts = payload.get("receipts") if isinstance(payload, dict) else None
    return {"ok": True,
            "rootId": str((payload or {}).get("rootId") or ""),
            "receipts": receipts if isinstance(receipts, list) else [],
            "message": str((payload or {}).get("message") or "")}


async def assignment_status(assignment_id: str = "",
                             wait_ms: int = 0) -> dict[str, Any]:
    """What happened to a delegation.

    With no id, every delegation of this conversation that is still open
    comes back. With an id, that one comes back — and with wait_ms it
    waits for it to settle.
    """
    grant = await gateway()
    if not grant.get("ok"):
        return grant
    query = ""
    if assignment_id:
        query = f"?id={assignment_id}"
    wait = max(0, min(int(wait_ms or 0), _MAX_WAIT_MS))
    if wait:
        query = f"{query}&wait_ms={wait}" if query else f"?wait_ms={wait}"
    status, body = await _call(
        "GET", f"/api/internal/assignment-status{query}",
        token=grant["token"], timeout=max(20.0, wait / 1000.0 + 20.0))
    if status != 200:
        return _error(status, body, "could not read the delegation")
    return {"ok": True, **(body if isinstance(body, dict) else {})}


async def create_bot(name: str, description: str = "",
                     title: str = "") -> dict[str, Any]:
    """Make a bot in universe bots.

    The chat that asks for one is the Universe's: this is where the answer
    becomes a bot.
    """
    bot_name = str(name or "").strip()
    if not bot_name:
        return {"ok": False, "error": "the bot needs a name"}
    body: dict[str, Any] = {"name": bot_name[:60]}
    about = str(description or "").strip()
    if about:
        body["description"] = about[:400]
    role = str(title or "").strip()
    if role:
        body["title"] = role[:80]
    status, payload = await _call("POST", "/api/bots", body=body, timeout=30.0)
    if status not in (200, 201):
        return _error(status, payload, "universe bots refused the new bot")
    bot = payload.get("bot") if isinstance(payload, dict) else None
    if not isinstance(bot, dict):
        return {"ok": False, "error": "universe bots did not return the new bot"}
    return {"ok": True,
            "id": str(bot.get("id") or ""),
            "name": str(bot.get("name") or bot_name),
            "description": str(bot.get("description") or ""),
            "threadId": str(bot.get("threadId") or "")}


PROXY_BOT_NAME = "Universe Engine"
PROXY_STATE_FILE = "universe-engine.json"


def bridge_token() -> str:
    """This process's own bridge token — the harness needs it to reach MCP."""
    return os.environ.get("UNIVERSE_BRIDGE_TOKEN", "").strip()


def tools_mcp_url() -> str:
    port = os.environ.get("UNIVERSE_BRIDGE_PORT", "8738").strip() or "8738"
    return f"http://127.0.0.1:{port}/api/universe-bots/mcp/mcp"


def proxy_state_path() -> Path:
    return runtimes_path().parent / PROXY_STATE_FILE


def proxy_state() -> dict[str, Any]:
    try:
        value = json.loads(proxy_state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_proxy_state(state: dict[str, Any]) -> None:
    path = proxy_state_path()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


async def register_tools_mcp() -> dict[str, Any]:
    """Mount Universe's tools as an MCP server the harness can use."""
    token = bridge_token()
    if not token:
        return {"ok": False, "error": "the bridge token is unavailable"}
    body = {
        "name": "universe-tools",
        "type": "http",
        "url": tools_mcp_url(),
        "headers": {"authorization": f"Bearer {token}"},
    }
    try:
        status, payload = await _call("POST", "/api/mcp/servers",
                                      body=body, timeout=30.0)
    except httpx.HTTPError:
        return {"ok": False, "error": "universe bots is not reachable"}
    # 409 means a server with that name already exists; the header may be
    # stale after a bridge restart, so update it in place.
    if status == 409:
        try:
            status, payload = await _call(
                "PATCH", "/api/mcp/servers/universe-tools",
                body={"headers": body["headers"]}, timeout=30.0)
        except httpx.HTTPError:
            return {"ok": False, "error": "universe bots is not reachable"}
    if status not in (200, 201):
        return _error(status, payload, "could not mount the tool server")
    return {"ok": True, "url": body["url"]}


async def ensure_proxy_bot(selection: dict[str, Any] | None = None) -> dict[str, Any]:
    """A hidden bot that runs engine turns for the OpenAI-compatible proxy."""
    state = proxy_state()
    bots = await _bots()
    bot = next((item for item in bots
                if item.get("id") == state.get("botId")), None)
    if bot is None:
        bot = next((item for item in bots
                    if item.get("name") == PROXY_BOT_NAME), None)
    if bot is None:
        body: dict[str, Any] = {"name": PROXY_BOT_NAME,
                                "sidebarHidden": True}
        if selection:
            body["modelSelection"] = selection
        try:
            status, payload = await _call("POST", "/api/bots",
                                          body=body, timeout=40.0)
        except httpx.HTTPError:
            return {"ok": False, "error": "universe bots is not reachable"}
        if status not in (200, 201) or not isinstance(payload, dict):
            return _error(status, payload, "could not create the proxy bot")
        bot = payload.get("bot") if isinstance(payload.get("bot"), dict) else payload
    bot_id = str(bot.get("id") or "")
    if not bot_id:
        return {"ok": False, "error": "the proxy bot has no id"}
    state["botId"] = bot_id
    save_proxy_state(state)
    return {"ok": True, "botId": bot_id}


async def enable_proxy_api(bot_id: str) -> dict[str, Any]:
    """Turn on the harness's Bot API for the proxy bot and keep its key."""
    state = proxy_state()
    if state.get("key") and state.get("botId") == bot_id:
        return {"ok": True, "key": state["key"]}
    try:
        status, payload = await _call(
            "POST", f"/api/bots/{bot_id}/bot-api",
            body={"enabled": True}, timeout=30.0)
    except httpx.HTTPError:
        return {"ok": False, "error": "universe bots is not reachable"}
    if status not in (200, 201) or not isinstance(payload, dict):
        return _error(status, payload, "could not enable the Bot API")
    key = str(payload.get("key") or "")
    if not key:
        return {"ok": False, "error": "no Bot API key was returned"}
    state["botId"] = bot_id
    state["key"] = key
    save_proxy_state(state)
    return {"ok": True, "key": key}


_engine_lock = asyncio.Lock()


async def set_bot_model(bot_id: str, selection: dict[str, Any]) -> bool:
    """Point the proxy bot at one engine/model for the next turn."""
    try:
        status, payload = await _call(
            "PATCH", f"/api/bots/{bot_id}",
            body={"modelSelection": selection}, timeout=40.0)
    except httpx.HTTPError:
        return False
    return status in (200, 201)


async def run_engine_turn(prompt: str, instance_id: str, model: str,
                          effort: str = "", variant: str = "",
                          wait_seconds: int = 120) -> dict[str, Any]:
    """Run one turn through a chosen engine and return its reply text.

    The proxy bot is hidden and shared, so the whole operation is serialized:
    its model selection is set for exactly one turn at a time.
    """
    async with _engine_lock:
        bot = await ensure_proxy_bot()
        if not bot.get("ok"):
            return bot
        bot_id = bot["botId"]
        selection: dict[str, str] = {"instanceId": instance_id, "model": model}
        if effort:
            selection["effort"] = effort
        if variant:
            selection["variant"] = variant
        if not await set_bot_model(bot_id, selection):
            return {"ok": False, "error": "could not select the engine"}

        # A fresh task per turn: the bot's main thread holds its own greeting,
        # which is not an answer to the caller.
        try:
            status, payload = await _call(
                "POST", f"/api/bots/{bot_id}/tasks",
                body={"title": "Engine turn"}, timeout=40.0)
        except httpx.HTTPError:
            return {"ok": False, "error": "universe bots is not reachable"}
        if not 200 <= status < 300 or not isinstance(payload, dict):
            return _error(status, payload, "could not open a turn thread")
        task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        thread = str(task.get("threadId") or "")
        if not thread:
            return {"ok": False, "error": "no thread for the turn"}

        try:
            status, payload = await _call(
                "POST", f"/api/bots/{bot_id}/messages",
                body={"text": prompt, "threadId": thread}, timeout=40.0)
        except httpx.HTTPError:
            return {"ok": False, "error": "universe bots is not reachable"}
        if not 200 <= status < 300 or not isinstance(payload, dict):
            return _error(status, payload, "the engine refused the turn")

    deadline = time.monotonic() + max(5, wait_seconds)
    while time.monotonic() < deadline:
        try:
            status, body = await _call(
                "GET", f"/api/threads/{thread}/messages?limit=40", timeout=20.0)
        except httpx.HTTPError:
            await asyncio.sleep(1)
            continue
        if status == 200 and isinstance(body, dict):
            rows = body.get("messages")
            if isinstance(rows, list):
                replies = [
                    str(row.get("text") or "").strip()
                    for row in rows
                    if isinstance(row, dict) and row.get("role") == "bot"
                    and row.get("kind") == "text"
                    and str(row.get("text") or "").strip()
                ]
                if replies:
                    return {"ok": True, "text": "\n\n".join(replies),
                            "threadId": thread}
        await asyncio.sleep(1)
    return {"ok": False, "error": "the engine did not answer in time",
            "threadId": thread}


def proxy_provider_name() -> str:
    return "universe-engine"


def proxy_base_url() -> str:
    port = os.environ.get("UNIVERSE_BRIDGE_PORT", "8738").strip() or "8738"
    return f"http://127.0.0.1:{port}/api/universe-bots/v1"


async def openai_models() -> dict[str, Any]:
    """The shared engine roster in OpenAI's /models shape."""
    rows = await instances()
    data = []
    for row in rows:
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        if snapshot.get("state") != "available":
            continue
        instance = str(row.get("instanceId") or "")
        options = row.get("models") if isinstance(row.get("models"), dict) else {}
        for option in options.get("options") or []:
            if not isinstance(option, dict):
                continue
            model = str(option.get("id") or "")
            if not model:
                continue
            data.append({
                "id": f"{instance}/{model}",
                "object": "model",
                "owned_by": instance,
            })
    return {"object": "list", "data": data}


def install_proxy_provider() -> dict[str, Any]:
    """Register the engine proxy as a Universe provider with this launch's key.

    The bridge token rotates per launch, so the stored key has to be refreshed
    here. The provider is rebuilt in place; nothing else in Universe's
    provider table is touched.
    """
    token = bridge_token()
    if not token:
        return {"ok": False, "error": "no bridge token"}
    try:
        from providers.manager import get_provider_manager
        from security.secrets_fallback import encrypt_secret

        manager = get_provider_manager()
        name = proxy_provider_name()
        if name in manager._providers:
            manager.remove_provider(name)
        manager.add_provider(name, proxy_base_url())
        manager.add_key(name, f"bridge-{token[:8]}", encrypt_secret(token))
        return {"ok": True, "name": name, "base_url": proxy_base_url()}
    except Exception as error:  # never let a provider refresh stop the bridge
        return {"ok": False, "error": str(error)}


async def default_model() -> dict[str, Any]:
    """The model new bots in universe bots will start with.

    The harness's `/api/config` reports only configured-or-not flags, so the
    selection itself is read from the shared data dir. This is the same file
    the harness reads on its next boot.
    """
    file = runtimes_path().parent / "config.json"
    try:
        config = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "could not read the shared settings"}
    selection = config.get("defaultModelSelection") if isinstance(config, dict) else None
    return {"ok": True, "selection": selection if isinstance(selection, dict) else {}}


async def set_default_model(instance_id: str, model: str,
                            effort: str = "", variant: str = "") -> dict[str, Any]:
    """Set the shared default selection.

    The harness validates the effort and variant against the instance's own
    capabilities, so an engine that has none must send neither.
    """
    selection: dict[str, str] = {"instanceId": str(instance_id),
                                 "model": str(model)}
    if effort:
        selection["effort"] = str(effort)
    if variant:
        selection["variant"] = str(variant)
    try:
        status, payload = await _call("PUT", "/api/config",
                                      body={"defaultModelSelection": selection},
                                      timeout=30.0)
    except httpx.HTTPError:
        return {"ok": False, "error": "universe bots is not reachable"}
    if status not in (200, 201):
        return _error(status, payload, "universe bots refused the model")
    return {"ok": True, "selection": selection}


_INSTANCES_TTL = 5.0
_instances_cache: tuple[float, list[dict[str, Any]]] | None = None


async def instances(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Every engine the harness can drive, with its live availability.

    Probing the CLIs takes a few seconds, so a short cache stands between
    the settings page and the harness. A refresh is explicit.
    """
    global _instances_cache
    moment = time.monotonic()
    if (not refresh and _instances_cache is not None and
            moment - _instances_cache[0] < _INSTANCES_TTL):
        return _instances_cache[1]
    try:
        status, body = await _call("GET", "/api/instances", timeout=30.0)
    except httpx.HTTPError:
        return _instances_cache[1] if _instances_cache else []
    if status != 200 or not isinstance(body, dict):
        return _instances_cache[1] if _instances_cache else []
    rows = body.get("instances")
    result = [row for row in rows if isinstance(row, dict)] \
        if isinstance(rows, list) else []
    _instances_cache = (moment, result)
    return result


def engine_model(option: dict[str, Any]) -> dict[str, Any]:
    """One model as the picker wants it: a label, an id, and what it can do."""
    variants = option.get("variants")
    return {
        "id": str(option.get("id") or ""),
        "label": str(option.get("label") or option.get("id") or ""),
        "contextWindow": option.get("contextWindow"),
        "provider": option.get("provider"),
        "custom": option.get("custom"),
        "variants": [variant for variant in variants if isinstance(variant, dict)]
        if isinstance(variants, list) else [],
        "needsCliUpdate": option.get("needsCliUpdate"),
    }


def engine_groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The engine roster, grouped the way universe bots' picker groups it.

    `subscription`, `api` and `custom` are the harness's own `access`
    values. Unavailable engines stay in their group with the reason, so a
    settings page can offer the install/sign-in step.
    """
    groups: dict[str, list[dict[str, Any]]] = {
        "subscription": [], "api": [], "custom": [],
    }
    for row in rows:
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
        options = row.get("models") if isinstance(row.get("models"), dict) else {}
        models = options.get("options")
        effort = capabilities.get("effortLevels")
        access = str(row.get("access") or "custom")
        engine = {
            "id": str(row.get("instanceId") or ""),
            "name": str(row.get("displayName") or row.get("instanceId") or ""),
            "driver": str(row.get("driverKind") or ""),
            "available": snapshot.get("state") == "available",
            "reason": str(snapshot.get("reason") or ""),
            "default": str(options.get("default") or ""),
            "models": [engine_model(model) for model in models if isinstance(model, dict)]
            if isinstance(models, list) else [],
            "effortLevels": [str(level) for level in effort] if isinstance(effort, list) else [],
            "supportsVariants": bool(capabilities.get("modelVariants")),
            "install": row.get("install") if isinstance(row.get("install"), dict) else {},
        }
        groups.setdefault(access, []).append(engine)
    available = sum(
        1 for group in groups.values() for engine in group if engine["available"])
    return {"ok": True, "groups": groups, "count": len(rows),
            "available": available}


async def outstanding() -> list[dict[str, Any]]:
    """The delegations this conversation still has open."""
    status, body = await _call("GET", "/api/internal/assignment-status")
    if status != 200 or not isinstance(body, dict):
        return []
    rows = body.get("outstanding")
    return [row for row in rows if isinstance(row, dict)] \
        if isinstance(rows, list) else []


def settled(status: str) -> bool:
    """Whether a delegation has come to rest."""
    return status in ("completed", "failed", "cancelled")


def describe(row: dict[str, Any]) -> str:
    """One line about a delegation, for a message or a notification."""
    name = str(row.get("botName") or row.get("botId") or "a bot")
    state = str(row.get("status") or "unknown")
    result = str(row.get("result") or "").strip()
    if result:
        return f"{name}: {result}"
    outcome = str(row.get("outcome") or "").strip()
    if outcome:
        return f"{name}: {state} ({outcome})"
    return f"{name}: {state}"


def now() -> float:
    """Seconds since the epoch — the stamp every record carries."""
    return time.time()


# --- the result watcher ------------------------------------------------------
#
# A delegation does not come back in the call that made it: the bot takes the
# task into its inbox and answers whenever it answers. The harness keeps the
# open ones, so the watcher asks it and reports what settled.

_watches: dict[str, dict[str, Any]] = {}
_reported: set[str] = set()


def remember(assignment_id: str, names: str, turn_id: str = "") -> None:
    """Remember who a delegation went to, so its result can be reported."""
    if not assignment_id:
        return
    _watches[assignment_id] = {"names": names, "turn_id": turn_id, "at": now()}


def watches() -> dict[str, dict[str, Any]]:
    """The delegations this process is waiting on."""
    return dict(_watches)


def _report(assignment: dict[str, Any],
            children: list[dict[str, Any]]) -> None:
    """Announce one settled delegation.

    A delegation's own row is the requester; the work and its result live
    in the children, so those are what the report says.
    """
    from observability.events import emit
    ident = str(assignment.get("id") or "")
    watch = _watches.get(ident, {})
    rows = [row for row in children if isinstance(row, dict)] or [assignment]
    text = "; ".join(describe(row) for row in rows)
    first = rows[0]
    emit("universe_bots_result",
         turn_id=str(watch.get("turn_id") or "") or None,
         assignment_id=ident,
         bot_name=str(first.get("botName") or assignment.get("botName") or ""),
         bots_status=str(first.get("status") or assignment.get("status") or ""),
         outcome=str(first.get("outcome") or assignment.get("outcome") or ""),
         bots_result=str(first.get("result") or ""),
         text=text)


class ResultWatcher:
    """Reports a delegation's result when it settles.

    The harness owns the open assignments, so the watcher asks it every few
    seconds and reports the ones that came to rest. A result that belongs
    to a Universe turn is emitted on that turn, so it lands in the
    conversation that asked for the work.
    """

    def __init__(self, interval: float = 5.0) -> None:
        self.interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.interval)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def tick(self) -> None:
        """One sweep: report everything that settled since the last one."""
        ids = set(_watches)
        for row in await outstanding():
            ident = str(row.get("id") or "")
            if ident:
                ids.add(ident)
        for ident in ids:
            if ident in _reported:
                continue
            detail = await assignment_status(ident)
            if not detail.get("ok"):
                continue
            assignment = detail.get("assignment")
            if not isinstance(assignment, dict):
                continue
            if not settled(str(assignment.get("status") or "")):
                continue
            children = detail.get("children")
            _reported.add(ident)
            _report(assignment,
                    [row for row in children if isinstance(row, dict)]
                    if isinstance(children, list) else [])
