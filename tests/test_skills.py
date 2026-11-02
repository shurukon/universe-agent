# Adapted for the 2026-10-31 skills purge: the bundled 1,584-skill library
# was removed by product order (skills/loader.py is an empty-catalog stub,
# config.TRUSTED_SKILL_SOURCES == ()). These tests now seed a small corpus
# through the product's own ingestion pipeline (the fresh_home_with_skills
# fixture in conftest.py) and pin the tier/signature/search machinery that
# is still live in skill/ingestion.py, skill/signing.py, skill/taxonomy.py
# and core/memory/layers/l4_procedural.py — plus the honest empty-catalog
# boot contract. The security semantics (signatures, quarantine, the v20
# toggle) are kept meaningful; no fake 1,000-skill library is recreated.
"""tests/test_skills.py — ingestion pipeline, confidence levels, signing."""
import pytest


def test_trusted_sources_load_at_correct_confidence(fresh_home_with_skills):
    """§5 tiers still hold for allowlisted sources: ECC + UIUXProMax at
    0.95, OpenDesign + AnthropicSkills at 0.90. (The allowlist itself is
    the fixture's test allowlist; the production tuple is empty post-purge.)"""
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
    """§10: stored signatures verify; a tampered body is detected. (The
    loader's verify_all_signatures() is an empty-catalog stub since the
    purge — the signing machinery itself is live in skill.signing and is
    verified directly here.)"""
    from skill.signing import sign_skill
    from core.memory import get_memory
    from storage.local import get_storage
    mem = get_memory()
    skills = mem.l4.list(status="trusted")
    assert skills, "the seeded corpus must be ingested as trusted"
    for s in skills:
        assert sign_skill(s.name, s.source, s.version, s.body) == s.signature, s.name
    # tamper with one skill's body -> the stored signature no longer matches
    st = get_storage()
    with st.transaction() as conn:
        conn.execute("UPDATE skills SET body='TAMPERED' WHERE id=?;", (skills[0].id,))
    tampered = mem.l4.get(skills[0].id)
    assert sign_skill(tampered.name, tampered.source, tampered.version,
                      tampered.body) != tampered.signature


def test_l4_search_ranks_relevant_first(fresh_home_with_skills):
    """§13: BM25 search over L4 returns the most relevant skill first."""
    from core.memory import get_memory
    mem = get_memory()
    hits = mem.search_l4("debug python traceback", category="code_generation",
                         target_agent="code", top_k=3)
    assert hits
    assert "python" in hits[0].name.lower() or "debug" in hits[0].name.lower()


def test_only_four_trusted_sources(fresh_home_with_skills):
    """The seeded corpus carries only allowlisted sources — nothing else
    sneaks into L4 as trusted. (Post-purge the production allowlist itself
    is empty; the fixture restores the four tier names for the seeded rows.)"""
    from core.memory import get_memory
    from config import TRUSTED_SKILL_SOURCES
    mem = get_memory()
    sources = {s.source for s in mem.l4.list()}
    assert sources.issubset(set(TRUSTED_SKILL_SOURCES))
    assert "OpenHuman" not in sources  # §2: explicitly excluded


def test_fresh_memory_boots_with_an_honest_empty_catalog(fresh_home):
    """Post-purge reality (2026-10-31): a plain runtime home starts with an
    EMPTY L4 — no bundled corpus exists and nothing fabricates rows. The
    bootstrap stub reports the empty catalog honestly instead of pretending."""
    from core.memory import get_memory
    from storage.local import get_storage
    from skills.loader import ensure_skills_bootstrapped, _bundled_skill_count
    mem = get_memory()
    st = get_storage()
    assert len(mem.l4.list()) == 0
    assert _bundled_skill_count() == 0
    boot = ensure_skills_bootstrapped()
    assert boot["bootstrapped"] is False
    assert boot["expected_bundled"] == 0
    assert st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"] == 0


def test_web_development_alias_routes_to_engineering_groups(fresh_home_with_skills):
    """web_development must retrieve web skills despite source categories."""
    from core.memory import get_memory
    mem = get_memory()
    code_hits = mem.search_l4("Express frontend backend REST API", category="web_development", target_agent="code", top_k=5)
    review_hits = mem.search_l4("webapp browser testing API", category="web_development", target_agent="review", top_k=5)
    assert code_hits
    assert review_hits
    assert all(s.solar_system_id for s in code_hits + review_hits)


def test_skill_tool_is_registered_and_refuses_honestly_when_disabled(fresh_home):
    """Post-purge Skill tool contract: the tool stays registered (agent
    prompts still reference it) but search returns an honest empty result
    and read/activate refuse — skills are disabled, never faked."""
    from connectors.builtin import get_registry
    registry = get_registry()
    names = set(registry.names())
    assert "Skill" in names
    searched = registry.get("Skill").handler(query="python testing")
    assert searched["ok"] is True and searched["count"] == 0
    read = registry.get("Skill").handler(skill_name="no-such-skill")
    assert read["ok"] is False and "disabled" in read["error"]
    activated = registry.get("Skill").handler(skill_id="skill-nope", action="activate")
    assert activated["ok"] is False and "disabled" in activated["error"]


def test_every_agent_can_see_skill_tools_and_procedures(fresh_home_with_skills):
    """The prompt machinery is alive: any registered agent's toolset carries
    the Skill tool, and a skill passed in the context renders under the
    ACTIVE L4 SKILL PROCEDURES block with its body."""
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
