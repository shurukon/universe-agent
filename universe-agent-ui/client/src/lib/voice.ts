/**
 * lib/voice.ts — the browser half of the voice channel (§8).
 *
 * mic (16kHz PCM16 via a 16k AudioContext) → WS /api/voice/stream →
 * wake word → STT → goal; TTS mp3 plays back through the same socket.
 * voice_level frames from the bridge drive the universe core's breathing
 * (one source of truth — the same level gates the VAD server-side).
 */

import { bridgeConfig } from "./api";

export type VoiceState =
  "off" | "connecting" | "listening" | "awake" | "transcribing";

interface VoiceHandlers {
  onState: (s: VoiceState, detail?: string) => void;
  onTranscript: (text: string) => void;
  onTts?: (mp3: ArrayBuffer) => void;
}

export class VoiceClient {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private proc: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private handlers: VoiceHandlers;
  state: VoiceState = "off";
  /** the live mic level 0..1 — the universe core breathes with this */
  level = 0;

  constructor(handlers: VoiceHandlers) {
    this.handlers = handlers;
  }

  private setState(s: VoiceState, detail?: string) {
    this.state = s;
    this.handlers.onState(s, detail);
  }

  async start(): Promise<boolean> {
    if (this.state !== "off") return true;
    this.setState("connecting");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch (err) {
      this.setState(
        "off",
        `microphone unavailable: ${String(err).slice(0, 120)}`
      );
      return false;
    }
    // WS auth: browsers cannot set custom headers on WebSocket — the token
    // rides as a query param (the bridge's auth middleware accepts either)
    const { url: baseUrl, token } = await bridgeConfig();
    const url =
      baseUrl.replace(/^http/, "ws") +
      `/api/voice/stream${token ? `?token=${encodeURIComponent(token)}` : ""}`;
    try {
      this.ws = new WebSocket(url);
      this.ws.binaryType = "arraybuffer";
    } catch {
      this.cleanup();
      this.setState("off", "voice socket failed");
      return false;
    }
    this.ws.onopen = () => {
      this.setState("listening");
      void this.pipeMic();
    };
    this.ws.onclose = () => {
      if (this.state !== "off") this.setState("off", "voice channel closed");
      this.cleanup();
    };
    this.ws.onmessage = msg => {
      if (typeof msg.data !== "string") return;
      let ev: { type: string; [k: string]: unknown };
      try {
        ev = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (ev.type === "voice_level") {
        this.level = Number(ev.level ?? 0);
        // dispatch per frame: the universe core's mic-breathing listens to
        // 'universe:voice'. Dispatching only from onState (as before) meant
        // the glow updated on state changes, never on live amplitude
        // (audit 2026-09)
        window.dispatchEvent(
          new CustomEvent("universe:voice", { detail: this.level })
        );
      } else if (ev.type === "voice_status") {
        const st = String(ev.state);
        if (st === "awake") this.setState("awake");
        else if (st === "transcribing") this.setState("transcribing");
        else if (
          st === "too_short" ||
          st === "empty" ||
          st === "stt_error" ||
          st === "tts_error" ||
          st === "turn_error"
        ) {
          this.setState(
            "listening",
            st === "empty" ? undefined : String(ev.error ?? st)
          );
        } else if (st === "error") {
          this.setState("off", String(ev.error ?? "voice error"));
        }
      } else if (ev.type === "voice_transcript") {
        this.handlers.onTranscript(String(ev.text ?? ""));
        this.setState("listening");
      } else if (ev.type === "voice_tts_start") {
        // mp3 follows as one binary frame
      }
    };
    // binary = TTS mp3
    this.ws.addEventListener("message", msg => {
      if (msg.data instanceof ArrayBuffer) this.handlers.onTts?.(msg.data);
    });
    return true;
  }

  private async pipeMic(): Promise<void> {
    // 16kHz AudioContext: Chromium resamples the mic for us
    this.ctx = new AudioContext({ sampleRate: 16000 });
    this.source = this.ctx.createMediaStreamSource(this.stream!);
    // ScriptProcessor is deprecated but universally present and perfect for a
    // fixed 30ms push; an AudioWorklet lands with P9's hardening pass.
    this.proc = this.ctx.createScriptProcessor(2048, 1, 1);
    this.proc.onaudioprocess = e => {
      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = floatToPcm16(input);
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(pcm16.buffer as ArrayBuffer);
      }
    };
    this.source.connect(this.proc);
    this.proc.connect(this.ctx.destination); // required for onaudioprocess to fire
  }

  stop(): void {
    this.cleanup();
    this.setState("off");
  }

  private cleanup(): void {
    try {
      this.proc?.disconnect();
    } catch {
      /* noop */
    }
    try {
      this.source?.disconnect();
    } catch {
      /* noop */
    }
    try {
      this.ctx?.close();
    } catch {
      /* noop */
    }
    this.proc = null;
    this.source = null;
    this.ctx = null;
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) {
      try {
        this.ws.close();
      } catch {
        /* noop */
      }
    }
    this.ws = null;
  }
}

function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}
