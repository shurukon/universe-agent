"""universe_desktop_bridge/voice.py — the voice channel (plan §8).

Two halves, both over one WebSocket (/api/voice/stream):

  browser →  binary frames (16kHz mono PCM16 chunks) from the mic
  browser ←  JSON control + binary TTS audio (mp3) + level frames

Pipeline (all local, pluggable):
  mic → webrtcvad (silence gating) → STT (faster-whisper local, or OpenAI
  Whisper with the user's key) → wake check = STT fuzzy match on the custom
  phrase ("wake up L") → goal/chat start → ... → reply text → TTS
  (edge-tts default; OpenAI or ElevenLabs with the user's key) → binary mp3
  back to the mic badge.

Every stage degrades loudly-but-gracefully: a missing model or key emits a
voice_status event with the exact reason instead of silently doing nothing.
"""
from __future__ import annotations

import asyncio
import io
import struct
import time
import wave
from typing import Any

from observability.events import emit
from storage.local import get_storage

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * 2 * FRAME_MS / 1000)  # PCM16 mono
MIN_WAKE_S = 0.5        # idle segments shorter than this can't hold the phrase


def _settings() -> dict[str, Any]:
    """Single source of truth: the `desktop` section written by
    Settings / onboarding. The legacy `voice` section is still honored if
    the desktop one never stored a value (old installs)."""
    from config import get_config
    cfg = get_config()
    desktop = cfg.get("desktop", {}) or {}
    legacy = cfg.get("voice", {}) or {}
    merged: dict[str, Any] = {
        "wake_word": "wake up L",
        "stt_provider": "local",
        "stt_model": "base",
        "stt_api_key": "",
        "tts_provider": "edge-tts",
        "tts_voice": "en-US-AriaNeural",
        "tts_api_key": "",
        "voice_enabled": False,
    }
    for key in ("wake_word", "stt_provider", "stt_model", "stt_api_key",
                "stt_cloud_model", "stt_language", "tts_provider", "tts_voice",
                "tts_model", "tts_api_key"):
        # desktop wins when it has a non-empty value; otherwise fall back
        # to the legacy section so pre-unification installs keep working
        if desktop.get(key):
            merged[key] = desktop[key]
        elif key in legacy:
            merged[key] = legacy[key]
    # voice keys are AES blobs at rest now (see app.write_settings) — decrypt
    # for in-process use; legacy plaintext values pass through unchanged
    for k in ("stt_api_key", "tts_api_key"):
        if merged.get(k):
            try:
                from security.secrets_fallback import decrypt_secret
                merged[k] = decrypt_secret(str(merged[k]))
            except Exception:
                pass  # legacy plaintext or dead key — used verbatim/as-is
    if "voice_enabled" in desktop:
        merged["voice_enabled"] = bool(desktop["voice_enabled"])
    return merged


