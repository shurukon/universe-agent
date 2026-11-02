"""core/core_agents/__init__.py — the UNIVERSE single-agent architecture.

The universe agent is the sole doer owning every tool; all historical
specialists have been removed (2026-10-31). No swarm subagents remain.
"""
from .agents import UniverseAgent, ALL_AGENTS, get_agent, get_all_agents

__all__ = ["UniverseAgent", "ALL_AGENTS", "get_agent", "get_all_agents"]
