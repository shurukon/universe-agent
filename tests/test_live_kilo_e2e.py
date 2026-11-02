"""tests/test_live_kilo_e2e.py — REAL end-to-end: a real goal through the
full bridge stack (preflight → orchestrator → Kilo Gateway → completion)
using the REAL stored Kilo key. No mocks anywhere: real HTTP to the live
Kilo API, real SQLite, real orchestrator.

Runs against a COPY of the live ~/.universe home (never the live DB itself),
via UNIVERSE_HOME override + TestClient. Skipped automatically when no Kilo
key is installed.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _live_home() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".universe"


def _has_live_kilo_key() -> bool:
    import sqlite3
    db = _live_home() / "universe.db"
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT COUNT(*) FROM provider_keys WHERE provider='Kilo Gateway'"
        ).fetchone()
        conn.close()
        return bool(rows and rows[0] > 0)
    except Exception:
        return False


needs_live_kilo = pytest.mark.skipif(
    not _has_live_kilo_key() or os.environ.get("UNIVERSE_LIVE") != "1",
    reason="live LLM e2e requires UNIVERSE_LIVE=1 (they spend real API credits "
           "from installed keys) AND a live Kilo Gateway key in ~/.universe",
)


@pytest.fixture
def live_copy_home(monkeypatch):
    """A filesystem copy of the live home: real key, real routing, real
    config — but writes land in temp, never in the user's DB."""
    src = _live_home()
    tmp = Path(tempfile.mkdtemp(prefix="gax-live-"))
    # The production bridge keeps this DB open — provider/key rows written
    # after the last checkpoint may live ONLY in the -wal sidecar, which a
    # raw file copy skips (and copying it mid-write risks a torn DB).
    # sqlite3's online-backup API folds the WAL into a consistent snapshot.
    src_db = src / "universe.db"
    dst_db = tmp / "universe.db"
    try:
        sconn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        try:
            dconn = sqlite3.connect(dst_db)
            try:
                sconn.backup(dconn)
            finally:
                dconn.close()
        finally:
            sconn.close()
    except sqlite3.Error:
        shutil.copy2(src_db, dst_db)  # last resort: no open writer
    for item in src.iterdir():
        if item.name in ("universe.db", "universe.db-shm", "universe.db-wal"):
            continue
        dest = tmp / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore_dangling_symlinks=True)
        else:
            shutil.copy2(item, dest)
    monkeypatch.setenv("UNIVERSE_HOME", str(tmp))
    from config import get_config
    cfg = get_config()
    cfg.reset_for_tests(tmp)
    cfg.ensure_dirs()
    from storage.local import reset_storage_for_tests
    reset_storage_for_tests(cfg.db_path)
    from schema.migrations import ensure_latest
    ensure_latest()
    from universe_desktop_bridge.app import reset_runtime_for_tests
    from observability.events import reset_event_bus_for_tests
    reset_event_bus_for_tests()
    reset_runtime_for_tests()
    yield tmp
    # leave no process-global bridge goals behind: later suites sharing
    # this process (e.g. prune tests asserting exact evict counts) must
    # see a clean registry
    reset_event_bus_for_tests()
    reset_runtime_for_tests()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def live_client(live_copy_home):
    from universe_desktop_bridge import app as app_mod
    with TestClient(app_mod.app) as c:
        yield c


@needs_live_kilo
def test_live_providers_honest(live_client):
    """GET /api/providers reports a routable provider (the exact claim
    the UI badge makes)."""
    r = live_client.get("/api/providers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_provider"] is True
    assert body["currently_serving"] == "Kilo Gateway"


@needs_live_kilo
def test_live_goal_accepted_and_honest(live_client):
    """A REAL goal lifecycle: POST /api/turn → accepted 200 → the detail
    endpoint always reports an honest state (bridge_status while live,
    terminal DB state after). Free-tier latency/rate-limits make a fixed
    completion deadline flaky, so completion itself is asserted
    opportunistically: if the goal terminates in-bounds, the reply must
    carry the marker; a still-running goal with a live bridge_status is
    a PASS for honesty (the UI spins truthfully, never lies)."""
    r = live_client.post("/api/turn", json={
        "text": "Reply with exactly: UNIVERSE-LIVE-OK",
        "project_id": "",
    })
    assert r.status_code == 200, r.text
    turn_id = r.json()["turn_id"]
    assert turn_id.startswith("goal-")
    deadline = time.time() + 240
    last = None
    while time.time() < deadline:
        g = live_client.get(f"/api/turn/{turn_id}").json()
        last = g
        status = g.get("bridge_status") or g.get("status")
        assert status in ("queued", "running", "completed", "failed",
                          "cancelled"), g
        if status in ("completed", "failed", "cancelled"):
            break
        time.sleep(5)
    assert last is not None
    status = last.get("bridge_status") or last.get("status")
    if status == "completed":
        assert "UNIVERSE-LIVE-OK" in (last.get("reply") or ""), last
    elif status in ("failed", "cancelled"):
        # terminal but unsuccessful: surface the real error — free-tier
        # rate limits (429) and credit walls (402) are environmental, so
        # they SKIP (with the reason) instead of failing; anything else
        # is a product bug worth failing on
        err = (last.get("error") or "")
        if not err:
            # empty-error failure: dump steps + summary keys so the next
            # run names the culprit instead of asserting blind
            pytest.fail(
                "goal failed with EMPTY error "
                f"(steps={last.get('steps')}, "
                f"halted={last.get('halted')}, "
                f"success={last.get('success')}, "
                f"conversation={last.get('conversation')})"
            )
        if "429" in err or "Rate limit" in err or "rate limit" in err.lower():
            pytest.skip(f"Kilo free tier rate-limited right now: {err[:200]}")
        if "402" in err or "credits" in err.lower():
            pytest.skip(f"Kilo account needs credits: {err[:200]}")
        pytest.fail(f"goal terminated {status}: {err[:300]}")
    else:
        # still live after the bound: honest running state. Two shapes:
        # a DB row with bridge_status overlay, or a bridge-only goal
        # (source=bridge, plain status) not yet flushed to the DB.
        assert status in ("queued", "running")
        assert (last.get("bridge_status") in ("queued", "running")
                or (last.get("source") == "bridge"
                    and last.get("status") in ("queued", "running")))
