"""universe_desktop_bridge/app.py — the FastAPI bridge.

Electron renderer  ──HTTP──  this bridge  ──  the real Universe core
                    ──WS──── (live events)    (orchestrator, gate, memory)

Contract (§4 of the desktop plan):
  GET  /api/health                 liveness + version + active goals
  GET  /api/memory                 live universe snapshot (systems/orbits/
                                   skills/stars + L1 goal planets)
  GET  /api/sessions                  real goal history from SQLite
  POST /api/turn                   {text, language?} -> {turn_id} (async task)
  POST /api/turn/{id}/cancel       task.cancel() (orchestrator handles it)
  GET  /api/turn/{id}              status + result + event summary
  GET  /api/turn/{id}/events       missed-event refill after reconnect
  POST /api/consent/{id}           {approve: bool} for a pending consent
  GET  /api/consent/pending        list open consent prompts
  GET/PUT /api/settings            desktop settings (config.json)
  WS   /api/events                 live event stream + 15s heartbeat

Errors are ALWAYS {"error": {"code", "message"}} — no stacktrace ever
leaves the process. The bridge binds 127.0.0.1 only; CORS allows localhost
origins only.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

# httpx is imported lazily everywhere it's used — but the FIRST import costs
# ~1.2s ON THE EVENT LOOP inside an async handler, blocking every request
# behind the first probe. Warm it at module import so no request ever pays.
import httpx  # noqa: F401  (used by _probe_provider / voice_check via local imports)

from loguru import logger

from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from config import UNIVERSE_VERSION, get_config
from universe_desktop_bridge.consent import ConsentManager
from universe_desktop_bridge.memory_view import memory_snapshot
from universe_desktop_bridge.state import get_state, reset_state_for_tests
from observability.events import emit, get_event_bus
from storage.local import get_storage

DEFAULT_PORT = 8738
HEARTBEAT_S = 15.0

# idempotency window for POST /api/turn client_msg_id: within this many
# seconds a duplicate send returns the original goal instead of double-run
_CLIENT_MSG_TTL_S = 60.0
_seen_client_msgs: dict[str, tuple[str, float]] = {}

# short-lived cache of the local-server reachability probes feeding
# /api/providers/catalog: Settings + Welcome + badge fetch the catalog in
# the same session moments, and each cold probe costs up to its timeout.
_catalog_probe_cache: dict[str, object] = {"value": None, "at": 0.0}

# per-provider LIVE model lists, pre-warmed at bridge startup (no TTL):
# every installed provider is probed once when the bridge boots so the
# model picker is instant when the user opens it. Re-probe happens on
# install / model switch / key changes (_invalidate_models_cache).
_models_cache: dict[str, list[str]] = {}


def _invalidate_models_cache(provider: str) -> None:
    _models_cache.pop(provider, None)


async def _prewarm_models() -> None:
    """Probe every installed provider's model list at bridge startup so
    the model picker is instant. Failures are silent (the endpoint
    re-probes on demand when a pre-warm result is missing)."""
    try:
        from providers.manager import get_provider_manager
        from security.secrets_fallback import decrypt_secret
        pm = get_provider_manager()
        for provider in pm.list_providers():
            try:
                key = ""
                pk = pm.pick_key(provider.name)
                if pk is not None:
                    try:
                        key = decrypt_secret(pk.encrypted_secret)
                    except Exception:
                        key = ""
                probe = await _probe_provider(provider.base_url, key)
                if probe["ok"]:
                    _models_cache[provider.name] = probe.get("models", [])
                    logger.info(f"pre-warmed {provider.name}: "
                                f"{len(probe.get('models', []))} models")
            except Exception as exc:
                logger.warning(f"pre-warm {provider.name} failed: {exc}")
    except Exception as exc:
        logger.warning(f"pre-warm models failed: {exc}")


def _prune_client_msgs() -> None:
    """Keep the idempotency map bounded: drop entries past the TTL window
    (oldest-first). Never grows past in-flight traffic × window."""
    now = time.time()
    expired = [k for k, (_, ts) in _seen_client_msgs.items()
               if now - ts > _CLIENT_MSG_TTL_S]
    for k in expired:
        _seen_client_msgs.pop(k, None)


# ---- runtime ---------------------------------------------------------------

# ---- internal bus consumer ------------------------------------------------
# The bridge used to fill goal ring buffers ONLY from the /api/events WS
# loop: no connected UI meant no ring fill, so reconnect refill (goal_events
# after_seq) returned gaps and the React seq-dedup had nothing to work with.
# A single process-wide consumer now feeds every bus event into its goal's
# ring from boot — the WS loop only forwards. Started in init(), idempotent.

_consumer_task: Any = None


async def _consume_bus_forever() -> None:
    """Pump every bus event into its goal's ring buffer (seq stamping
    happens in EventBus.emit). Survives bus resets (tests) by re-subscribing
    when the stream ends OR when the bus object is swapped under us: an
    untimed wait on a DEAD subscription never wakes when the bus is
    replaced, which left a fresh-bridge process silently unable to fill
    rings (verified in live validation). The short timeout turns that hang
    into a cheap re-subscribe check."""
    from observability.events import get_event_bus
    from universe_desktop_bridge.state import get_state
    while True:
        try:
            bus = get_event_bus()
            sub = bus.subscribe()
            try:
                while True:
                    try:
                        ev = await sub.next_event(timeout=5.0)
                    except asyncio.TimeoutError:
                        if get_event_bus() is not bus:
                            break  # bus swapped — re-subscribe below
                        continue
                    if ev is None:
                        break  # bus was reset (tests) — re-subscribe below
                    gid = ev.get("turn_id")
                    if gid:
                        gt = get_state().get(gid)
                        if gt is not None:
                            gt.add_event(dict(ev))
            finally:
                sub.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1.0)


class Runtime:
    """Everything the bridge owns beyond FastAPI's own state."""

    def __init__(self) -> None:
        self.started_at = time.time()
        self.consent = ConsentManager()
        from universe_desktop_bridge.questions import QuestionsBroker
        self.questions = QuestionsBroker()
        self.orchestrator: Any = None
        # follow-up queue (interaction_behavior="queue"): task_id -> [text].
        # Buffered while a goal runs on that task, drained in order by the
        # goal's own _run coroutine when it finishes (never concurrent runs).
        self.followup_queues: dict[str, list[str]] = {}
        self._followup_lock = asyncio.Lock()
        self.init_error: str = ""
        self._initialized = False

    def init(self) -> None:
        """Heavy imports + singleton wiring. Safe to call once; never raises
        out — a failed init is reported via /api/health instead."""
        if self._initialized:
            return
        self._initialized = True
        try:
            from schema.migrations import ensure_latest
            ensure_latest()
            # per-launch ownership id (the old sweep used to set this lazily;
            # goals carry it so a future sweep/UI can tell sessions apart)
            import uuid as _uuid
            if not getattr(self, "session_id", ""):
                self.session_id = f"bridge-{_uuid.uuid4().hex[:12]}"
            # documented 90-day audit retention was never wired (audit
            # 2026-09) — apply it at startup, then check the chain seam
            from security.audit import prune_at_startup, verify_chain
            prune_at_startup()
            seam = verify_chain(limit=500)
            if not seam["ok"]:
                emit("audit_chain_broken", first_bad_line=seam["first_bad_line"])
            from core.agent.orchestrator import get_orchestrator
            from security.capability import get_gate
            self.orchestrator = get_orchestrator()
            get_gate().set_consent_handler(self.consent.handler)
            # AskUserQuestion twin: the question.ask tool resolves through
            # the bridge's QuestionsBroker (question_required event + POST)
            from connectors.builtin.ask_user import set_question_handler
            set_question_handler(self.questions.ask)
            # automations: the cron scheduler ticks on the bridge's loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                from scheduling import get_scheduler
                get_scheduler().start()
            self._sweep_orphaned_goals()
            # pre-warm every installed provider's model list at startup so
            # the model picker is instant — no cache TTL, one probe each
            if loop is not None:
                loop.create_task(_prewarm_models())
            # internal bus consumer: ring buffers fill from boot, not from
            # the first WS connection. (Re)started on EVERY init: the module
            # global may hold a task frozen on a previous event loop (tests
            # spin fresh TestClient loops), whose .done() stays False —
            # skipping on it left every later bridge with dead rings. The
            # old task can never run again (its loop is gone); add_event's
            # seq-dedup makes any overlap harmless.
            global _consumer_task
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                if _consumer_task is not None:
                    try:
                        _consumer_task.cancel()
                    except Exception:
                        pass
                _consumer_task = loop.create_task(_consume_bus_forever())
        except Exception as exc:  # bridge must still serve /api/health
            self.init_error = str(exc)[:300]

    def _sweep_orphaned_goals(self) -> None:
        """A hard kill (crash, force-quit) leaves goals stuck 'running' in
        the DB with no process able to finish them. Mark them failed with
        the honest reason — the UI then shows a terminal state + retry,
        never an eternal spinner.

        (failure-diagnosis.md root #4) The old sweep matched only THIS
        session's fresh random id — which no pre-existing row can carry —
        plus empty owners, so goals from dead sessions stayed 'running'
        forever (one survived 139.8h across dozens of restarts). The
        desktop bridge is the single goal runner (Electron single
        instance), so at startup EVERY stale running row is an orphan.
        One guard stays: rows younger than 10 minutes are left alone in
        case a parallel CLI run legitimately owns them."""
        try:
            st = get_storage()
            cutoff = time.time() - 600
            with st.transaction() as conn:
                rows = conn.execute(
                    "SELECT id FROM goals WHERE status='running' AND "
                    "COALESCE(created_at, 0) < ?;",
                    (cutoff,)).fetchall()
                for (gid,) in rows:
                    conn.execute(
                        "UPDATE goals SET status='failed', completed_at=?, "
                        "final_summary=? WHERE id=?;",
                        (time.time(), json.dumps({
                            "turn_id": gid, "success": False,
                            "summary": "interrupted: the backend exited while "
                                       "this goal was running"}), gid))
            if rows:
                logger.info(f"swept {len(rows)} orphaned goal(s) to failed")
        except Exception as exc:  # sweep must never block startup
            logger.warning(f"orphan sweep failed: {exc}")


_runtime = Runtime()


def get_runtime() -> Runtime:
    return _runtime


def reset_runtime_for_tests() -> Runtime:
    global _runtime
    _runtime = Runtime()
    reset_state_for_tests()
    return _runtime


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _runtime.init()
    # Universe Bots: the ROUTINE scheduler ticks bots.json every 30s and
    # fires due daily/interval/cron schedules (TASK→SKILL→ROUTINE).
    from bots.scheduler import get_scheduler
    get_scheduler().start()
    yield
    try:
        await get_scheduler().stop()
    except Exception:
        pass
    # release pooled provider sockets (AsyncOpenAI connection pools) —
    # without this every restart leaks half-open TLS connections until
    # the OS reaps them
    try:
        from providers.client import get_llm_client
        await get_llm_client().aclose()
    except Exception:
        pass


# ---- app --------------------------------------------------------------------

app = FastAPI(title="Universe Desktop Bridge", version=UNIVERSE_VERSION,
              lifespan=lifespan)

# The auth gate runs OUTSIDE (before) CORS so a rejected cross-site request
# never even reaches the CORS layer — and so WebSocket routes (which CORS
# middleware skips entirely) are covered too. Token + origin discipline:
# see universe_desktop_bridge/auth.py.
from universe_desktop_bridge.auth import BridgeAuthMiddleware  # noqa: E402

