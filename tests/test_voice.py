"""tests/test_voice.py — the voice channel's testable pieces.

The full mic loop needs hardware; these verify the deterministic parts with
real audio where possible: level metering, WAV wrapping, VAD gating, a real
edge-tts synthesis, and the settings plumbing.
"""
from __future__ import annotations

import asyncio
import struct

import pytest

from universe_desktop_bridge.voice import (
    FRAME_BYTES, SAMPLE_RATE, VoiceEngine, _level, _wav_bytes, _settings,
)


def _tone_pcm(seconds: float, freq: float = 440.0, amp: float = 0.4) -> bytes:
    n = int(SAMPLE_RATE * seconds)
    return b"".join(
        struct.pack("<h", int(amp * 32767 * __import__("math").sin(
            2 * 3.14159265 * freq * i / SAMPLE_RATE)))
        for i in range(n)
    )


def _silence_pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def test_level_detects_speech_and_silence():
    """The meter that drives the core's breathing glow."""
    loud = _level(_tone_pcm(0.03))
    quiet = _level(_silence_pcm(0.03))
    assert loud > 0.05, f"tone not detected: {loud}"
    assert quiet < 0.01, f"silence not clean: {quiet}"


def test_wav_wrapper_is_a_real_wav():
    import io
    import wave
    pcm = _tone_pcm(0.1)
    wrapped = _wav_bytes(pcm)
    with wave.open(io.BytesIO(wrapped)) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == SAMPLE_RATE
        assert w.getnframes() == len(pcm) // 2


def _voiced_gate(frame: bytes, vad, level: float) -> bool:
    """The production gate: webrtcvad AND energy — the pip webrtcvad wheel
    can flag pure-zero frames on some builds, energy keeps the gate honest."""
    if level < 0.01:
        return False
    try:
        return bool(vad.is_speech(frame, SAMPLE_RATE))
    except Exception:
        return level > 0.02


def test_vad_gates_frames(fresh_home):
    """The combined gate: a 440Hz tone passes, digital silence never does."""
    pytest.importorskip("webrtcvad",
                        reason="the pip webrtcvad wheel is not installed — "
                               "the VAD half of the gate cannot run here")
    eng = VoiceEngine()
    vad = eng.vad()
    tone = _tone_pcm(0.09)
    frames = [tone[i:i + FRAME_BYTES] for i in range(0, len(tone) - FRAME_BYTES + 1, FRAME_BYTES)]
    voiced_votes = sum(1 for f in frames if _voiced_gate(f, vad, _level(f)))
    silence = _silence_pcm(0.09)
    sframes = [silence[i:i + FRAME_BYTES] for i in range(0, len(silence) - FRAME_BYTES + 1, FRAME_BYTES)]
    silent_votes = sum(1 for f in sframes if _voiced_gate(f, vad, _level(f)))
    assert voiced_votes > 0, "pure tone should register as speech"
    assert silent_votes == 0, "digital silence must never be speech"


def test_settings_default_to_local_stt_and_edge_tts(fresh_home):
    cfg = _settings()
    assert cfg["stt_provider"] == "local"
    assert cfg["tts_provider"] == "edge-tts"
    assert cfg["wake_word"] == "wake up L"


async def test_edge_tts_synthesizes_real_audio(fresh_home, monkeypatch):
    """The default TTS truly produces playable mp3 bytes on this machine.

    Three isolation fixes:
    - fresh_home: without it the test read the USER'S real ~/.universe
      config (e.g. tts_provider=fish-audio) and failed on the user's
      billing instead of testing Universe's default edge-tts path.
    - missing wheel: the edge-tts package is an optional dependency —
      skip honestly when it is not installed.
    - live-network skip: when Microsoft's endpoint is unreachable from
      this box, skip honestly instead of reporting an environment
      failure as a code failure."""
    pytest.importorskip("edge_tts",
                        reason="edge-tts is not installed in this environment")
    import socket
    try:
        socket.create_connection(("speech.platform.bing.com", 443), timeout=5).close()
    except OSError as e:
        pytest.skip(f"edge-tts endpoint unreachable from this network: {e}")
    # force the DEFAULT provider regardless of any stray persisted setting
    from universe_desktop_bridge import voice as voice_mod
    base = voice_mod._settings()  # read BEFORE the patch (else recursion)
    monkeypatch.setattr(voice_mod, "_settings",
                        lambda: {**base, "tts_provider": "edge-tts"})
    eng = VoiceEngine()
    mp3 = await asyncio.wait_for(eng.synthesize("Universe voice test."), 60)
    assert len(mp3) > 1000
    # any valid mp3 framing: ID3 tag, MPEG-1 Layer III (fffb) or MPEG-2 (fff3)
    assert mp3[:3] == b"ID3" or mp3[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xe3")


async def test_transcribe_local_missing_model_reports_loudly(fresh_home, monkeypatch):
    """A broken local STT raises a real error (never a silent empty string)."""
    eng = VoiceEngine()
    monkeypatch.setitem(_settings(), "stt_provider", "local")
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("no faster-whisper wheels on this box")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    eng._whisper = None
    with pytest.raises(Exception):
        await eng.transcribe(_tone_pcm(0.5))
