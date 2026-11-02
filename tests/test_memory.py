"""tests/test_memory.py — the v0-bug-fix regression tests (§STEP 3, §STEP 4)."""
import pytest


@pytest.mark.asyncio
async def test_complete_task_preserves_category(fresh_home):
    """v0 bug: complete_task reset category to 'general'. The fix stores
    category/domain/language in session_context on the Planet (§3)."""
    from core.memory import get_memory
    mem = get_memory()
    planet = mem.l1.create_planet(
        turn_id="g1", goal_text="write a python csv reader",
        classification={"category": "code_generation", "domain": "python",
                        "intent": "write", "complexity": "medium"},
        language="en")
    handoffs = [
        {"agent": "code", "what_was_done": "wrote it", "key_decisions": ["x"],
         "task_success": True, "decision_confidence": 0.9, "is_knowledge_based": True},
    ]
    ast = mem.complete_task(planet=planet, handoffs=handoffs)
    assert ast.category == "code_generation"
    assert ast.domain == "python"
    assert ast.language == "en"
    assert ast.category != "general"  # the v0 bug


def test_gravity_not_stuck_at_040(fresh_home):
    """v0 bug: gravity was permanently stuck near 0.40. The fix computes
    Confidence Accumulation from real decision_confidence/is_knowledge_based
    data (§3)."""
    from core.memory.galactic_core import compute_gravity
    handoffs = [
        {"decision_confidence": 0.9, "is_knowledge_based": True, "task_success": True},
        {"decision_confidence": 0.85, "is_knowledge_based": True, "task_success": True},
        {"decision_confidence": 0.88, "is_knowledge_based": True, "task_success": True},
    ]
    gravity, prov = compute_gravity(handoffs)
    # with all knowledge-based + high confidence, gravity must be well above 0.40
    assert gravity > 0.70, f"gravity {gravity} is stuck near v0's 0.40"
    assert prov.decision_confidences == [0.9, 0.85, 0.88]
    assert all(prov.knowledge_based_flags)


def test_error_rate_is_windowed_not_inverse_gravity(fresh_home):
    """v0 bug: error_rate = 1 - gravity_score (made every domain look 60%
    failed). The fix computes error_rate from real task_success flags (§3)."""
    from core.memory import get_memory
    mem = get_memory()
    # record 10 successes, 0 failures for python
    for _ in range(10):
        mem.l5.record_outcome(domain="python", success=True)
    # gravity of some asteroid is irrelevant to error_rate
    assert mem.l5.error_rate("python") == 0.0
    # now add a failure
    mem.l5.record_outcome(domain="python", success=False)
    assert mem.l5.error_rate("python") == pytest.approx(1/11, rel=0.1)


def test_orbits_have_explicit_id_no_duplicates(fresh_home):
    """v0 bug: orbits generated a new UUID on every save -> duplicate rows.
    The fix: to_dict() includes 'id'; save() matches by id (§3)."""
    from core.memory.orbits import get_orbits
    o = get_orbits()
    g = o.get_galactic()
    o.save_galactic(g)
    first_id = g.id
    # save again — should UPDATE, not insert a new row
    g.name = "Updated"
    o.save_galactic(g)
    assert g.id == first_id  # id unchanged
    # only one galactic orbit row
    from storage.local import get_storage
    st = get_storage()
    rows = st.query_all("SELECT * FROM orbits WHERE kind='galactic';")
    assert len(rows) == 1
    assert rows[0]["id"] == first_id


def test_fingerprint_uses_llm_classification_not_hash(fresh_home):
    """v0 bug: SHA-256 was used for semantic similarity (wrong). The fix:
    classification is via UNIVERSE_META LLM classification; hash is ONLY for
    exact-repeat detection (§3)."""
    from core.memory import get_memory
    from core.memory.fingerprint import build_fingerprint, find_repeat_asteroid
    mem = get_memory()
    cls = {"category": "code_generation", "domain": "python", "intent": "write"}
    fp = build_fingerprint(cls, ["Read", "Write"])
    assert fp.classification == cls  # LLM classification, not a hash
    assert fp.repeat_hash  # narrow hash exists for exact-repeat only
    # create an asteroid with this fingerprint hash
    planet = mem.l1.create_planet(turn_id="g1", goal_text="t", classification=cls)
    mem.l2.create(turn_id="g1", planet_id=planet.id, task_description="t",
                  classification=cls, fingerprint=fp.to_dict(), fingerprint_hash=fp.repeat_hash)
    # an exact repeat within the recency window links to the same asteroid
    found = find_repeat_asteroid(fp.repeat_hash, recency_seconds=300)
    assert found is not None


