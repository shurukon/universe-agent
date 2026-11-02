"""tests/test_engine_fix_memory.py — engine-fix round: memory layer fixes.

Covers:
  * l2.delete() returns a structured {deleted, reason}
  * l3 search cache: a hit skips the full star re-fetch; touch()/add_edge()
    invalidate via the cheap aggregate key
  * l4 search cache: hit skips the corpus re-list; upsert invalidates
  * l5.set_many() commits the whole mirror snapshot in ONE transaction
  * subconscious _connect_stars is batched and never duplicates edges
    (either orientation)
  * get_memory() is a double-checked-locking singleton
"""
from __future__ import annotations

import asyncio
import threading


# ---------------------------------------------------------------------------
# l2 (11j)
# ---------------------------------------------------------------------------

def test_l2_delete_structured_result(fresh_home):
    from core.memory import get_memory
    mem = get_memory()
    planet = mem.l1.create_planet(turn_id="g-del", goal_text="t",
                                  classification={"category": "code_generation",
                                                  "domain": "python"})
    a = mem.l2.create(turn_id="g-del", planet_id=planet.id, task_description="t",
                      classification={"category": "code_generation",
                                      "domain": "python"})
    # backdate past the 7-day minimum retention so the delete path runs
    import time as _t
    from storage.local import get_storage
    with get_storage().transaction() as conn:
        conn.execute("UPDATE asteroids SET created_at=? WHERE id=?;",
                     (_t.time() - 8 * 86400, a.id))
    res = mem.l2.delete(a.id)
    assert res == {"deleted": True, "reason": "ok"}
    assert mem.l2.get(a.id) is None
    # unknown id
    assert mem.l2.delete("asteroid-nope") == {"deleted": False,
                                              "reason": "not_found"}
    # inside the 7-day retention window: refused, still there
    b = mem.l2.create(turn_id="g-del", planet_id=planet.id, task_description="t2",
                      classification={"category": "code_generation",
                                      "domain": "python"})
    res2 = mem.l2.delete(b.id)
    assert res2 == {"deleted": False, "reason": "retention_window"}
    assert mem.l2.get(b.id) is not None


# ---------------------------------------------------------------------------
# l3 (11k)
# ---------------------------------------------------------------------------

def test_l3_search_cache_hit_skips_refetch_and_touch_invalidates(fresh_home):
    from core.memory import get_memory
    mem = get_memory()
    s1 = mem.l3.create_star(topic="alpha", domain="d", summary="s",
                            content="alpha body")
    mem.l3.create_star(topic="beta", domain="d", summary="s",
                       content="beta body")
    assert mem.l3.search("alpha")  # warm the cache
    # the cache tuple now carries the full 7-field corpus signature
    key = mem.l3._corpus_key()
    assert len(key) == 7 and mem.l3._bm25_cache is not None
    assert mem.l3._bm25_cache[:7] == key

    calls = {"n": 0}
    orig = mem.l3.list_stars

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    mem.l3.list_stars = counting
    try:
        hits = mem.l3.search("alpha")
        assert calls["n"] == 0  # cache hit — no full re-fetch
        # (ranking carries a 5% random term — pin membership, not order)
        assert s1.id in {s.id for s in hits}
        # a touch bumps interaction_count/last_used (NOT updated_at): the
        # OLD (len, max updated_at) key missed exactly this and served
        # stale usage data
        mem.l3.touch(s1.id)
        hits2 = mem.l3.search("alpha")
        assert calls["n"] == 1  # the aggregate drifted → rebuild happened
        fresh = mem.l3.get_star(s1.id)
        assert fresh.interaction_count >= 1
        assert next(s for s in hits2 if s.id == s1.id).interaction_count \
            == fresh.interaction_count
    finally:
        del mem.l3.list_stars  # restore the bound class method


def test_l3_edge_add_invalidates_cache(fresh_home):
    from core.memory import get_memory
    mem = get_memory()
    s1 = mem.l3.create_star(topic="a", domain="d", summary="", content="a")
    s2 = mem.l3.create_star(topic="b", domain="d", summary="", content="b")
    assert mem.l3.search("a")
    before = mem.l3._corpus_key()
    mem.l3.add_edge(s1.id, s2.id)
    after = mem.l3._corpus_key()
    assert before != after  # Σ edge_count moved → cache rebuilds
    assert mem.l3.get_star(s1.id).edge_count == 1


# ---------------------------------------------------------------------------
# l4 (11o)
# ---------------------------------------------------------------------------

