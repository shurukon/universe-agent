"""resources/router.py — tier → default model resolution (A19).

The per-task-type routing table was retired: nothing dispatched on task
type in practice (every configured agent gets an explicit model pin at
provider-install time; audit 2026-09). What survives is the tiering the
provider layer actually resolves.
"""
from __future__ import annotations



# tier -> default model name per provider (when user hasn't pinned). The
# retired ids (o1, gemini-1.5-pro, claude-haiku/sonnet/opus unprefixed) were
# unreachable in practice — every configured agent gets an explicit pin at
# provider-install time — but a lost routing row degrading here deserves
# current names (audit 2026-09).
TIER_DEFAULTS: dict[str, dict[str, str]] = {
    "cheap": {"OpenAI": "gpt-5-mini", "Anthropic": "claude-haiku-4-5-20251001"},
    "mid":   {"OpenAI": "gpt-5", "Anthropic": "claude-sonnet-5"},
    "expensive": {"OpenAI": "gpt-5", "Anthropic": "claude-opus-5-5"},
    "long_context": {"Google AI Studio": "gemini-3-pro-preview"},
}


def model_for_tier(tier: str, provider: str) -> str | None:
    return TIER_DEFAULTS.get(tier, {}).get(provider)
