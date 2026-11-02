"""eval/runner.py — /eval command (§16).

Runs the v1 smoke suite against the live system and reports real pass/fail
numbers per category. /eval --compare shows the delta vs the last run.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path

from config import get_config
from eval.suite import get_suite


async def run_eval(io, *, compare: bool = False) -> str:
    suite = get_suite()
    io.print(f"Running v1 eval suite ({len(suite)} tasks)...\n")
    results: list[dict] = []
    by_category: dict[str, list[bool]] = defaultdict(list)

    for task in suite:
        io.print(f"  [{task.id}] {task.description}...")
        try:
            summary = await _run_one(task)
            passed, reason = task.rubric(summary)
        except Exception as e:
            passed, reason = False, f"error: {e}"
            summary = {}
        results.append({
            "id": task.id, "category": task.category, "passed": passed,
            "reason": reason, "gravity": summary.get("gravity_score", 0),
            "agents": [s["agent"] for s in summary.get("steps", [])],
            "success": summary.get("success", False),
        })
        by_category[task.category].append(passed)
        mark = "PASS" if passed else "FAIL"
        io.print(f"      {mark} — {reason}")

    # summary
    io.print("\n" + "=" * 50)
    io.print("Eval Summary by Category:")
    total_pass = 0
    total = 0
    prev = _load_prev()
    for cat, passes in sorted(by_category.items()):
        p = sum(passes)
        n = len(passes)
        total_pass += p
        total += n
        delta = ""
        if compare and cat in prev:
            prev_p = prev[cat]
            d = p - prev_p
            delta = f"  ({'+' if d>=0 else ''}{d})" if d != 0 else "  (=)"
        io.print(f"  {cat:24s} {p}/{n}{delta}")
    io.print(f"  {'TOTAL':24s} {total_pass}/{total}")
    io.print("=" * 50)

    _save_prev({cat: sum(p) for cat, p in by_category.items()})
    return f"Eval complete: {total_pass}/{total} passed."


async def _run_one(task) -> dict:
    """Run one eval task against the orchestrator. Each task gets a fresh
    planet/memory context so results are independent. The task's
    time_budget_s is enforced with asyncio.timeout — a stuck goal can no
    longer hang the whole suite (and every other task's budget with it)."""
    from core.agent.orchestrator import get_orchestrator
    orch = get_orchestrator()
    async with asyncio.timeout(task.time_budget_s):
        summary = await orch.run_turn(task.goal, language="en")
    return summary


def _prev_path() -> Path:
    return get_config().eval_history


def _load_prev() -> dict:
    p = _prev_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def _save_prev(data: dict) -> None:
    p = _prev_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), "utf-8")
