"""context/file_select.py — BM25 file ranking (§13).

For any tool call that needs file context: score every candidate file by
BM25(task description + path + recent symbols) + recency + interaction count,
take the top-K (K scales with remaining context budget, default 20).
"""
from __future__ import annotations

import math
import time
from typing import Any

from rank_bm25 import BM25Okapi
import re


def _tok(t: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", (t or "").lower())


def select_files(query: str, files: list[dict[str, Any]], *,
                 top_k: int = 20) -> list[dict[str, Any]]:
    """Score files by BM25(query, path+symbols) + recency + interactions."""
    if not files:
        return []
    corpus = [_tok(f.get("path", "") + " " + " ".join(f.get("symbols", []))) for f in files]
    try:
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(_tok(query))
    except (ZeroDivisionError, ValueError):
        # an empty/unrankable corpus (paths with no alphanumerics — e.g.
        # CJK-only filenames under the [a-z0-9_] tokenizer — or zero
        # documents) zero-divides inside rank_bm25. BM25 then contributes
        # nothing: rank by the recency/interaction signals alone instead
        # of crashing the tool call.
        scores = [0.0] * len(files)
    now = time.time()
    ranked = []
    for f, s in zip(files, scores):
        recency = math.exp(-max(0.0, now - f.get("mtime", 0)) / (30 * 86400))
        interactions = min(1.0, f.get("interaction_count", 0) / 10.0)
        score = float(s) + recency * 0.5 + interactions * 0.3
        ranked.append((score, f))
    ranked.sort(key=lambda t: t[0], reverse=True)
    return [f for _, f in ranked[:top_k]]