class VoiceEngine:
    """Owns the VAD, wake-word detector, and the pluggable STT/TTS clients."""

    def __init__(self) -> None:
        self._vad: Any | None = None
        self._oww: Any | None = None
        self._whisper: Any | None = None
        self._whisper_lock: Any = None  # asyncio.Lock, lazy (no loop at import)
        self._stt_error = ""
        self._tts_error = ""

    # -- lazy, loud failures ------------------------------------------------

    def vad(self) -> Any:
        if self._vad is None:
            import webrtcvad
            self._vad = webrtcvad.Vad(2)  # aggressiveness 0-3
        return self._vad

    # -- STT ----------------------------------------------------------------

    async def transcribe(self, pcm16: bytes) -> str:
        cfg = _settings()
        provider = str(cfg.get("stt_provider", "local"))
        if provider == "openai":
            return await self._transcribe_openai(pcm16, cfg)
        if provider == "groq":
            return await self._transcribe_groq(pcm16, cfg)
        return await self._transcribe_local(pcm16, cfg)

    async def _transcribe_local(self, pcm16: bytes, cfg: dict[str, Any]) -> str:
        # STT language follows Settings (Arabic installs transcribe Arabic):
        # the old hardcoded language="en" made every non-English utterance
        # transcribe as near-gibberish with no user-visible cause.
        language = str(cfg.get("stt_language", "") or "").strip() or None
        if self._whisper_lock is None:
            self._whisper_lock = asyncio.Lock()
        async with self._whisper_lock:
            if self._whisper is None:
                from faster_whisper import WhisperModel
                model = str(cfg.get("stt_model", "base"))
                # model load is heavy disk/CPU work — NEVER on the event loop
                self._whisper = await asyncio.to_thread(
                    WhisperModel, model, device="cpu", compute_type="int8")
            whisper = self._whisper
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language"] = language
        segments, _info = await asyncio.to_thread(
            whisper.transcribe, _wav_file(pcm16), **kwargs)
        return " ".join(s.text for s in segments).strip()

    async def _transcribe_openai(self, pcm16: bytes, cfg: dict[str, Any]) -> str:
        key = str(cfg.get("stt_api_key", ""))
        if not key:
            raise RuntimeError("stt_api_key not set (Settings)")
        import httpx
        wav = _wav_bytes(pcm16)
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", wav, "audio/wav")},
                data={"model": str(cfg.get("stt_cloud_model", "whisper-1"))},
            )
            res.raise_for_status()
            return str(res.json().get("text", "")).strip()

    async def _transcribe_groq(self, pcm16: bytes, cfg: dict[str, Any]) -> str:
        """Groq serves OpenAI-compatible /audio/transcriptions with its own
        whisper-large-v3 / distil models — fast and genuinely free tier."""
        key = str(cfg.get("stt_api_key", ""))
        if not key:
            raise RuntimeError("stt_api_key not set (Settings)")
        import httpx
        wav = _wav_bytes(pcm16)
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", wav, "audio/wav")},
                data={"model": str(cfg.get("stt_cloud_model",
                                            "whisper-large-v3"))},
            )
            res.raise_for_status()
            return str(res.json().get("text", "")).strip()

    # -- TTS ----------------------------------------------------------------

    async def synthesize(self, text: str) -> bytes:
        cfg = _settings()
        provider = str(cfg.get("tts_provider", "edge-tts"))
        if provider == "openai":
            return await self._tts_openai(text, cfg)
        if provider == "fish-audio":
            return await self._tts_fish_audio(text, cfg)
        if provider == "elevenlabs":
            return await self._tts_elevenlabs(text, cfg)
        return await self._tts_edge(text, cfg)

    async def _tts_edge(self, text: str, cfg: dict[str, Any]) -> bytes:
        import edge_tts
        voice = str(cfg.get("tts_voice", "en-US-AriaNeural"))
        communicate = edge_tts.Communicate(text, voice)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    async def _tts_openai(self, text: str, cfg: dict[str, Any]) -> bytes:
        key = str(cfg.get("tts_api_key", ""))
        if not key:
            raise RuntimeError("tts_api_key not set (Settings)")
        import httpx
        model = str(cfg.get("tts_model", "gpt-4o-mini-tts")) or "gpt-4o-mini-tts"
        voice = str(cfg.get("tts_voice", "alloy")) or "alloy"
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "voice": voice,
                      "input": text, "response_format": "mp3"},
            )
            res.raise_for_status()
            return res.content

    async def _tts_fish_audio(self, text: str, cfg: dict[str, Any]) -> bytes:
        """Fish Audio S2.1 — two routes the user can pick:
        - 'fish-audio': the direct API (api.fish.audio). Tries S2.1 Pro;
          with empty paid credit the account's free developer tier of the
          SAME model applies (s2.1-pro-free on request).
        - 'fish-openrouter': the same S2.1 Pro model served through
          OpenRouter, billed via OpenRouter credits.
        A 'model' setting (s2.1-pro / s2.1-pro-free / s1) overrides."""
        key = str(cfg.get("tts_api_key", ""))
        if not key:
            raise RuntimeError("tts_api_key not set (Settings)")
        model = str(cfg.get("tts_model", "s2.1-pro"))
        voice = str(cfg.get("tts_voice", ""))
        if voice.startswith("fish-openrouter:"):
            # OpenRouter route: voice carries "fish-openrouter:<or-model>"
            or_model = voice.split(":", 2)[1] if ":" in voice else "fish-audio/s2.1-pro"
            import httpx
            async with httpx.AsyncClient(timeout=90) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/audio/speech",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": or_model, "input": text,
                          "response_format": "mp3"},
                )
                res.raise_for_status()
                return res.content
        import httpx
        headers = {"Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        payload: dict[str, Any] = {"text": text, "format": "mp3"}
        if model:
            payload["model"] = model
        if voice and not voice.startswith("edge:"):
            payload["reference_id"] = voice
        # the direct API accepts a model hint via the query param used by
        # the playground; the engine picks the account's best S2.1 tier
        async with httpx.AsyncClient(timeout=90) as client:
            res = await client.post(
                "https://api.fish.audio/v1/tts", headers=headers,
                params={"model": model} if model else None, json=payload)
            res.raise_for_status()
            return res.content

    async def _tts_elevenlabs(self, text: str, cfg: dict[str, Any]) -> bytes:
        """ElevenLabs — voice id from tts_voice (default Rachel, the most
        neutral multilingual), model configurable in Settings."""
        key = str(cfg.get("tts_api_key", ""))
        if not key:
            raise RuntimeError("tts_api_key not set (Settings)")
        voice = str(cfg.get("tts_voice", "21m00Tcm4TlvDq8ikWAM"))
        model = str(cfg.get("tts_model", "eleven_multilingual_v2"))
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={"xi-api-key": key},
                json={"text": text, "model_id": model,
                      "output_format": "mp3_44100_128"},
            )
            res.raise_for_status()
            return res.content

    def status(self) -> dict[str, Any]:
        """Provider preflight for the voice hello: can STT/TTS actually
        run with the current Settings? Cloud providers without a key fail
        every segment — say so up front instead of after the first mic
        frame. Local STT is always 'ready' (model lazy-loads on first use;
        a load failure then surfaces as stt_error on that segment)."""
        cfg = _settings()
        stt_err = self._stt_error
        if not stt_err and str(cfg.get("stt_provider", "local")) != "local"                 and not str(cfg.get("stt_api_key", "")):
            stt_err = "stt_api_key not set (Settings → Voice)"
        tts_err = self._tts_error
        if not tts_err and str(cfg.get("tts_provider", "edge-tts"))                 not in ("local", "edge-tts")                 and not str(cfg.get("tts_api_key", "")):
            tts_err = "tts_api_key not set (Settings → Voice)"
        return {"stt_error": stt_err, "tts_error": tts_err}


