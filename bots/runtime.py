"""bots/runtime.py — the bot execution engine.

Primary runtime: Meta's **Muse Code** CLI (`muse exec --json`) — the real
bots runtime per the Universe Bots design: bots are built ON Muse Code, in
the bot's own workspace directory, streaming the official MSP JSONL events.
When Muse Code is absent, or no Meta API key is configured, the run falls
back to Universe's own LLM engine — and says so honestly in the transcript
(nothing pretends to be Muse).

Risk gating (Grok-Bot parity): a task that matches the risky-step pattern
(send / publish / purchase / push …) is held in the approvals queue — the
bot status becomes waiting_approval and the run starts only after the user
allows it. "Require approval" always wins.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from bots import store
from providers.meta_media import resolve_key

# risky-step heuristic: these verbs route through the approvals queue
RISKY_RE = re.compile(
    r"\b(send|email|publish|post|purchase|buy|pay|checkout|push|deploy|"
    r"delete|remove all|format)\b", re.IGNORECASE)

_active: dict[str, asyncio.Task[Any]] = {}

# JSONL payload types worth surfacing as transcript milestones (first hit
# per type per run — a long muse run emits hundreds of low-level events)
_MILESTONE_HINTS = ("file", "edit", "command", "tool", "search", "browser",
                    "terminal", "plan")
_seen_milestones: set[str] = set()


def is_risky(task: str, bot: dict[str, Any] | None = None) -> bool:
    if bot and any(re.search(rf"\b{re.escape(k)}\b", task, re.IGNORECASE)
                   for k in ("send", "email", "publish", "purchase", "push")
                   if k in (bot.get("always_allow") or [])):
        return False
    return bool(RISKY_RE.search(task or ""))


async def request_run(bot_id: str, task: str) -> dict[str, Any]:
    """Entry point used by the bridge. Risky tasks are queued for approval;
    everything else starts immediately as a fire-and-forget task."""
    bot = store.get_bot(bot_id)
    if bot is None:
        raise KeyError(bot_id)
    if bot_id in _active and not _active[bot_id].done():
        return {"started": False, "reason": "bot is already running"}
    if is_risky(task, bot):
        aid = store.add_approval(
            bot_id, "risky_step",
            f"“{task[:120]}” looks like a risky step",
            "Require approval: sending, publishing or purchasing steps wait "
            "for an explicit decision (Allow once / Always allow / Deny).")
        store.append_event(bot_id, "approval",
                           f"Waiting for approval: {task[:200]}")
        return {"started": False, "approval_required": True,
                "approval_id": aid}
    return _spawn(bot_id, task)


def _spawn(bot_id: str, task: str) -> dict[str, Any]:
    store.append_event(bot_id, "user", task)
    t = asyncio.create_task(_execute(bot_id, task))
    _active[bot_id] = t
    return {"started": True, "run_id": f"run-{id(t):x}"}


def stop_run(bot_id: str) -> bool:
    t = _active.get(bot_id)
    if t is None or t.done():
        return False
    t.cancel()
    return True


# ---- execution ---------------------------------------------------------------

async def _execute(bot_id: str, task: str) -> None:
    bot = store.get_bot(bot_id)
    if bot is None:
        return
    store.set_status(bot_id, "running", result=None)
    store.append_event(bot_id, "system", f"Task started: {task[:200]}")
    error: str | None = None
    final_text = ""
    try:
        muse_bin = shutil.which("muse")
        key: str | None = None
        try:
            key = await resolve_key()
        except Exception:
            key = None
        if muse_bin and key is not None:
            final_text, error = await _run_muse(bot_id, bot, task,
                                                muse_bin, key)
        else:
            if muse_bin and key is None:
                store.append_event(
                    bot_id, "system",
                    "Muse Code is installed but no Meta API key is configured "
                    "— running with Universe's built-in engine instead. Add "
                    "MODEL_API_KEY in Settings → Providers → Meta to run bots "
                    "on Muse Code.")
            final_text, error = await _run_llm(bot_id, bot, task)
    except asyncio.CancelledError:
        store.set_status(bot_id, "idle", result="stopped by the user")
        store.append_event(bot_id, "system", "Run stopped by the user.")
        _active.pop(bot_id, None)
        raise
    except Exception as exc:  # noqa: BLE001 — a bot run must never crash the loop
        logger.warning(f"bot {bot_id} run failed: {exc}")
        error = str(exc)
    if error:
        store.set_status(bot_id, "error", result=error[:500])
        store.append_event(bot_id, "system", f"Run failed: {error[:400]}")
    else:
        store.set_status(bot_id, "idle",
                         result=(final_text or "done")[:500])
        if final_text:
            store.append_event(bot_id, "bot", final_text[:4000])
        else:
            store.append_event(bot_id, "system", "Run completed.")
    _active.pop(bot_id, None)


async def _run_muse(bot_id: str, bot: dict[str, Any], task: str,
                    muse_bin: str, key: str) -> tuple[str, str | None]:
    """Real Muse Code run: `muse exec --json` in the bot workspace, Meta key
    injected as META_API_KEY (the variable Meta's CLI documents)."""
    ws = Path(bot["workspace"])
    ws.mkdir(parents=True, exist_ok=True)
    prompt_file = ws / ".universe-task.md"
    skill = bot.get("skill")
    prompt = task if not skill else (
        f"Run the saved routine {skill}. Job definition: {bot['description']}\n\n"
        f"Current task: {task}")
    prompt += ("\n\nShared computer: all Universe bots can read and write "
               f"files under {store.shared_dir()} — write your outputs there "
               "and read other bots' outputs from there.")
    prompt_file.write_text(prompt, encoding="utf-8")
    cmd = [muse_bin, "exec", "--json", "--provider", "meta",
           "--prompt-file", str(prompt_file)]
    env = dict(os.environ)
    env["META_API_KEY"] = key
    env.setdefault("MUSE_INSTALL_DIR", str(Path(muse_bin).parent))
    final_text, error = "", None
    _seen_milestones.clear()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ws), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                if line:
                    store.append_event(bot_id, "system", line[:300])
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            text, err, approval = _digest_muse_event(evt)
            if text:
                store.append_event(bot_id, text[0], text[1])
                if text[0] == "bot":
                    final_text = text[1]
            if err:
                error = err
            if approval:
                store.add_approval(bot_id, approval[0], approval[1],
                                   approval[2])
        await proc.wait()
        if proc.returncode not in (0, None) and not error:
            error = f"Muse Code exited with code {proc.returncode}"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        error = f"Muse runtime error: {exc}"
    return final_text, error


