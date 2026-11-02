"""tests/test_storage.py — LocalStorage primitives (storage/local.py).

Pins the storage-layer contract the migration runner and every subsystem
depend on:
- transaction(): commits on success, ROLLS BACK on exception (no
  half-written rows — the §3 atomicity guarantee)
- backup(): a readable, self-contained copy another process can open
- the connection pragmas are actually set (WAL, synchronous=NORMAL,
  foreign_keys=ON, busy_timeout=30000) — queried via PRAGMA, not trusted
- get_storage() is a true singleton; reset_storage_for_tests() replaces it
"""
from __future__ import annotations

import sqlite3

import pytest

from storage.local import LocalStorage, get_storage, reset_storage_for_tests


def test_transaction_rollback_on_exception(fresh_home):
    """§3 atomicity: an exception mid-transaction must leave NO partial
    write behind."""
    st = get_storage()
    with pytest.raises(RuntimeError):
        with st.transaction() as conn:
            conn.execute(
                "INSERT INTO goals(id, text, status, created_at) "
                "VALUES('g-rollback', 'must vanish', 'pending', 1.0);")
            raise RuntimeError("boom — simulated failure")
    assert st.query_one("SELECT id FROM goals WHERE id='g-rollback';") is None


def test_transaction_commit_on_success(fresh_home):
    st = get_storage()
    with st.transaction() as conn:
        conn.execute(
            "INSERT INTO goals(id, text, status, created_at) "
            "VALUES('g-commit', 'stays', 'pending', 1.0);")
    assert st.query_one("SELECT id FROM goals WHERE id='g-commit';") is not None


def test_backup_creates_readable_copy(fresh_home, tmp_path):
    st = get_storage()
    with st.transaction() as conn:
        conn.execute(
            "INSERT INTO goals(id, text, status, created_at) "
            "VALUES('g-backup', 'sentinel', 'pending', 1.0);")
    dest = tmp_path / "backup-copy.db"
    st.backup(dest)
    assert dest.exists()
    # the copy is a self-contained database a separate connection can open
    con = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT text FROM goals WHERE id='g-backup';").fetchone()
    finally:
        con.close()
    assert row is not None and row["text"] == "sentinel"
    # and the live db keeps working after the backup
    assert st.query_one("SELECT id FROM goals WHERE id='g-backup';") is not None


def test_backup_overwrites_existing_dest(fresh_home, tmp_path):
    """backup() to an occupied path must not fail (migrations reuse
    timestamped names, but a same-second collision would re-use one)."""
    st = get_storage()
    dest = tmp_path / "backup-copy.db"
    st.backup(dest)
    st.backup(dest)  # second online backup over the same file
    con = sqlite3.connect(dest)
    try:
        assert con.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        con.close()


def test_connection_pragmas_actually_set(fresh_home):
    st = get_storage()
    st.execute("SELECT 1;")  # force the connection open
    assert st.query_one("PRAGMA journal_mode;")["journal_mode"] == "wal"
    # 1 = NORMAL (0=off, 2=full)
    assert st.query_one("PRAGMA synchronous;")["synchronous"] == 1
    assert st.query_one("PRAGMA foreign_keys;")["foreign_keys"] == 1
    # sqlite quirk: this pragma's result column is named "timeout"
    assert st.query_one("PRAGMA busy_timeout;")["timeout"] == 30000


def test_user_version_roundtrip(fresh_home):
    st = get_storage()
    st.set_user_version(7)
    assert st.user_version() == 7


def test_get_storage_singleton_identity(fresh_home):
    s1 = get_storage()
    s2 = get_storage()
    assert s1 is s2, "get_storage() must return the same instance"

    # reset_storage_for_tests swaps the singleton and get_storage()
    # reflects the swap immediately
    from config import get_config
    st = reset_storage_for_tests(get_config().db_path)
    assert get_storage() is st
    assert isinstance(st, LocalStorage)


def test_close_thread_reopens_lazily(fresh_home):
    """close_thread() is called on the migration restore path — after it,
    the next query must transparently reopen a working connection with
    the pragmas re-applied."""
    st = get_storage()
    st.close_thread()
    assert st.query_one("PRAGMA foreign_keys;")["foreign_keys"] == 1
    st.close_thread()
    assert st.integrity_check() is True
