"""Stubbed Skill tool — catalog purged 2026-10-31, 0 skills."""
from __future__ import annotations

from typing import Any
from core.agent.base_agent import Tool

def register(reg) -> None:
    def skill_search(query: str = "", category: str = "", domain: str = "", target_agent: str = "", solar_system: str = "", orbit: str = "", top_k: int = 5) -> dict[str, Any]:
        return {"ok": True, "count": 0, "skills": []}
    def skill_read(skill_id: str = "", skill_name: str = "") -> dict[str, Any]:
        return {"ok": False, "error": "skills are disabled — no trusted corpus", "skill_id": skill_id, "skill_name": skill_name, "suggestions": []}
    def skill_activate(skill_id: str = "", skill_name: str = "", agent: str = "", turn_id: str = "", outcome: str = "success") -> dict[str, Any]:
        return {"ok": False, "error": "skills are disabled — activation not available", "skill_id": skill_id, "skill_name": skill_name, "status": "disabled"}
    def skill(action: str = "", query: str = "", category: str = "", domain: str = "", target_agent: str = "", solar_system: str = "", orbit: str = "", top_k: int = 5, skill_id: str = "", skill_name: str = "", outcome: str = "success", agent: str = "", turn_id: str = "") -> dict[str, Any]:
        act = str(action or "").strip().lower()
        if not act:
            act = "read" if (skill_id or skill_name) and not query else "search"
        if act == "activate":
            return skill_activate(skill_id=skill_id, skill_name=skill_name, agent=agent, turn_id=turn_id, outcome=outcome)
        if act == "read":
            return skill_read(skill_id=skill_id, skill_name=skill_name)
        return skill_search(query=query, category=category, domain=domain, target_agent=target_agent, solar_system=solar_system, orbit=orbit, top_k=top_k)
    reg.register(Tool(name="Skill", capability="memory.read", description="Skills are disabled (search returns empty, read/activate refuse) — corpus purged 2026-10-31.", handler=skill, consent="auto", resources=["universe:skills"]))
