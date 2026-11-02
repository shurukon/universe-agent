"""connectors/builtin/memory.py — memory_query tool (always allowed; Universe's own DB)."""
from __future__ import annotations

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry


def memory_query(query: str = "", layer: str = "l3", top_k: int = 5,
                 text: str = "", q: str = "") -> dict:
    """Query Universe memory with tolerant aliases for OpenAI-compatible models."""
    query = str(query or text or q or "recent relevant memory")
    try:
        top_k = max(1, min(20, int(top_k)))
    except (TypeError, ValueError):
        top_k = 5
    layer = str(layer or "l3").lower()
    from core.memory import get_memory
    mem = get_memory()
    if layer == "l4":
        hits = mem.search_l4(query, top_k=top_k)
        return {"ok": True, "layer": "l4", "results": [s.to_dict() for s in hits]}
    if layer == "l2":
        recent = mem.l2.list_recent(limit=top_k)
        return {"ok": True, "layer": "l2", "results": [a.to_dict() for a in recent]}
    hits = mem.search_l3(query, top_k=top_k)
    return {"ok": True, "layer": "l3", "results": [s.to_dict() for s in hits]}


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(name="MemoryQuery", capability="memory.read",
                      description="Query Universe's L2/L3/L4 memory",
                      handler=memory_query, consent="auto",
                      resources=["universe:memory"]))