app.add_middleware(BridgeAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    # dev/preview serve from localhost ports; the packaged Electron app loads
    # the client from the app://universe custom protocol — both must pass CORS
    allow_origin_regex=(
        r"^(app://universe"
        r"|https?://(localhost|127\.0\.0\.1)(:\d+)?)$"
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- error discipline: {error:{code,message}}, never a stacktrace -----------

from starlette.exceptions import HTTPException as _StarletteHTTPException


@app.exception_handler(_StarletteHTTPException)
async def _http_error(_: Request, exc: _StarletteHTTPException) -> JSONResponse:
    # registered against the Starlette parent so routing 404/405s (which
    # raise the parent class, not fastapi's subclass) get the same shape.
    # A dict detail carries a specific code; a bare string falls back to http_N.
    code = f"http_{exc.status_code}"
    if isinstance(exc.detail, dict) and exc.detail.get("code"):
        code = str(exc.detail["code"])
    return JSONResponse(status_code=exc.status_code,
                        content={"error": {"code": code,
                                           "message": str(exc.detail if not isinstance(exc.detail, dict) else exc.detail.get("message", ""))}})


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    # server-side detail only; the wire gets a clean, stable shape
    try:
        from loguru import logger
        logger.exception("bridge error")
    except Exception:
        pass
    return JSONResponse(status_code=500, content={"error": {
        "code": "internal_error", "message": "the bridge hit an unexpected error"}})


# ---- models -------------------------------------------------------------------

class GoalRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    language: str = "en"
    project_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    turn_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    goal_id: str | None = Field(default=None, max_length=64)
    parent_goal_id: str | None = Field(default=None, max_length=64)
    # idempotency: a client-generated id; a duplicate within the window is
    # rejected with the original turn_id instead of double-sending.
    client_msg_id: str | None = Field(default=None, max_length=64)
    # ZCode "Max" chip (thinking level): fast/balanced/max maps to the goal's
    # per-round token budget in the agent loop. Absent = the effort field or
    # the 4096 default — additive and optional, so older clients are unaffected.
    effort: str | None = Field(default=None, pattern="^(low|medium|high)$")
    # the ZCode-parity name for the same chip (Settings picker vocabulary):
    # fast/balanced/max; when present it wins over the legacy effort value.
    thinking_level: str | None = Field(default=None, pattern="^(fast|balanced|max)$")
    # composer attachments (images / video / docs): real absolute file
    # paths, validated + capped in start_goal and merged into the goal
    # text as one fenced block — the agent reads them with its tools
    # (VisionAnalyze for images, shell ffprobe for video) instead of
    # receiving megabytes inline
    attachments: list[str] = Field(default_factory=list)


def _sanitize_attachments(paths: list[str]) -> list[str]:
    """Only real, non-empty files survive: attributes the picker or the OS
    paste actually produced. De-duplicated case-insensitively (Windows
    paths), capped at 8 per message — a stale picker path is DROPPED, never
    a 422 for the user's send."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in (paths or [])[:24]:
        if not isinstance(raw, str):
            continue
        p = raw.strip().strip("\"'")
        if not p or len(p) > 2000:
            continue
        key = p.casefold()
        if key in seen:
            continue
        try:
            if not (os.path.isfile(p) and os.path.getsize(p) > 0):
                continue
        except OSError:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= 8:
            break
    return out


def _text_with_attachments(text: str, attachments: list[str]) -> str:
    """One fenced block after the user's text: the paths are REAL local
    files the agent reads with its own tools — never content to invent."""
    block = "\n".join(f"- {p}" for p in attachments)
    return (
        f"{text}\n\n---\nAttached files (REAL local files — read them with "
        f"your tools before saying anything about their content; images: "
        f"VisionAnalyze, video/audio: shell + ffprobe; never claim to have "
        f"seen one you did not read):\n{block}"
    )


def _resolve_effort(effort: str | None, thinking_level: str | None) -> str | None:
    """One vocabulary for the thinking-level chips: the ZCode-parity
    fast/balanced/max names win when present; the legacy low/medium/high
    effort value survives for older clients (fast≈low, max≈high)."""
    if thinking_level:
        return {"fast": "low", "balanced": "medium", "max": "high"}[thinking_level]
    return effort


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None
    pinned: bool | None = None
    project_id: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    root: str = ""


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    root: str | None = None
    archived: bool | None = None


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    transport: str = Field(pattern="^(stdio|http|sse)$")
    command: str = ""            # stdio
    url: str = ""                # http/sse
    headers: dict[str, str] = Field(default_factory=dict)
    declared_hosts: list[str] = Field(default_factory=list)
    read_only: bool = True


class ConsentDecision(BaseModel):
    approve: bool


class SettingsUpdate(BaseModel):
    wake_word: str | None = None
    voice_enabled: bool | None = None
    stt_provider: str | None = None
    stt_api_key: str | None = None
    stt_model: str | None = None
    stt_cloud_model: str | None = None
    stt_language: str | None = None
    tts_provider: str | None = None
    tts_api_key: str | None = None
    tts_voice: str | None = None
    tts_model: str | None = None
    assistant_name: str | None = None
    project_root: str | None = None
    # default language hints for goal runs ("en" | "ar" | "" = auto).
    # sendGoal sends it instead of the hardcoded "en".
    response_language: str | None = None
    # ZCode parity: what a message typed WHILE a goal is running does —
    # "steer" = course-correct mid-run (existing); "queue" = run as the
    # next follow-up once the goal finishes (never interrupts)
    interaction_behavior: str | None = None
    # ask_user auto-continue timeout (seconds); 5–3600, default 300
    auto_continue_questions_s: int | None = None
    # ---- Phase 3 (settings page) — additive keys, all default-on ----------
    # global hook kill switch: when False HookManager._load reads zero
    # hooks (builtins included) and every call passes until re-enabled
    hooks_enabled: bool | None = None
    # the user's desktop.hooks[] rule list (event/matcher/action/message)
    hooks: list[dict[str, Any]] | None = None
    # composer "+ Add context" text attachments (v1) — default on
    attachments_enabled: bool | None = None
    # chat view: fold consecutive same-family tool calls into one row
    group_explore_tools: bool | None = None
    group_terminal_tools: bool | None = None
    group_change_tools: bool | None = None
    # the composer's default Max-chip level for NEW messages (per-message
    # pick still wins; validated fast|balanced|max below)
    default_thinking_level: str | None = None
    custom_commands: list[dict[str, Any]] | None = None
    theme: str | None = None
    locale: str | None = None
    uiFontSize: int | None = None
    fontSize: int | None = None
    integratedTerminalShell: str | None = None
    terminalProfile: str | None = None
    terminalFontFamily: str | None = None
    httpProxy: str | None = None
    httpProxyNoProxy: str | None = None
    httpProxyCaCertPath: str | None = None
    closeToTrayOnWindows: bool | None = None
    keepAwakeWhileRunning: bool | None = None
    autoDownloadAndInstallUpdates: bool | None = None
    taskAutoArchive: str | None = None
    taskAutoArchiveDays: int | None = None
    wrapLongLines: bool | None = None
    dataBaseDir: str | None = None
    browserHeadless: bool | None = None
    browserAllowInsecureCerts: bool | None = None
    computerUseEnabled: bool | None = None
    computerUseComposerEntry: bool | None = None
    indexingEnabled: bool | None = None
    indexingRoot: str | None = None


DESKTOP_DEFAULTS = {
    "wake_word": "wake up L",
    "voice_enabled": False,
    "stt_provider": "local",
    "stt_api_key": "",
    "stt_model": "",
    "stt_cloud_model": "",
    "stt_language": "",
    "tts_provider": "edge-tts",
    "tts_api_key": "",
    "tts_voice": "",
    "tts_model": "",
    "assistant_name": "Universe",
    "response_language": "",
    "interaction_behavior": "steer",
    "auto_continue_questions_s": 300,
    "hooks_enabled": True,
    "hooks": [],
    "attachments_enabled": True,
    "group_explore_tools": True,
    "group_terminal_tools": True,
    "group_change_tools": True,
    "default_thinking_level": "balanced",
    "custom_commands": [],
    "theme": "dark",
    "locale": "system",
    "uiFontSize": 14,
    "fontSize": 13,
    "integratedTerminalShell": "auto",
    "terminalProfile": "",
    "terminalFontFamily": "",
    "httpProxy": "",
    "httpProxyNoProxy": "localhost,127.0.0.1,::1",
    "httpProxyCaCertPath": "",
    "closeToTrayOnWindows": False,
    "keepAwakeWhileRunning": False,
    "autoDownloadAndInstallUpdates": True,
    "taskAutoArchive": "off",
    "taskAutoArchiveDays": 7,
    "wrapLongLines": False,
    "dataBaseDir": "",
    "browserHeadless": False,
    "browserAllowInsecureCerts": False,
    "computerUseEnabled": False,
    "computerUseComposerEntry": False,
    "indexingEnabled": False,
    "indexingRoot": "",
}


def _read_desktop_settings() -> dict[str, Any]:
    stored = get_config().get("desktop", {})
    if not isinstance(stored, dict):
        stored = {}
    merged = dict(DESKTOP_DEFAULTS)
    merged.update({k: stored[k] for k in DESKTOP_DEFAULTS if k in stored})
    # voice keys are stored as AES blobs (see write_settings) and decrypted
    # for in-process use only; legacy plaintext values pass through so old
    # installs keep working until the user next saves settings
    for k in ("stt_api_key", "tts_api_key"):
        if merged.get(k):
            merged[k] = _safe_decrypt(str(merged[k]))
    return merged


def _mask_secret(v: str) -> str:
    """Keys are never echoed back over HTTP; the UI shows a masked preview."""
    if not v:
        return ""
    if len(v) <= 8:
        return "••••••"
    return f"{v[:3]}…{v[-3:]}"


def _masked_view(settings: dict[str, Any]) -> dict[str, Any]:
    out = dict(settings)
    for k in ("stt_api_key", "tts_api_key"):
        out[k] = _mask_secret(str(out.get(k, "")))
    return out


def _safe_decrypt(blob: str) -> str:
    """Decrypt only to mask it: an undecryptable (or legacy plaintext) blob
    yields '' so the UI shows a dead-key hint rather than leaking garbage."""
    if not blob:
        return ""
    try:
        from security.secrets_fallback import decrypt_secret
        return decrypt_secret(blob)
    except Exception:
        return ""


# ---- HTTP endpoints ---------------------------------------------------------

@app.get("/api/health")
async def health() -> dict[str, Any]:
    _runtime.init()  # lazy + idempotent: covers servers that skip lifespan
    st = get_state()
    return {
        "ok": not _runtime.init_error,
        "version": UNIVERSE_VERSION,
        "uptime_s": round(time.time() - _runtime.started_at, 1),
        "ready": _runtime.orchestrator is not None,
        "active_goals": len(st.running()),
        "error": _runtime.init_error or None,
    }


@app.get("/api/memory")
async def memory() -> dict[str, Any]:
    return memory_snapshot()


# ---- Round-7 CC parity readouts (/status /version /context /doctor /memory) —

@app.get("/api/status-parity")
async def status_parity() -> dict[str, Any]:
    """One real readout for the CC-parity commands. Every field is live
    data — no filler, no placeholder text."""
    _runtime.init()
    st = get_state()
    out: dict[str, Any] = {
        "ok": True,
        "version": UNIVERSE_VERSION,
        "uptime_s": round(time.time() - _runtime.started_at, 1),
        "ready": _runtime.orchestrator is not None,
        "init_error": _runtime.init_error or None,
        "active_goals": len(st.running()),
    }
    try:
        from security.capability import get_gate
        out["permission_mode"] = get_gate().get_permission_mode()
    except Exception as exc:
        out["permission_mode_error"] = str(exc)
    try:
        snap = memory_snapshot()
        out["memory"] = {
            "systems": len(snap.get("systems", [])),
            "orbits": len(snap.get("orbits", [])),
            "skills": len(snap.get("skills", [])),
        }
    except Exception as exc:
        out["memory_error"] = str(exc)
    return out


class ExportRequest(BaseModel):
    turn_id: str = ""


@app.post("/api/export")
async def export_transcript(req: ExportRequest) -> dict[str, Any]:
    """Round-7 CC parity (/export): write the task's transcript thread to a
    real markdown file under <project_root>/.zcode/exports/ and return the
    path. The thread is the bridge's own message store — nothing new."""
    st = get_state()
    turn_id = req.turn_id or ""
    if not turn_id:
        running = st.running()
        if running:
            turn_id = running[0].get("turn_id", "") or ""
    if not turn_id:
        raise HTTPException(409, {"code": "nothing_to_export",
                                  "message": "no running or named task to export"})
    msgs = _task_messages(turn_id)
    if not msgs:
        raise HTTPException(404, {"code": "empty_transcript",
                                  "message": f"task {turn_id} has no messages"})
    try:
        from config import get_config
        root = get_config().get("project_root") or "."
        exports = Path(root) / ".zcode" / "exports"
        exports.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise HTTPException(500, {"code": "export_root",
                                  "message": f"cannot prepare exports dir: {exc}"})
    path = exports / f"{turn_id[:48]}-{int(time.time())}.md"
    lines = [f"# Task {turn_id}", "",
             f"Exported {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for m in msgs:
        role = m.get("role", "?")
        who = "user" if role == "user" else "universe"
        lines += [f"## {who}", "", str(m.get("text", "")), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "path": str(path), "messages": len(msgs)}


@app.post("/api/dev/synthesize")
async def dev_synthesize(spec: Request) -> dict[str, Any]:
    """DEV-ONLY purpose-built event synthesizer (parity verification).

    Replays a scripted BridgeEvent stream for a synthetic goal so the UI's
    timeline renders without running any LLM provider (zero-cost visual
    verification). Gated: only answers when UNIVERSE_DEV_SIM=1 in the bridge
    process — production and packaged apps 404.
    Body: {turn_id, text, script: [{dt, type, fields}], reply?, final_status?}
    """
    if os.environ.get("UNIVERSE_DEV_SIM") != "1":
        raise HTTPException(404, "dev synthesis disabled")
    try:
        body = await spec.json()
    except Exception:
        raise HTTPException(422, "invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(422, "body must be an object")
    turn_id = "".join(
        c for c in str(body.get("turn_id") or "") if c.isalnum() or c in " -_:"
    )[:80]
    text = str(body.get("text") or "")[:300] or "synthesize"
    script = body.get("script") if isinstance(body.get("script"), list) else []
    reply = body.get("reply") if isinstance(body.get("reply"), str) else None
    if reply is not None:
        reply = reply[:4000]
    final_status = str(body.get("final_status") or "completed")
    if final_status not in ("completed", "failed", "cancelled"):
        raise HTTPException(422, "final_status must be completed|failed|cancelled")
    if not turn_id:
        raise HTTPException(422, "turn_id required")
    from universe_desktop_bridge.state import get_state
    from observability.events import emit as _emit

    state = get_state()
    gt = state.get(turn_id)
    if gt is None:
        gt = state.create(turn_id, text, task_id="")
        # persisted as a first-class kind='task' row so the sidebar shows it
        # with a durable thread: cleanup sweeps (refreshTasks) and history
        # reloads treat it exactly like a real run (dev tool only)
        from storage.local import get_storage
        _st = get_storage()
        if _st.query_one("SELECT id FROM goals WHERE id=?;", (turn_id,)) is None:
            with _st.transaction() as conn:
                conn.execute(
                    "INSERT INTO goals (id, text, status, created_at, "
                    "project_id, title) VALUES (?, ?, 'running', ?, "
                    "'', 'New task')  # unassigned: no fake project rows",
                    (turn_id, text, time.time()),
                )
    gt.status = "running"

    async def run_script() -> None:
        try:
            for step in script:
                if not isinstance(step, dict):
                    continue
                ev_type = str(step.get("type") or "")
                if not ev_type or not ev_type.replace("_", "a").isalnum():
                    continue
                await asyncio.sleep(max(0.0, min(float(step.get("dt") or 0), 30)))
                fields = step.get("fields") if isinstance(step.get("fields"), dict) else {}
                _emit(ev_type, turn_id=turn_id, **fields)
            if reply is not None:
                gt.result = {"conversation": True, "reply": reply, "success": True}
            gt.set_status(final_status)
            # mirror what the real orchestrator emits (core/agent/orchestrator:
            # turn_completed carries reply+conversation) so the client fold
            # sees a terminal event - the status strip unmounts and the card
            # ends exactly as it does after a live run
            terminal_event = {
                "completed": "turn_completed",
                "failed": "turn_failed",
                "cancelled": "turn_cancelled",
            }[final_status]
            _emit(terminal_event, turn_id=turn_id,
                  reply=reply or "", conversation=True)
            # make the durable row match the terminal state (thread fallback
            # renders its final_summary.reply as the agent turn)
            from storage.local import get_storage as _gs
            summary = {"success": final_status == "completed",
                       "conversation": reply is not None,
                       "reply": reply or "", "steps": [], "artifacts": [],
                       "elapsed_ms": None}
            _gs().execute(
                "UPDATE goals SET status=?, completed_at=?, final_summary=? "
                "WHERE id=? AND kind='task';",
                (final_status, time.time(), json.dumps(summary), turn_id),
            )
        except Exception as e:  # noqa: BLE001 — dev tool must not crash bridge
            logger.warning(f"dev_synthesize script failed: {e}")
            gt.set_status("failed")

    gt.task = asyncio.create_task(run_script())
    return {"ok": True, "turn_id": turn_id, "scripted": len(script)}


def _goal_row(r: dict[str, Any]) -> dict[str, Any]:
    summary = None
    if r.get("final_summary"):
        try:
            import json
            summary = json.loads(r["final_summary"])
        except Exception:
            summary = None
    out = {
        "turn_id": r.get("id"),
        "text": r.get("text"),
        "status": r.get("status"),
        "created_at": r.get("created_at"),
        "completed_at": r.get("completed_at"),
        "source": "database",
    }
    for key in ("project_id", "title", "session_id", "archived",
                "deleted", "pinned", "kind"):
        if key in r:
            out[key] = r[key]
    if summary:
        out["success"] = summary.get("success")
        # conversational goals: the reply IS the result — surface it so the
        # chat bubble renders the actual answer, not a factory report
        out["reply"] = summary.get("reply")
        out["conversation"] = bool(summary.get("conversation"))
        out["halted"] = bool(summary.get("halted"))
        if summary.get("error"):
            out["error"] = summary.get("error")
        out["steps"] = [{"agent": s.get("agent"), "what": s.get("what"),
                         "success": s.get("success")}
                        for s in (summary.get("steps") or [])[:12]]
        out["artifacts"] = summary.get("artifacts") or []
        out["gravity_score"] = summary.get("gravity_score")
        out["elapsed_ms"] = summary.get("elapsed_ms")
    return out


def _task_messages(task_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """The thread inside one task: its message goals (kind='message',
    session_id=task) in creation order, each with role mapping — a user
    message renders from `text`, an agent reply from final_summary.reply.
    Legacy pre-v17 tasks fall back to the task row's own text as the single
    user turn so old chats still open as a thread."""
    st = get_storage()
    rows = st.query_all(
        "SELECT id, text, status, created_at, completed_at, final_summary "
        "FROM goals WHERE parent_goal_id=? AND kind='message' "
        "ORDER BY created_at ASC LIMIT ?;", (task_id, limit))
    if rows:
        out = []
        for r in rows:
            out.append({"turn_id": r["id"], "role": "user",
                        "text": r.get("text") or "",
                        "created_at": r.get("created_at")})
            reply, success, steps, tool_history = "", True, [], []
            started_at = r.get("created_at")
            completed_at = r.get("completed_at")
            elapsed_ms = None
            todos = []
            todos_idx = None
            if r.get("final_summary"):
                try:
                    s = json.loads(r["final_summary"])
                    reply = s.get("reply") or ""
                    success = bool(s.get("success", True))
                    steps = [{"agent": x.get("agent"), "what": x.get("what"),
                              "success": x.get("success")}
                             for x in (s.get("steps") or [])[:12]]
                    tool_history = s.get("tool_history") or []
                    todos = s.get("todos") or []
                    todos_idx = s.get("todos_idx")
                    started_at = s.get("started_at") or started_at
                    completed_at = s.get("completed_at") or completed_at
                    elapsed_ms = s.get("elapsed_ms")
                except Exception:
                    pass
            out.append({"turn_id": r["id"], "role": "agent", "text": reply,
                        "success": success, "steps": steps,
                        "tool_history": tool_history,
                        "todos": todos, "todos_idx": todos_idx,
                        "started_at": started_at, "completed_at": completed_at,
                        "elapsed_ms": elapsed_ms,
                        "status": r.get("status"),
                        "created_at": r.get("created_at")})
        return out
    # legacy: pre-threading single-goal tasks — synthesize the thread
    task_row = st.query_one(
        "SELECT id, text, final_summary FROM goals WHERE id=?;", (task_id,))
    if task_row is None:
        return []
    msgs = [{"turn_id": task_id, "role": "user",
             "text": task_row.get("text") or "", "created_at": None}]
    reply = ""
    if task_row.get("final_summary"):
        try:
            reply = (json.loads(task_row["final_summary"]).get("reply")
                     or "")
        except Exception:
            reply = ""
    if reply:
        msgs.append({"turn_id": task_id, "role": "agent", "text": reply,
                     "created_at": None})
    return msgs


@app.get("/api/sessions")
async def sessions(limit: int = 50, project_id: str | None = None,
                   include_archived: bool = False, q: str = "") -> dict[str, Any]:
    # Dual-read: sessions is authority, but old tasks live in goals — union both for seamless open
    limit = max(1, min(limit, 200))
    rows = []
    # New sessions
    try:
        sql_s = ("SELECT id, title as text, 'active' as status, created_at, updated_at as completed_at, '' as final_summary, "
                 "project_id, title, id as session_id, archived, deleted, pinned, 'task' as kind "
                 "FROM sessions WHERE deleted=0")
        params_s: list[Any] = []
        if not include_archived:
            sql_s += " AND archived=0"
        if project_id:
            sql_s += " AND (project_id=? OR project_id IS NULL)"
            params_s += [project_id]
        if q:
            sql_s += " AND (title LIKE ?)"
            params_s += [f"%{q}%"]
        sql_s += " ORDER BY pinned DESC, created_at DESC LIMIT ?;"
        params_s.append(limit)
        rows.extend(get_storage().query_all(sql_s, params_s))
    except Exception:
        pass
    # Legacy goals tasks (for backward compat until migration complete)
    try:
        sql_g = ("SELECT id, text, status, created_at, completed_at, final_summary, "
                 "project_id, title, parent_goal_id as session_id, archived, deleted, pinned, kind "
                 "FROM goals WHERE deleted=0 AND kind='task'")
        params_g: list[Any] = []
        if not include_archived:
            sql_g += " AND archived=0"
        if project_id:
            sql_g += " AND (project_id=? OR project_id IS NULL)"
            params_g += [project_id]
        if q:
            sql_g += " AND (text LIKE ? OR title LIKE ?)"
            params_g += [f"%{q}%", f"%{q}%"]
        sql_g += " ORDER BY pinned DESC, created_at DESC LIMIT ?;"
        params_g.append(limit)
        for r in get_storage().query_all(sql_g, params_g):
            if not any(x["id"] == r["id"] for x in rows):
                rows.append(r)
    except Exception:
        pass
    rows = sorted(rows, key=lambda x: x.get("created_at") or 0, reverse=True)[:limit]
    out = []
    for r in rows:
        item = _goal_row(r)
        # session_id keeps its SQL-mapped meaning: the session's own id for
        # session rows, the PARENT task id (parent_goal_id) for legacy goal
        # rows — the old blanket override with the row's own id destroyed
        # the thread linkage the UI needs.
        out.append({**item, "turn_id": r["id"], "goal_id": r["id"]})
    return {"sessions": out, "goals": out}

@app.get("/api/session/{session_id}")
async def session_detail(session_id: str) -> dict[str, Any]:
    # Session authority: read from sessions + transcript, fallback to goals
    row = get_storage().query_one("SELECT id, title as text, 'active' as status, created_at, updated_at as completed_at, '' as final_summary, project_id, title, '' as parent_goal_id, archived, deleted, pinned, 'task' as kind FROM sessions WHERE id=?;", (session_id,))
    if row:
        item = _goal_row(row)
        try:
            from core.session.store import get_session_store
            transcript = get_session_store().transcript(session_id)
            # Convert transcript to messages format expected by UI
            msgs = []
            for e in transcript:
                role = e.get("role", "user")
                text = e.get("content", "")
                msgs.append({"session_id": session_id, "turn_id": e.get("turn_id") or e.get("goal_id") or session_id, "role": role, "text": text, "created_at": e.get("ts")})
            if msgs:
                item["messages"] = msgs
            else:
                item["messages"] = _task_messages(session_id)
        except Exception:
            item["messages"] = _task_messages(session_id)
        return item
    # fallback to legacy goals
    return await goal_detail(turn_id=session_id)

@app.get("/api/goals")
async def goals(limit: int = 50, project_id: str | None = None,
               include_archived: bool = False, q: str = "") -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    # tasks only in the sidebar: message goals (children) are rendered
    # INSIDE their task thread, never as separate rows
    sql = ("SELECT id, text, status, created_at, completed_at, final_summary, "
           "project_id, title, owner_session_id AS session_id, archived, "
           "deleted, pinned, kind "
           "FROM goals WHERE deleted=0 AND kind='task'")
    params: list[Any] = []
    if not include_archived:
        sql += " AND archived=0"
    if project_id:
        sql += " AND (project_id=? OR project_id IS NULL)"
        params += [project_id]
    if q:
        sql += " AND (text LIKE ? OR title LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY pinned DESC, created_at DESC LIMIT ?;"
    params.append(limit)
    rows = get_storage().query_all(sql, params)
    st = get_state()
    out = []
    for r in rows:
        item = _goal_row(r)
        # a task's live status = does any of its messages run right now?
        if any(gt.task_id == r["id"] and gt.status in ("queued", "running")
               for gt in st.goals.values()):
            item["status"] = "running"
        gt = st.get(r["id"])
        if gt is not None:
            item["bridge_status"] = gt.status
        out.append(item)
    for gt in st.running():                      # bridge goals not yet flushed
        if not any(o["turn_id"] == gt.turn_id for o in out):
            # only message-goals whose task row isn't flushed yet surface as
            # their parent task (never as standalone sidebar rows)
            if gt.task_id and any(o["turn_id"] == gt.task_id for o in out):
                continue
            out.insert(0, {"turn_id": gt.turn_id, "text": gt.text,
                           "status": "running", "created_at": gt.created_at,
                           "kind": "task", "source": "bridge",
                           "bridge_status": gt.status})
    return {"goals": out}


@app.get("/api/turn/{turn_id}")
@app.get("/api/goal/{goal_id}")
async def goal_detail(turn_id: str = None, goal_id: str = None) -> dict[str, Any]:
    turn_id = turn_id or goal_id
    row = get_storage().query_one(
        "SELECT id, text, status, created_at, completed_at, final_summary, "
        "kind, parent_goal_id, title, project_id, archived, deleted, pinned "
        "FROM goals WHERE id=?;", (turn_id,))
    gt = get_state().get(turn_id)
    if row is None and gt is None:
        raise HTTPException(404, "goal not found")
    if row is None:
        out = {"turn_id": turn_id, "text": gt.text if gt else "",
               "status": (gt.status if gt else "running"),
               "created_at": gt.created_at if gt else None,
               "source": "bridge"}
        # synthesized goals carry their result fields on the registry row
        r = (gt.result or {}) if gt else {}
        for k in ("reply", "conversation", "success", "error", "halted",
                  "steps", "artifacts", "gravity_score"):
            if k in r:
                out[k] = r[k]
        return out
    item = _goal_row(row)
    if gt is not None:
        item["bridge_status"] = gt.status
        item["error"] = gt.error or None
    # a thread request (kind='task') or its running message: return the full
    # message list so the UI can render the whole conversation
    if item.get("kind") == "task":
        item["messages"] = _task_messages(turn_id)
    else:
        parent = item.get("session_id")
        if parent:
            item["messages"] = _task_messages(parent)
    return item


@app.get("/api/turn/{turn_id}/events")
@app.get("/api/goal/{goal_id}/events")
async def goal_events(turn_id: str = None, goal_id: str = None, after_seq: int = 0) -> dict[str, Any]:
    turn_id = turn_id or goal_id
    """Reconnect refill: every ring event past `after_seq` for one goal.
    `dropped` is the ring-overflow count — a client that sees dropped > 0
    knows the ring itself has gaps (very old events evicted) and should
    fall back to the goal log, not just the ring."""
    gt = get_state().get(turn_id)
    if gt is not None:
        events = [e for e in gt.events if e.get("seq", 0) > after_seq]
        return {"turn_id": turn_id, "after_seq": after_seq,
                "latest_seq": gt.seq, "dropped": gt.dropped, "events": events}
    # Fallback: old goal not in ring — rebuild events from persisted tool_history + todos + lifecycle
    row = get_storage().query_one("SELECT final_summary, created_at, completed_at, kind FROM goals WHERE id=?;", (turn_id,))
    if row and not row.get("final_summary") and row.get("kind") == "task":
        last_msg = get_storage().query_one("SELECT final_summary, created_at, completed_at FROM goals WHERE parent_goal_id=? AND kind='message' ORDER BY created_at DESC LIMIT 1;", (turn_id,))
        if last_msg:
            row = last_msg
    if row and row.get("final_summary"):
        try:
            s = json.loads(row["final_summary"])
            th = s.get("tool_history") or []
            todos = s.get("todos") or []
            todos_idx = s.get("todos_idx")
            started_at = s.get("started_at") or row.get("created_at") or 0
            completed_at = s.get("completed_at") or row.get("completed_at") or (started_at + (s.get("elapsed_ms", 0) / 1000.0) if s.get("elapsed_ms") else started_at)
            events = []
            seq = 0
            # turn_started
            seq += 1
            if seq > after_seq:
                events.append({"seq": seq, "type": "turn_started", "ts": started_at, "turn_id": turn_id, "source": "history"})
            # todos
            if todos:
                seq += 1
                if seq > after_seq:
                    events.append({"seq": seq, "type": "todo_updated", "ts": started_at + 0.1, "todos": todos, "in_progress_idx": todos_idx, "turn_id": turn_id, "source": "history"})
            # tool events with incremental timestamps
            span = max(0.1, (completed_at - started_at) if completed_at and started_at else 1)
            per_tool = span / max(1, len(th)) if th else 0.1
            for i, h in enumerate(th):
                seq += 1
                if seq > after_seq:
                    ts = (h.get("ts") or (started_at + per_tool * (i + 0.5))) if started_at else None
                    events.append({"seq": seq, "type": "tool_finished" if h.get("ok", True) else "tool_failed", "tool": h.get("tool",""),
                                   "ok": h.get("ok", True), "args": h.get("args",{}),
                                   "result_preview": h.get("result_preview",""), "agent": h.get("agent","universe"),
                                   "duration_ms": h.get("duration_ms", 0), "ts": ts, "turn_id": turn_id, "source": "history"})
            # turn_completed
            seq += 1
            if seq > after_seq:
                term_type = "turn_completed" if s.get("success", True) else "turn_failed"
                events.append({"seq": seq, "type": term_type, "ts": completed_at, "turn_id": turn_id, "source": "history"})
            # filter by after_seq already done via seq check
            return {"turn_id": turn_id, "after_seq": after_seq,
                    "latest_seq": seq, "dropped": 0, "events": events}
        except Exception:
            pass
    raise HTTPException(404, "goal not known to this bridge session")


@app.post("/api/turn")
@app.post("/api/goal")
async def start_goal(req: GoalRequest) -> dict[str, Any]:
    _runtime.init()  # lazy + idempotent
    if _runtime.orchestrator is None:
        raise HTTPException(503, {"code": "backend_not_initialized",
                                  "message": "backend not initialized",
                                  "init_error": _runtime.init_error or None})
    text = req.text.strip()
    # composer attachments (v1 → structured): real files only, ≤8, then
    # merged into the goal text as ONE fenced block the agent reads with
    # its own tools. Applied HERE — before the follow-up queue branch —
    # so queued follow-ups carry their attachments exactly like direct
    # sends, and the raw 8000-char message budget stays intact.
    _attachments = _sanitize_attachments(req.attachments)
    if _attachments:
        text = _text_with_attachments(text, _attachments)
    if not text:
        raise HTTPException(422, "empty goal text")
    # follow-up queue (interaction_behavior="queue"): a message sent while a
    # goal on the SAME task is still running is buffered and runs next — it
    # never interrupts (that is what steer is for). Returns queued status so
    # the composer can show its "queued" indicator.
    if req.turn_id:
        settings = _read_desktop_settings()
        if settings.get("interaction_behavior") == "queue":
            task_row = get_storage().query_one(
                "SELECT id FROM goals WHERE id=? AND kind='task';",
                (req.turn_id,))
            if task_row is not None:
                running = get_storage().query_one(
                    "SELECT id FROM goals WHERE parent_goal_id=? AND "
                    "status IN ('queued','running') LIMIT 1;", (req.turn_id,))
                if running is not None:
                    async with _runtime._followup_lock:
                        q = _runtime.followup_queues.setdefault(req.turn_id, [])
                        if len(q) >= 100:
                            # bounded buffer: a scripted POST loop must not
                            # grow memory without limit (each entry ≤8000
                            # chars, so 100 caps a task's queue under 1 MB)
                            raise HTTPException(422, {
                                "code": "followup_queue_full",
                                "message": "This task already has 100 queued "
                                           "follow-ups — wait for the running "
                                           "goal to drain them."})
                        q.append(text)
                    emit("steer_queued", turn_id=req.turn_id,
                         text=text[:200], queued_as="followup")
                    return {"turn_id": None, "task_id": req.turn_id,
                            "status": "queued", "accepted": True,
                            "queued": len(_runtime.followup_queues[req.turn_id])}
    # honest preflight: with no configured provider every LLM call would fail
    # anyway — reject up front with a precise, actionable error instead of
    # letting the goal die somewhere deep in the pipeline
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    if pm.best_available("universe") is None:
        raise HTTPException(422, {
            "code": "no_provider",
            "message": "No LLM provider is configured. Open Settings → Model "
                       "provider and connect one (a free-tier cloud key or a "
                       "local Ollama/LM Studio both work).",
        })
    # idempotency: the same client_msg_id within the window returns the
    # original goal instead of starting a duplicate run (double-Enter races,
    # retries). Never a second POST effect.
    if req.client_msg_id:
        prior = _seen_client_msgs.get(req.client_msg_id)
        if prior is not None:
            return {"turn_id": prior[0], "status": "duplicate_ignored",
                    "accepted": True, "duplicate": True}
    from core.agent.base_agent import new_id
    # continuation vs. fresh task: with turn_id set the message runs INSIDE
    # that task (kind='message', session_id) and no new sidebar row is
    # created. Without it, this first message auto-creates its task
    # container titled "New task" — never titled after the message text.
    task_id = req.session_id or req.parent_goal_id or req.goal_id or req.turn_id
    if task_id:
        row = get_storage().query_one(
            "SELECT id, kind, archived, deleted, project_id FROM goals "
            "WHERE id=?;", (task_id,))
        if row is None or row.get("deleted"):
            raise HTTPException(404, {
                "code": "turn_not_found",
                "message": "that task no longer exists"})
        if row.get("kind") != "task":
            task_id = row.get("parent_goal_id") or row.get("session_id") or task_id
        if row.get("archived"):
            with get_storage().transaction() as conn:
                conn.execute(
                    "UPDATE goals SET archived=0 WHERE id=?;", (task_id,))
        project_id = row.get("project_id") or req.project_id or ""
    else:
        task_id = new_id("task-")
        project_id = req.project_id or ""
        with get_storage().transaction() as conn:
            conn.execute(
                "INSERT INTO goals(id, text, status, kind, title, project_id, "
                "created_at, owner_session_id) "
                "VALUES(?,?,?,?,?,?,?,?);",
                (task_id, text[:2000], "active", "task", "New task",
                 project_id, time.time(),
                 getattr(_runtime, "session_id", "") or "desktop"))
            # dual-write to sessions for session authority
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO sessions(id, workspace_root, title, created_at, updated_at, project_id, archived, deleted, pinned) "
                    "VALUES(?,?,?,?,?,?,?,?,?);",
                    (task_id, "", "New task", time.time(), time.time(), project_id, 0, 0, 0),
                )
            except Exception:
                pass
    turn_id = new_id("turn-")
    if req.client_msg_id:
        _seen_client_msgs[req.client_msg_id] = (turn_id, time.time())
        _prune_client_msgs()
    gt = get_state().create(turn_id, text, task_id=task_id)
    # the TTL pruner existed but was only ever called by tests — a long-lived
    # bridge accumulated every goal's ring buffer forever (audit 2026-09).
    # Prune on every new goal; a surge at least frees what came before it.
    get_state().prune_expired()
    gt.set_status("running")
    orch = _runtime.orchestrator
    bus = get_event_bus()

    # Resolve project_id -> project_root for filesystem scoping
    project_root = ""
    if project_id:
        try:
            _prow = get_storage().query_one("SELECT root FROM projects WHERE id=?", (project_id,))
            if _prow and _prow.get("root"):
                project_root = str(_prow["root"])
        except Exception:
            pass

    async def _run() -> None:
        try:
            with bus.bind_goal(turn_id):
                emit("turn_started", turn_id=turn_id, text=text[:2000],
                     language=req.language, task_id=task_id)
                summary = await orch.run_turn(
                    text, language=req.language,
                    turn_id=turn_id,
                    owner_session_id=getattr(
                        _runtime, "session_id", "") or "desktop",
                    project_id=project_id,
                    project_root=project_root,
                    session_id=task_id,
                    effort=_resolve_effort(req.effort, req.thinking_level),
                )
                gt.result = summary
                # the contract is the SUMMARY, not the absence of an
                # exception: success=False with no raise is a soft failure
                # and must read failed (the old code marked it completed)
                if isinstance(summary, dict) and not summary.get("success", True):
                    gt.set_status("failed")
                    gt.error = str(summary.get("error", ""))[:500]
                else:
                    gt.set_status("completed")
                    gt.error = ""
                # a message goal keeps its task container active (the
                # conversation stays open); the TASK row itself only
                # closes when the user archives/deletes it
        except asyncio.CancelledError:
            gt.set_status("cancelled")
            raise
        except Exception as exc:
            # the orchestrator already recorded turn_failed in its own
            # handler; here we only keep the bridge's bookkeeping truthful.
            # Never clobber an error already recorded (e.g. from the DB
            # row or an earlier summary) with a shallower one.
            gt.set_status("failed")
            if not gt.error:
                gt.error = str(exc)[:500]
        finally:
            # drain the follow-up queue: exactly one queued message per
            # finished goal, dispatched as its own goal on the same task
            # (the status update above lands first, so the drain sees the
            # queue and the next start_goal finds no running goal on it)
            try:
                async with _runtime._followup_lock:
                    q = _runtime.followup_queues.get(task_id) or []
                    next_text = q.pop(0) if q else ""
                    if not q:
                        _runtime.followup_queues.pop(task_id, None)
                if next_text:
                    async def _dispatch(t: str = next_text) -> None:
                        try:
                            await start_goal(GoalRequest(
                                text=t, turn_id=task_id,
                                language=req.language,
                                project_id=project_id))
                        except Exception as exc:
                            # the head message can never start (no provider,
                            # deleted task): nothing will drain the rest —
                            # drop the queue LOUDLY instead of stranding it
                            async with _runtime._followup_lock:
                                stranded = len(
                                    _runtime.followup_queues.pop(task_id, []))
                            emit("steer_queued", turn_id=task_id,
                                 text=f"follow-up dispatch failed: {exc}"[:200],
                                 queued_as="dropped",
                                 dropped=stranded + 1)
                    asyncio.get_running_loop().call_later(
                        0.05,
                        lambda: asyncio.ensure_future(_dispatch()))
            except Exception:
                pass  # a drain hiccup must never break the finished goal

    gt.task = asyncio.create_task(_run())
    return {"turn_id": turn_id, "task_id": task_id, "status": "running",
            "accepted": True}


@app.post("/api/tasks/draft")
async def create_task_draft(project_id: str | None = None,
                            title: str | None = None) -> dict[str, Any]:
    """A user-created empty task (the New Task button). No message text is
    involved: the row is created with a neutral title so tasks are NEVER
    named after message content. Renaming is the only way to name a task."""
    from core.agent.base_agent import new_id
    task_id = new_id("task-")
    now = time.time()
    safe_title = (title or "").strip()[:200] or "New task"
    with get_storage().transaction() as conn:
        conn.execute(
            "INSERT INTO goals(id, text, status, kind, title, project_id, "
            "created_at, owner_session_id) VALUES(?,?,?,?,?,?,?,?);",
            (task_id, "", "active", "task", safe_title,
             project_id or "", now,
             getattr(_runtime, "session_id", "") or "desktop"))
    return {"ok": True, "task": {"turn_id": task_id, "title": safe_title,
                                 "status": "active", "kind": "task",
                                 "project_id": project_id or "",
                                 "created_at": now}}


@app.post("/api/turn/{turn_id}/cancel")
@app.post("/api/goal/{goal_id}/cancel")
async def cancel_goal(turn_id: str) -> dict[str, Any]:
    gt = get_state().get(turn_id)
    if gt is None:
        # a restart lost this goal's task — say so distinctly so the client
        # can finalize it locally instead of spinning forever
        raise HTTPException(404, {"code": "turn_not_found",
                                  "message": "goal is not known to this bridge session "
                                             "(it may have restarted)"})
    if gt.task is None or gt.status not in ("queued", "running"):
        raise HTTPException(409, {"code": "turn_not_running",
                                  "message": "goal is not running"})
    gt.task.cancel()
    # queue mode: stopping the goal must drop its buffered follow-ups too —
    # the finally-drain would otherwise auto-dispatch the head ~50ms later
    # and the user's Stop silently restarts work (Phase-2 review)
    dropped = _runtime.followup_queues.pop(turn_id, None)
    if dropped:
        emit("steer_queued", turn_id=turn_id, text="", queued_as="dropped")
    return {"turn_id": turn_id, "status": "cancelling", "cancelling": True,
            "followups_dropped": len(dropped or [])}


# ---- steer + file serving (M2/M3) ------------------------------------------------

@app.post("/api/turn/{turn_id}/steer")
@app.post("/api/goal/{goal_id}/steer")
async def steer_goal(turn_id: str, req: dict[str, Any]) -> dict[str, Any]:
    """Queue a course-correction for a RUNNING goal. The orchestrator drains
    it at the nearest decision point (the boundary between plan steps, never
    mid-tool) and re-plans the remaining work around it."""
    text = str((req or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(422, {"code": "empty_steer",
                                  "message": "steer text is required"})
    gt = get_state().get(turn_id)
    if gt is None:
        raise HTTPException(404, {"code": "turn_not_found",
                                  "message": "goal is not known to this bridge session "
                                             "(it may have restarted)"})
    if gt.task is None or gt.status not in ("queued", "running"):
        raise HTTPException(409, {"code": "turn_not_running",
                                  "message": "goal is not running — send it as a new message instead"})
    from core.agent import steer as steer_bus
    try:
        item = steer_bus.push_steer(turn_id, text)
    except ValueError as exc:
        raise HTTPException(422, {"code": "empty_steer", "message": str(exc)})
    emit("steer_queued", turn_id=turn_id, text=text[:200])
    return {"turn_id": turn_id, "accepted": True,
            "queued": steer_bus.pending_count(turn_id), "ts": item.get("ts")}


@app.get("/api/file")
async def serve_file(path: str = "", request: Request = None) -> Response:
    """Serve a file INSIDE the project scope for the Rail preview surface
    (code/markdown text, images, audio, video with seeking, PDF, HTML).
    Scope guard: the same _within_scope rule the file tools enforce — a
    request for anything outside project_root (or the configured
    file_allowlist) is rejected before a single byte is read. Range
    requests are honored so <video>/<audio> can seek."""
    if not path:
        raise HTTPException(422, {"code": "missing_path",
                                  "message": "path query param is required"})
    from connectors.builtin.files import _resolved, _within_scope
    if not _within_scope(path):
        raise HTTPException(403, {"code": "outside_scope",
                                  "message": "path is outside the project scope"})
    p = _resolved(path)
    if not p.is_file():
        raise HTTPException(404, {"code": "not_found",
                                  "message": "file not found"})
    import mimetypes
    import re as _re
    media_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    size = p.stat().st_size
    range_header = request.headers.get("range") if request is not None else None
    start, end = 0, size - 1
    partial = False
    if range_header:
        m = _re.match(r"bytes=(\d*)-(\d*)", range_header)
        if m:
            partial = True
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
    if size == 0:
        return Response(content=b"", media_type=media_type,
                        headers={"Accept-Ranges": "bytes"})
    if start > end or start >= size:
        return Response(status_code=416,
                        headers={"Content-Range": f"bytes */{size}"})
    # cap a single response at 8MB: browsers/players issue further ranges
    end = min(end, start + 8 * 1024 * 1024 - 1, size - 1)
    length = end - start + 1
    with p.open("rb") as fh:
        fh.seek(start)
        data = fh.read(length)
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": "inline"}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(content=data, status_code=206, media_type=media_type,
                        headers=headers)
    return Response(content=data, media_type=media_type, headers=headers)


@app.websocket("/api/browser/stream")
async def browser_stream_ws(ws: WebSocket) -> None:
    """M4 live view: CDP screencast frames of the browser agent's active
    tab as binary JPEG messages; a text browser_meta carries the URL on
    connect. Lazy: the screencast starts with the first viewer and stops
    when the last one leaves — no viewer, no cost."""
    await ws.accept()
    from connectors.builtin import browser as browser_mod
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)

    async def listener(frame: bytes, meta: dict) -> None:
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            # latest-wins under a slow viewer: drop the oldest frame
            try:
                queue.get_nowait()
                queue.put_nowait(frame)
            except Exception:
                pass

    async def sender() -> None:
        while True:
            frame = await queue.get()
            await ws.send_bytes(frame)

    send_task = asyncio.create_task(sender())
    try:
        url = ""
        try:
            url = await browser_mod.ensure_screencast(None)
        except Exception as e:
            await ws.send_text(json.dumps({"type": "browser_error",
                                           "error": str(e)[:200]}))
            return
        await ws.send_text(json.dumps({"type": "browser_meta", "url": url or ""}))
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    finally:
        send_task.cancel()
        browser_mod.remove_screencast_listener(listener)
        if browser_mod.screencast_listener_count() == 0:
            try:
                await browser_mod.stop_screencast()
            except Exception:
                pass


# ---- browser REST wrappers (user-initiated, no agent needed) ---------------------

class BrowserNavigateRequest(BaseModel):
    url: str
    tab: str | None = None

@app.get("/api/browser/state")
async def browser_state() -> dict[str, Any]:
    try:
        from connectors.builtin.browser import _browser, _cdp_browser, _pages, _cdp_pages, _active_tab
        tabs = []
        for tid in list(_pages.keys()) + list(_cdp_pages.keys()):
            tabs.append({"id": tid, "title": tid})
        active = _active_tab if (_browser or _cdp_browser) else None
        return {"tabs": tabs, "activeTab": active, "available": bool(_browser or _cdp_browser)}
    except Exception as e:
        return {"tabs": [], "activeTab": None, "available": False, "error": str(e)[:200]}

@app.post("/api/browser/navigate")
async def browser_navigate_api(req: BrowserNavigateRequest) -> dict[str, Any]:
    from connectors.builtin import get_registry
    reg = get_registry()
    res = await reg.call("BrowserNavigate", agent="universe", turn_id="browser-ui", args={"url": req.url, "tab": req.tab or "default"})
    return res

@app.post("/api/browser/tabs")
async def browser_tabs_api(req: dict[str, Any]) -> dict[str, Any]:
    from connectors.builtin import get_registry
    reg = get_registry()
    res = await reg.call("BrowserTabs", agent="universe", turn_id="browser-ui", args=req)
    return res

@app.get("/api/usage")
async def usage_stats(range: str = "7d") -> dict[str, Any]:
    import datetime as _dt
    from pathlib import Path as _Path
    cfg = get_config()
    log = _Path(cfg.llm_log) if hasattr(cfg, "llm_log") else _Path.home() / ".universe" / "llm_calls.jsonl"
    total_in = total_out = calls = 0
    by_provider: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, int]] = {}
    try:
        if log.exists():
            for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    j = json.loads(line)
                    prov = j.get("provider") or j.get("model") or "unknown"
                    inp = int(j.get("input_tokens") or j.get("prompt_tokens") or 0)
                    out = int(j.get("output_tokens") or j.get("completion_tokens") or 0)
                    total_in += inp
                    total_out += out
                    calls += 1
                    bp = by_provider.setdefault(prov, {"provider": prov, "input": 0, "output": 0, "calls": 0})
                    bp["input"] += inp
                    bp["output"] += out
                    bp["calls"] += 1
                    try:
                        d = str(j.get("ts") or j.get("timestamp") or "")[:10]
                        if d and len(d) == 10:
                            db = daily.setdefault(d, {"date": d, "input": 0, "output": 0})
                            db["input"] += inp
                            db["output"] += out
                    except Exception:
                        pass
                except Exception:
                    continue
    except Exception:
        pass
    return {"range": range, "totalTokens": {"input": total_in, "output": total_out}, "calls": calls, "byProvider": list(by_provider.values()), "daily": sorted(daily.values(), key=lambda x: x["date"])}

@app.get("/api/indexing/status")
async def indexing_status() -> dict[str, Any]:
    cfg = get_config()
    d = cfg.get("desktop", {}) if isinstance(cfg.get("desktop"), dict) else {}
    return {"enabled": bool(d.get("indexingEnabled")), "root": d.get("indexingRoot") or cfg.get("project_root", ""), "count": 0}

@app.post("/api/indexing/resync")
async def indexing_resync() -> dict[str, Any]:
    return {"ok": True, "indexed": 0, "message": "indexing resync queued"}

# ---- first-run setup gate --------------------------------------------------------

@app.get("/api/setup")
async def setup_state() -> dict[str, Any]:
    """The UI's onboarding gate: is this a first run, is a provider wired,
    and what name does the assistant go by (for voice + copy)."""
    _runtime.init()
    from providers.manager import get_provider_manager
    cfg = get_config()
    pm = get_provider_manager()
    return {
        "setup_complete": bool(cfg.get("setup_complete", False)),
        "has_provider": pm.best_available("universe") is not None,
        "assistant_name": (cfg.get("desktop", {}) or {}).get("assistant_name")
                          or cfg.get("assistant_name", "") or "Universe",
    }


@app.post("/api/setup/complete")
async def complete_setup(req: dict[str, Any]) -> dict[str, Any]:
    """Finish onboarding: mark complete + persist the assistant name."""
    import time as _time
    cfg = get_config()
    cfg.set("setup_complete", True)
    cfg.set("setup_at", _time.time())
    name = str(req.get("assistant_name", "")).strip()[:60]
    if name:
        desktop = dict(cfg.get("desktop", {}) or {})
        desktop["assistant_name"] = name
        cfg.set("desktop", desktop)
    return {"ok": True, "setup_complete": True,
            "assistant_name": name or "Universe"}


# ---- projects --------------------------------------------------------------------

# git branch per project root, cached 60s — feeds the ProjectBar branch chip
_branch_cache: dict[str, tuple[float, "str | None"]] = {}


def _git_branch(root: str) -> "str | None":
    """The repo's checked-out branch, or None when root isn't a git repo."""
    import subprocess
    import time as _time
    root = (root or "").strip()
    if not root:
        return None
    now = _time.time()
    hit = _branch_cache.get(root)
    if hit and now - hit[0] < 60:
        return hit[1]
    branch: "str | None" = None
    try:
        r = subprocess.run(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode == 0:
            branch = r.stdout.strip() or None
    except Exception:
        branch = None
    _branch_cache[root] = (now, branch)
    return branch


@app.get("/api/projects")
async def list_projects() -> dict[str, Any]:
    rows = get_storage().query_all(
        "SELECT id, name, root, created_at, updated_at, archived FROM projects "
        "ORDER BY archived, name COLLATE NOCASE;")
    out = []
    for r in rows:
        n = get_storage().query_one(
            "SELECT COUNT(*) AS c FROM goals WHERE deleted=0 AND archived=0 "
            "AND project_id=?;",
            (r["id"],))
        out.append({**dict(r), "goal_count": n["c"] if n else 0,
                    "branch": _git_branch(r["root"])})
    # v22 retired the General safety net: only REAL projects exist here,
    # tasks without a project group under the composer "Select project" slot.
    return {"projects": out}


@app.post("/api/projects")
async def create_project(req: ProjectCreate) -> dict[str, Any]:
    import time as _time
    from core.agent.base_agent import new_id
    pid = new_id("proj-")
    now = _time.time()
    st = get_storage()
    with st.transaction() as conn:
        conn.execute(
            "INSERT INTO projects(id,name,root,created_at,updated_at) "
            "VALUES(?,?,?,?,?);", (pid, req.name.strip()[:80], req.root, now, now))
    return {"ok": True, "project": {"id": pid, "name": req.name.strip()[:80],
                                     "root": req.root, "created_at": now,
                                     "updated_at": now, "archived": 0}}


@app.patch("/api/projects/{pid}")
async def patch_project(pid: str, req: ProjectPatch) -> dict[str, Any]:
    import time as _time
    st = get_storage()
    row = st.query_one("SELECT id FROM projects WHERE id=?;", (pid,))
    if row is None:
        raise HTTPException(404, {"code": "project_not_found",
                                   "message": f"project {pid!r} unknown"})
    sets, params = [], []
    if req.name is not None:
        sets.append("name=?")
        params.append(req.name.strip()[:80])
    if req.root is not None:
        sets.append("root=?")
        params.append(req.root)
    if req.archived is not None:
        sets.append("archived=?")
        params.append(1 if req.archived else 0)
    if sets:
        sets.append("updated_at=?")
        params.append(_time.time())
        params.append(pid)
        with st.transaction() as conn:
            conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?;",
                         params)
    return {"ok": True, "project_id": pid}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str, hard: bool = False) -> dict[str, Any]:
    """Soft-delete: keep the project row archived unless hard=true AND it
    has no chats. Its goals become unassigned (empty project_id) — the UI
    groups them under the composer's "Select project" slot."""
    st = get_storage()
    row = st.query_one("SELECT id FROM projects WHERE id=?;", (pid,))
    if row is None:
        raise HTTPException(404, {"code": "project_not_found",
                                   "message": f"project {pid!r} unknown"})
    n = st.query_one("SELECT COUNT(*) AS c FROM goals WHERE project_id=? "
                     "AND deleted=0;", (pid,))
    if hard and (n or {"c": 0})["c"] > 0:
        raise HTTPException(409, {"code": "project_not_empty",
                                   "message": "delete its chats first or soft-archive instead"})
    with st.transaction() as conn:
        if hard:
            conn.execute("DELETE FROM projects WHERE id=?;", (pid,))
            # its goals become unassigned ('') — the UI's "Select project"
            # slot is where they regroup; no synthetic project is created
            conn.execute("UPDATE goals SET project_id='' WHERE project_id=?;",
                         (pid,))
        else:
            conn.execute("UPDATE projects SET archived=1, updated_at=? WHERE id=?;",
                          (time.time(), pid))
    return {"ok": True, "removed": pid, "hard": hard}


# ---- automations (Phase C — ZCode Automations page) ------------------------------
#
# Thin CRUD over scheduling.AutomationScheduler — the same engine the agent's
# cron.* tools drive. The USER creating an automation from the desktop page
# is direct user intent (the principal typed it), so the consent gate that
# protects agent-initiated cron.create does not apply here; every change is
# audit-logged below.

class AutomationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    schedule: str
    prompt: str = Field(min_length=1, max_length=4000)


class AutomationPatch(BaseModel):
    name: str | None = None
    schedule: str | None = None
    prompt: str | None = None
    enabled: bool | None = None


class SkillPatch(BaseModel):
    enabled: bool | None = None


@app.get("/api/automations")
async def list_automations() -> dict[str, Any]:
    from scheduling import get_scheduler
    return {"automations": get_scheduler().list()}


@app.post("/api/automations")
async def create_automation(req: AutomationCreate) -> dict[str, Any]:
    from scheduling import get_scheduler
    from scheduling.cron import CronError
    try:
        row = get_scheduler().create(req.name, req.schedule, req.prompt)
    except CronError as exc:
        raise HTTPException(422, {"code": "invalid_schedule",
                                   "message": str(exc)})
    from security.audit import log as _audit
    _audit(actor="user", action="automation:create",
           args={"id": row["id"], "name": row["name"], "schedule": row["schedule"]},
           result="ok")
    return {"ok": True, "automation": row}


@app.patch("/api/automations/{aid}")
async def patch_automation(aid: str, req: AutomationPatch) -> dict[str, Any]:
    from scheduling import get_scheduler
    from scheduling.cron import CronError
    try:
        row = get_scheduler().update(
            aid, name=req.name, schedule=req.schedule,
            prompt=req.prompt, enabled=req.enabled)
    except CronError as exc:
        raise HTTPException(422, {"code": "invalid_schedule",
                                   "message": str(exc)})
    if row is None:
        raise HTTPException(404, {"code": "automation_not_found",
                                   "message": f"automation {aid!r} unknown"})
    from security.audit import log as _audit
    _audit(actor="user", action="automation:update",
           args={"id": aid, "patch": req.model_dump(exclude_none=True)}, result="ok")
    return {"ok": True, "automation": row}


@app.delete("/api/automations/{aid}")
async def delete_automation(aid: str) -> dict[str, Any]:
    from scheduling import get_scheduler
    if not get_scheduler().delete(aid):
        raise HTTPException(404, {"code": "automation_not_found",
                                   "message": f"automation {aid!r} unknown"})
    from security.audit import log as _audit
    _audit(actor="user", action="automation:delete", args={"id": aid}, result="ok")
    return {"ok": True, "removed": aid}


# ---- skills inventory (Phase C — Settings → Skills) -------------------------------

@app.get("/api/skills")
async def list_skills(q: str = "", category: str = "",
                      limit: int = 400) -> dict[str, Any]:
    """The real L4 corpus: every stored skill with its live metadata.
    Search + category filter run in SQL (LIKE) — the corpus is thousands of
    rows, not millions; the honest filter beats a client-side fold."""
    where, params = [], []
    if q:
        like = f"%{q}%"
        where.append("(name LIKE ? OR description LIKE ? OR tags LIKE ?)")
        params += [like, like, like]
    if category:
        where.append("category=?")
        params.append(category)
    base = ("SELECT id,name,source,version,description,tags,category,status,"
            "use_count,last_used,confidence,target_agent,needs_review,enabled,"
            "solar_system_id,orbit_id FROM skills")
    if where:
        base += " WHERE " + " AND ".join(where)
    base += " ORDER BY use_count DESC, name COLLATE NOCASE LIMIT ?;"
    rows = [dict(r) for r in get_storage().query_all(base, tuple(params + [max(1, min(limit, 1000))]))]
    for r in rows:
        r["use_count"] = int(r.get("use_count") or 0)
        r["enabled"] = bool(r.get("enabled", 1))
        r["needs_review"] = bool(r.get("needs_review", 0))
    cats = [dict(r) for r in get_storage().query_all(
        "SELECT category, COUNT(*) AS n FROM skills GROUP BY category ORDER BY n DESC;")]
    return {"skills": rows, "categories": cats}


@app.patch("/api/skills/{skill_id}")
async def patch_skill(skill_id: str, req: SkillPatch) -> dict[str, Any]:
    """v20 toggle. Only `enabled` is writable from the desktop in v1 —
    status/quarantine decisions belong to the signature verifier."""
    st = get_storage()
    row = st.query_one("SELECT id FROM skills WHERE id=?;", (skill_id,))
    if row is None:
        raise HTTPException(404, {"code": "skill_not_found",
                                   "message": f"skill {skill_id!r} unknown"})
    if req.enabled is not None:
        with st.transaction() as conn:
            conn.execute("UPDATE skills SET enabled=? WHERE id=?;",
                         (1 if req.enabled else 0, skill_id))
        # the L4 BM25 search cache never sees raw SQL writes — without this
        # a just-disabled skill kept ranking in SkillSearch until the next
        # ingestion (audit 2026-10; test_phase_c_bridge pins the contract)
        try:
            from core.memory import get_memory
            get_memory().l4._invalidate()
        except Exception:
            pass  # best-effort: memory subsystem may not be booted here
    from security.audit import log as _audit
    _audit(actor="user", action="skill:toggle",
           args={"id": skill_id, "enabled": req.enabled}, result="ok")
    return {"ok": True, "skill_id": skill_id, "enabled": req.enabled}


# ---- goal lifecycle (chats) --------------------------------------------------------

@app.patch("/api/turn/{turn_id}")
@app.patch("/api/goal/{goal_id}")
async def patch_goal(turn_id: str, req: GoalPatch) -> dict[str, Any]:
    st = get_storage()
    row = st.query_one("SELECT id FROM goals WHERE id=?;", (turn_id,))
    if row is None:
        raise HTTPException(404, {"code": "turn_not_found",
                                   "message": "goal is not in the database (still running?)"})
    sets, params = [], []
    if req.title is not None:
        sets.append("title=?")
        params.append(req.title[:200])
    if req.archived is not None:
        sets.append("archived=?")
        params.append(1 if req.archived else 0)
        if not req.archived:
            sets.append("deleted=0")  # unarchive also undeletes (soft path)
    if req.pinned is not None:
        sets.append("pinned=?")
        params.append(1 if req.pinned else 0)
    if req.project_id is not None:
        sets.append("project_id=?")
        params.append(req.project_id)
    if sets:
        params.append(turn_id)
        with st.transaction() as conn:
            conn.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id=?;", params)
    return {"ok": True, "turn_id": turn_id}


@app.delete("/api/turn/{turn_id}")
@app.delete("/api/goal/{goal_id}")
async def delete_goal(turn_id: str, hard: bool = False) -> dict[str, Any]:
    """Soft by default (archived+deleted flags). hard=1 also removes the
    planet + timeline file — used only from an explicit confirm dialog."""
    st = get_storage()
    row = st.query_one("SELECT id FROM goals WHERE id=?;", (turn_id,))
    if row is None:
        raise HTTPException(404, {"code": "turn_not_found",
                                   "message": "goal is not in the database (still running?)"})
    with st.transaction() as conn:
        if hard:
            conn.execute("DELETE FROM goals WHERE id=?;", (turn_id,))
            conn.execute("DELETE FROM planets WHERE turn_id=?;", (turn_id,))
            conn.execute("DELETE FROM handoffs WHERE turn_id=?;", (turn_id,))
            conn.execute("DELETE FROM asteroids WHERE turn_id=?;", (turn_id,))
        else:
            conn.execute("UPDATE goals SET archived=1, deleted=1 WHERE id=?;",
                         (turn_id,))
    # timeline file goes only on hard delete
    if hard:
        try:
            from config import get_config as _gc
            ( _gc().home / "timelines" / f"{turn_id}.jsonl").unlink(missing_ok=True)
            ( _gc().home / "checkpoints" / f"{turn_id}.json").unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True, "deleted": turn_id, "hard": hard}


