"""tests/test_meta_media.py — the Meta Model API client is pinned to the
OFFICIAL endpoints and request shapes (no mock mode exists in the product;
these tests assert the real wire contract via a recording transport)."""
from __future__ import annotations

import asyncio
import json

import pytest

from providers import meta_media


# ---- key resolution ----------------------------------------------------------

def test_resolve_key_requires_configuration(fresh_home, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    with pytest.raises(meta_media.MetaAPIError) as exc:
        asyncio.run(meta_media.resolve_key(None))
    assert exc.value.status == 401
    assert "Meta API key" in exc.value.message


def test_resolve_key_env_precedence(fresh_home, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "env-key-1")
    assert asyncio.run(meta_media.resolve_key(None)) == "env-key-1"
    assert asyncio.run(meta_media.resolve_key("explicit")) == "explicit"


# ---- wire contract (recording transport) ------------------------------------

class _RecordingClient:
    """Stands in for httpx.AsyncClient and records every request so the
    test can assert the OFFICIAL endpoint paths and payload fields. All
    instances share one class-level request log; the canned response is
    installed via `install` before the code under test runs."""

    requests: list[dict] = []
    _resp: object = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        _RecordingClient.requests.append({"method": "GET", "url": url, **kw})
        return self._resp

    async def post(self, url, **kw):
        _RecordingClient.requests.append({"method": "POST", "url": url, **kw})
        return self._resp


class _Resp:
    def __init__(self, payload, status=200, ctype="application/json"):
        self.status_code = status
        self.headers = {"content-type": ctype}
        self._payload = payload
        self.text = (json.dumps(payload) if ctype == "application/json"
                     else str(payload))

    def json(self):
        return self._payload


@pytest.fixture
def recording(monkeypatch):
    _RecordingClient.requests = []
    _RecordingClient._resp = None
    monkeypatch.setattr(meta_media.httpx, "AsyncClient", _RecordingClient)

    def install(payload, status=200, ctype="application/json"):
        _RecordingClient._resp = _Resp(payload, status, ctype)

    return install


def _last_request() -> dict:
    return _RecordingClient.requests[-1]


def test_list_models_hits_official_endpoint(recording, fresh_home):
    recording({"data": [{"id": "muse-wonder-1.0"}, {"id": "muse-spark-1.3"}]})
    models = asyncio.run(meta_media.list_models("key-1"))
    req = _last_request()
    assert req["url"] == "https://api.meta.ai/v1/models"
    assert req["headers"]["Authorization"] == "Bearer key-1"
    assert "muse-wonder-1.0" in models


def test_generate_image_official_contract(recording, fresh_home):
    recording({"created": 1784584435,
               "data": [{"b64_json": "QUJD"}],
               "output_format": "webp"})
    out = asyncio.run(
        meta_media.generate_image("key-1", "a blue spiral galaxy"))
    req = _last_request()
    assert req["url"] == "https://api.meta.ai/v1/images/generations"
    payload = json.loads(req["content"])
    assert payload["model"] == "muse-image-1.0"
    assert payload["prompt"] == "a blue spiral galaxy"
    assert payload["response_format"] == "b64_json"
    assert req["headers"]["Authorization"] == "Bearer key-1"
    assert out == {"b64": "QUJD", "output_format": "webp"}


def test_transcribe_official_contract(recording, fresh_home):
    recording({"text": "hello universe"})
    text = asyncio.run(
        meta_media.transcribe("key-1", b"RIFF....", filename="audio.wav"))
    req = _last_request()
    assert req["url"] == "https://api.meta.ai/asr/transcribe"
    assert req["headers"]["Authorization"] == "Bearer key-1"
    request_meta = json.loads(req["data"]["request"])
    assert request_meta["model"] == "muse-voice-transcribe-1.0"
    assert request_meta["audioEncoding"] == "WAV"
    assert request_meta["mode"] == "PUSH_TO_TALK"
    assert req["files"]["audio"][0] == "audio.wav"
    assert text == "hello universe"


def test_auth_failure_is_explicit(recording, fresh_home):
    recording({"error": "bad key"}, status=401)
    with pytest.raises(meta_media.MetaAPIError) as exc:
        asyncio.run(meta_media.list_models("wrong"))
    assert exc.value.status == 401
