"""core/memory/galactic_core.py — Gravity Score + provenance (reflection engine).

§3, §25 Phase 2 ⑧. The v0 bug (gravity stuck near 0.40) is fixed because
Confidence Accumulation is computed from REAL decision_confidence /
is_knowledge_based data agents are required to report — not a hardcoded flat
value. The Subconscious Loop independently re-evaluates and promotes asteroids
at gravity >= 0.45 during idle time (see core/memory/subconscious/loop.py), so
promotion isn't solely dependent on real-time scoring.

Formula (exact, §3):
    Gravity = (Confidence Accumulation × 0.6) + (LLM Progressive Analysis × 0.4)
    Confidence Accumulation = avg(decision_confidence) × knowledge_based_ratio

Thresholds (§3): 0–0.30 Nebula | 0.30–0.60 Asteroid | 0.60–0.85 Planet→L3 |
0.85+ Star→L3 permanent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from config import GRAVITY_THRESHOLDS


@dataclass
class GravityProvenance:
    """Every score carries a provenance blob (§3) recording contributors."""
    decision_confidences: list[float] = field(default_factory=list)
    knowledge_based_flags: list[bool] = field(default_factory=list)
    llm_progressive_analysis: float = 0.5
    agents: list[str] = field(default_factory=list)
    computed_at: float = 0.0
    formula: str = "(conf_acc * 0.6) + (llm_prog * 0.4)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_confidences": self.decision_confidences,
            "knowledge_based_flags": self.knowledge_based_flags,
            "llm_progressive_analysis": self.llm_progressive_analysis,
            "agents": self.agents,
            "computed_at": self.computed_at,
            "formula": self.formula,
        }


def compute_gravity(handoffs: list[dict[str, Any]]) -> tuple[float, GravityProvenance]:
    """Compute the Gravity Score from a list of handoff-package dicts.

    handoffs: list of HandoffPackage.model_dump() from every agent step in the
    goal. Must contain decision_confidence (float) and is_knowledge_based (bool).
    """
    confs = [float(h.get("decision_confidence", 0.5)) for h in handoffs if h]
    kb_flags = [bool(h.get("is_knowledge_based", False)) for h in handoffs if h]
    agents = [str(h.get("agent", "")) for h in handoffs if h]

    if confs:
        avg_conf = sum(confs) / len(confs)
    else:
        avg_conf = 0.5
    if kb_flags:
        kb_ratio = sum(1 for f in kb_flags if f) / len(kb_flags)
    else:
        kb_ratio = 0.0

    confidence_accumulation = avg_conf * kb_ratio

    # LLM Progressive Analysis: embedded in the Orchestrator's reasoning. We
    # approximate it from the spread of confidences + success ratio — a real
    # run passes the Orchestrator's own assessment via the 'llm_analysis'
    # field of the final handoff; this default keeps the score meaningful when
    # that isn't supplied.
    successes = [1.0 if h.get("task_success", True) else 0.0 for h in handoffs if h]
    success_ratio = (sum(successes) / len(successes)) if successes else 0.5
    llm_prog = handoffs[-1].get("llm_analysis", success_ratio) if handoffs else success_ratio
    llm_prog = float(llm_prog)

    gravity = (confidence_accumulation * 0.6) + (llm_prog * 0.4)
    gravity = max(0.0, min(1.0, gravity))

    prov = GravityProvenance(
        decision_confidences=confs,
        knowledge_based_flags=kb_flags,
        llm_progressive_analysis=llm_prog,
        agents=agents,
        computed_at=time.time(),
    )
    return gravity, prov


def classify_gravity(gravity: float) -> str:
    """Return the lifecycle bucket per §3 thresholds."""
    if gravity < GRAVITY_THRESHOLDS["nebula"]:
        return "nebula"        # low value — candidate for forgetting
    if gravity < GRAVITY_THRESHOLDS["asteroid"]:
        return "asteroid"      # kept as asteroid
    if gravity < GRAVITY_THRESHOLDS["planet_to_l3"]:
        return "planet_to_l3"  # promote to L3
    return "star_permanent"    # permanent L3 Star


def should_promote(gravity: float) -> bool:
    """Real-time promotion threshold (§3): 0.85+. The old
    `>= GRAVITY_THRESHOLDS["asteroid"]` (0.60) let a routine successful goal
    (gravity ~0.79) mint a permanent L3 star every time — stars grew without
    bound and nothing ever deleted one (audit 2026-09). 0.85 matches the
    README's documented star bar; the Subconscious Loop's independent
    idle-time pass still promotes at 0.45."""
    return gravity >= GRAVITY_THRESHOLDS["planet_to_l3"]