@app.post("/api/turn/{turn_id}/branch")
@app.post("/api/goal/{goal_id}/branch")
async def branch_goal(turn_id: str) -> dict[str, Any]:
    """Fork a finished chat (C2 regression fix — this used to be a
    guaranteed 500 because it queried goals.session_id, a column that
    never existed).

    Session authority: if the goal row owns a real session, the branch IS
    a session fork (Codex fork parity) — a new session with a copied
    transcript and lineage recorded. Otherwise the legacy contract holds:
    a placeholder task row (kind='task') referencing the parent via
    parent_goal_id, ready to run or reference."""
    from core.agent.base_agent import new_id
    from core.session.store import get_session_store
    st = get_storage()
    row = st.query_one(
        "SELECT id, text, project_id, title, owner_session_id FROM goals "
        "WHERE id=?;", (turn_id,))
    if row is None:
        raise HTTPException(404, {"code": "turn_not_found",
                                   "message": "cannot branch an unknown goal"})
    store = get_session_store()
    src_sid = str(row["owner_session_id"] or "").strip()
    if src_sid and store.get(src_sid) is not None:
        forked = store.fork(src_sid)
        return {"ok": True, "turn_id": row["id"], "session_id": forked["id"],
                "parent_session_id": src_sid, "text": row["text"]}
    # Legacy fallback: no session backing this goal — placeholder task row.
    new_gid = new_id("goal-")
    with st.transaction() as conn:
        conn.execute(
            "INSERT INTO goals(id,text,status,created_at,project_id,title,"
            "owner_session_id,parent_goal_id,kind) VALUES(?,?,?,?,?,?,?,?,?);",
            (new_gid, row["text"], "pending", time.time(),
             row["project_id"] or "",
             f"⑂ {(row['title'] or row['text'])[:80]}",
             str(row["owner_session_id"] or ""), turn_id, "task"),
        )
    return {"ok": True, "turn_id": new_gid, "session_id": turn_id,
            "text": row["text"]}


