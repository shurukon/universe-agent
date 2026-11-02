"""Operational metrics rendered from persisted Universe telemetry."""
from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from config import get_config
from providers.manager import get_provider_manager
from storage.local import get_storage


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((percentile / 100) * (len(values) - 1)))))
    return values[index]


def _jsonl(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text("utf-8").splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def render_metrics(io) -> str:
    st = get_storage()
    now = time.time()
    day_ago = now - 86400
    goals = st.query_all("SELECT * FROM goals ORDER BY created_at DESC;")
    total = len(goals)
    recent = [row for row in goals if float(row.get("created_at", 0)) >= day_ago]
    succeeded = sum(row.get("status") == "completed" for row in goals)
    failed = sum(row.get("status") == "failed" for row in goals)
    durations = [max(0.0, float(row.get("completed_at", 0)) - float(row.get("created_at", 0)))
                 for row in goals if row.get("completed_at")]
    io.print("=== Metrics ===")
    io.print(f"Tasks: {total} total ({len(recent)} last 24h)")
    io.print(f"Success rate: {(succeeded / total * 100) if total else 0:.1f}% ({succeeded} ok / {failed} failed)")
    io.print(f"Goal duration: p50={_percentile(durations, 50):.3f}s p95={_percentile(durations, 95):.3f}s")
    ast = st.query_one("SELECT COUNT(*) AS c, AVG(gravity_score) AS g FROM asteroids;") or {"c": 0, "g": 0}
    stars = st.query_one("SELECT COUNT(*) AS c FROM stars;") or {"c": 0}
    skills = st.query_one("SELECT COUNT(*) AS c FROM skills WHERE status='trusted';") or {"c": 0}
    io.print(f"Asteroids: {ast['c']} avg gravity: {float(ast.get('g') or 0):.2f}")
    io.print(f"Stars: {stars['c']} | Trusted skills: {skills['c']}")
    domain_rows = st.query_all(
        "SELECT domain, COUNT(*) AS n, SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS fails "
        "FROM task_outcomes GROUP BY domain ORDER BY n DESC LIMIT 5;")
    if domain_rows:
        io.print("Top domains:")
        for row in domain_rows:
            n = int(row["n"])
            io.print(f"  {row['domain']:20s} {n:4d} tasks  {(int(row['fails']) / n * 100) if n else 0:.1f}% error")
    handoff_rows = st.query_all("SELECT from_agent, COUNT(*) AS n FROM handoffs GROUP BY from_agent ORDER BY n DESC;")
    if handoff_rows:
        io.print("Agent throughput: " + ", ".join(f"{r['from_agent']}={r['n']}" for r in handoff_rows))
    tool_counts: Counter[str] = Counter()
    audit_rows = _jsonl(get_config().audit_log)
    error_counts: Counter[str] = Counter()
    for row in audit_rows:
        action = str(row.get("action", ""))
        result = str(row.get("result", ""))
        if action.startswith("tool:"):
            tool_counts[action.removeprefix("tool:")] += 1
        if result.startswith("error:") or result.startswith("blocked:"):
            error_counts[result.split(":", 1)[0]] += 1
    if tool_counts:
        io.print("Tool calls: " + ", ".join(f"{name}={count}" for name, count in tool_counts.most_common()))
    if error_counts:
        io.print("Top errors: " + ", ".join(f"{name}={count}" for name, count in error_counts.most_common(5)))
    llm_rows = _jsonl(get_config().llm_log)
    llm_latencies = [float(row.get("latency_ms", 0)) for row in llm_rows]
    io.print(f"LLM calls: {len(llm_rows)} p50={_percentile(llm_latencies, 50):.0f}ms p95={_percentile(llm_latencies, 95):.0f}ms")
    if llm_rows:
        providers = Counter(str(row.get("provider", "?")) for row in llm_rows)
        io.print("LLM by provider: " + ", ".join(f"{k}={v}" for k, v in providers.most_common()))
    health = get_provider_manager().health_snapshot()
    if health:
        io.print("Provider health:")
        for row in health:
            io.print(f"  {row['provider']} / {row['key']}: requests={row['requests_5m']} errors={row['errors_5m']} "
                     f"rate={row['error_rate'] * 100:.1f}% p50={row['p50_latency_ms']}ms disabled={row['disabled']}")
    io.print(f"Audit entries: {len(audit_rows)}")
    return "Metrics rendered."
