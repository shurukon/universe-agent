"""tests/test_schema.py — the migration runner (schema/migrations.py).

Pins the runner contract the 2026-10 audit called out:
- forward apply to latest on a fresh home (0 -> current_schema_version)
- a backup file (universe.db.bak.*) is written BEFORE any migration runs
- a failing migration raises loudly, rolls back, and restores the backup
  (v0022's old `except: pass` silently "succeeding" must never return)
- dry_run previews pending migrations and writes nothing
- user_version is pinned step by step (run to v5, then to v9, ...)
- the v0022 row transforms (universe rename, run_mode, project re-parent)
  and the v0023 hot-path indexes actually land on a fresh database
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def raw_home(monkeypatch):
    """A fresh ~/.universe WITHOUT migrations applied — fresh_home in
    conftest.py runs ensure_latest() for you; the runner tests here need
    a database still sitting at user_version 0."""
    tmp = Path(tempfile.mkdtemp(prefix="gax-schema-"))
    from config import get_config
    cfg = get_config()
    cfg.reset_for_tests(tmp)
    monkeypatch.setenv("UNIVERSE_HOME", str(tmp))
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", "dev")
    from storage.local import reset_storage_for_tests
    reset_storage_for_tests(cfg.db_path)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _bak_files(home: Path) -> list[Path]:
    return sorted(home.glob("universe.db.bak.*"))


def test_forward_apply_to_latest_on_fresh_home(raw_home):
    from config import current_schema_version
    from schema import migrations
    from storage.local import get_storage

    st = get_storage()
    assert st.user_version() == 0

    lines = migrations.run()

    assert any("applying v0001" in line for line in lines)
    assert st.user_version() == current_schema_version()
    assert st.integrity_check() is True


def test_backup_file_created_before_migrate(raw_home):
    from schema import migrations
    from storage.local import get_storage

    lines = migrations.run(target_version=3)

    st = get_storage()
    assert st.user_version() == 3
    # exactly one backup, written before ANY statement of the run
    baks = _bak_files(raw_home)
    assert len(baks) == 1, lines
    assert any("backup written" in line for line in lines)
    # the backup is a real readable database holding the PRE-run state
    # (it is taken before any statement of the run — that is the state the
    # restore path rolls back to)
    import sqlite3
    con = sqlite3.connect(f"file:{baks[0]}?mode=ro", uri=True)
    try:
        version = con.execute("PRAGMA user_version;").fetchone()[0]
        has_goals = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='goals';").fetchone()[0]
    finally:
        con.close()
    assert version == 0, "backup must hold the pre-migration state"
    assert has_goals == 0


def test_dry_run_previews_and_writes_nothing(raw_home):
    from config import current_schema_version
    from schema import migrations
    from storage.local import get_storage

    st = get_storage()
    lines = migrations.run(dry_run=True)

    assert any("applying v0001" in line for line in lines)
    assert len(_bak_files(raw_home)) == 0  # no backup written
    assert st.user_version() == 0          # version untouched
    # nothing applied: the goals table does not exist yet
    assert st.query_all("PRAGMA table_info(goals);") == []
    # a real run afterwards takes the db to latest
    migrations.run()
    assert st.user_version() == current_schema_version()


def test_user_version_pinning_step_by_step(raw_home):
    from schema import migrations
    from storage.local import get_storage

    st = get_storage()
    migrations.run(target_version=5)
    assert st.user_version() == 5

    # pending() respects the pinned version: exactly 6..9 remain
    pend = [v for v, _, _ in migrations.pending(target_version=9)]
    assert pend == [6, 7, 8, 9]

    migrations.run(target_version=9)
    assert st.user_version() == 9
    migrations.run()  # to latest
    from config import current_schema_version
    assert st.user_version() == current_schema_version()


def test_failed_migration_restores_backup(raw_home):
    """A failing migration must be LOUD: raised with its version, rolled
    back, the pre-run backup restored, and user_version pinned at the
    pre-failure value. (v0022's old except-pass bumped the version on
    failure — this test is the regression guard.)"""
    from schema import migrations
    from storage.local import get_storage

    st = get_storage()
    migrations.run(target_version=5)
    with st.transaction() as conn:
        conn.execute(
            "INSERT INTO goals(id, text, status, created_at) "
            "VALUES('g-sentinel', 'pre-failure row', 'pending', 1.0);")

    @migrations.register(9999, "injected bad migration")
    def _boom(st):  # noqa: ARG001
        st.execute(
            "INSERT INTO goals(id, text, status, created_at) "
            "VALUES('g-bad-poison', 'poison', 'pending', 2.0);")
        raise RuntimeError("boom — simulated migration failure")

    try:
        with pytest.raises(RuntimeError) as ei:
            migrations.run(target_version=9999)
    finally:
        # never leave the injected migration in the shared MIGRATIONS list
        migrations.MIGRATIONS[:] = [
            t for t in migrations.MIGRATIONS if t[0] != 9999]

    assert "v9999" in str(ei.value)
    assert "backup restored" in str(ei.value)

    st2 = get_storage()  # re-opens lazily after the restore closed it
    assert st2.user_version() == 5, "user_version must be pinned at the value before the failed run"
    assert st2.query_one("SELECT id FROM goals WHERE id='g-bad-poison';") is None
    assert st2.query_one("SELECT id FROM goals WHERE id='g-sentinel';") is not None
    assert st2.integrity_check() is True


def test_restored_backup_has_no_stale_wal_sidecars(raw_home):
    """Audit MEDIUM: after a failed run the -wal/-shm sidecars of the
    FAILED run must be gone — a stale -wal would be applied on top of the
    restored (older) main file by the next connection."""
    import sqlite3

    from schema import migrations

    migrations.run(target_version=2)
    db = raw_home / "universe.db"

    @migrations.register(9998, "injected bad migration")
    def _boom(st):  # noqa: ARG001
        raise RuntimeError("boom")

    try:
        with pytest.raises(RuntimeError):
            migrations.run(target_version=9998)
    finally:
        migrations.MIGRATIONS[:] = [
            t for t in migrations.MIGRATIONS if t[0] != 9998]

    assert not (Path(str(db) + "-wal")).exists(), "stale -wal survived the restore"
    assert not (Path(str(db) + "-shm")).exists(), "stale -shm survived the restore"
    # the restored database is readable and at the pinned version
    con = sqlite3.connect(db)
    try:
        version = con.execute("PRAGMA user_version;").fetchone()[0]
    finally:
        con.close()
    assert version == 2
    from storage.local import get_storage
    assert get_storage().user_version() == 2


def test_v22_row_transforms_on_fresh_database(raw_home):
    """The v0022 rewrite (loud preconditions replacing except-pass) must
    produce the exact record transforms on a FRESH database: seed legacy
    rows at v21, migrate to latest, verify every rename/re-parent."""
    from schema import migrations
    from storage.local import get_storage

    st = get_storage()
    migrations.run(target_version=21)
    with st.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO agent_models(agent, provider, model) "
                     "VALUES('general','p1','m1');")
        conn.execute("INSERT OR REPLACE INTO agent_fallbacks(agent, position, provider, model) "
                     "VALUES('general', 0, 'p1', 'm1');")
        conn.execute("INSERT OR REPLACE INTO skill_solar_systems(id, name, description, "
                     "trust_level, taxonomy_version, created_at, updated_at) "
                     "VALUES('sys1','System One','','bundled_trusted','2026.08.1',1.0,1.0);")
        conn.execute("INSERT OR REPLACE INTO skill_orbits(id, solar_system_id, name, "
                     "description, created_at, updated_at) "
                     "VALUES('orb1','sys1','Orbit One','',1.0,1.0);")
        conn.execute("INSERT OR REPLACE INTO skill_agent_ownership"
                     "(agent, solar_system_id, orbit_id, ownership, created_at) "
                     "VALUES('general','sys1','orb1','primary',1.0);")
        conn.execute("INSERT OR REPLACE INTO projects(id, name, root, created_at, updated_at) "
                     "VALUES('proj-real','Real','',2.0,2.0);")
        conn.execute("INSERT INTO goals(id, text, status, created_at, project_id, kind) "
                     "VALUES('g-old','legacy task','pending',0.0,'general','task');")
        conn.execute("INSERT INTO skills(id, name, source, created_at, target_agent) "
                     "VALUES('s-old','legacy skill','bundled',0.0,'general');")

    migrations.run()  # applies v22 + v23 + ... + v25 (run_mode added then retired)

    assert st.query_one("SELECT agent FROM agent_models WHERE agent='general';") is None
    row = st.query_one("SELECT provider, model FROM agent_models WHERE agent='universe';")
    assert row == {"provider": "p1", "model": "m1"}
    assert st.query_one(
        "SELECT agent FROM skill_agent_ownership WHERE agent='general';") is None
    # v28 (single universe) purges the ENTIRE skills corpus ("honest empty",
    # 2026-10-31): the seeded s-old row never survives to be renamed — the
    # corpus ends at zero rows by design
    assert st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"] == 0

    g = st.query_one("SELECT project_id FROM goals WHERE id='g-old';")
    assert g["project_id"] == "proj-real"   # re-parented to the oldest REAL project
    assert st.query_one("SELECT id FROM projects WHERE id='general';") is None


def test_v23_hot_path_indexes_exist(raw_home):
    from schema import migrations
    from storage.local import get_storage

    migrations.run()
    st = get_storage()
    names = {r["name"] for r in st.query_all(
        "SELECT name FROM sqlite_master WHERE type='index';")}
    for idx in ("idx_planets_goal", "idx_moons_planet", "idx_handoffs_goal",
                "idx_skill_activations_skill", "idx_goals_sidebar"):
        assert idx in names, f"missing hot-path index {idx}"


def test_v23_indexes_serve_the_hot_queries(raw_home):
    """The index names matter less than the plans: the bridge's sidebar /
    memory-snapshot queries must actually reference them."""
    from schema import migrations
    from storage.local import get_storage

    migrations.run()
    st = get_storage()
    plans = [
        ("planets", ("g-x",),
         "SELECT * FROM planets WHERE turn_id=? ORDER BY created_at DESC LIMIT 1;"),
        ("moons", ("p-x",),
         "SELECT * FROM moons WHERE planet_id=? ORDER BY created_at;"),
        ("handoffs", ("g-x",),
         "DELETE FROM handoffs WHERE turn_id=?;"),
        ("activations", (),
         "SELECT skill_id, COUNT(*) AS n FROM skill_activations GROUP BY skill_id;"),
        ("sidebar", (),
         "SELECT id FROM goals WHERE deleted=0 AND kind='task' AND archived=0 "
         "ORDER BY pinned DESC, created_at DESC LIMIT 50;"),
    ]
    for label, params, sql in plans:
        rows = st.query_all("EXPLAIN QUERY PLAN " + sql, params)
        used = " ".join(r.get("detail", "") for r in rows)
        assert ("USING INDEX" in used or "USING COVERING INDEX" in used), (
            f"{label} query does not use its index: {used}")