@app.post("/api/turn/{turn_id}/compact")
async def compact_turn(turn_id: str) -> dict[str, Any]:
    """Compact the session backing a task/chat (UI /compact action).

    The compaction is a recorded event in the transcript: entries before
    the marker leave the replay, the marker's summary IS the prior
    context. Returns the approximate token delta for the toast."""
    from core.session.store import get_session_store
    st = get_storage()
    row = st.query_one(
        "SELECT owner_session_id FROM goals WHERE id=?;", (turn_id,))
    sid = str((row or {}).get("owner_session_id") or "").strip() or turn_id
    store = get_session_store()
    if store.get(sid) is None or not store.transcript(sid):
        return {"ok": True, "compacted": False,
                "note": "empty — nothing to compact"}
    msgs = store.history_messages(sid)
    pre = max(1, sum(len(str(m.get("content") or "")) for m in msgs) // 4)
    store.compact_separator(sid, pre, 0, "[context compacted from the UI]")
    return {"ok": True, "compacted": True, "session_id": sid,
            "pre_tokens": pre, "post_tokens": 0}


class TruncateRequest(BaseModel):
    """Edit-and-resend cut marker: the USER message whose turn (plus every
    later turn) leaves the thread. The UI seeds the composer with that text
    first; the edited resend becomes a fresh message goal on the task."""
    message_goal_id: str


@app.post("/api/turn/{task_id}/truncate")
@app.post("/api/goal/{goal_id}/truncate")
async def truncate_task(task_id: str, req: TruncateRequest) -> dict[str, Any]:
    """Cut a thread at one message: delete that message goal and every
    later message goal of the task (rows + planets/handoffs/asteroids +
    per-goal timeline/checkpoint files). Steps exactly like hard delete.
    Refused while any to-be-deleted turn still runs in the bridge."""
    from config import get_config as _gc
    st = get_storage()
    if st.query_one("SELECT id FROM goals WHERE id=?;", (task_id,)) is None:
        raise HTTPException(404, {"code": "turn_not_found",
                                   "message": "task not found"})
    rows = st.query_all(
        "SELECT id, created_at FROM goals WHERE parent_goal_id=? "
        "AND kind='message' ORDER BY created_at ASC;", (task_id,))
    idx = next(
        (i for i, r in enumerate(rows) if r["id"] == req.message_goal_id), None
    )
    if idx is None:
        # legacy single-goal task: the conversation has no turn rows, so
        # nothing can be cut — the UI keeps the thread and only seeds the
        # composer (edit without history rewrite)
        return {"ok": True, "removed": 0, "legacy": True}
    dead_ids = [r["id"] for r in rows[idx:]]
    running_ids = {gt.turn_id for gt in get_state().running()}
    if running_ids & set(dead_ids):
        raise HTTPException(
            409,
            {"code": "turn_still_running",
             "message": "a later turn is still running — wait or stop it first"},
        )
    with st.transaction() as conn:
        for gid in dead_ids:
            for sql in (
                "DELETE FROM goals WHERE id=?;",
                "DELETE FROM planets WHERE turn_id=?;",
                "DELETE FROM handoffs WHERE turn_id=?;",
                "DELETE FROM asteroids WHERE turn_id=?;",
            ):
                conn.execute(sql, (gid,))
        # the task-level summary described the removed tail — retire it so
        # the row reflects the remaining thread
        conn.execute("UPDATE goals SET final_summary=NULL WHERE id=?;",
                     (task_id,))
    for gid in dead_ids:
        try:
            (_gc().home / "timelines" / f"{gid}.jsonl").unlink(missing_ok=True)
            (_gc().home / "checkpoints" / f"{gid}.json").unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True, "removed": len(dead_ids), "goal_ids": dead_ids}