_engine: VoiceEngine | None = None


def get_voice_engine() -> VoiceEngine:
    global _engine
    if _engine is None:
        _engine = VoiceEngine()
    return _engine


def reset_voice_for_tests() -> VoiceEngine:
    global _engine
    _engine = VoiceEngine()
    return _engine


def _wav_bytes(pcm16: bytes) -> bytes:
    """Wrap raw PCM16 mono 16k into a WAV container for whisper/httpx."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm16)
    return buf.getvalue()


def _wav_file(pcm16: bytes) -> io.BytesIO:
    """The same WAV, as a file object — faster-whisper requires read()."""
    return io.BytesIO(_wav_bytes(pcm16))


def _level(pcm: bytes) -> float:
    """RMS 0..1 of a PCM16 frame — drives the core's breathing glow."""
    if not pcm:
        return 0.0
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    ints = struct.unpack(f"<{min(n, 400)}h", pcm[: min(n, 400) * 2])
    peak = max(abs(v) for v in ints) or 1
    rms = (sum(v * v for v in ints) / len(ints)) ** 0.5
    return min(1.0, rms / 32768.0 * (1 + peak / 65536))


def _spoken_summary(summary: Any) -> str:
    """Plain-English version of the goal summary — for TTS. The UI keeps
    its visual (✓/✦) rendering; speech needs a linear, mark-free text."""
    if not isinstance(summary, dict):
        return "Done."
    steps = [s for s in (summary.get("steps") or []) if isinstance(s, dict)]
    done = [f"{s.get('agent') or 'agent'}: {s.get('what') or 'done'}"
            for s in steps if s.get("success", True)]
    parts = ["Goal completed." if summary.get("success") else "Goal ended."]
    if done:
        parts.append(" ".join(done)[:400])
    arts = summary.get("artifacts") or []
    if len(arts) == 1:
        parts.append("One artifact was created.")
    elif len(arts) > 1:
        parts.append(f"{len(arts)} artifacts were created.")
    return " ".join(parts)


