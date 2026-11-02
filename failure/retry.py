"""failure/retry.py — cascading-failure detection (§15).

The recovery-strategy table (key rotation, provider fallbacks, context
compaction) lives where it actually runs: providers/client.py::complete
and core/agent/base_agent.py's tool loop. The generic retry_with_backoff
wrapper that mirrored §15 but had zero callers was removed in m2 — dead
policy tables that nothing executes are worse than no table.

HONEST STATUS (engine-fix round): cascading_failure_check itself is an
OPTIONAL utility, currently unwired — its only callers are tests
(tests/test_integration.py). Kept for §15 parity; the live ladder handles
transient kinds via TRANSIENT_KINDS in providers/client.py instead.
"""
from __future__ import annotations


def cascading_failure_check(recent_errors: list[str]) -> bool:
    """If 3 consecutive steps failed with similar errors, pause (§15)."""
    if len(recent_errors) < 3:
        return False
    last3 = recent_errors[-3:]
    # "similar" = same error class substring
    return any(e in last3[0] and e in last3[1] and e in last3[2]
               for e in ["rate_limit", "timeout", "db_locked", "provider_down"])
