"""storage/local.py — SQLite storage layer (thread-safe, fixed id handling).

§25 Phase 1 ②. The single persistence substrate for L1–L5, orbits, rules,
audit log, skills, providers, checkpoints. WAL mode for concurrent reads.
Each thread gets its own connection (sqlite3 connections are not shareable
across threads by default). Per-entity asyncio locks live in
concurrency/locks.py and wrap the async operations that call into here.

Design notes that fix v0 bugs:
- IDs are generated ONCE at insert time and stored on the row. Updates always
  match by id, never re-generate a UUID — this is the fix for the duplicate
  orbit-rows bug (§3).
- All multi-step writes go through a transaction context manager; a failure
  rolls back, so there are no half-written asteroids (§3 atomicity).
- The schema is created via the migration runner (schema/migrations.py); this
  module only holds the connection plumbing and a few helpers.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class LocalStorage:
    """Thread-safe SQLite access. One connection per (thread, db_path)."""

    _lock = threading.Lock()

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        # thread-local connection storage
        self._tls = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            db_parent = self.db_path.parent
            db_parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we manage txns ourselves
                timeout=30.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=30000;")
            self._tls.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transactional context. Commits on success, rolls back on exception.

        sqlite3 with isolation_level=None needs explicit BEGIN.
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._conn()
        return conn.execute(sql, params)

    def executemany(self, sql: str, params_seq: list[tuple]) -> sqlite3.Cursor:
        conn = self._conn()
        return conn.executemany(sql, params_seq)

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        cur = self.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def query_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cur = self.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def user_version(self) -> int:
        row = self.query_one("PRAGMA user_version;")
        return int(row["user_version"]) if row else 0

    def set_user_version(self, version: int) -> None:
        self.execute(f"PRAGMA user_version = {int(version)};")

    def integrity_check(self) -> bool:
        row = self.query_one("PRAGMA integrity_check;")
        return bool(row and row["integrity_check"] == "ok")

    def backup(self, dest: Path) -> Path:
        """Online backup to dest. Used before schema migrations (§20)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = self._conn()
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
        return dest

    def close_thread(self) -> None:
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            conn.close()
            self._tls.conn = None


# Module-level singleton accessor — every subsystem shares the same storage
# instance so WAL and per-thread connections are consistent.
_storage: LocalStorage | None = None
_storage_lock = threading.Lock()


def get_storage(db_path: Path | None = None) -> LocalStorage:
    global _storage
    if _storage is None or (db_path is not None and _storage.db_path != db_path):
        with _storage_lock:
            if _storage is None or (db_path is not None and _storage.db_path != db_path):
                from config import get_config
                path = db_path or get_config().db_path
                _storage = LocalStorage(path)
    return _storage


def reset_storage_for_tests(db_path: Path) -> LocalStorage:
    """Force a fresh storage instance pointed at db_path (tests only)."""
    global _storage
    with _storage_lock:
        _storage = LocalStorage(db_path)
    return _storage