# ---- MCP servers --------------------------------------------------------------------

def _mcp_row(srv) -> dict[str, Any]:
    return {
        "name": srv.name, "transport": srv.transport,
        "command": srv.command, "url": srv.url,
        "read_only": srv.read_only, "enabled": srv.enabled,
        "handshake_ok": srv.handshake_ok,
        "tool_count": len(srv.tools),
        "declared_hosts": srv.declared_hosts,
    }


@app.get("/api/mcp/servers")
async def mcp_list() -> dict[str, Any]:
    from connectors.mcp_client import get_mcp_client
    out = [_mcp_row(s) for s in get_mcp_client().list_servers()]
    return {"servers": out}


@app.post("/api/mcp/servers")
async def mcp_add(req: McpServerCreate) -> dict[str, Any]:
    """Register + REALLY handshake a server before committing. A server that
    can't answer initialize/tools/list is rejected — same policy as provider
    keys."""
    from connectors.mcp_client import get_mcp_client
    mcp = get_mcp_client()
    try:
        srv = mcp.add(name=req.name, command=req.command,
                      declared_hosts=req.declared_hosts or None,
                      read_only=req.read_only,
                      transport=req.transport, url=req.url,
                      headers=req.headers or None)
    except ValueError as e:
        raise HTTPException(422, {"code": "mcp_invalid",
                                   "message": str(e)})
    if srv.transport == "stdio":
        probe = await mcp.list_tools(req.name)
        if not probe.get("ok"):
            await mcp.remove(req.name)
            raise HTTPException(422, {"code": "mcp_rejected",
                                       "message": f"handshake failed: {probe.get('error')}"})
    return {"ok": True, "server": _mcp_row(mcp.get(req.name))}


@app.delete("/api/mcp/servers/{name}")
async def mcp_remove(name: str) -> dict[str, Any]:
    from connectors.mcp_client import get_mcp_client
    ok = await get_mcp_client().remove(name)
    if not ok:
        raise HTTPException(404, {"code": "mcp_not_found",
                                   "message": f"server {name!r} unknown"})
    return {"ok": True, "removed": name}


@app.patch("/api/mcp/servers/{name}")
async def mcp_patch(name: str, req: dict[str, Any]) -> dict[str, Any]:
    from connectors.mcp_client import get_mcp_client
    mcp = get_mcp_client()
    if mcp.get(name) is None:
        raise HTTPException(404, {"code": "mcp_not_found",
                                   "message": f"server {name!r} unknown"})
    if "enabled" in req:
        mcp.set_enabled(name, bool(req["enabled"]))
    # full edit: reconfigure url/command/headers/declared_hosts/read_only
    # in place (same validation as McpServerCreate), then clear the stale
    # handshake so the next tools/list re-verifies against the new target
    reconfig_keys = {"url", "command", "headers", "declared_hosts",
                     "read_only", "transport"}
    if any(k in req for k in reconfig_keys):
        try:
            await mcp.reconfigure(name,
                            **{k: req[k] for k in reconfig_keys if k in req})
        except ValueError as e:
            raise HTTPException(422, {"code": "mcp_invalid",
                                       "message": str(e)})
        if req.get("verify", True) and mcp.get(name).transport == "stdio":
            probe = await mcp.list_tools(name)
            if not probe.get("ok"):
                raise HTTPException(422, {"code": "mcp_rejected",
                                           "message": f"handshake failed: {probe.get('error')}"})
    return {"ok": True, "server": _mcp_row(mcp.get(name))}


# ---- agents ----------------------------------------------------------------------

@app.get("/api/agents")
async def agents_list() -> dict[str, Any]:
    """The SUBAGENTS only: who they are and which tools they may use. The
    universe agent is the main loop itself, not a peer in this list (it has
    no separate model — subagents inherit the one model set from the chat
    composer, so per-agent (provider, model) editing is gone from the UI)."""
    _runtime.init()
    from core.core_agents import ALL_AGENTS, get_agent
    out = []
    for name in list(ALL_AGENTS):
        if name == "universe":
            continue  # the main agent is the conversation, not a roster row
        a = get_agent(name)
        out.append({
            "name": name,
            "specialty": getattr(a, "specialty", "routing + synthesis"),
            "tools": list(getattr(a, "tool_whitelist_names", []) or []),
            "tier": getattr(a, "default_model_tier", ""),
        })
    return {"agents": out}


