"""providers/client.py — unified LLM client with retry, key rotation, logging.

Every agent calls LLMClient.complete(). The client:
- Resolves (provider, model, key) via ProviderManager.best_available(agent).
- Routes every call to the OpenAI-compatible SDK against the provider's
  base_url with the decrypted key.
- On rate-limit/timeout errors, rotates keys / falls back per §15.
- When no provider is configured (or every configured one fails), raises
  LLMError(kind="no_provider") so callers surface an explicit setup error.
- Logs a redacted LLM-call record (prompt hash only, never content) for /debug.

There is no internal spend or rate-limit budget here by design (§12) — caps
belong to the provider, set at the API-key level. The only thing this client
does about a rate-limit error is react to it like any other transient
failure: rotate to the next key/provider in the chain.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from .manager import get_provider_manager


NO_PROVIDER_MESSAGE = (
    "No LLM provider is configured — open Settings → Model provider and "
    "connect one (a free-tier cloud key or a local Ollama/LM Studio both work)."
)


def _tc_field(call: Any, path: str, default: str = "") -> str:
    """Read a tool_call field in BOTH wire shapes this client sees:
    OpenAI SDK objects (non-streaming provider responses) AND plain dicts
    (the _stream_completion reconstruction). The streaming path feeds
    dicts; getattr on a dict silently returned '' — the empty names then
    either poisoned the next request (400 'non-empty string', the
    historical killer) or, after the guard, dropped every streamed tool
    call. One accessor, both shapes."""
    obj = call
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            obj = getattr(obj, part, None)
        if obj is None:
            return default
    return obj if isinstance(obj, str) else default


def _parse_tool_calls(message: Any, reverse_tools: dict[str, str]) -> list[dict]:
    """Extract provider tool_calls as raw dicts for the orchestrator.

    GUARD (failure-diagnosis.md root #2 + the streaming shape): a call
    with an EMPTY function name carries no dispatchable intent — it must
    never enter the conversation history, or the next request replays it
    and strict providers 400 until the whole goal dies."""
    out: list[dict] = []
    for call in (getattr(message, "tool_calls", None) or []):
        fname = _tc_field(call, "function.name")
        if not fname.strip():
            logger.warning("dropping tool_call with empty function name "
                           f"(id={_tc_field(call, 'id')!r})")
            continue
        # reverse the per-call sanitize map so dispatch sees real names
        fname = reverse_tools.get(fname, fname)
        out.append({
            "id": _tc_field(call, "id"),
            "type": _tc_field(call, "type") or "function",
            "function": {
                "name": fname,
                "arguments": _tc_field(call, "function.arguments") or "{}",
            },
        })
    return out


def _supports_response_format(provider_name: str) -> bool:
    """Catalog-driven: providers whose endpoint rejects the OpenAI
    response_format transport field declare supports_response_format=False
    in providers/catalog.py. Unknown providers (user-added base_urls) are
    assumed compatible — the failure mode is a malformed error we already
    classify, not silent prompt degradation."""
    from providers.catalog import PROVIDER_CATALOG
    for preset in PROVIDER_CATALOG:
        if preset.name == provider_name:
            return preset.supports_response_format
    return True


def _sanitize_tool_name(name: str) -> str:
    """Provider-safe function name: the OpenAI spec allows [a-zA-Z0-9_-]
    but Universe's internal tools are dotted (file.list, mcp.call …).
    Tolerant providers accept dots; strict ones (dots-studio, some
    gateways) 400 the whole request. Dots → underscores is reversible
    via the reverse map built per call."""
    return "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in name)


class LLMError(Exception):
    """Raised when an LLM call fails after all retries / rotation."""

    def __init__(self, message: str, *, kind: str = "unknown",
                 provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.kind = kind  # no_provider | rate_limit | timeout | provider_down | content_filter
                          # | overflow | malformed | auth | model_unavailable
        self.provider = provider
        self.model = model


# kinds worth retrying the SAME chain entry on (transient transport/quota
# failures); everything else (auth, malformed request, missing model,
# content filter) fails identically on an immediate second attempt.
TRANSIENT_KINDS = frozenset({"rate_limit", "timeout", "provider_down"})
# per-chain-entry attempt schedule: 1 try + up to _RETRY_MAX retries,
# exponential backoff (0.5/1/2/4s). The autonomous-agent rule the user set:
# a flaky link or a momentary quota window must never halt Universe outright —
# only an exhausted ladder does, and then with the RAW provider error.
_RETRY_BASE_S = 0.5
_RETRY_MAX = 4


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    finish_reason: str
    raw: dict[str, Any]
    key_alias: str = ""


async def _stream_completion(client: Any, kwargs: dict[str, Any],
                             on_delta: Any) -> Any:
    """Streamed completion (M5 live thinking). Reconstructs the non-stream
    response shape — text assembled from deltas, tool_calls concatenated
    from per-index fragments — while on_delta receives every text chunk.
    Raises on any provider hiccup; the caller falls back to the proven
    non-streaming call."""
    from types import SimpleNamespace
    try:
        # m6 (audit 2026-10): without stream_options the provider never
        # sends the final usage-only chunk, so every streamed call was
        # accounted as 0 tokens. TypeError guard: OpenAI SDKs predating
        # stream_options reject the kwarg; a provider that rejects the
        # FIELD fails the stream transport and the caller's existing
        # non-stream fallback applies (same as other stream failures).
        stream = await client.chat.completions.create(
            **kwargs, stream=True, stream_options={"include_usage": True})
    except TypeError:
        stream = await client.chat.completions.create(**kwargs, stream=True)
    text_parts: list[str] = []
    tc_acc: dict[int, dict] = {}
    finish_reason = "stop"
    usage = None
    async for chunk in stream:
        chs = getattr(chunk, "choices", None) or []
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not chs:
            continue
        ch = chs[0]
        delta = getattr(ch, "delta", None)
        if delta is not None:
            dtext = delta.content or ""
            if dtext:
                text_parts.append(dtext)
                try:
                    on_delta(dtext)
                except Exception:
                    pass  # display-only: a broken viewer never breaks the LLM call
            for tc in (getattr(delta, "tool_calls", None) or []):
                idx = getattr(tc, "index", 0)
                slot = tc_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    slot["id"] = slot["id"] or tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if fn.name:
                        slot["name"] += fn.name
                    if fn.arguments:
                        slot["arguments"] += fn.arguments
        if getattr(ch, "finish_reason", None):
            finish_reason = ch.finish_reason
    text = "".join(text_parts)
    tool_calls = [
        {"id": slot["id"] or f"call_{i}", "type": "function",
         "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"}}
        for i, slot in sorted(tc_acc.items())
    ]
    if not text and not tool_calls and usage is None:
        # Zero SSE events received: the provider ignored stream=true and
        # answered with a plain JSON body, which parses as an empty stream.
        # That shape used to return a VALID-LOOKING empty reply (the agent
        # turn said nothing with no error). Treat it as a stream-transport
        # failure — the caller falls back to the proven non-stream call.
        raise RuntimeError("stream produced no SSE events — provider ignored stream=true")
    message = SimpleNamespace(content=text, tool_calls=tool_calls or None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class LLMClient:
    """Async LLM client. One instance shared app-wide."""

    def __init__(self) -> None:
        self._pm = get_provider_manager()
        self._call_log: list[dict[str, Any]] = []
        # bounded (audit 2026-09): the log grew for the process lifetime; a
        # long-lived desktop bridge slowly leaked memory. session_tokens()
        # only needs totals, which the running sums below preserve.
        self._tokens_in = 0
        self._tokens_out = 0
        self._MAX_CALL_LOG = 200
        # AsyncOpenAI clients keyed by (base_url, key alias): building one
        # per call leaked sockets and re-ran TLS setup on every LLM round.
        # The cache is closed explicitly by aclose() (called at shutdown);
        # entries are evicted when a key is removed from the manager.
        self._clients: dict[tuple[str, str], Any] = {}

    async def complete(self, *, agent: str, messages: list[dict[str, str]],
                       max_tokens: int = 1024, temperature: float = 0.0,
                       response_format: dict | None = None,
                       tools: list[dict[str, Any]] | None = None,
                       tool_choice: str | dict | None = None,
                       tier_hint: str | None = None,
                       on_delta: Any = None) -> LLMResponse:
        """Run one completion with full retry/rotation. Raises LLMError on
        terminal failure.

        tier_hint (m2 wiring of resources/router.py §19): when the agent has
        no user-pinned model, the hint selects a per-tier default model for
        the provider (cheap models for classification/formatting, etc.).
        With no tier mapping for the provider the previous behavior — the
        provider's first suggested model — applies unchanged.

        on_delta (M5 live thinking): optional sync callback receiving each
        streamed text chunk. Best-effort — providers/gateways without
        streaming fall back to the proven non-stream call silently."""
        pm = self._pm
        # one shared chain builder: pinned model + configured fallbacks +
        # the keyed-provider emergency tail (a lost routing row must degrade
        # to a working default, not to a dead app)
        chain = pm.chain_for(agent)
        primary = pm.get_agent_model(agent)
        if not chain:
            raise LLMError(NO_PROVIDER_MESSAGE, kind="no_provider")

        if tier_hint and primary is None:
            # no explicit pin: consult the §19 routing table for a tier
            # default on the first provider in the chain
            from resources.router import model_for_tier
            provider_name, model = chain[0]
            tier_model = model_for_tier(tier_hint, provider_name)
            if tier_model and tier_model != model:
                chain = [(provider_name, tier_model), *chain[1:]]

        # walk the chain with a bounded retry per entry: transient kinds
        # (rate_limit/timeout/provider_down) get up to _RETRY_MAX backoff
        # retries on the SAME provider (the reconnecting pattern), permanent
        # kinds (auth/malformed/model_unavailable/content_filter/overflow)
        # advance the chain immediately. Errors are COLLECTED, never
        # dropped: the surfaced error is the PRIMARY's (the chain head the
        # user configured), with the rest appended as "tried also" — a dead
        # emergency-tail model must never masquerade as the user's failure.
        errors: list[LLMError] = []
        for idx, (provider_name, model) in enumerate(chain):
            attempt = 0
            while True:
                try:
                    return await self._call_once(
                        provider_name, model, agent, messages,
                        max_tokens, temperature, response_format, tools, tool_choice,
                        on_delta,
                    )
                except LLMError as e:
                    errors.append(e)
                    if e.kind in TRANSIENT_KINDS and attempt < _RETRY_MAX:
                        attempt += 1
                        # the autonomous-agent promise, made visible: the
                        # UI shows "retrying (n/5)" instead of a silent hang
                        try:
                            from observability.events import emit
                            emit("provider_retry", agent=agent,
                                 provider=provider_name, model=model,
                                 attempt=attempt, max_retries=_RETRY_MAX,
                                 kind=e.kind)
                        except Exception:
                            pass
                        await asyncio.sleep(
                            _RETRY_BASE_S * (2 ** (attempt - 1))
                            + (time.time_ns() % 100_000_000) / 1e9 * 0.1)  # ≤100ms jitter
                        continue
                    break
        err = errors[0] if errors else LLMError(
            NO_PROVIDER_MESSAGE, kind="no_provider")
        if len(errors) > 1:
            tail = "; ".join(
                f"{e.provider}:{e.kind}"
                for e in errors[1:]
            )
            err = LLMError(
                f"{err} (fallbacks also failed: {tail})",
                kind=err.kind, provider=err.provider, model=err.model)
        # terminal failure is still accounting: the UI's /cost view and the
        # JSONL log must show the failed attempt (finish_reason="error" +
        # error_kind), not a silent gap in the sequence
        await self._record_failure(agent, err, messages)
        raise err

    async def aclose(self) -> None:
        """Close every cached AsyncOpenAI client (sockets, connection
        pools). Called once at process shutdown (bridge/app lifespan)."""
        clients, self._clients = self._clients, {}
        for client in clients.values():
            try:
                await client.close()
            except Exception:
                pass

    def _client_for(self, base_url: str, alias: str, secret: str) -> Any:
        """Cached AsyncOpenAI per (base_url, key alias)."""
        from openai import AsyncOpenAI
        client = self._clients.get((base_url, alias))
        if client is not None:
            return client
        client = AsyncOpenAI(base_url=base_url, api_key=secret, timeout=60.0)
        self._clients[(base_url, alias)] = client
        return client

    def evict_client(self, base_url: str, alias: str) -> None:
        """Drop one cached AsyncOpenAI client WITHOUT closing it (the pool
        may still serve in-flight retries; close() happens once at
        aclose()). Called by ProviderManager.remove_key so a deleted
        credential's pooled connection never serves the next call."""
        self._clients.pop((base_url, alias), None)

    def forget_provider(self, base_url: str) -> None:
        """Evict EVERY cached AsyncOpenAI client whose (base_url, alias)
        key matches a base_url — called by ProviderManager.remove_provider.
        remove_key only dropped the removed key's entry; deleting a whole
        provider used to leave every remaining key's pooled connection
        authenticated against a provider the app no longer has."""
        doomed = [k for k in self._clients if k[0] == base_url]
        for k in doomed:
            self._clients.pop(k, None)

    async def _call_once(self, provider_name: str, model: str, agent: str,
                         messages: list[dict[str, str]], max_tokens: int,
                         temperature: float, response_format: dict | None,
                         tools: list[dict[str, Any]] | None,
                         tool_choice: str | dict | None,
                         on_delta: Any = None,
                         ) -> LLMResponse:
        """Try EVERY key of this provider (§15 rotation) before the caller
        moves to the next provider in the chain."""
        pm = self._pm
        provider = pm.get_provider(provider_name)
        if provider is None:
            raise LLMError(f"unknown provider {provider_name!r}", kind="provider_down",
                           provider=provider_name, model=model)

        keys = pm.list_keys(provider_name, agent=agent)
        if not keys:
            raise LLMError(f"no key for {provider_name}", kind="provider_down",
                           provider=provider_name, model=model)
        last_err: LLMError | None = None
        for key in keys:
            try:
                return await self._try_key(
                    provider, key, model, agent, messages,
                    max_tokens, temperature, response_format, tools, tool_choice,
                    on_delta,
                )
            except LLMError as e:
                last_err = e
                # auth/malformed/content-filter/model-unavailable are
                # request- or model-shaped errors: trying the next key of
                # the SAME provider is honest for auth (a different key may
                # be valid) but pointless for the others — the next key
                # hits the identical 400/410
                if e.kind in ("malformed", "content_filter", "model_unavailable"):
                    break
                continue
        raise last_err or LLMError(f"no key for {provider_name}", kind="provider_down",
                                   provider=provider_name, model=model)

    async def _try_key(self, provider: Any, key: Any, model: str, agent: str,
                       messages: list[dict[str, str]], max_tokens: int,
                       temperature: float, response_format: dict | None,
                       tools: list[dict[str, Any]] | None,
                       tool_choice: str | dict | None,
                       on_delta: Any = None,
                       ) -> LLMResponse:
        pm = self._pm
        provider_name = provider.name
        secret = self._decrypt_key(key.encrypted_secret)

        start = time.time()
        try:
            from openai import (
                APIConnectionError,
                APIError,
                APITimeoutError,
                AuthenticationError,
                BadRequestError,
                RateLimitError,
            )
        except Exception as e:  # pragma: no cover — openai is a hard dep
            raise LLMError(f"openai SDK unavailable: {e}", kind="provider_down",
                           provider=provider_name, model=model)
        client = self._client_for(provider.base_url, key.alias, secret)
        try:
            kwargs: dict[str, Any] = {
                "model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature,
            }
            # Providers whose endpoint rejects the OpenAI response_format
            # transport field are flagged in the catalog
            # (supports_response_format=False); the model can still follow
            # an explicit JSON instruction in the prompt, so we omit only
            # this optional transport field there.
            if response_format and _supports_response_format(provider_name):
                kwargs["response_format"] = response_format
            reverse_tools: dict[str, str] = {}
            if tools:
                # strict providers 400 dotted function names (file.list …):
                # send sanitized names, map them back on the response so
                # dispatch (registry.call) always sees the real tool name
                safe_tools = []
                for t in tools:
                    fn = dict((t.get("function") or {}))
                    orig = str(fn.get("name", ""))
                    safe = _sanitize_tool_name(orig)
                    if safe != orig:
                        reverse_tools[safe] = orig
                        fn["name"] = safe
                    safe_tools.append({**t, "function": fn})
                kwargs["tools"] = safe_tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
            resp = None
            if on_delta is not None:
                # M5 live thinking: stream when the provider supports it;
                # falls back to the proven non-stream call only for
                # stream-TRANSPORT failures. Rate limits and auth errors
                # used to be swallowed here and retried identically non-
                # streamed — doubling request pressure exactly when the
                # provider is rate-limiting, delaying the surfaced error
                # and skewing per-key error budgets (audit 2026-09). The
                # chain-walk retry logic above owns real provider errors;
                # deltas already shown are display-only, so a fallback is
                # still safe for genuinely stream-shaped failures.
                try:
                    resp = await _stream_completion(client, kwargs, on_delta)
                except (RateLimitError, AuthenticationError, APITimeoutError):
                    raise
                except APIConnectionError as e:
                    # a mid-stream connection death is a REAL provider
                    # failure, not a stream-shape quirk: the generic
                    # fallthrough below used to re-send the whole request
                    # non-streamed — doubling request pressure on a dying
                    # link and hiding the provider error behind a second
                    # identical one. Record the key error and classify
                    # provider_down so the chain walk rotates honestly.
                    # (Order matters: APITimeoutError subclasses this, and
                    # is already caught above.)
                    pm.record_key_error(provider_name, key.id)
                    raise LLMError(str(e), kind="provider_down",
                                   provider=provider_name, model=model) from e
                except Exception:
                    resp = None
            if resp is None:
                resp = await client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            pm.record_key_error(provider_name, key.id)
            raise LLMError(str(e), kind="rate_limit",
                           provider=provider_name, model=model) from e
        except APITimeoutError as e:
            pm.record_key_error(provider_name, key.id)
            raise LLMError(str(e), kind="timeout",
                           provider=provider_name, model=model) from e
        except AuthenticationError as e:
            # auth is a SETUP failure, never a key-health signal: do NOT
            # burn the key's error budget for a bad secret, and say so so
            # callers route the user to Settings instead of rotating forever
            raise LLMError(str(e), kind="auth",
                           provider=provider_name, model=model) from e
        except BadRequestError as e:
            # request-shaped failure (bad model name, bad tool schema…):
            # rotating keys/providers would only repeat the same 400.
            # BUT gateways (Kilo, OpenRouter) also answer a transient 400
            # "Provider returned error" when the upstream model is busy or
            # rate-limited behind them (measured live: design step died on
            # it) — that shape is retryable and must move the chain.
            if "provider returned error" in str(e).casefold():
                pm.record_key_error(provider_name, key.id)
                raise LLMError(str(e), kind="provider_down",
                               provider=provider_name, model=model) from e
            raise LLMError(str(e), kind="malformed",
                           provider=provider_name, model=model) from e
        except APIConnectionError as e:
            pm.record_key_error(provider_name, key.id)
            raise LLMError(str(e), kind="provider_down",
                           provider=provider_name, model=model) from e
        except APIError as e:
            msg = str(e).lower()
            status = getattr(e, "status_code", None)
            if status in (404, 410) or "end of life" in msg or "no longer available" in msg or "model not found" in msg:
                # the MODEL is dead (410 Gone / 404), not the key and not
                # the provider: never burn key health for it, and the
                # chain walk moves on immediately (a second attempt would
                # fail identically)
                raise LLMError(str(e), kind="model_unavailable",
                               provider=provider_name, model=model) from e
            if "context" in msg and "length" in msg:
                raise LLMError(str(e), kind="overflow",
                               provider=provider_name, model=model) from e
            if ("content" in msg and "filter" in msg) or "content_policy" in msg:
                # the provider refused THIS content — a different key or
                # provider will refuse it too. Re-prompting safely is the
                # caller's move (failure/retry table §15).
                raise LLMError(str(e), kind="content_filter",
                               provider=provider_name, model=model) from e
            pm.record_key_error(provider_name, key.id)
            raise LLMError(str(e), kind="provider_down",
                           provider=provider_name, model=model) from e

        latency = int((time.time() - start) * 1000)
        choices = list(getattr(resp, "choices", None) or [])
        if not choices:
            # an empty choices list used to crash with IndexError (raw,
            # unclassified); now it is a structured provider failure the
            # orchestrator's provider-halt guard can understand
            raise LLMError("provider returned no choices", kind="provider_down",
                           provider=provider_name, model=model)
        text = choices[0].message.content or ""
        usage = resp.usage
        in_t = getattr(usage, "prompt_tokens", 0) or 0
        out_t = getattr(usage, "completion_tokens", 0) or 0
        message = choices[0].message
        raw_tool_calls = _parse_tool_calls(message, reverse_tools)
        result = LLMResponse(
            text=text, model=model, provider=provider_name,
            input_tokens=in_t, output_tokens=out_t, latency_ms=latency,
            finish_reason=choices[0].finish_reason or "stop",
            raw={"tool_calls": raw_tool_calls},
            key_alias=key.alias,
        )
        pm.record_key_success(provider_name, key.id, latency)
        await self._record(agent, result, messages)
        return result

    def _decrypt_key(self, blob: str) -> str:
        if not blob:
            return ""
        from security.secrets_fallback import DecryptionError, decrypt_secret
        try:
            return decrypt_secret(blob)
        except DecryptionError as exc:
            # an undecryptable blob must NEVER be sent to a provider as if
            # it were the key — surface it as an empty key (clear auth
            # failure at the provider, key rotation moves to the next key)
            logger.warning(f"provider key undecryptable: {exc}")
            return ""

    async def _record(self, agent: str, resp: LLMResponse, messages: list[dict[str, str]]) -> None:
        # Prompt content is NEVER stored — only a hash (§11).
        joined = "\n".join(str(m.get("content") or "") for m in messages)
        prompt_hash = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "agent": agent, "provider": resp.provider, "model": resp.model,
            "key_alias": resp.key_alias,
            "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
            "latency_ms": resp.latency_ms,
            "prompt_hash": prompt_hash, "finish_reason": resp.finish_reason,
        }
        self._call_log.append(rec)
        if len(self._call_log) > self._MAX_CALL_LOG:
            del self._call_log[:-self._MAX_CALL_LOG]
        self._tokens_in += resp.input_tokens
        self._tokens_out += resp.output_tokens

        def _durable() -> None:
            # append to on-disk LLM log
            try:
                from config import get_config
                cfg = get_config()
                cfg.home.mkdir(parents=True, exist_ok=True)
                with open(cfg.llm_log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass
            # durable row in the llm_calls table: the accounting the UI and the
            # user can audit — a call that isn't counted here didn't happen.
            # v16 added key_alias + error_kind columns; the INSERT names them
            # explicitly (every entry point runs ensure_latest() at startup).
            try:
                import time as _t
                from storage.local import get_storage
                with get_storage().transaction() as conn:
                    conn.execute(
                        "INSERT INTO llm_calls(ts, agent, provider, model, "
                        "input_tokens, output_tokens, latency_ms, prompt_hash, "
                        "finish_reason, key_alias, error_kind) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?);",
                        (_t.time(), agent, resp.provider, resp.model,
                         resp.input_tokens, resp.output_tokens, resp.latency_ms,
                         prompt_hash, resp.finish_reason, resp.key_alias, ""),
                    )
            except Exception:
                pass  # accounting must never break the answer itself

        # engine-fix round: the JSONL append + llm_calls INSERT are blocking
        # disk I/O on the event loop — every LLM round stalled on them. The
        # write hops to a worker thread; awaiting here keeps per-call
        # ordering (call N's row commits before call N+1 starts, so the log
        # stays monotonic for the UI's /cost view).
        await asyncio.to_thread(_durable)

    async def _record_failure(self, agent: str, err: LLMError,
                              messages: list[dict[str, str]]) -> None:
        """Account a terminal LLM failure the same way successes are
        accounted: JSONL line + llm_calls row with finish_reason="error"
        and the structured error_kind. Zero tokens, zero latency."""
        joined = chr(10).join(str(m.get("content") or "") for m in messages)
        prompt_hash = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "agent": agent, "provider": err.provider, "model": err.model,
            "key_alias": "",
            "input_tokens": 0, "output_tokens": 0,
            "latency_ms": 0,
            "prompt_hash": prompt_hash, "finish_reason": "error",
            "error_kind": err.kind,
        }
        self._call_log.append(rec)
        if len(self._call_log) > self._MAX_CALL_LOG:
            del self._call_log[:-self._MAX_CALL_LOG]

        def _durable() -> None:
            try:
                from config import get_config
                cfg = get_config()
                cfg.home.mkdir(parents=True, exist_ok=True)
                with open(cfg.llm_log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + chr(10))
            except Exception:
                pass
            try:
                import time as _t
                from storage.local import get_storage
                with get_storage().transaction() as conn:
                    conn.execute(
                        "INSERT INTO llm_calls(ts, agent, provider, model, "
                        "input_tokens, output_tokens, latency_ms, prompt_hash, "
                        "finish_reason, key_alias, error_kind) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?);",
                        (_t.time(), agent, err.provider, err.model,
                         0, 0, 0, prompt_hash, "error", "", err.kind),
                    )
            except Exception:
                pass  # accounting must never break the error itself

        # same event-loop treatment as _record: blocking writes off the
        # loop, awaited so failure accounting stays strictly ordered with
        # the next call's accounting
        await asyncio.to_thread(_durable)

    # ---- cost / call-log accessors ---------------------------------------
    def call_log(self) -> list[dict[str, Any]]:
        return list(self._call_log)

    def session_tokens(self) -> tuple[int, int]:
        # running sums over ALL calls (the log itself is capped at
        # _MAX_CALL_LOG entries; summing the capped list would undercount)
        return self._tokens_in, self._tokens_out


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm_client_for_tests() -> LLMClient:
    global _client
    _client = LLMClient()
    return _client