async def voice_session(ws: Any, orchestrator: Any) -> None:
    """One browser mic connection. Streams events + TTS back on the socket.

    Wake-word flow: while idle, VAD-gated voiced segments (0.5–3s) go to a
    lightweight whisper pass; a fuzzy match on the custom phrase ("wake up
    L") wakes the channel, the rest of the utterance (if spoken in one
    breath) becomes the goal; otherwise we keep listening for the command.
    """
    # accept FIRST, then refuse: a reject before accept surfaces as a
    # failed handshake in the client (indistinguishable from a dead
    # bridge), while an accepted socket carrying a clean error state is
    # an honest, actionable message.
    await ws.accept()
    cfg = _settings()
    if not cfg.get("voice_enabled", False):
        # the user turned voice off in Settings — refuse politely instead
        # of silently eating mic frames
        await ws.send_json({
            "type": "voice_status",
            "state": "error",
            "error": "voice is disabled in Settings → Voice",
        })
        await ws.close()
        return
    from universe_desktop_bridge import wake as wake_mod

    engine = get_voice_engine()
    # provider preflight: the hello carries whether STT/TTS can actually
    # run, so the mic badge never shows an armed state for a backend that
    # will fail every segment
    preflight = engine.status()
    await ws.send_json({"type": "voice_hello",
                        "wake_word": cfg.get("wake_word", "wake up L"),
                        "sample_rate": SAMPLE_RATE, "frame_ms": FRAME_MS,
                        "stt_ready": not preflight.get("stt_error"),
                        "tts_ready": not preflight.get("tts_error"),
                        "stt_error": preflight.get("stt_error") or None,
                        "tts_error": preflight.get("tts_error") or None})

    vad = engine.vad()
    wake_word = str(cfg.get("wake_word", "wake up L")).strip() or "wake up L"

    # one shared segment accumulator: collects a voiced run, then flushes
    segment: bytearray = bytearray()
    carry: bytes = b""  # trailing partial frame kept for the next message
    voiced_ms = 0.0
    after_voice_ms = 0.0
    collecting = False
    listening = False  # awake: next voiced segment IS the command
    awake_since = 0.0  # monotonic: an awake channel that never hears a
    # command must go back to sleep (60s), not listen forever
    AWAKE_TIMEOUT_S = 60.0
    MAX_SEGMENT_S = 30.0  # an awake command longer than this is flushed
    # rather than buffered without bound

    def frame_voiced(frame: bytes, level: float) -> bool:
        if level < 0.01:
            return False
        try:
            return bool(vad.is_speech(frame, SAMPLE_RATE))
        except Exception:
            return True

    async def flush_segment() -> None:
        """A voiced segment ended. Wake check when idle; command when awake."""
        nonlocal segment, listening, voiced_ms, after_voice_ms, collecting
        audio = bytes(segment)
        segment = bytearray()
        collecting = False
        voiced_ms = 0.0
        after_voice_ms = 0.0
        dur = len(audio) / (SAMPLE_RATE * 2)
        min_dur = MIN_WAKE_S if not listening else 0.3
        if dur < min_dur:
            return  # too short to mean anything
        await ws.send_json({"type": "voice_status", "state": "transcribing"})
        try:
            text = await engine.transcribe(audio)
        except Exception as exc:
            await ws.send_json({"type": "voice_status", "state": "stt_error",
                                "error": str(exc)[:200]})
            return
        if not text:
            return
        if not listening:
            if wake_mod.is_wake(text, wake_word):
                await ws.send_json({"type": "voice_status", "state": "awake"})
                listening = True
                asyncio.get_running_loop().time()
                # phrase + goal in one breath? keep what follows the phrase
                rest = wake_mod.strip_wake(text, wake_word)
                if rest:
                    await _start_goal(rest)
                    listening = False
            # not the wake phrase: ignore (ambient speech), keep idling
            return
        # awake: this segment is the command
        listening = False
        await _start_goal(text)

    # the voice channel threads into ONE task per session: the first spoken
    # goal creates it, later spoken goals continue it (same desktop model
    # as typed messages — never one sidebar row per utterance)
    voice_task_id: str | None = None

    async def _start_goal(text: str) -> None:
        nonlocal voice_task_id
        text = text.strip()
        if not text:
            await ws.send_json({"type": "voice_status", "state": "empty"})
            return
        await ws.send_json({"type": "voice_transcript", "text": text})
        try:
            from core.agent.base_agent import new_id
            from universe_desktop_bridge.state import get_state
            from observability.events import get_event_bus
            st = get_storage()
            # continuation: reuse the session's task if it still exists;
            # otherwise create the container (titled "New task", never the
            # spoken text)
            task_id = voice_task_id
            if task_id:
                row = st.query_one(
                    "SELECT id FROM goals WHERE id=? AND deleted=0;",
                    (task_id,))
                if row is None:
                    task_id = None
            if not task_id:
                task_id = new_id("task-")
                with st.transaction() as conn:
                    conn.execute(
                        "INSERT INTO goals(id, text, status, kind, title, "
                        "project_id, created_at, owner_session_id) "
                        "VALUES(?,?,?,?,?,?,?,?);",
                        (task_id, "", "active", "task", "New task", "",
                         time.time(), "desktop-voice"))
            voice_task_id = task_id
            turn_id = new_id("goal-")
            gt = get_state().create(turn_id, text, task_id=task_id)
            gt.status = "running"
            bus = get_event_bus()

            async def _run() -> None:
                try:
                    with bus.bind_goal(turn_id):
                        emit("turn_started", turn_id=turn_id, text=text[:2000],
                             via="voice", task_id=task_id)
                        summary = await orchestrator.run_turn(
                            text, turn_id=turn_id, owner_session_id="desktop-voice",
                            session_id=task_id)
                        gt.result = summary
                        # same contract as the HTTP path: success=False is
                        # failed even without an exception; and a TTS
                        # failure must never flip a completed goal back to
                        # failed (it is reported as its own event)
                        if isinstance(summary, dict) and not summary.get("success", True):
                            gt.set_status("failed")
                            gt.error = str(summary.get("error", ""))[:500]
                        else:
                            gt.set_status("completed")
                            gt.error = ""
                        # a spoken goal deserves a spoken answer — a
                        # conversational reply is spoken as the reply itself
                        reply = str(summary.get("reply", "")) if isinstance(summary, dict) else ""
                        await speak_reply(reply.strip() if reply.strip()
                                          else _spoken_summary(summary))
                except asyncio.CancelledError:
                    gt.set_status("cancelled")
                    raise
                except Exception as exc:
                    gt.set_status("failed")
                    if not gt.error:
                        gt.error = str(exc)[:500]
            gt.task = asyncio.create_task(_run())
        except Exception as exc:
            await ws.send_json({"type": "voice_status", "state": "turn_error",
                                "error": str(exc)[:200]})

    async def speak_reply(text: str) -> None:
        try:
            mp3 = await engine.synthesize(text)
            await ws.send_json({"type": "voice_tts_start", "format": "mp3",
                                "bytes": len(mp3)})
            await ws.send_bytes(mp3)
        except Exception as exc:
            await ws.send_json({"type": "voice_status", "state": "tts_error",
                                "error": str(exc)[:200]})

    try:
        while True:
            # an awake channel with no command goes back to sleep — an
            # eternal listen is a mic left hot with no user-visible cause
            if listening and awake_since:
                now = asyncio.get_running_loop().time()
                if now - awake_since >= AWAKE_TIMEOUT_S:
                    listening = False
                    awake_since = 0.0
                    segment = bytearray()
                    collecting = False
                    voiced_ms = 0.0
                    after_voice_ms = 0.0
                    await ws.send_json({"type": "voice_status",
                                        "state": "idle",
                                        "reason": "awake_timeout"})
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is None:
                continue
            # carry: a message that is not a whole number of frames used to
            # silently drop its trailing partial bytes — over a long session
            # the drops add up to audible gaps. Prepend last time's leftover.
            data = carry + bytes(data)
            carry = b""
            usable = (len(data) // FRAME_BYTES) * FRAME_BYTES
            carry = data[usable:]
            for off in range(0, usable, FRAME_BYTES):
                frame = data[off:off + FRAME_BYTES]
                level = _level(frame)
                await ws.send_json({"type": "voice_level", "level": round(level, 3),
                                    "listening": listening})
                voiced = frame_voiced(frame, level)
                if voiced:
                    segment.extend(frame)
                    voiced_ms += FRAME_MS / 1000.0
                    after_voice_ms = 0.0
                    collecting = True
                    # hard cap while idle (it's a wake check, not a command)
                    if not listening and voiced_ms > 3.0:
                        await flush_segment()
                    # ...and a (much larger) cap while awake: a command
                    # segment must be flushed, not buffered without bound
                    elif listening and voiced_ms > MAX_SEGMENT_S:
                        await flush_segment()
                        listening = False
                        awake_since = 0.0
                elif collecting:
                    after_voice_ms += FRAME_MS / 1000.0
                    if after_voice_ms >= 0.35:
                        await flush_segment()
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