@app.patch("/api/agents/{name}/model")
async def agent_model(name: str, req: dict[str, Any]) -> dict[str, Any]:
    """Override one agent's (provider, model) — from the Agents settings."""
    from core.core_agents import ALL_AGENTS
    from providers.manager import get_provider_manager
    if name not in list(ALL_AGENTS) + ["orchestrator"]:
        raise HTTPException(404, {"code": "agent_not_found",
                                   "message": f"agent {name!r} unknown"})
    # legacy label: the orchestrator IS the universe agent now
    name = "universe" if name == "orchestrator" else name
    provider = str(req.get("provider", "")).strip()
    model = str(req.get("model", "")).strip()
    if not (provider and model):
        raise HTTPException(422, {"code": "invalid_assignment",
                                   "message": "provider and model are required"})
    pm = get_provider_manager()
    if pm.get_provider(provider) is None:
        raise HTTPException(422, {"code": "invalid_assignment",
                                   "message": f"provider {provider!r} is not installed"})
    pm.set_agent_model(name, provider, model)
    return {"ok": True, "agent": name, "provider": provider, "model": model}


@app.post("/api/consent/{pending_id}")
async def decide_consent(pending_id: str, req: ConsentDecision) -> dict[str, Any]:
    ok = _runtime.consent.decide(pending_id, req.approve)
    if not ok:
        raise HTTPException(404, "consent request unknown or already resolved")
    return {"pending_id": pending_id, "approved": req.approve}


@app.get("/api/consent/pending")
async def pending_consents() -> dict[str, Any]:
    return {"pending": _runtime.consent.pending()}


# ---- structured questions (AskUserQuestion twin) -----------------------------

class QuestionAnswer(BaseModel):
    answer: str


@app.post("/api/question/{pending_id}")
async def answer_question(pending_id: str, req: QuestionAnswer) -> dict[str, Any]:
    ok = _runtime.questions.answer(pending_id, req.answer)
    if not ok:
        raise HTTPException(404, "question unknown or already answered")
    return {"pending_id": pending_id, "answered": True}


@app.get("/api/question/pending")
async def pending_questions() -> dict[str, Any]:
    return {"pending": _runtime.questions.pending()}


# ---- permission modes (full/plan/ask) ----------------------------------------

class CommandResolve(BaseModel):
    text: str = ""


class PermissionModeUpdate(BaseModel):
    mode: str


@app.get("/api/commands")
async def list_slash_commands() -> dict[str, Any]:
    """The live slash-command menu (ZCode's built-ins + .md command files)."""
    from universe_desktop_bridge.slash import list_commands
    return {"commands": list_commands()}


@app.post("/api/commands/resolve")
async def resolve_slash_command(req: CommandResolve) -> dict[str, Any]:
    """Resolve one /token [args] input: built-in action, custom prompt, or
    None (typed paths never match). The composer asks before sending."""
    from universe_desktop_bridge.slash import resolve
    out = resolve(req.text)
    return {"command": out}


@app.get("/api/permission-mode")
async def get_permission_mode() -> dict[str, Any]:
    from security.capability import get_gate, PERMISSION_MODES
    return {"mode": get_gate().get_permission_mode(),
            "modes": list(PERMISSION_MODES)}


@app.put("/api/permission-mode")
async def set_permission_mode(req: PermissionModeUpdate) -> dict[str, Any]:
    from security.capability import get_gate, normalize_permission_mode
    canonical = normalize_permission_mode(req.mode)
    if canonical is None:
        from security.capability import PERMISSION_MODES
        raise HTTPException(422, {"code": "invalid_mode",
                                   "message": f"mode must be one of {PERMISSION_MODES} (aliases accepted)"})
    return {"mode": get_gate().set_permission_mode(canonical)}


# ---- message feedback (Like / Dislike on an assistant reply) -----------------

class FeedbackRequest(BaseModel):
    turn_id: str = Field(min_length=1, max_length=64)
    rating: str = Field(pattern="^(like|dislike)$")


@app.post("/api/feedback")
async def file_feedback(req: FeedbackRequest) -> dict[str, Any]:
    """ZCode chat-row parity: the assistant message's Like/Dislike buttons.
    The goal must exist (a rating on a phantom row is a client bug), but any
    lifecycle state is accepted — the user may rate the moment a reply ends.
    Idempotent per (goal, rating): re-clicking the same button is a no-op so
    the UI can fire freely; switching rating keeps both rows (the latest
    wins by ts for any future reader)."""
    row = get_storage().query_one(
        "SELECT id FROM goals WHERE id=?;", (req.turn_id,))
    if row is None:
        raise HTTPException(404, {"code": "turn_not_found",
                                   "message": "that goal no longer exists"})
    # idempotent per (goal, rating): re-clicking the same button is a no-op
    # (WHERE NOT EXISTS, so it holds on every schema state — no unique index
    # dependency); switching rating keeps both rows, latest ts wins for readers
    get_storage().execute(
        "INSERT INTO turn_feedback(turn_id, rating, ts) "
        "SELECT ?,?,? WHERE NOT EXISTS("
        "SELECT 1 FROM turn_feedback WHERE turn_id=? AND rating=?);",
        (req.turn_id, req.rating, time.time(), req.turn_id, req.rating))
    return {"ok": True, "turn_id": req.turn_id, "rating": req.rating}


# ---- real LLM providers ----------------------------------------------------

class ProviderSetupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    # "" = use the catalog preset's URL; a non-empty value is only trusted
    # for custom providers and templated presets (url_fields). The old
    # min_length=8 forced every catalog install to ship a fake URL.
    base_url: str = Field(default="", max_length=300)
    api_key: str = Field(max_length=400)  # empty allowed for local servers
    # optional since the wizard no longer picks models: the bridge routes
    # agents to the FIRST live model the probe discovered
    model: str = Field(default="", max_length=120)
    set_default: bool = True


class ModelSwitchRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)


@app.get("/api/providers")
async def list_providers() -> dict[str, Any]:
    """What answers goals right now, honestly. No provider configured means
    goals are rejected with an explicit no-provider error — the UI surfaces
    this loudly so nobody waits on a response that will never come."""
    _runtime.init()
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    out = []
    has_provider = False
    # per-key health rows (in-memory 5-min windows) power the disabled
    # badge in the per-key list below
    disabled_aliases = {r["key"] for r in pm.health_snapshot() if r["disabled"]}
    for p in pm.list_providers():
        if not p.keys:
            continue  # a provider without keys answers nothing
        out.append({
            "name": p.name,
            "base_url": p.base_url,
            "key_count": len(p.keys),
            "key_aliases": [k.alias for k in p.keys],
            # full per-key view: ids + masked previews + health flags, so
            # the Settings UI manages N keys per provider without ever
            # seeing a secret
            "keys": [{
                "id": k.id,
                "alias": k.alias,
                "preview": _mask_secret(_safe_decrypt(k.encrypted_secret)),
                "status": k.status,
                "disabled": k.alias in disabled_aliases,
            } for k in p.keys],
        })
    # honest gate: a stored key alone is NOT enough — the orchestrator must
    # actually resolve (key + agent_models routing), exactly like the
    # POST /api/turn preflight. Otherwise the badge says "serving" while
    # every send is rejected with no_provider.
    has_provider = pm.best_available("universe") is not None
    # which provider actually served the most recent LLM call (if any).
    # Tail-read: the log grows without bound and this endpoint is polled —
    # reading the whole file per poll is O(history) per poll.
    serving = "none"
    try:
        from config import get_config as _gc
        log = _gc().llm_log
        if log.exists():
            with open(log, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 4096))
                tail = fh.read().decode("utf-8", errors="replace")
            last = ""
            for line in tail.splitlines():
                if line.strip():
                    last = line
            if last:
                import json as _json
                serving = _json.loads(last).get("provider", "none")
    except Exception:
        pass
    if serving == "Universe Echo":  # stale log from before the purge
        serving = "none"
    # The log only records the past. On a fresh install — or right after the
    # history wipe — it is gone, but a configured provider still routes every
    # new goal. The badge must never claim "no provider" while a key exists.
    installed = [e["name"] for e in out]
    if installed and (serving == "none" or serving not in installed):
        serving = installed[0]
    # The chat composer's model chip reads its live state from here: the
    # (provider, model) the universe agent is pinned to — one poll for the
    # badge AND the chip instead of a second /api/agents round-trip.
    active_provider: str = ""
    active_model: str = ""
    try:
        assignment = pm.get_agent_model("universe")
        if assignment:
            active_provider, active_model = assignment[0], assignment[1]
    except Exception:
        pass
    return {"providers": out, "currently_serving": serving,
            "has_provider": has_provider,
            "active_provider": active_provider,
            "active_model": active_model}


@app.get("/api/providers/catalog")
async def provider_catalog() -> dict[str, Any]:
    """The curated provider catalog (free cloud / paid cloud / local) plus a
    live reachability probe for the local servers — local cards light up in
    the UI the moment Ollama/LM Studio/vLLM are actually running.

    Serves INSTANTLY: the catalog is static, and local reachability is
    probed in the background on first request (the probe path — cold httpx
    import + connection attempts — cost the page ~4s when it ran inline).
    When the background probe finishes it emits `catalog_probes_updated`;
    the UI may refetch, and any later request within 30s gets cached
    verdicts immediately."""
    from providers.catalog import catalog_payload
    payload = catalog_payload()
    now = time.monotonic()
    cached = _catalog_probe_cache.get("value")
    reachable: dict[str, bool] = {}
    if cached is not None and now - _catalog_probe_cache["at"] < 30:
        reachable = cached  # type: ignore[assignment]
    else:
        # freshen in the background — never block the response
        if _catalog_probe_task is None or _catalog_probe_task.done():
            _start_catalog_probe(payload)
        if cached is not None:
            reachable = cached  # type: ignore[assignment]  stale > blank
    return {"catalog": payload, "local_reachable": reachable}


_catalog_probe_task: Any = None


def _start_catalog_probe(payload: list[dict[str, object]]) -> None:
    global _catalog_probe_task
    loop = asyncio.get_running_loop()

    async def _run() -> None:
        global _catalog_probe_task
        try:
            locals_ = [(str(e["name"]), str(e["base_url"]))
                       for e in payload if e["category"] == "local"]
            if not locals_:
                return
            async def _probe(base: str) -> bool:
                return (await _probe_provider(base, "", timeout=2.5))["ok"]
            verdicts = await asyncio.gather(*(_probe(base) for _, base in locals_))
            reachable = {name: ok for (name, _), ok in zip(locals_, verdicts)}
            _catalog_probe_cache["value"] = reachable
            _catalog_probe_cache["at"] = time.monotonic()
            emit("catalog_probes_updated", local_reachable=reachable)
        except Exception:
            # a failed probe pass leaves the previous cache — the page must
            # never be worse than "instant static catalog"
            pass
        finally:
            _catalog_probe_task = None

    _catalog_probe_task = loop.create_task(_run())


@app.post("/api/providers")
async def add_provider_key(req: ProviderSetupRequest) -> dict[str, Any]:
    """Add (or update) an OpenAI-compatible provider with a real key and
    route every agent to it. The key is encrypted at rest via the same
    keyring-backed encryptor the CLI uses — the bridge never stores it
    in plain text and never echoes it back. Local servers (Ollama, LM
    Studio, vLLM) accept an empty key.

    N keys per provider: re-installing the SAME provider (same base_url)
    APPENDS the new key instead of replacing — the rotation layer
    (pick_key/list_keys) already walks every key. Changing the base_url
    re-creates the provider (old keys belonged to a different endpoint)."""
    _runtime.init()
    if _runtime.orchestrator is None:
        raise HTTPException(503, "backend not initialized")
    from providers.catalog import get_preset
    from providers.manager import get_provider_manager
    from security.secrets_fallback import encrypt_secret
    from core.core_agents.agents import ALL_AGENTS
    pm = get_provider_manager()

    preset = get_preset(req.name)
    if preset is not None and preset.url_fields:
        # templated URL (Cloudflare account_id, Azure resource): trust the
        # client-resolved base_url; the preset is only a shape
        if req.base_url:
            base_url = req.base_url
        else:
            raise HTTPException(422, {
                "code": "provider_rejected",
                "message": f"{req.name} needs a resolved URL (fill: "
                           + ", ".join(preset.url_fields) + ")",
            })
    else:
        base_url = (preset.base_url if preset else None) or req.base_url
    if not base_url.strip():
        raise HTTPException(422, {"code": "provider_rejected",
                                  "message": "a base URL is required"})
    is_local = bool(preset and preset.category == "local")
    if is_local and not req.api_key:
        req.api_key = "local-no-key"  # OpenAI SDK requires a non-empty string
    if not req.api_key:
        raise HTTPException(422, {"code": "provider_rejected",
                                  "message": "an API key is required for cloud providers"})

    # sanity: the key must actually work before we commit it — one real
    # models-list call against the provider
    probe = await _probe_provider(base_url, req.api_key)
    if not probe["ok"]:
        raise HTTPException(422, {"code": "provider_rejected",
                                  "message": f"key check failed: {probe['error']}"})

    # no model given (the wizard never picks one): route agents to the
    # first live model this key actually discovered. Gateways that forbid
    # GET /models (listing_forbidden: OpenCode Zen) yield no live list —
    # fall back to the catalog preset's own model ids, never the literal
    # "default" (a nonexistent model breaks every agent the moment it's
    # routed; verified in production with a 410 Gone chain).
    discovered = (probe.get("models") or [])[0] if probe.get("models") else ""
    fallback = ""
    if preset is not None and not discovered:
        fallback = (preset.models or [""])[0]
    model = req.model or discovered or fallback or "default"

    provider = pm.get_provider(req.name)
    same_endpoint = provider is not None and provider.base_url == base_url
    if provider is None:
        provider = pm.add_provider(req.name, base_url)
    elif not same_endpoint:
        pm.remove_provider(req.name)
        provider = pm.add_provider(req.name, base_url)
    if same_endpoint and provider.keys:
        # append: give the new key an unused alias (desktop-2, desktop-3…)
        taken = {k.alias for k in provider.keys}
        n = len(provider.keys) + 1
        alias = f"desktop-{n}"
        while alias in taken:
            n += 1
            alias = f"desktop-{n}"
        appended = True
    else:
        # first key (or endpoint changed): classic single "desktop" alias
        for k in list(provider.keys):
            pm.remove_key(req.name, k.id)
        alias = "desktop"
        appended = False
    pm.add_key(req.name, alias=alias, encrypted_secret=encrypt_secret(req.api_key))
    _invalidate_models_cache(req.name)

    if req.set_default:
        for agent in ALL_AGENTS:
            pm.set_agent_model(agent, req.name, model)
        routed = list(ALL_AGENTS)
    else:
        # an install that routes NOBODY leaves has_provider=true (a key
        # exists) while best_available() is empty — the exact "connected
        # but send says no provider" split. Always route agents that have
        # no assignment yet; explicit assignments are never clobbered.
        routed = []
        for agent in list(ALL_AGENTS):
            if pm.get_agent_model(agent) is None:
                pm.set_agent_model(agent, req.name, model)
                routed.append(agent)

    return {"ok": True, "provider": req.name, "model": model,
            "models": probe.get("models", []),
            "verified": True, "key_appended": appended,
            "agents_routed": routed}


@app.get("/api/providers/{name}/keys")
async def list_provider_keys(name: str) -> dict[str, Any]:
    """Per-key inventory (masked previews only) for the Settings manager."""
    _runtime.init()
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    provider = pm.get_provider(name)
    if provider is None:
        raise HTTPException(404, {"code": "provider_not_found",
                                  "message": f"provider {name!r} is not installed"})
    disabled = {r["key"] for r in pm.health_snapshot() if r["disabled"]}
    return {"provider": name, "keys": [{
        "id": k.id, "alias": k.alias,
        "preview": _mask_secret(_safe_decrypt(k.encrypted_secret)),
        "status": k.status, "disabled": k.alias in disabled,
    } for k in provider.keys]}


class KeyAddRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=400)


@app.post("/api/providers/{name}/keys")
async def add_single_key(name: str, req: KeyAddRequest) -> dict[str, Any]:
    """Add ONE more key to an installed provider (unlimited per provider).
    The key is probed live before it is stored; on failure nothing changes."""
    _runtime.init()
    from providers.manager import get_provider_manager
    from security.secrets_fallback import encrypt_secret
    pm = get_provider_manager()
    provider = pm.get_provider(name)
    if provider is None:
        raise HTTPException(404, {"code": "provider_not_found",
                                  "message": f"provider {name!r} is not installed"})
    probe = await _probe_provider(provider.base_url, req.api_key)
    if not probe["ok"]:
        raise HTTPException(422, {"code": "key_rejected",
                                  "message": f"key check failed: {probe['error']}"})
    taken = {k.alias for k in provider.keys}
    n = len(provider.keys) + 1
    alias = f"key-{n}" if "desktop" in taken or n > 1 else "desktop"
    while alias in taken:
        n += 1
        alias = f"key-{n}"
    key = pm.add_key(name, alias=alias,
                     encrypted_secret=encrypt_secret(req.api_key))
    _invalidate_models_cache(name)
    return {"ok": True, "provider": name, "key_id": key.id, "alias": alias}


