"""Shim — use core.agent.universe_agent directly."""
from core.agent.universe_agent import UniverseAgent, get_universe_agent, reset_universe_agent_for_tests
Orchestrator = UniverseAgent
get_orchestrator = get_universe_agent
reset_orchestrator_for_tests = reset_universe_agent_for_tests