def _digest_muse_event(
        evt: dict[str, Any]) -> tuple[tuple[str, str] | None, str | None,
                                      tuple[str, str, str] | None]:
    """Map one official MSP JSONL record onto (transcript event, error,
    approval). Tolerant by design: unknown payload types are milestones at
    most, never crashes."""
    pt = str(evt.get("payload_type") or "")
    payload = evt.get("payload") or {}
    if not pt:
        return None, None, None
    if "approval" in pt or "permission" in pt:
        summary = str(payload.get("title") or payload.get("summary")
                      or payload.get("kind") or pt)
        return None, None, (pt, summary, json.dumps(payload)[:800])
    if pt.endswith(".failed") or "error" in pt:
        reason = ""
        ev = payload.get("event")
        if isinstance(ev, dict):
            reason = str(ev.get("reason") or ev.get("message") or "")
        reason = reason or str(payload.get("reason") or payload.get("message") or pt)
        return None, reason, None
    if pt in ("run.lifecycle.started",):
        return ("system", "Muse run started."), None, None
    if "completed" in pt and ("run" in pt or "turn" in pt):
        text = (payload.get("text") or payload.get("message")
                or payload.get("result") or "")
        if isinstance(text, str) and text.strip():
            return ("bot", text.strip()[:4000]), None, None
        return ("system", "Muse run completed."), None, None
    for hint in _MILESTONE_HINTS:
        if hint in pt and pt not in _seen_milestones:
            _seen_milestones.add(pt)
            detail = payload.get("kind") or payload.get("path") or payload.get("cmd")
            label = f"{pt.split('.')[-1]}: {detail}" if detail else pt
            return ("system", str(label)[:200]), None, None
    return None, None, None


async def _run_llm(bot_id: str, bot: dict[str, Any],
                   task: str) -> tuple[str, str | None]:
    """Universe-engine fallback: the bot's job runs through the configured
    provider chain (the same routing chat uses)."""
    try:
        from providers.client import get_llm_client
        client = get_llm_client()
        messages = [
            {"role": "system",
             "content": (f"You are '{bot['name']}', an autonomous Universe bot. "
                         f"Job definition (sources and boundaries): "
                         f"{bot['description']} Complete the task and report "
                         "the outcome concisely. If the task requires an "
                         "action you cannot take (sending email, purchasing, "
                         "publishing), say exactly what you would do and stop. "
                         f"Shared computer: write outputs under "
                         f"{store.shared_dir()} so other bots can read them.")},
            {"role": "user", "content": task},
        ]
        res = await client.complete(agent="bots", messages=messages)
        text = ""
        if isinstance(res, dict):
            text = str(res.get("content") or res.get("text")
                       or res.get("message") or "")
        elif isinstance(res, str):
            text = res
        text = text.strip()
        if not text:
            return "", "the engine returned an empty reply"
        return text, None
    except Exception as exc:  # noqa: BLE001
        return "", ("no runtime available — install Muse Code "
                    "(curl -fsSL https://dev.meta.ai/install.sh | bash) or "
                    f"configure a provider key. Upstream: {exc}")
