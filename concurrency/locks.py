"""concurrency/locks.py — per-entity asyncio locks (§14).

All persistent state mutations go through SQLite (WAL mode + an asyncio.Lock
per entity type). In-memory caches are read-only snapshots; mutations always
go through the database. This is the structural fix for the v0 SQLite
race-condition risk.

HONEST STATUS (engine-fix round): this is an OPTIONAL utility, currently
unwired — get_locks() has no production callers (mutations rely on SQLite
WAL + short transactions instead). Kept as the documented §14 mechanism;
wire it around multi-step write sequences if/when they appear. It is
exercised by tests/test_integration.py so the surface can't rot silently.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class LockManager:
    """One lock per entity type. Held for the shortest scope that keeps the
    write atomic."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {
            "planets": asyncio.Lock(),
            "moons": asyncio.Lock(),
            "asteroids": asyncio.Lock(),
            "stars": asyncio.Lock(),
            "skills": asyncio.Lock(),
            "orbits": asyncio.Lock(),
            "rules": asyncio.Lock(),
            "goals": asyncio.Lock(),
            "audit": asyncio.Lock(),
            "vault": asyncio.Lock(),
            "checkpoint": asyncio.Lock(),
        }

    @asynccontextmanager
    async def acquire(self, entity: str) -> AsyncIterator[None]:
        lock = self._locks.get(entity)
        if lock is None:
            raise KeyError(f"no lock for entity {entity!r}")
        async with lock:
            yield

    def has(self, entity: str) -> bool:
        return entity in self._locks


_locks: LockManager | None = None


def get_locks() -> LockManager:
    global _locks
    if _locks is None:
        _locks = LockManager()
    return _locks
