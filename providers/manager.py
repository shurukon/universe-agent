"""providers/manager.py — provider + key registry, rotation, persistence.

Implements §8's multi-key-per-provider design and §19's free-provider tiers.
Keys are stored AES-encrypted (security/secrets_fallback.py handles the actual
encryption); here we store the encrypted blob and the non-secret metadata.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from storage.local import get_storage

# Single source of truth (m2): both sets derive from providers/catalog.py —
# the curated list. The old hand-maintained copies had already drifted
# (Together.ai was "free" here but "paid" in the catalog).
from providers.catalog import PROVIDER_CATALOG, BY_NAME

# name -> base_url for every catalog provider that has a reachable endpoint
KNOWN_PROVIDERS: dict[str, str] = {
    p.name: p.base_url for p in PROVIDER_CATALOG if p.base_url
}

# free-tier-friendly providers (§19, used by /setup Free Mode): anything the
# catalog marks "free", plus "local" (a local server needs no key at all)
FREE_PROVIDERS: set[str] = {
    p.name for p in PROVIDER_CATALOG if p.category in ("free", "local")
}


@dataclass
class ProviderKey:
    id: int
    alias: str
    status: str  # high | medium | low
    encrypted_secret: str = ""  # ciphertext blob (base64) from secrets_fallback
    tier: str = "paid"  # paid | free — informational label only; Universe does not
                        # track spend or rate-limit headroom internally (see §12)
    last_error_ts: float = 0.0
    error_count_5min: list[float] = field(default_factory=list)
    request_count_5min: list[float] = field(default_factory=list)
    latencies_5min: list[tuple[float, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("error_count_5min", None)
        d.pop("request_count_5min", None)
        d.pop("latencies_5min", None)
        # NEVER expose the ciphertext blob through the dict view — this is
        # what the desktop UI serializes (pm.to_dict). Decryption for real
        # use happens attribute-side (key.encrypted_secret) in the two
        # bridge code paths that need it, not via the serialized shape.
        d.pop("encrypted_secret", None)
        return d


# ---- key-health predicates (single source, engine-fix round) -----------
# pick_key, list_keys and health_snapshot each re-derived the disabled
# rule inline with subtly different code that could drift. One predicate,
# one derived-status function, both pinned by tests.

_DISABLED_MIN_REQUESTS = 4
_DISABLED_ERROR_RATE = 0.25


def _is_disabled(key: ProviderKey) -> bool:
    """A key is error-disabled when its 5-min window has enough samples
    (≥4 requests) AND ≥25% of them failed."""
    total = len(key.request_count_5min)
    failures = len(key.error_count_5min)
    return total >= _DISABLED_MIN_REQUESTS and failures / total >= _DISABLED_ERROR_RATE


def _effective_status(key: ProviderKey) -> str:
    """Derived health tier from the same 5-min stats _is_disabled reads:
    ≥25% errors → low, any error → medium, clean window → high. The stored
    key.status is only the baseline while there are too few samples (<4)
    to judge. pick_key/list_keys rank on this, so a recently-failing key
    drops behind a clean one even when both carry the same stored status."""
    total = len(key.request_count_5min)
    failures = len(key.error_count_5min)
    if total < _DISABLED_MIN_REQUESTS:
        return key.status
    rate = failures / total
    if rate >= _DISABLED_ERROR_RATE:
        return "low"
    if failures > 0:
        return "medium"
    return "high"


@dataclass
class Provider:
    name: str
    base_url: str
    keys: list[ProviderKey] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "keys": [k.to_dict() for k in self.keys],
        }


class ProviderManager:
    """Owns the provider registry. Single instance shared app-wide."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, Provider] = {}
        # default agent -> (provider, model) assignments (§19)
        self._agent_models: dict[str, tuple[str, str]] = {}
        # optional explicit agent -> (provider, key_id) assignment
        self._agent_keys: dict[str, tuple[str, int]] = {}
        # fallback chain per agent: list of (provider, model)
        self._fallbacks: dict[str, list[tuple[str, str]]] = {}
        self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self) -> None:
        st = get_storage()
        rows = st.query_all("SELECT * FROM providers ORDER BY name;")
        for r in rows:
            p = Provider(name=r["name"], base_url=r["base_url"])
            krows = st.query_all(
                "SELECT * FROM provider_keys WHERE provider=? ORDER BY id;",
                (r["name"],),
            )
            for kr in krows:
                p.keys.append(ProviderKey(
                    id=kr["id"], alias=kr["alias"], status=kr["status"],
                    encrypted_secret=kr["encrypted_secret"] or "",
                    tier=kr["tier"] or "paid",
                ))
            self._providers[p.name] = p
        am = st.query_all("SELECT * FROM agent_models;")
        for r in am:
            self._agent_models[r["agent"]] = (r["provider"], r["model"])
        try:
            ak = st.query_all("SELECT agent, provider, key_id FROM agent_provider_keys;")
        except Exception:
            ak = []
        for r in ak:
            self._agent_keys[r["agent"]] = (r["provider"], int(r["key_id"]))
        fb = st.query_all("SELECT * FROM agent_fallbacks ORDER BY agent, position;")
        for r in fb:
            self._fallbacks.setdefault(r["agent"], []).append((r["provider"], r["model"]))

    # ---- registry ----------------------------------------------------------
    def list_providers(self) -> list[Provider]:
        with self._lock:
            return list(self._providers.values())

    def get_provider(self, name: str) -> Provider | None:
        with self._lock:
            return self._providers.get(name)

    def add_provider(self, name: str, base_url: str) -> Provider:
        with self._lock:
            p = Provider(name=name, base_url=base_url)
            self._providers[name] = p
            st = get_storage()
            with st.transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO providers(name, base_url) VALUES(?,?);",
                    (name, base_url),
                )
        from security.audit import log as _audit
        _audit(actor="user", action="provider:add",
               args={"provider": name, "base_url": base_url})
        return p

    def remove_provider(self, name: str) -> bool:
        with self._lock:
            if name not in self._providers:
                return False
            # capture BEFORE the del: the cached-client eviction needs the
            # base_url of the provider being removed
            base_url = self._providers[name].base_url
            del self._providers[name]
            st = get_storage()
            with st.transaction() as conn:
                conn.execute("DELETE FROM providers WHERE name=?;", (name,))
                conn.execute("DELETE FROM provider_keys WHERE provider=?;", (name,))
                conn.execute("DELETE FROM agent_models WHERE provider=?;", (name,))
                conn.execute("DELETE FROM agent_fallbacks WHERE provider=?;", (name,))
            # evict every cached AsyncOpenAI client for this provider's
            # base_url: remove_key only dropped the one key's entry, so a
            # provider removal left all its other pooled connections alive
            try:
                from providers.client import get_llm_client
                get_llm_client().forget_provider(base_url)
            except Exception:
                pass
        from security.audit import log as _audit
        _audit(actor="user", action="provider:remove", args={"provider": name})
        return True

    # ---- keys --------------------------------------------------------------
    def add_key(self, provider: str, alias: str, encrypted_secret: str,
                tier: str = "paid", status: str = "high") -> ProviderKey:
        with self._lock:
            p = self._providers.get(provider)
            if p is None:
                raise KeyError(f"unknown provider {provider!r}")
            st = get_storage()
            with st.transaction() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(id),0)+1 AS next_id FROM provider_keys WHERE provider=?;",
                    (provider,),
                ).fetchone()
                next_id = int(row["next_id"])
                conn.execute(
                    "INSERT INTO provider_keys(provider,id,alias,status,encrypted_secret,tier) "
                    "VALUES(?,?,?,?,?,?);",
                    (provider, next_id, alias, status, encrypted_secret, tier),
                )
            key = ProviderKey(id=next_id, alias=alias, status=status,
                              encrypted_secret=encrypted_secret, tier=tier)
            p.keys.append(key)
        from security.audit import log as _audit
        _audit(actor="user", action="provider_key:add",
               args={"provider": provider, "alias": alias, "id": next_id, "tier": tier})
        return key

    def remove_key(self, provider: str, key_id: int) -> bool:
        with self._lock:
            p = self._providers.get(provider)
            if p is None:
                return False
            doomed = [k for k in p.keys if k.id == key_id]
            p.keys = [k for k in p.keys if k.id != key_id]
            st = get_storage()
            with st.transaction() as conn:
                conn.execute(
                    "DELETE FROM provider_keys WHERE provider=? AND id=?;",
                    (provider, key_id),
                )
            # evict the cached AsyncOpenAI client for the removed key: its
            # stored secret is gone, and a pooled connection authenticated
            # with a deleted credential must not serve the next call.
            # _clients is keyed by (base_url, alias), so resolve the URL.
            for k in doomed:
                try:
                    from providers.client import get_llm_client
                    get_llm_client().evict_client(p.base_url, k.alias)
                except Exception:
                    pass
            return True

    # ---- agent -> model assignment ----------------------------------------
    def set_agent_model(self, agent: str, provider: str, model: str) -> None:
        with self._lock:
            self._agent_models[agent] = (provider, model)
            st = get_storage()
            with st.transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_models(agent,provider,model) VALUES(?,?,?);",
                    (agent, provider, model),
                )

    def get_agent_model(self, agent: str) -> tuple[str, str] | None:
        with self._lock:
            return self._agent_models.get(agent)

    def set_agent_key(self, agent: str, provider: str, key_id: int) -> None:
        """Pin one agent to one provider key while preserving model assignment."""
        with self._lock:
            if self._providers.get(provider) is None:
                raise KeyError(f"unknown provider {provider!r}")
            if self._key(provider, int(key_id)) is None:
                raise KeyError(f"unknown key {provider!r}:{key_id}")
            key = (provider, int(key_id))
            self._agent_keys[agent] = key
            st = get_storage()
            with st.transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_provider_keys(agent,provider,key_id) VALUES(?,?,?);",
                    (agent, provider, int(key_id)),
                )

    def clear_agent_key(self, agent: str) -> None:
        with self._lock:
            self._agent_keys.pop(agent, None)
            st = get_storage()
            with st.transaction() as conn:
                conn.execute("DELETE FROM agent_provider_keys WHERE agent=?;", (agent,))

    def get_agent_key(self, agent: str) -> tuple[str, int] | None:
        with self._lock:
            return self._agent_keys.get(agent)

    def set_fallback_chain(self, agent: str, chain: list[tuple[str, str]]) -> None:
        with self._lock:
            self._fallbacks[agent] = list(chain)
            st = get_storage()
            with st.transaction() as conn:
                conn.execute("DELETE FROM agent_fallbacks WHERE agent=?;", (agent,))
                for pos, (prov, model) in enumerate(chain):
                    conn.execute(
                        "INSERT INTO agent_fallbacks(agent,position,provider,model) VALUES(?,?,?,?);",
                        (agent, pos, prov, model),
                    )

    def get_fallback_chain(self, agent: str) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._fallbacks.get(agent, []))

    # ---- rotation ----------------------------------------------------------
    def pick_key(self, provider: str, agent: str | None = None) -> ProviderKey | None:
        """Pick the best key for a provider. Highest-status healthy key wins;
        a provider's ONLY key is never excluded for error-rate. Returns None
        if the provider has no keys (caller should fall back to another
        provider, or report that no provider is configured).

        Honesty rule: a provider's ONLY key is never excluded for error-rate
        — excluding it turned every later agent into "no key for X" while the
        key existed all along. Error-rate only RANKS keys when there are
        alternatives to rank."""
        with self._lock:
            p = self._providers.get(provider)
            if not p or not p.keys:
                return None
            if agent:
                assigned = self._agent_keys.get(agent)
                if assigned and assigned[0] == provider:
                    assigned_key = next((k for k in p.keys if k.id == assigned[1]), None)
                    # a stale per-agent assignment (key removed) must not
                    # doom the agent: fall through to the general picker
                    if assigned_key is not None:
                        self._trim_health(assigned_key, time.time())
                        return assigned_key
            status_rank = {"high": 3, "medium": 2, "low": 1}
            now = time.time()
            healthy: list[ProviderKey] = []
            for k in p.keys:
                self._trim_health(k, now)
                if _is_disabled(k):
                    continue
                healthy.append(k)
            # no healthy candidate: use the sole/first key anyway rather
            # than report a phantom "no key" to the user
            pool = healthy or list(p.keys)
            # rank on the DERIVED status, not the stored one: a key whose
            # window has one error ranks below a clean key with the same
            # stored "high" label
            scored = sorted(pool,
                            key=lambda k: status_rank.get(_effective_status(k), 0),
                            reverse=True)
            return scored[0] if scored else None

    def list_keys(self, provider: str, agent: str | None = None) -> list[ProviderKey]:
        """ALL keys for a provider, best-first (§15 key rotation).

        complete() must try every key of a provider before moving to the
        next provider in the chain — using only pick_key() abandons a
        provider that has N-1 healthy keys after a single rate-limit hit.
        The per-agent pinned key (if any) leads; the rest follow the same
        healthy-first ordering pick_key uses."""
        with self._lock:
            p = self._providers.get(provider)
            if not p or not p.keys:
                return []
            now = time.time()
            ordered: list[ProviderKey] = []
            if agent:
                assigned = self._agent_keys.get(agent)
                if assigned and assigned[0] == provider:
                    pinned = next((k for k in p.keys if k.id == assigned[1]), None)
                    if pinned is not None:
                        self._trim_health(pinned, now)
                        ordered.append(pinned)
            status_rank = {"high": 3, "medium": 2, "low": 1}
            rest = [k for k in p.keys if k not in ordered]
            healthy = [k for k in rest if not _is_disabled(k)]
            pool_tail = [k for k in rest if k not in healthy]
            ordered.extend(sorted(healthy,
                                  key=lambda k: status_rank.get(_effective_status(k), 0),
                                  reverse=True))
            ordered.extend(sorted(pool_tail,
                                  key=lambda k: status_rank.get(_effective_status(k), 0),
                                  reverse=True))
            return ordered

    def _chain_unlocked(self, agent: str) -> list[tuple[str, str]]:
        """Routing chain for an agent WITHOUT taking the lock (callers hold
        it). Pinned model first, then configured fallbacks.

        The keyed-provider EMERGENCY TAIL only joins when the agent has NO
        routing at all (agent_models wiped — the production incident that
        made a keyed provider report "no provider"). When a primary exists
        it is the user's explicit choice: silently substituting another
        provider's catalog defaults hid the real failure (their provider's
        error reported as a stranger's 410) and could route to models the
        user never picked. Routing loss degrades to a default; routing
        present honors only what was configured."""
        chain: list[tuple[str, str]] = []
        primary = self._agent_models.get(agent)
        if primary:
            chain.append(primary)
            chain.extend(self._fallbacks.get(agent, []))
        else:
            for prov, p in self._providers.items():
                if not p.keys:
                    continue
                preset = BY_NAME.get(prov)
                if preset is not None and preset.models:
                    chain.append((prov, preset.models[0]))
        # GAX_OFFLINE (audit 2026-09): the flag was parsed into
        # UniverseConfig.offline and documented as "forces zero network —
        # all LLM calls routed to a local model", but no code ever read
        # it. Enforce it here at the single routing chokepoint: only
        # loopback providers (Ollama / LM Studio / vLLM) may stay in the
        # chain; everything else is dropped so the call physically cannot
        # leave the machine.
        try:
            from config import get_config
            if getattr(get_config(), "offline", False):
                local_only = [(prov, model) for prov, model in chain
                              if self._is_local_provider(prov)]
                return local_only
        except Exception:
            pass
        return chain

    def _is_local_provider(self, prov: str) -> bool:
        from urllib.parse import urlparse
        p = None
        with self._lock:
            p = self._providers.get(prov)
        host = ""
        if p is not None:
            try:
                host = (urlparse(p.base_url).hostname or "").lower()
            except Exception:
                host = ""
        return host in ("localhost", "127.0.0.1", "::1")

    def chain_for(self, agent: str) -> list[tuple[str, str]]:
        """Public view of the routing chain (see _chain_unlocked)."""
        with self._lock:
            return self._chain_unlocked(agent)

    def best_available(self, agent: str) -> tuple[str, str, ProviderKey | None] | None:
        """Resolve (provider, model, key) for an agent, walking the fallback
        chain if the primary has no key. Returns None when no configured
        provider can serve this agent — callers must surface that as an
        explicit setup error, never paper over it."""
        with self._lock:
            for prov, model in self._chain_unlocked(agent):
                p = self._providers.get(prov)
                if p is None:
                    continue
                key = self.pick_key(prov, agent=agent)
                if key is not None:
                    return (prov, model, key)
            return None

    def _trim_health(self, key: ProviderKey, now: float) -> None:
        key.error_count_5min = [t for t in key.error_count_5min if now - t < 300]
        key.request_count_5min = [t for t in key.request_count_5min if now - t < 300]
        key.latencies_5min = [(t, ms) for t, ms in key.latencies_5min if now - t < 300]

    def _key(self, provider: str, key_id: int) -> ProviderKey | None:
        p = self._providers.get(provider)
        if not p:
            return None
        return next((key for key in p.keys if key.id == key_id), None)

    def record_key_error(self, provider: str, key_id: int) -> None:
        with self._lock:
            key = self._key(provider, key_id)
            if key is None:
                return
            now = time.time()
            self._trim_health(key, now)
            key.last_error_ts = now
            key.error_count_5min.append(now)
            key.request_count_5min.append(now)
            # write the derived tier back so the stored status reflects the
            # key's recent behavior (pick_key/list_keys read this when the
            # window is too small to derive from)
            key.status = _effective_status(key)

    def record_key_success(self, provider: str, key_id: int, latency_ms: int) -> None:
        with self._lock:
            key = self._key(provider, key_id)
            if key is None:
                return
            now = time.time()
            self._trim_health(key, now)
            key.request_count_5min.append(now)
            key.latencies_5min.append((now, int(latency_ms)))
            key.status = _effective_status(key)

    def health_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            now = time.time()
            rows: list[dict[str, Any]] = []
            for provider in self._providers.values():
                for key in provider.keys:
                    self._trim_health(key, now)
                    total = len(key.request_count_5min)
                    errors = len(key.error_count_5min)
                    latencies = sorted(ms for _, ms in key.latencies_5min)
                    rows.append({"provider": provider.name, "key": key.alias,
                                 "requests_5m": total, "errors_5m": errors,
                                 "error_rate": errors / total if total else 0.0,
                                 "p50_latency_ms": latencies[len(latencies) // 2] if latencies else 0,
                                 "disabled": _is_disabled(key)})
            return rows

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "providers": {n: p.to_dict() for n, p in self._providers.items()},
                "agent_models": {a: list(m) for a, m in self._agent_models.items()},
                "fallbacks": {a: [list(x) for x in c] for a, c in self._fallbacks.items()},
                "agent_keys": {a: [p, k] for a, (p, k) in self._agent_keys.items()},
            }


_pm: ProviderManager | None = None
_pm_lock = threading.Lock()


def get_provider_manager() -> ProviderManager:
    global _pm
    if _pm is None:
        with _pm_lock:
            if _pm is None:
                _pm = ProviderManager()
    return _pm


def reset_provider_manager_for_tests() -> ProviderManager:
    global _pm
    with _pm_lock:
        _pm = ProviderManager()
    return _pm