@app.delete("/api/providers/{name}/keys/{key_id}")
async def remove_single_key(name: str, key_id: int) -> dict[str, Any]:
    """Remove ONE key. The last key is refused with a clear hint: removing
    it would silently disable the provider — use the provider's delete
    instead."""
    _runtime.init()
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    provider = pm.get_provider(name)
    if provider is None:
        raise HTTPException(404, {"code": "provider_not_found",
                                  "message": f"provider {name!r} is not installed"})
    if len(provider.keys) <= 1:
        raise HTTPException(409, {
            "code": "last_key",
            "message": "this is the provider's only key — delete the whole "
                       "provider instead, or add another key first",
        })
    if not pm.remove_key(name, key_id):
        raise HTTPException(404, {"code": "key_not_found",
                                  "message": f"key {key_id} not found"})
    _invalidate_models_cache(name)
    return {"ok": True, "removed": key_id}


async def _probe_provider(base_url: str, api_key: str, timeout: float = 8) -> dict[str, Any]:
    """One real request to prove the key works. No retries — a key that
    can't answer one models-list is not worth installing.

    m1 SSRF guard: a provider base_url pointing at private/link-local
    network space is refused unless the user EXPLICITLY typed localhost
    (local inference servers like Ollama/llama.cpp are legitimate, but a
    config mistake or a hostile string must not silently probe the LAN)."""
    url = base_url.rstrip("/")
    # SSRF check on the ORIGINAL host before building any request
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"provider URL scheme not allowed: {parsed.scheme!r}"}
    host = parsed.hostname or ""
    explicit_local = host in ("localhost", "127.0.0.1", "::1")
    if not explicit_local:
        import ipaddress
        import socket as _socket
        # getaddrinfo is a BLOCKING syscall (measured 2.3s for an NXDOMAIN
        # host on Windows DNS); inline it froze the whole event loop — every
        # concurrent endpoint, including /api/providers, stalled behind it.
        # Push it to a worker thread.
        import asyncio as _asyncio

        def _resolve() -> list:
            try:
                return _socket.getaddrinfo(host, None)
            except _socket.gaierror:
                return []

        infos = await _asyncio.to_thread(_resolve)
        if not infos:
            return {"ok": False, "error": f"cannot resolve provider host {host!r}"}
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return {"ok": False,
                        "error": f"provider host {host!r} resolves to private "
                                 f"address {ip} — use localhost explicitly for "
                                 f"local inference servers"}
    # Providers differ: most serve /v1/models, Gemini serves /v1beta/openai/models,
    # Perplexity serves /models directly. Trust the given base URL and only
    # append /v1 when it has no version path at all.
    path = urlparse(url).path.rstrip("/")
    if not any(seg in path for seg in ("/v1", "/v1beta", "/v2", "/v3")):
        url = f"{url}/v1"
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(f"{url}/models", headers=headers)
        if res.status_code == 200:
            return {"ok": True, "models": [m.get("id") for m in res.json().get("data", [])]}
        if res.status_code == 401:
            return {"ok": False, "error": "HTTP 401 from provider (key rejected)"}
        if res.status_code in (403, 404):
            # listing endpoint forbidden/missing (OpenCode Zen returns 403):
            # the key may still be perfect for chat. One micro chat call
            # decides honestly — 401 there means bad key, anything else
            # means the endpoint accepted the credential.
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    probe = await client.post(
                        f"{url}/chat/completions", headers=headers,
                        json={"model": "probe", "max_tokens": 1,
                              "messages": [{"role": "user", "content": "."}]})
                if probe.status_code == 401:
                    return {"ok": False,
                            "error": "key rejected (401 on chat probe)"}
                return {"ok": True, "models": [], "listing_forbidden": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:200]}
        return {"ok": False, "error": f"HTTP {res.status_code} from provider"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@app.delete("/api/providers/{name}")
async def remove_provider(name: str) -> dict[str, Any]:
    """Remove a provider and every routing that pointed at it. With no
    provider left, goals are rejected with no_provider — the honest state."""
    _runtime.init()
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    if pm.get_provider(name) is None:
        raise HTTPException(404, {"code": "provider_not_found",
                                   "message": f"provider {name!r} is not installed"})
    pm.remove_provider(name)
    return {"ok": True, "removed": name}


@app.get("/api/providers/{name}/models")
async def list_provider_models(name: str) -> dict[str, Any]:
    """The LIVE model list of an installed provider — ALL models, no cap,
    no internal fallback. Pre-warmed at bridge startup (_prewarm_models)
    so the picker is instant; a fresh probe only runs when the pre-warm
    result is absent (first call after startup, or after invalidation).
    Responses carry `cached`: True when served from the pre-warm cache."""
    _runtime.init()
    from providers.manager import get_provider_manager
    from security.secrets_fallback import decrypt_secret
    pm = get_provider_manager()
    provider = pm.get_provider(name)
    if provider is None:
        raise HTTPException(404, {"code": "provider_not_found",
                                   "message": f"provider {name!r} is not installed"})
    # pre-warmed result from startup — instant, no network wait
    prewarmed = _models_cache.get(name)
    if prewarmed is not None:
        return {"ok": True, "provider": name, "models": prewarmed,
                "cached": True}
    # cold: live probe (this only happens on the very first call racing
    # startup, or after install/key/model-change invalidation)
    key = ""
    pk = pm.pick_key(name)
    if pk is not None:
        try:
            key = decrypt_secret(pk.encrypted_secret)
        except Exception:
            key = ""
    probe = await _probe_provider(provider.base_url, key)
    if not probe["ok"]:
        # NO catalog fallback: the user's instruction is explicit — show
        # ONLY what the provider returns; free-text covers the rest.
        return {"ok": True, "provider": name, "models": [],
                "free_text": True, "probe_error": probe["error"],
                "cached": False}
    models = probe.get("models", [])
    if not models and probe.get("listing_forbidden"):
        return {"ok": True, "provider": name, "models": [],
                "free_text": True, "cached": False}
    _models_cache[name] = models
    return {"ok": True, "provider": name, "models": models,
            "cached": False}


@app.post("/api/providers/{name}/model")
async def switch_model(name: str, req: ModelSwitchRequest) -> dict[str, Any]:
    """Re-route every agent to a different model on an already-installed
    provider (the key stays encrypted on disk; the UI never sees it)."""
    _runtime.init()
    from providers.manager import get_provider_manager
    from core.core_agents.agents import ALL_AGENTS
    pm = get_provider_manager()
    if pm.get_provider(name) is None:
        raise HTTPException(404, {"code": "provider_not_found",
                                   "message": f"provider {name!r} is not installed"})
    for agent in ALL_AGENTS:
        pm.set_agent_model(agent, name, req.model)
    _invalidate_models_cache(name)
    return {"ok": True, "provider": name, "model": req.model,
            "agents_routed": list(ALL_AGENTS)}


@app.get("/api/settings")
async def read_settings() -> dict[str, Any]:
    cfg = get_config()
    return {
        "settings": _masked_view(_read_desktop_settings()),
        "project_root": cfg.get("project_root", "") or "",
        "version": UNIVERSE_VERSION,
    }


@app.put("/api/settings")
async def write_settings(req: SettingsUpdate) -> dict[str, Any]:
    cfg = get_config()
    # merge-safe: read the PERSISTED dict raw and write back into it —
    # the old flow projected through _read_desktop_settings() (defaults +
    # declared keys only) and cfg.set'd that, silently dropping any key a
    # future subsystem added under `desktop`. Unknown keys must survive.
    raw = cfg.get("desktop", {})
    if not isinstance(raw, dict):
        raw = {}
    stored = _read_desktop_settings()
    merged: dict[str, Any] = dict(raw)
    for k, v in stored.items():
        merged.setdefault(k, v)
    changed: dict[str, Any] = {}
    for key, value in req.model_dump(exclude_none=True).items():
        if key == "project_root":
            # an explicit value — including "" — clears/sets the root
            if value != (cfg.get("project_root", "") or ""):
                cfg.set("project_root", value)
                changed["project_root"] = value
            continue
        if key not in stored:
            continue
        if key == "wake_word" and isinstance(value, str) and not value.strip():
            # an empty wake word would kill the voice trigger silently —
            # ignore it and keep the current/default phrase
            continue
        if key == "default_thinking_level" and value not in (
                "fast", "balanced", "max"):
            continue
        if key == "theme" and value not in ("light", "dark", "system", "zai-light", "zai-dark"):
            continue
        if key == "locale" and value not in ("system", "en-US", "zh-CN", "ar", "en", ""):
            continue
        if key in ("uiFontSize", "fontSize") and not (8 <= int(value) <= 32):
            continue
        if key == "integratedTerminalShell" and value not in ("auto", "cmd.exe", "Git Bash", "pwsh", "bash", ""):
            continue
        if key == "taskAutoArchive" and value not in ("off", "3", "7", "14", "30"):
            continue
        if key == "taskAutoArchiveDays" and not (1 <= int(value) <= 90):
            continue
        if key == "httpProxy" and value and not str(value).startswith(("http://", "https://", "")):
            continue
        if key in ("stt_api_key", "tts_api_key") and value:
            # a masked echo means "unchanged" — never clobber the real key
            if value == _mask_secret(str(stored[key])):
                continue
            # encrypt at rest (audit 2026-09): these keys used to sit as
            # plaintext strings in ~/.universe/config.json while every LLM
            # provider key was AES-encrypted in SQLite. config.json now
            # stores the blob; _read_desktop_settings decrypts for use.
            from security.secrets_fallback import encrypt_secret
            value = encrypt_secret(str(value))
        if value != stored[key]:
            stored[key] = value
            merged[key] = value
            changed[key] = value
    if any(k != "project_root" for k in changed):
        cfg.set("desktop", merged)
    # user hooks live under desktop.hooks[] — a settings write must reload
    # the hook manager or the new rules never take effect
    from hooks import get_hook_manager
    get_hook_manager().reload()
    emit("settings_updated", **changed)
    if changed:
        from security.audit import log as _audit
        _audit(actor="user", action="settings:update",
               args={k: v for k, v in changed.items()
                     if k not in ("stt_api_key", "tts_api_key")},
               result="ok")
    return {"settings": _masked_view(_read_desktop_settings()),
            "project_root": cfg.get("project_root", "") or "",
            "changed": list(changed)}


# ---- voice channel (plan §8) -------------------------------------------------

class VoiceCheckRequest(BaseModel):
    kind: str                 # 'stt' | 'tts'
    provider: str             # groq / openai / fish-audio / elevenlabs / …
    api_key: str


@app.post("/api/voice/check")
async def voice_check(req: VoiceCheckRequest) -> dict[str, Any]:
    """One real request proving a voice key works before it's saved —
    the same honesty rule as LLM provider keys. Cloud STT keys get a
    tiny WAV models-list/whoami probe; TTS keys a whoami-style call."""
    try:
        if req.kind == "stt" and req.provider == "groq":
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {req.api_key}"})
            ok = res.status_code == 200
            return {"ok": ok,
                    "error": None if ok else f"HTTP {res.status_code}"}
        if req.kind == "stt" and req.provider == "openai":
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {req.api_key}"})
            ok = res.status_code == 200
            return {"ok": ok,
                    "error": None if ok else f"HTTP {res.status_code}"}
        if req.kind == "tts" and req.provider == "fish-audio":
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    "https://api.fish.audio/v1/models?page_size=1",
                    headers={"Authorization": f"Bearer {req.api_key}"})
            ok = res.status_code == 200
            return {"ok": ok,
                    "error": None if ok else f"HTTP {res.status_code}"}
        if req.kind == "tts" and req.provider == "elevenlabs":
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": req.api_key})
            ok = res.status_code == 200
            return {"ok": ok,
                    "error": None if ok else f"HTTP {res.status_code}"}
        if req.kind == "tts" and req.provider == "openai":
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {req.api_key}"})
            ok = res.status_code == 200
            return {"ok": ok,
                    "error": None if ok else f"HTTP {res.status_code}"}
        return {"ok": False, "error": f"no check for {req.kind}/{req.provider}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@app.websocket("/api/voice/stream")
async def voice_ws(ws: WebSocket) -> None:
    """Mic in (binary PCM16 frames) · wake word · STT · spoken goals · TTS out."""
    _runtime.init()
    if _runtime.orchestrator is None:
        await ws.accept()
        await ws.send_json({"type": "voice_status", "state": "error",
                            "error": "backend not initialized",
                            "init_error": _runtime.init_error or None})
        await ws.close()
        return
    from universe_desktop_bridge.voice import voice_session
    await voice_session(ws, _runtime.orchestrator)


# ---- live events over WebSocket ---------------------------------------------

@app.websocket("/api/events")
async def events_ws(ws: WebSocket) -> None:
    await ws.accept()
    bus = get_event_bus()
    sub = bus.subscribe()
    try:
        await ws.send_json({"type": "bridge_hello", "ts": round(time.time(), 3),
                            "version": UNIVERSE_VERSION})
        while True:
            try:
                ev = await sub.next_event(timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "heartbeat", "ts": round(time.time(), 3)})
                continue
            if ev is None:
                break  # bus was reset (tests) — end this socket cleanly
            # every goal event already carries its seq from emit() (the
            # single numbering source) and is in the goal's ring via the
            # process-wide consumer — this loop only FORWARDS. The old
            # re-stamp here produced a second, different seq for the same
            # event, which slipped past the client's seq-dedup on refill.
            await ws.send_json(ev)
    except WebSocketDisconnect:
        pass
    except Exception:
        # a dying socket must never take the bridge down
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        sub.stop()


# ---- terminal rail over WebSocket (Phase C — no node-pty) -------------------------
#
# ZCode-parity terminal panel. There is NO pseudo-terminal: every submitted
# line is one shell.exec-style run (spawn → capture → 5-min kill-tree),
# streamed back as a result message. A per-connection `cwd` makes `cd`
# stick across runs: the panel sends {"type":"cd","path":…} as an explicit
# protocol message and the server resolves it (relative paths join the
# tracked cwd) — no marker-line parsing, no nested cmd quoting games.
#
# Principal: the USER. The person typing into the terminal panel is the
# same person who could open cmd.exe; this is not an agent action, so the
# consent gate that protects agent-initiated shell use does not apply.
# The socket itself is loopback + bearer-token gated like every bridge
# route. Commands run through shell_exec_unsandboxed() — the same
# execution function the unsandboxed TOOL wraps — so both principals
# share one spawn/capture/timeout implementation.

TERMINAL_TIMEOUT_S = 5 * 60  # matches shell.exec's hard cap


@app.websocket("/api/terminal")
async def terminal_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        from config import get_config as _gc
        cwd = str(_gc().get("project_root", "") or "") or os.getcwd()
        await ws.send_json({"type": "ready", "cwd": cwd})
        while True:
            try:
                msg = await ws.receive_json()
            except WebSocketDisconnect:
                break
            mtype = str(msg.get("type") or "")
            if mtype == "cd":
                target = str(msg.get("path") or "").strip()
                if target in ("", "~"):
                    cwd = os.path.expanduser("~")
                elif os.path.isabs(target):
                    cwd = target
                else:
                    cwd = os.path.normpath(os.path.join(cwd, target))
                if not os.path.isdir(cwd):
                    await ws.send_json({"type": "error",
                                        "message": f"cd: no such directory: {target}",
                                        "cwd": cwd})
                    continue
                await ws.send_json({"type": "cwd", "cwd": cwd})
                continue
            if mtype != "run":
                await ws.send_json({"type": "error",
                                    "message": f"unknown message type {mtype!r}",
                                    "cwd": cwd})
                continue
            cmd = str(msg.get("cmd") or "").strip()
            if not cmd:
                continue
            await ws.send_json({"type": "start", "cmd": cmd, "cwd": cwd})
            from connectors.builtin.shell import shell_exec_unsandboxed
            result = await asyncio.to_thread(
                shell_exec_unsandboxed, cmd, cwd, TERMINAL_TIMEOUT_S, None)
            await ws.send_json({"type": "result",
                                "cmd": cmd,
                                "stdout": str(result.get("stdout") or ""),
                                "stderr": str(result.get("stderr") or ""),
                                "returncode": result.get("returncode"),
                                "elapsed_ms": result.get("elapsed_ms", 0),
                                "cwd": cwd})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        # a dying terminal socket must never take the bridge down
        logger.debug(f"terminal ws closed: {exc}")
        try:
            await ws.close()
        except Exception:
            pass


# ==== Universe Bots · Studio · Nebula · Meta media (P0 2026-11) ==============

