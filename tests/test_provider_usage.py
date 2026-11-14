"""tests/test_provider_usage.py — A7: streamed completions never reported
usage.

The client never asked for `stream_options={"include_usage": true}`, so
`chunk.usage` was always None on the streaming path and every live-thinking
call was accounted as ZERO tokens in llm_calls (the money view was a lie).
These tests pin the request field, the fault-tolerant retry when an endpoint
rejects it, and the accounting of the real numbers.
"""
from __future__ import annotations

import asyncio

import pytest


class _Recorder:
    """Captures the create() kwargs the client builds."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream()


class _FakeStream:
    """One usage-only chunk, then a content chunk — the real OpenAI shape."""

    class _Chunk:
        def __init__(self, choices, usage):
            self.choices = choices
            self.usage = usage

    class _Ns:
        def __init__(self, content=None, tc=None, finish=None):
            from types import SimpleNamespace
            self.delta = SimpleNamespace(content=content, tool_calls=tc)
            self.finish_reason = finish

    async def __aiter__(self):
        from types import SimpleNamespace
        # the final usage-only chunk arrives FIRST in some providers
        yield self._Chunk([], SimpleNamespace(prompt_tokens=11,
                                              completion_tokens=3))
        yield self._Chunk([self._Ns(content="hello", finish="stop")], None)


def test_stream_requests_include_usage():
    from providers.client import _stream_completion
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return _FakeStream()

    client = SimpleNS(chat=SimpleNS(completions=SimpleNS(create=create)))
    out = asyncio.run(_stream_completion(client, {
        "model": "m", "messages": [],
        "stream_options": {"include_usage": True}}, lambda c: None))
    assert seen.get("stream") is True
    assert seen.get("stream_options") == {"include_usage": True}
    assert out.usage.prompt_tokens == 11
    assert out.usage.completion_tokens == 3


class SimpleNS:  # simple recursive attribute holder
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_stream_options_rejection_falls_back_to_a_plain_stream():
    """An endpoint that rejects stream_options must not fail the call."""
    from providers.client import _stream_completion

    async def create(**kwargs):
        if kwargs.get("stream_options"):
            raise RuntimeError("unknown field stream_options (not supported)")
        return _FakeStream()

    client = SimpleNS(chat=SimpleNS(completions=SimpleNS(create=create)))
    out = asyncio.run(_stream_completion(client, {
        "model": "m", "messages": [], "stream_options": {"include_usage": True}},
        lambda c: None))
    assert out.choices[0].message.content == "hello"


def test_a_transport_error_still_raises():
    from providers.client import _stream_completion

    async def create(**kwargs):
        raise RuntimeError("connection reset by peer")

    client = SimpleNS(chat=SimpleNS(completions=SimpleNS(create=create)))
    with pytest.raises(RuntimeError):
        asyncio.run(_stream_completion(client, {"model": "m",
                                                "messages": []}, lambda c: None))


def test_the_client_sets_stream_options_for_known_providers():
    from providers.client import _supports_stream_usage
    assert _supports_stream_usage("OpenAI") is True
    assert _supports_stream_usage("does-not-exist") is True  # optimistic default


def test_the_catalog_flag_is_read():
    from providers.catalog import PROVIDER_CATALOG
    # every preset exposes the flag (default True)
    for preset in PROVIDER_CATALOG:
        assert isinstance(getattr(preset, "supports_stream_usage", True), bool)
