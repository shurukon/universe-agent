"""v18: S1 swarm registry — agent name migration.

The 12-specialist fixed ladder retired: Universe Agent is now the general
agent (+ research/code/review/security/design subagents). This migration
moves durable rows that still point at the retired agents:

  skills.target_agent / skill_agent_ownership.agent
      planning, write, file, api, data, devops -> general
      browser -> research
  agent_models / agent_fallbacks
      the user's 'orchestrator' routing rows are copied to 'general'
      (the new LLM billing label) — the original rows stay for compat.

Search-time aliasing (skill.taxonomy.LEGACY_AGENT_ALIASES + the L4 search
resolution) already tolerates old names for re-ingested data; this migration
makes the durable state agree.
"""
from schema.migrations import register


@register(18, "swarm registry: legacy agent names -> general/research + orchestrator routing copy")
def upgrade(st) -> None:
    # skills: retired ladder agents -> their heirs
    st.execute(
        "UPDATE skills SET target_agent='general' WHERE target_agent IN "
        "('planning','write','file','api','data','devops');"
    )
    st.execute(
        "UPDATE skills SET target_agent='research' WHERE target_agent='browser';"
    )
    st.execute(
        "UPDATE skill_agent_ownership SET agent='general' WHERE agent IN "
        "('planning','write','file','api','data','devops');"
    )
    st.execute(
        "UPDATE skill_agent_ownership SET agent='research' WHERE agent='browser';"
    )
    # LLM routing: copy the user's orchestrator rows to the general agent
    st.execute(
        "INSERT OR IGNORE INTO agent_models(agent, provider, model) "
        "SELECT 'general', provider, model FROM agent_models "
        "WHERE agent='orchestrator';"
    )
    st.execute(
        "INSERT OR IGNORE INTO agent_fallbacks(agent, position, provider, model) "
        "SELECT 'general', position, provider, model FROM agent_fallbacks "
        "WHERE agent='orchestrator';"
    )