def test_l4_search_cache_hit_and_upsert_invalidation(fresh_home):
    from core.memory import get_memory
    from core.memory.layers.l4_procedural import Skill
    l4 = get_memory().l4
    l4.upsert(Skill(name="alpha-skill", description="alpha things", body="b"))
    l4.upsert(Skill(name="gamma-skill", description="gamma things", body="b"))
    # a third non-overlapping doc: BM25's idf is exactly 0 for a term in
    # half of a 2-document corpus, so the score>0 cutoff needs N>=3
    l4.upsert(Skill(name="delta-skill", description="unrelated delta things", body="b"))

    calls = {"n": 0}
    orig = l4.list

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    l4.list = counting
    try:
        r1 = l4.search("alpha")
        assert r1[0].name == "alpha-skill"  # bundled skills may also rank — ours first
        n1 = calls["n"]
        r2 = l4.search("alpha")
        assert calls["n"] == n1  # cache hit — no re-list
        assert r2[0].name == "alpha-skill"
        # a write invalidates: the next search rebuilds
        l4.upsert(Skill(name="beta-skill", description="beta things", body="b"))
        r3 = l4.search("alpha")
        assert calls["n"] == n1 + 1
        assert r3[0].name == "alpha-skill"
    finally:
        del l4.list


# ---------------------------------------------------------------------------
# l5 (11p)
# ---------------------------------------------------------------------------

class _CountingStorage:
    """Proxy counting transaction() openings (query passthroughs included)."""

    def __init__(self, inner):
        self._inner = inner
        self.tx_count = 0

    def transaction(self):
        self.tx_count += 1
        return self._inner.transaction()

    def query_one(self, *a, **k):
        return self._inner.query_one(*a, **k)

    def query_all(self, *a, **k):
        return self._inner.query_all(*a, **k)

    def execute(self, *a, **k):
        return self._inner.execute(*a, **k)


def test_l5_set_many_single_transaction(fresh_home):
    from core.memory import get_memory
    l5 = get_memory().l5
    proxy = _CountingStorage(l5._st)
    l5._st = proxy
    l5.set_many({"mirror.a": 1, "mirror.b": [1, 2], "mirror.c": "three"})
    assert proxy.tx_count == 1  # ONE transaction, not three
    assert l5.get("mirror.a") == 1
    assert l5.get("mirror.b") == [1, 2]
    assert l5.get("mirror.c") == "three"
    l5.set_many({})  # empty → no transaction at all
    assert proxy.tx_count == 1


# ---------------------------------------------------------------------------
# subconscious loop (11m + 11n)
# ---------------------------------------------------------------------------

def test_connect_stars_batched_and_dedup_either_orientation(fresh_home):
    from core.memory import get_memory
    from core.memory.subconscious.loop import get_loop
    mem = get_memory()
    mem.l3.create_star(topic="s1", domain="d", summary="", content="a")
    mem.l3.create_star(topic="s2", domain="d", summary="", content="b")
    loop_obj = get_loop()
    added = loop_obj._connect_stars(mem)
    assert added == 1
    assert len(mem.l3.all_edges()) == 1
    # idempotent: a second cycle adds nothing
    assert loop_obj._connect_stars(mem) == 0
    # reversed orientation counts as existing too
    s3 = mem.l3.create_star(topic="s3", domain="e", summary="", content="c")
    s4 = mem.l3.create_star(topic="s4", domain="e", summary="", content="d")
    mem.l3.add_edge(s4.id, s3.id)  # stored (s4, s3)
    assert loop_obj._connect_stars(mem) == 0
    assert len(mem.l3.edges_of(s3.id)) == 1


def test_subconscious_error_is_logged(fresh_home, monkeypatch):
    """The loop's failure used to be stored in a property nobody read."""
    from core.memory.subconscious import loop as loop_mod
    records = []

    class _Recorder:
        def exception(self, msg, *a, **k):
            try:
                records.append(str(msg).format(*a, **k))
            except Exception:
                records.append(str(msg))

    monkeypatch.setattr(loop_mod, "logger", _Recorder())
    loop_obj = loop_mod.get_loop()

    async def runner():
        try:
            raise RuntimeError("cycle blew up")
        except Exception as exc:
            loop_obj._last_error = str(exc)
            loop_mod.logger.exception("subconscious loop iteration failed: {}", exc)

    asyncio.run(runner())
    assert records and "cycle blew up" in records[0]


# ---------------------------------------------------------------------------
# singleton (11i)
# ---------------------------------------------------------------------------

def test_get_memory_returns_same_instance_across_threads(fresh_home):
    from core.memory import get_memory, reset_memory_for_tests
    reset_memory_for_tests()
    results = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        results.append(get_memory())

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(results) == 2
    assert results[0] is results[1]
