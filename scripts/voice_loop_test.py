"""Full voice-loop integration check against a live bridge.

Streams a real edge-tts synthesized utterance ("wake up L. write a poem
about mars", 16 kHz PCM16) frame-by-frame over WS /api/voice/stream and
verifies the complete chain:

  mic frames -> VAD segment -> wake-phrase detection -> local STT
  -> turn_started(via=voice) -> (consent approved like the UI would)
  -> turn_completed -> spoken reply (voice_tts_start + binary mp3)

Usage:  python scripts/voice_loop_test.py [port]

The spoken goal runs real agents, so the bridge needs a real provider
configured (Settings → Model provider, or UNIVERSE_TEST_* env for a test
bridge). There is no built-in model anymore.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import wave
from pathlib import Path

import httpx
import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8742
WAV = Path(os.environ.get("TMP", os.environ.get("TEMP", "."))) / "universe-wake-test.wav"
MP3_OUT = Path(os.environ.get("TMP", os.environ.get("TEMP", "."))) / "universe-reply.mp3"
FRAME_MS = 30
SAMPLE_RATE = 16000
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000  # 960


async def main() -> int:
    import httpx as _h
    try:
        r = await _h.AsyncClient(timeout=5).get(f"http://127.0.0.1:{PORT}/api/providers")
        if not r.json().get("has_provider"):
            print("FAIL: no LLM provider configured on the bridge — the spoken")
            print("goal would be rejected. Connect one (Settings → Model")
            print("provider) or start the bridge with UNIVERSE_TEST_* env set.")
            return 1
    except Exception as exc:
        print(f"FAIL: bridge not reachable on port {PORT}: {exc}")
        return 1
    if not WAV.exists():
        print(f"FAIL: test WAV not found: {WAV}")
        print("regen:  edge-tts --voice en-US-GuyNeural --text "
              "'wake up L. write a poem about mars' --write-media <wav>")
        return 1
    with wave.open(str(WAV)) as w:
        pcm = w.readframes(w.getnframes())
        assert w.getframerate() == SAMPLE_RATE and w.getsampwidth() == 2, \
            f"unexpected wav fmt {w.getframerate()}Hz/{w.getsampwidth()*8}bit"

    events_uri = f"ws://127.0.0.1:{PORT}/api/events"
    voice_uri = f"ws://127.0.0.1:{PORT}/api/voice/stream"
    async with websockets.connect(events_uri, max_size=None) as ev_ws, \
               websockets.connect(voice_uri, max_size=None) as v_ws, \
               httpx.AsyncClient(timeout=30) as http:
        hello = json.loads(await asyncio.wait_for(ev_ws.recv(), 10))
        assert hello.get("type") == "bridge_hello", hello
        vhello = json.loads(await asyncio.wait_for(v_ws.recv(), 10))
        assert vhello.get("type") == "voice_hello", vhello
        print(f"voice_hello: wake_word={vhello.get('wake_word')!r}")

        loop = asyncio.get_event_loop()
        events: list[dict] = []
        printed = 0
        approved: set[str] = set()

        async def pump_events() -> None:
            try:
                async for m in ev_ws:
                    ev = json.loads(m)
                    if ev.get("type") != "heartbeat":
                        events.append(ev)
            except Exception:
                pass

        # stream the utterance in real time (VAD needs natural pacing)
        frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
        print(f"streaming {len(frames)} frames ({len(pcm)/2/SAMPLE_RATE:.2f}s)…")

        voice_states: list[str] = []
        transcript: str | None = None
        tts_bytes: bytes | None = None
        tts_started = False

        async def stream() -> None:
            for f in frames:
                if len(f) < FRAME_BYTES:
                    f = f + b"\x00" * (FRAME_BYTES - len(f))
                await v_ws.send(f)
                await asyncio.sleep(FRAME_MS / 1000)

        pump = asyncio.create_task(pump_events())
        await stream()  # real-time pacing; the drain below reads what arrived
        try:
            while transcript is None:
                # whisper base int8 cold-load can take a while on first run
                m = await asyncio.wait_for(v_ws.recv(), 150)
                if isinstance(m, bytes):
                    continue
                d = json.loads(m)
                t = d.get("type")
                if t == "voice_level":
                    continue
                if t == "voice_status":
                    voice_states.append(str(d.get("state")))
                    print(f"  [voice] state={d.get('state')} {str(d.get('error',''))[:80]}")
                elif t == "voice_transcript":
                    transcript = str(d.get("text"))
                    print(f"  [voice] transcript={transcript!r}")
        except (asyncio.TimeoutError, TimeoutError):
            pass

        # drive the goal to completion: approve consent like the UI modal,
        # then wait for turn_completed + the spoken reply on the voice socket
        got_goal = None
        goal_done = False
        done_since = float("inf")
        deadline = loop.time() + 300  # whisper load + full agent run
        while loop.time() < deadline:
            for ev in events[printed:]:
                et = ev.get("type")
                print(f"  [event] {et} agent={ev.get('agent','-')} "
                      f"text={str(ev.get('text',''))[:50]}")
                if et == "turn_started":
                    got_goal = ev
                elif et == "consent_required" and ev.get("pending_id") not in approved:
                    r = await http.post(
                        f"http://127.0.0.1:{PORT}/api/consent/{ev['pending_id']}",
                        json={"approve": True})
                    approved.add(ev["pending_id"])
                    print(f"  [test] consent approve -> {r.status_code}")
            printed = len(events)
            was_done = goal_done
            goal_done = any(e.get("type") == "turn_completed" for e in events)
            if goal_done and not was_done:
                done_since = loop.time()
            if goal_done and tts_started and tts_bytes:
                break
            # single reader for the voice socket from here on
            try:
                m = await asyncio.wait_for(v_ws.recv(), timeout=1.0)
            except (asyncio.TimeoutError, TimeoutError):
                # goal done but TTS never came -> give it 20s then stop waiting
                if goal_done and loop.time() > done_since + 20:
                    break
                continue
            if isinstance(m, bytes):
                tts_bytes = m
                if tts_started:
                    break
                continue
            d = json.loads(m)
            t = d.get("type")
            if t == "voice_level":
                continue
            if t == "voice_status":
                voice_states.append(str(d.get("state")))
                print(f"  [voice] state={d.get('state')} {str(d.get('error',''))[:80]}")
            elif t == "voice_transcript":
                pass  # already captured
            elif t == "voice_tts_start":
                tts_started = True
                print(f"  [voice] tts_start {d.get('bytes')} bytes mp3")

        pump.cancel()

    print("\n=== VERDICT ===")
    woke = "awake" in voice_states
    print(f"states={voice_states}")
    print(f"awake={woke} transcript={transcript!r}")
    print(f"turn_started={bool(got_goal)} via={got_goal.get('via') if got_goal else None}")
    print(f"turn_completed={goal_done}")
    print(f"tts_started={tts_started} mp3_bytes={len(tts_bytes) if tts_bytes else 0}")
    if tts_bytes:
        MP3_OUT.write_bytes(tts_bytes)
        print(f"reply saved: {MP3_OUT}")
        # 11-bit MPEG syncword: 0xFF then top-3-bits 111 (edge-tts sends MPEG-2)
        ok_mp3 = tts_bytes[0] == 0xFF and (tts_bytes[1] & 0xE0) == 0xE0
        print(f"mp3 syncword plausible: {ok_mp3}")
    else:
        ok_mp3 = False
    ok = woke and transcript and got_goal and got_goal.get("via") == "voice" \
        and goal_done and tts_started and tts_bytes and ok_mp3
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
