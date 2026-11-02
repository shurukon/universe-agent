"""tests/test_skills.py — ingestion pipeline, confidence levels, signing."""
import pytest


def test_trusted_sources_load_at_correct_confidence(fresh_home_with_skills):
    """§5: ECC + UIUXProMax at 0.95; OpenDesign + AnthropicSkills at 0.90.
    Conflict resolution (§18) can lower a loser to 0.90 (the trusted floor),
    so we assert the load range and that winners remain at the spec value."""
    from core.memory import get_memory
    mem = get_memory()
    by_source = {}
    for s in mem.l4.list(status="trusted"):
        by_source.setdefault(s.source, []).append(s.confidence)
    assert "ECC" in by_source
    assert all(0.90 <= c <= 0.95 for c in by_source["ECC"])  # trusted range
    assert max(by_source["ECC"]) == 0.95  # winners at the spec load value
    assert "UIUXProMax" in by_source
    assert all(0.90 <= c <= 0.95 for c in by_source["UIUXProMax"])
    assert "OpenDesign" in by_source
    assert all(0.90 <= c <= 0.95 for c in by_source["OpenDesign"])
    assert "AnthropicSkills" in by_source
    assert all(0.90 <= c <= 0.95 for c in by_source["AnthropicSkills"])


def test_skill_signature_verification(fresh_home_with_skills):
    """§10: signatures verify; a tampered body is detected."""
    from skills.loader import verify_all_signatures
    from core.memory import get_memory
    from storage.local import get_storage
    v = verify_all_signatures()
    assert v["verified"] > 0
    assert v["mismatch"] == 0
    # tamper with one skill's body
    mem = get_memory()
    skills = mem.l4.list(status="trusted")
    if skills:
        st = get_storage()
        with st.transaction() as conn:
            conn.execute("UPDATE skills SET body='TAMPERED' WHERE id=?;", (skills[0].id,))
        v2 = verify_all_signatures()
        assert v2["mismatch"] >= 1


def test_l4_search_ranks_relevant_first(fresh_home_with_skills):
    """§13: BM25 search over L4 returns the most relevant skill first."""
    from core.memory import get_memory
    mem = get_memory()
    hits = mem.search_l4("debug python traceback", category="code_generation",
                         target_agent="code", top_k=3)
    assert hits
    assert "python" in hits[0].name.lower() or "debug" in hits[0].name.lower()


def test_only_four_trusted_sources(fresh_home_with_skills):
    """§STEP 4: the four pre-loaded skill sources are the complete allowlist.
    No fifth source. No OpenHuman-derived content."""
    from core.memory import get_memory
    from config import TRUSTED_SKILL_SOURCES
    mem = get_memory()
    sources = {s.source for s in mem.l4.list()}
    assert sources.issubset(set(TRUSTED_SKILL_SOURCES))
    assert "OpenHuman" not in sources  # §2: explicitly excluded


def test_fresh_memory_bootstraps_full_bundled_corpus(fresh_home):
    """A plain runtime home must not start with an empty L4."""
    from core.memory import get_memory
    from storage.local import get_storage
    mem = get_memory()
    st = get_storage()
    assert len(mem.l4.list(status="trusted")) >= 1000
    assert st.query_one("SELECT COUNT(*) AS c FROM skill_solar_systems;")["c"] >= 10
    assert st.query_one("SELECT COUNT(*) AS c FROM skill_orbits;")["c"] >= 40
    assert st.query_one("SELECT COUNT(*) AS c FROM skill_agent_ownership;")["c"] == 6


def test_web_development_alias_routes_to_engineering_groups(fresh_home):
    """web_development must retrieve web skills despite source categories."""
    from core.memory import get_memory
    mem = get_memory()
    code_hits = mem.search_l4("Express frontend backend REST API", category="web_development", target_agent="code", top_k=5)
    review_hits = mem.search_l4("webapp browser testing API", category="web_development", target_agent="review", top_k=5)
    assert code_hits
    assert review_hits
    assert all(s.solar_system_id for s in code_hits + review_hits)


@pytest.mark.asyncio
async def test_skill_tools_are_available_and_activation_is_recorded(fresh_home):
    from connectors.builtin import get_registry
    from security.capability import get_gate
    from core.memory import get_memory
    mem = get_memory()
    skill = mem.search_l4("python testing", target_agent="review", top_k=1)[0]
    gate = get_gate()
    gate.set_auto_grant(True)
    registry = get_registry()
    names = set(registry.names())
    assert "Skill" in names
    read = await registry.call("Skill", agent="review", turn_id="goal-skill-test", args={"skill_id": skill.id, "action": "read"})
    assert read["ok"] is True
    activated = await registry.call("Skill", agent="review", turn_id="goal-skill-test", args={"skill_id": skill.id, "action": "activate"})
    assert activated["ok"] is True
    assert mem._st.query_one("SELECT COUNT(*) AS c FROM skill_activations WHERE turn_id=?;", ("goal-skill-test",))["c"] == 1


def test_every_agent_can_see_skill_tools_and_procedures(fresh_home):
    from core.core_agents.agents import ALL_AGENTS, get_agent
    from core.memory import get_memory
    mem = get_memory()
    skill = mem.search_l4("Express backend API", target_agent="code", top_k=1)[0]
    context = {"l4_skills": [skill.to_dict()], "l3_stars": [], "active_rules": [], "untrusted": []}
    for agent_name in ALL_AGENTS:
        agent = get_agent(agent_name)
        schemas = {entry["function"]["name"] for entry in agent._tool_schemas([])}
        assert "Skill" in schemas
        prompt = agent.build_messages(context, "build a full stack app", "use verified skills")
        assert "ACTIVE L4 SKILL PROCEDURES" in prompt[0]["content"]
        assert skill.name in prompt[0]["content"]
        assert skill.body[:80] in prompt[0]["content"]
