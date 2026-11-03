"""providers/meta_media.py — the REAL Meta (Meta Model API) media client.

Implements the official documented endpoints — no mock mode exists anywhere:
  * GET  https://api.meta.ai/v1/models               (key test / model list)
  * POST https://api.meta.ai/v1/images/generations   (Muse Image, $0.01/img)
         model "muse-image-1.0", {prompt, n, size?, response_format, output_format}
  * POST https://api.meta.ai/asr/transcribe          (Muse Voice Transcribe)
         multipart: request={"model":"muse-voice-transcribe-1.0",
                             "audioEncoding":"WAV","mode":"PUSH_TO_TALK"} + audio file
LLM chat goes through the standard OpenAI-compatible path (providers/client.py)
against the catalog base_url https://api.meta.ai/v1 with muse-wonder-1.0 /
muse-spark-1.3 — the moment the user enters their MODEL_API_KEY everything
works with zero further configuration.

Key resolution order (never logged, never persisted in plaintext by us):
  1. explicit key passed by the caller (Settings "test" flow)
  2. MODEL_API_KEY env (the name Meta's own docs use)
  3. the encrypted "Meta" provider key stored via Settings → Providers
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_API_BASE = "https://api.meta.ai"
IMAGE_MODEL = "muse-image-1.0"
STT_MODEL = "muse-voice-transcribe-1.0"
_LLM_MODELS = ["muse-wonder-1.0", "muse-spark-1.3", "muse-spark-1.2"]


class MetaAPIError(Exception):
    """Raised with the upstream status + a human-safe message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def api_base() -> str:
    return os.environ.get("UNIVERSE_META_API_BASE", DEFAULT_API_BASE).rstrip("/")


async def resolve_key(explicit: str | None = None) -> str:
    """Resolve the Meta API key. Raises MetaAPIError(401) when absent so the
    UI can point the user at Settings instead of failing opaquely later."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY")
    if env and env.strip():
        return env.strip()
    try:
        from providers.manager import get_provider_manager
        from security.secrets_fallback import decrypt_secret
        pk = get_provider_manager().pick_key("Meta")
        if pk is not None:
            secret = decrypt_secret(pk.encrypted_secret)
            if secret and secret.strip():
                return secret.strip()
    except Exception:
        pass
    raise MetaAPIError(401, "No Meta API key configured — add one in "
                            "Settings → Providers → Meta (MODEL_API_KEY).")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def list_models(key: str) -> list[str]:
    """GET /v1/models — the real key test. Any model ids Meta returns are
    authoritative; the curated LLM list is appended as suggestions."""
    url = f"{api_base()}/v1/models"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=_headers(key))
        if resp.status_code in (401, 403):
            raise MetaAPIError(resp.status_code, "Meta rejected the API key "
                               f"(HTTP {resp.status_code}).")
        if resp.status_code != 200:
            raise MetaAPIError(resp.status_code,
                               f"Meta /v1/models failed (HTTP {resp.status_code}).")
        body = resp.json()
    ids: list[str] = []
    for item in body.get("data", []) or []:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str) and mid:
            ids.append(mid)
    for m in _LLM_MODELS:
        if m not in ids:
            ids.append(m)
    return ids


async def generate_image(key: str, prompt: str, size: str | None = None,
                         n: int = 1) -> dict[str, Any]:
    """POST /v1/images/generations — Muse Image. Returns the first image as
    {"b64": ..., "output_format": ...} or {"url": ...} per response_format."""
    payload: dict[str, Any] = {"model": IMAGE_MODEL, "prompt": prompt,
                               "n": max(1, min(int(n), 10)),
                               "response_format": "b64_json"}
    if size:
        payload["size"] = size
    url = f"{api_base()}/v1/images/generations"
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, headers={**_headers(key),
                                              "Content-Type": "application/json"},
                                 content=json.dumps(payload))
    if resp.status_code in (401, 403):
        raise MetaAPIError(resp.status_code, "Meta rejected the API key "
                           f"(HTTP {resp.status_code}).")
    if resp.status_code != 200:
        detail = resp.text[:300] if resp.text else ""
        raise MetaAPIError(resp.status_code,
                           f"Muse Image failed (HTTP {resp.status_code}) {detail}")
    body = resp.json()
    data = body.get("data") or []
    if not data:
        raise MetaAPIError(502, "Muse Image returned no images.")
    first = data[0]
    if first.get("b64_json"):
        return {"b64": first["b64_json"],
                "output_format": body.get("output_format") or "webp"}
    if first.get("url"):
        return {"url": first["url"]}
    raise MetaAPIError(502, "Muse Image response had neither b64_json nor url.")


async def transcribe(key: str, audio: bytes, filename: str = "audio.wav",
                     encoding: str = "WAV", mode: str = "PUSH_TO_TALK") -> str:
    """POST /asr/transcribe — Muse Voice Transcribe (one-shot). Multipart
    per the official docs: a JSON `request` part + the `audio` part."""
    url = f"{api_base()}/asr/transcribe"
    request_meta = {"model": STT_MODEL, "audioEncoding": encoding, "mode": mode}
    files = {"audio": (filename, audio, "audio/wav" if encoding == "WAV"
                       else "audio/webm")}
    data = {"request": json.dumps(request_meta)}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=_headers(key), files=files, data=data)
    if resp.status_code in (401, 403):
        raise MetaAPIError(resp.status_code, "Meta rejected the API key "
                           f"(HTTP {resp.status_code}).")
    if resp.status_code != 200:
        raise MetaAPIError(resp.status_code,
                           f"Transcribe failed (HTTP {resp.status_code}).")
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        body = resp.json()
        text = body.get("text") or body.get("transcript") or ""
        if isinstance(body.get("segments"), list) and not text:
            text = " ".join(str(s.get("text") or "") for s in body["segments"])
        return str(text).strip()
    return resp.text.strip()


async def segment(key: str, text: str, image_url: str) -> str:
    """SAM 3.1 media segmentation — POST /v1/responses with model "sam-3.1":
    a noun-phrase prompt + one input_image. The model answers with
    special-token text (one box + one mask per match). Handles both the
    buffered JSON shape and the SSE stream shape."""
    url = f"{api_base()}/v1/responses"
    payload = {
        "model": "sam-3.1",
        "input": [{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": text},
                {"type": "input_image", "image_url": image_url},
            ],
        }],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, headers={**_headers(key),
                                              "Content-Type": "application/json"},
                                 content=json.dumps(payload))
    if resp.status_code in (401, 403):
        raise MetaAPIError(resp.status_code, "Meta rejected the API key "
                           f"(HTTP {resp.status_code}).")
    if resp.status_code != 200:
        raise MetaAPIError(resp.status_code,
                           f"SAM 3.1 failed (HTTP {resp.status_code}).")
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        deltas: list[str] = []
        for raw_line in resp.text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data: "):
                continue
            chunk = line[len("data: "):]
            if chunk == "[DONE]":
                break
            try:
                event = json.loads(chunk)
            except Exception:
                continue
            if event.get("type") == "response.output_text.delta":
                deltas.append(str(event.get("delta") or ""))
        return "".join(deltas).strip()
    body = resp.json()
    if isinstance(body.get("output_text"), str) and body["output_text"]:
        return body["output_text"].strip()
    pieces: list[str] = []
    for item in body.get("output") or []:
        for part in (item.get("content") or []):
            if isinstance(part, dict) and part.get("text"):
                pieces.append(str(part["text"]))
    return "\n".join(pieces).strip()