class BotCreate(BaseModel):
    name: str
    description: str


class BotRunReq(BaseModel):
    task: str


class SkillReq(BaseModel):
    name: str


class ScheduleReq(BaseModel):
    kind: str = Field(pattern="^(none|daily|interval|cron)$")
    time: str | None = None
    expr: str | None = None
    tz: str | None = None


class ApprovalDecisionReq(BaseModel):
    decision: str = Field(pattern="^(allow_once|always_allow|deny)$")


class ImageReq(BaseModel):
    prompt: str
    size: str | None = None


class NoteCreate(BaseModel):
    title: str
    content: str = ""


class NoteUpdate(BaseModel):
    content: str


class DistillReq(BaseModel):
    session_id: str | None = None


class MetaTestReq(BaseModel):
    key: str | None = None


@app.get("/api/bots")
async def bots_list() -> dict[str, Any]:
    from bots import store
    return {"bots": store.list_bots()}


@app.post("/api/bots")
async def bots_create(req: BotCreate) -> dict[str, Any]:
    from bots import store
    if not req.name.strip():
        raise HTTPException(400, {"code": "bad_bot", "message": "name required"})
    return {"bot": store.create_bot(req.name, req.description)}


@app.get("/api/bots/{bot_id}")
async def bots_get(bot_id: str) -> dict[str, Any]:
    from bots import store
    bot = store.get_bot(bot_id)
    if bot is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    return {**bot, "transcript": bot.get("transcript", [])}


@app.delete("/api/bots/{bot_id}")
async def bots_delete(bot_id: str) -> dict[str, Any]:
    from bots import store
    if not store.delete_bot(bot_id):
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    return {"ok": True}


@app.post("/api/bots/{bot_id}/run")
async def bots_run(bot_id: str, req: BotRunReq) -> dict[str, Any]:
    from bots import runtime, store
    if store.get_bot(bot_id) is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    if not req.task.strip():
        raise HTTPException(400, {"code": "bad_task",
                                  "message": "task text required"})
    result = await runtime.request_run(bot_id, req.task.strip())
    return {"ok": True, **result}


@app.post("/api/bots/{bot_id}/stop")
async def bots_stop(bot_id: str) -> dict[str, Any]:
    from bots import runtime, store
    if store.get_bot(bot_id) is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    stopped = runtime.stop_run(bot_id)
    if stopped:
        return {"ok": True}
    raise HTTPException(409, {"code": "not_running",
                              "message": "bot has no active run"})


@app.post("/api/bots/{bot_id}/skill")
async def bots_save_skill(bot_id: str, req: SkillReq) -> dict[str, Any]:
    """TASK→SKILL: capture the last run's task + outcome as a method."""
    from bots import store
    bot = store.get_bot(bot_id)
    if bot is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    last_user = next((e["text"] for e in reversed(bot.get("transcript", []))
                      if e.get("role") == "user"), "")
    last_bot = next((e["text"] for e in reversed(bot.get("transcript", []))
                     if e.get("role") == "bot"), "")
    method = (f"# {req.name.strip().lstrip('/')}\n\n"
              f"Job: {bot['description']}\n\n"
              f"## Task\n{last_user}\n\n"
              f"## Method / outcome\n{last_bot or '(no recorded output yet)'}\n")
    skill = store.add_skill(bot_id, req.name, method)
    store.append_event(bot_id, "system",
                       f"Method saved as skill {skill} (run it on a schedule "
                       "to complete TASK → SKILL → ROUTINE).")
    return {"ok": True, "skill": skill}


@app.post("/api/bots/{bot_id}/schedule")
async def bots_schedule(bot_id: str, req: ScheduleReq) -> dict[str, Any]:
    from bots import store
    bot = store.get_bot(bot_id)
    if bot is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    sched: dict[str, Any] = {"kind": req.kind}
    if req.kind == "daily":
        if not req.time:
            raise HTTPException(400, {"code": "bad_schedule",
                                      "message": "daily needs a time (HH:MM)"})
        sched["time"] = req.time
        sched["tz"] = req.tz or "UTC"
    elif req.kind == "interval":
        if not req.expr:
            raise HTTPException(400, {"code": "bad_schedule",
                                      "message": "interval needs minutes"})
        sched["expr"] = req.expr
    elif req.kind == "cron":
        if not req.expr:
            raise HTTPException(400, {"code": "bad_schedule",
                                      "message": "cron needs an expression"})
        from scheduling.cron import CronSchedule
        try:
            CronSchedule(req.expr)
        except Exception as exc:
            raise HTTPException(400, {"code": "bad_cron",
                                      "message": f"invalid cron: {exc}"})
        sched["expr"] = req.expr
    bot = store.update_bot(bot_id, schedule=sched)
    assert bot is not None
    return {"bot": bot}


@app.post("/api/bots/{bot_id}/schedule/clear")
async def bots_schedule_clear(bot_id: str) -> dict[str, Any]:
    from bots import store
    bot = store.get_bot(bot_id)
    if bot is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    bot = store.update_bot(bot_id, schedule={"kind": "none"})
    assert bot is not None
    return {"bot": bot}


@app.get("/api/bots/{bot_id}/approvals")
async def bots_approvals(bot_id: str) -> dict[str, Any]:
    from bots import store
    if store.get_bot(bot_id) is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    return {"approvals": store.list_approvals(bot_id)}


@app.post("/api/bots/{bot_id}/approvals/{approval_id}")
async def bots_approve(bot_id: str, approval_id: str,
                       req: ApprovalDecisionReq) -> dict[str, Any]:
    from bots import store
    if store.get_bot(bot_id) is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    res = store.resolve_approval(bot_id, approval_id, req.decision)
    if res is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    store.append_event(bot_id, "system",
                       f"Approval {req.decision.replace('_', ' ')}.")
    return {"ok": True}


@app.get("/api/bots/{bot_id}/screen")
async def bots_screen(bot_id: str) -> Response:
    """The bot's screen panel: a live, honest view of the bot's machine —
    status, latest transcript and the workspace file tree. Rendered for the
    iframe (GET + ?token= auth is enforced by the bridge middleware)."""
    import html as _html
    from bots import store
    bot = store.get_bot(bot_id)
    if bot is None:
        raise HTTPException(404, {"code": "bot_not_found",
                                  "message": f"bot {bot_id!r} unknown"})
    events = bot.get("transcript", [])[-14:]
    rows = "".join(
        f"<div class='ev {e.get('role')}'><span class='role'>"
        f"{_html.escape(str(e.get('role')))}</span>"
        f"<span class='txt'>{_html.escape(str(e.get('text'))[:300])}</span></div>"
        for e in events)
    ws_files = 0
    try:
        root = Path(bot.get("workspace") or "/nonexistent")
        ws_files = sum(1 for p in root.rglob("*") if p.is_file())
    except Exception:
        ws_files = 0
    shared = store.shared_files()
    shared_rows = "".join(
        f"<div class='file'>{_html.escape(f['name'])}" 
        f"<span class='sz'>{f['size']}B</span></div>"
        for f in shared[:12]) or \
        "<div class='file'>(shared computer is empty — bots write here)</div>"
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>{_html.escape(bot['name'])} — screen</title>
<style>
 body{{background:#161616;color:#d4d4d4;font:13px/1.5 ui-monospace,monospace;margin:0;padding:14px}}
 h1{{font-size:15px;color:#d4d4d4;margin:0 0 4px}}
 .sub{{color:#999999;margin-bottom:10px}}
 .ev{{display:flex;gap:8px;padding:5px 8px;margin:3px 0;border:1px solid #414141;border-radius:8px;background:#2b2b2b}}
 .ev.user{{border-color:#4b4b4b;background:#363636}}
 .role{{color:#999999;min-width:52px;text-transform:uppercase;font-size:10px;padding-top:2px}}
 .txt{{flex:1;word-break:break-word}}
 .badge{{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid #414141;margin-right:6px;color:#d4d4d4}}
 h2{{font-size:11px;color:#999999;text-transform:uppercase;letter-spacing:.12em;margin:14px 0 4px}}
 .file{{display:flex;justify-content:space-between;gap:8px;padding:3px 8px;margin:2px 0;border-radius:6px;background:#2b2b2b}}
 .sz{{color:#999999}}
</style></head><body>
<h1>{_html.escape(bot['name'])} <span class="badge">{bot['status']}</span>
<span class="badge">workspace: {ws_files} files</span></h1>
<div class="sub">{_html.escape(bot['description'][:200])}</div>
{rows}
<h2>Shared computer</h2>
{shared_rows}
</body></html>"""
    return Response(content=doc, media_type="text/html")


# ---- Bots shared computer + Studio MCP driving + SAM 3.1 --------------------

@app.get("/api/bots/shared/files")
async def bots_shared_files() -> dict[str, Any]:
    """The bots' shared computer: one directory every bot reads/writes —
    the files substrate bot collaboration flows through."""
    from bots import store
    return {"path": str(store.shared_dir()), "files": store.shared_files()}


class AveActReq(BaseModel):
    instruction: str


class SegmentReq(BaseModel):
    prompt: str
    image_url: str


@app.get("/api/studio/ave/tools")
async def studio_ave_tools() -> dict[str, Any]:
    """Live tool catalog from AVE's MCP server (JSON-RPC tools/list)."""
    from universe_desktop_bridge import studio_mcp
    try:
        tools = await studio_mcp.list_tools()
        return {"ok": True, "reachable": True, "tools": tools}
    except studio_mcp.AveMcpError as exc:
        return {"ok": False, "reachable": exc.reachable, "tools": [],
                "message": exc.message}


@app.post("/api/studio/ave/act")
async def studio_ave_act(req: AveActReq) -> dict[str, Any]:
    """Universe drives AVE: a natural-language instruction → the configured
    LLM picks ONE tool from the LIVE catalog → a real JSON-RPC tools/call.
    Honest errors when AVE is not running."""
    from universe_desktop_bridge import studio_mcp
    try:
        return await studio_mcp.drive(req.instruction)
    except studio_mcp.AveMcpError as exc:
        raise HTTPException(503 if not exc.reachable else 502,
                            {"code": "ave_mcp", "message": exc.message})


@app.post("/api/meta/segment")
async def meta_segment(req: SegmentReq) -> dict[str, Any]:
    """SAM 3.1 media segmentation — POST /v1/responses (model "sam-3.1"):
    a noun-phrase prompt + one input_image; returns the special-token text
    (one box + one mask per match)."""
    from providers import meta_media
    try:
        key = await meta_media.resolve_key()
        output = await meta_media.segment(key, req.prompt, req.image_url)
        return {"output": output}
    except meta_media.MetaAPIError as exc:
        raise HTTPException(exc.status if exc.status >= 400 else 502,
                            {"code": "meta_error", "message": exc.message})


# ---- Meta media (real Meta Model API endpoints) -----------------------------

@app.post("/api/meta/test")
async def meta_test(req: MetaTestReq) -> dict[str, Any]:
    """Real key test: GET https://api.meta.ai/v1/models with the key."""
    from providers import meta_media
    try:
        key = await meta_media.resolve_key(req.key)
        models = await meta_media.list_models(key)
        return {"ok": True, "models": models}
    except meta_media.MetaAPIError as exc:
        raise HTTPException(exc.status if exc.status >= 400 else 502,
                            {"code": "meta_error", "message": exc.message})


@app.get("/api/meta/models")
async def meta_models() -> dict[str, Any]:
    from providers import meta_media
    try:
        key = await meta_media.resolve_key()
        return {"models": await meta_media.list_models(key)}
    except meta_media.MetaAPIError as exc:
        raise HTTPException(exc.status if exc.status >= 400 else 502,
                            {"code": "meta_error", "message": exc.message})


@app.post("/api/meta/image")
async def meta_image(req: ImageReq) -> dict[str, Any]:
    """Muse Image — POST /v1/images/generations (model muse-image-1.0)."""
    from providers import meta_media
    try:
        key = await meta_media.resolve_key()
        return await meta_media.generate_image(key, req.prompt, req.size)
    except meta_media.MetaAPIError as exc:
        raise HTTPException(exc.status if exc.status >= 400 else 502,
                            {"code": "meta_error", "message": exc.message})


@app.post("/api/meta/transcribe")
async def meta_transcribe(audio: "UploadFile" = None) -> dict[str, Any]:  # type: ignore[assignment]
    """Muse Voice Transcribe — POST /asr/transcribe (one-shot). Non-WAV
    uploads are converted to 16k mono WAV with the local ffmpeg first."""
    import tempfile
    from providers import meta_media
    data = await audio.read()  # type: ignore[union-attr]
    if not data:
        raise HTTPException(400, {"code": "bad_audio",
                                  "message": "empty audio upload"})
    name = audio.filename or "audio.wav"  # type: ignore[union-attr]
    encoding = "WAV"
    if not name.lower().endswith(".wav"):
        # browser MediaRecorder produces webm/opus — convert to WAV
        try:
            with tempfile.NamedTemporaryFile(suffix=Path(name).suffix or ".webm",
                                             delete=False) as src:
                src.write(data)
                src_path = src.name
            dst_path = src_path + ".wav"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-v", "error", "-i", src_path,
                "-ac", "1", "-ar", "16000", dst_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(err.decode()[:200] or "ffmpeg failed")
            data = Path(dst_path).read_bytes()
            Path(src_path).unlink(missing_ok=True)
            Path(dst_path).unlink(missing_ok=True)
        except FileNotFoundError:
            raise HTTPException(500, {"code": "ffmpeg_missing",
                                      "message": "ffmpeg is required to "
                                                 "convert microphone audio"})
        except Exception as exc:
            raise HTTPException(500, {"code": "audio_convert_failed",
                                      "message": f"audio conversion failed: {exc}"})
    try:
        key = await meta_media.resolve_key()
        text = await meta_media.transcribe(key, data, filename="audio.wav",
                                           encoding=encoding)
        return {"text": text}
    except meta_media.MetaAPIError as exc:
        raise HTTPException(exc.status if exc.status >= 400 else 502,
                            {"code": "meta_error", "message": exc.message})


# ---- Studio services (AVE · open-design · Blender MCP) ----------------------

@app.get("/api/studio/services")
async def studio_services() -> dict[str, Any]:
    from universe_desktop_bridge import studio
    return {"services": await studio.list_services()}


@app.post("/api/studio/services/{sid}/start")
async def studio_start(sid: str) -> dict[str, Any]:
    from universe_desktop_bridge import studio
    if sid not in studio.SERVICES:
        raise HTTPException(404, {"code": "service_unknown",
                                  "message": f"studio service {sid!r} unknown"})
    return await studio.start_service(sid)


@app.post("/api/studio/services/{sid}/stop")
async def studio_stop(sid: str) -> dict[str, Any]:
    from universe_desktop_bridge import studio
    if sid not in studio.SERVICES:
        raise HTTPException(404, {"code": "service_unknown",
                                  "message": f"studio service {sid!r} unknown"})
    return await studio.stop_service(sid)


# ---- Nebula (Memory — Obsidian-like vault) ----------------------------------

@app.get("/api/nebula/notes")
async def nebula_notes() -> dict[str, Any]:
    from nebula import vault
    return {"notes": vault.list_notes()}


@app.post("/api/nebula/notes")
async def nebula_create(req: NoteCreate) -> dict[str, Any]:
    from nebula import vault
    if not req.title.strip():
        raise HTTPException(400, {"code": "bad_note",
                                  "message": "title required"})
    note = vault.create_note(req.title, req.content)
    note.pop("content", None)
    return {"note": note}


@app.get("/api/nebula/notes/{note_id}")
async def nebula_get(note_id: str) -> dict[str, Any]:
    from nebula import vault
    note = vault.get_note(note_id)
    if note is None:
        raise HTTPException(404, {"code": "note_not_found",
                                  "message": f"note {note_id!r} unknown"})
    return {"note": note}


@app.put("/api/nebula/notes/{note_id}")
async def nebula_update(note_id: str, req: NoteUpdate) -> dict[str, Any]:
    from nebula import vault
    note = vault.update_note(note_id, req.content)
    if note is None:
        raise HTTPException(404, {"code": "note_not_found",
                                  "message": f"note {note_id!r} unknown"})
    note.pop("content", None)
    return {"note": note}


@app.delete("/api/nebula/notes/{note_id}")
async def nebula_delete(note_id: str) -> dict[str, Any]:
    from nebula import vault
    if not vault.delete_note(note_id):
        raise HTTPException(404, {"code": "note_not_found",
                                  "message": f"note {note_id!r} unknown"})
    return {"ok": True}


@app.get("/api/nebula/graph")
async def nebula_graph() -> dict[str, Any]:
    from nebula import vault
    return vault.graph()


@app.post("/api/nebula/distill")
async def nebula_distill(req: DistillReq) -> dict[str, Any]:
    """Dust → Stars: condense a session transcript into a linked note."""
    from nebula import vault
    try:
        return await vault.distill(req.session_id)
    except ValueError as exc:
        raise HTTPException(400, {"code": "distill_failed",
                                  "message": str(exc)})