def test_vault_sync_removes_cache_when_file_deleted(fresh_home):
    """§3: the vault is authoritative; deleting a vault file removes the
    SQLite row on next sync."""
    from config import get_config
    from core.memory import get_memory
    mem = get_memory()
    mem.l3.create_star(topic="test", domain="python",
                              summary="s", content="c")
    cfg = get_config()
    vfiles = list(cfg.vault_dir.rglob("*.md"))
    assert len(vfiles) == 1
    vfiles[0].unlink()
    stats = mem.vault_sync()
    assert stats["removed"] == 1
    assert len(mem.l3.list_stars()) == 0


def test_subconscious_loop_promotes_idle(fresh_home):
    """§3: the Subconscious Loop promotes asteroids at gravity >= 0.45 during
    idle time, so promotion isn't solely dependent on real-time scoring."""
    from core.memory import get_memory
    from core.memory.subconscious.loop import get_loop
    mem = get_memory()
    planet = mem.l1.create_planet(turn_id="g1", goal_text="t",
                                  classification={"category": "code_generation", "domain": "python"})
    # create an asteroid with gravity 0.50 (above 0.45 threshold)
    mem.l2.create(turn_id="g1", planet_id=planet.id, task_description="t",
                  classification={"category": "code_generation", "domain": "python"},
                  gravity_score=0.50)
    loop = get_loop()
    stats = asyncio_run(loop.cycle_once())
    assert stats["promoted"] >= 1
    # idempotent: running again promotes nothing new
    stats2 = asyncio_run(loop.cycle_once())
    assert stats2["promoted"] == 0


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def test_forgetting_pass_compresses_real_asteroid_into_l3(fresh_home):
    import time

    from core.memory import get_memory
    from storage.local import get_storage

    mem = get_memory()
    cls = {"category": "code_generation", "domain": "python"}
    planet = mem.l1.create_planet(turn_id="g-compress", goal_text="old csv task", classification=cls)
    asteroid = mem.l2.create(turn_id="g-compress", planet_id=planet.id,
                             task_description="old csv task", classification=cls,
                             decisions=["use csv.DictReader"], outcomes=["completed"],
                             gravity_score=0.40)
    with get_storage().transaction() as conn:
        conn.execute("UPDATE asteroids SET created_at=? WHERE id=?;",
                     (time.time() - 31 * 86400, asteroid.id))
    stats = mem.l2.forgetting_pass()
    assert stats["compressed"] == 1
    refreshed = mem.l2.get(asteroid.id)
    assert refreshed is not None and refreshed.promoted_to
    stars = [s for s in mem.l3.list_stars("python") if s.topic == f"memory:{asteroid.id}"]
    assert len(stars) == 1
    assert asteroid.id in stars[0].content


def test_l3_connected_star_gets_graph_priority(fresh_home):
    from core.memory import get_memory

    mem = get_memory()
    first = mem.l3.create_star(topic="shared topic", domain="python", summary="same", content="same")
    second = mem.l3.create_star(topic="shared topic", domain="python", summary="same", content="same")
    peers = [mem.l3.create_star(topic=f"peer-{i}", domain="python", summary="peer", content="peer") for i in range(4)]
    for peer in peers:
        mem.l3.add_edge(first.id, peer.id)
    results = mem.l3.search("shared topic", top_k=2)
    assert results[0].id == first.id
    assert mem.l3.get_star(first.id).edge_count > mem.l3.get_star(second.id).edge_count


@pytest.mark.asyncio
async def test_repl_starts_and_stops_subconscious_loop(fresh_home):
    from cli.repl import ConsoleIO, repl_loop
    from core.memory.subconscious.loop import get_loop

    io = ConsoleIO()
    io.feed("exit")
    await repl_loop(io)
    assert get_loop().is_running is False
