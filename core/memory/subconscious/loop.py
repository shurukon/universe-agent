"""Bounded idle-time memory consolidation loop."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from config import SUBCONSCIOUS_INTERVAL_SECONDS, SUBCONSCIOUS_MAX_CYCLE_SECONDS
from core.memory import get_memory
from skills.loader import verify_all_signatures


class SubconsciousLoop:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_cycle_at = 0.0
        self._last_signature_check = 0.0
        self._cycle_count = 0
        self._last_error = ""

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def last_cycle_at(self) -> float:
        return self._last_cycle_at

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def last_error(self) -> str:
        return self._last_error

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="universe-subconscious-loop")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(SUBCONSCIOUS_INTERVAL_SECONDS)
                from core.agent.orchestrator import get_orchestrator
                if get_orchestrator().is_goal_active():
                    continue
                await self.cycle_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                # engine-fix round: the loop failure used to be stored in a
                # property NOBODY read — an always-broken loop was invisible.
                # Log it (loguru), the same honesty rule as the scheduler tick.
                logger.exception("subconscious loop iteration failed: {}", exc)

    async def cycle_once(self) -> dict[str, Any]:
        """Run one cancellable cycle and never exceed the configured bound."""
        started = time.monotonic()
        stats: dict[str, Any] = {"promoted": 0, "edges": 0, "compressed": 0,
                                  "signatures_verified": 0, "elapsed_ms": 0,
                                  "timed_out": False, "error": ""}
        try:
            result = await asyncio.wait_for(self._cycle_body(),
                                            timeout=SUBCONSCIOUS_MAX_CYCLE_SECONDS)
            stats.update(result)
        except asyncio.TimeoutError:
            stats["timed_out"] = True
            stats["error"] = "cycle timeout"
            self._last_error = stats["error"]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stats["error"] = str(exc)
            self._last_error = str(exc)
        finally:
            stats["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            self._last_cycle_at = time.time()
            self._cycle_count += 1
        return stats

    async def _cycle_body(self) -> dict[str, int]:
        # The whole body is synchronous SQLite/disk work (full-asteroid
        # forgetting pass, per-star edge queries, SHA-256 verification of
        # ~1,580 skills). It used to run ON the event loop, so the 300s
        # wait_for could only arm at await points that never came — the
        # bridge, voice and CLI froze for the duration of a large scan
        # (audit 2026-09). One to_thread hop makes the timeout real and
        # keeps the loop responsive.
        return await asyncio.to_thread(self._cycle_body_sync)

    def _cycle_body_sync(self) -> dict[str, int]:
        mem = get_memory()
        promoted = mem.promote_idle(threshold=0.45)
        edges = self._connect_stars(mem)
        compressed = mem.l2.forgetting_pass().get("compressed", 0)
        mem.l5.refresh_mirror(mem)
        # §18 lifecycle: skills idle >90 days decay toward review. It was
        # defined in l4 but never invoked from anywhere — dead policy (m3).
        decayed = mem.l4.apply_idle_decay()
        # P4: post-session reflection — the ONLY memory writer for session
        # turns (the turn hot path never touches memory; the plan's
        # "memory writes are NOT on the hot path" contract).
        reflected = self._reflect_sessions(mem)
        verified = 0
        if time.time() - self._last_signature_check > 7 * 86400:
            verified = verify_all_signatures().get("verified", 0)
            self._last_signature_check = time.time()
        return {"promoted": len(promoted), "edges": edges,
                "compressed": compressed, "decayed": decayed,
                "reflected": reflected,
                "signatures_verified": verified}

    def _reflect_sessions(self, mem) -> int:
        """Distill NEW transcript content of recently-active sessions into
        L3 (semantic stars, domain='session'). Idempotent: a 'reflected'
        watermark row in each transcript marks what was already distilled,
        so a restart re-reflects nothing. Best-effort — a failing session
        is skipped, never fatal to the cycle."""
        try:
            from core.session.store import get_session_store
            store = get_session_store()
        except Exception:
            return 0
        distilled = 0
        try:
            sessions = store.list_sessions(limit=5)[:3]
        except Exception:
            return 0
        for sess in sessions:
            try:
                sid = str(sess.get("id") or "")
                if not sid:
                    continue
                ents = store.transcript(sid)
                if not ents:
                    continue
                # watermark: only rows AFTER the last reflected marker
                last_reflected = 0
                for e in ents:
                    if e.get("kind") == "reflected":
                        last_reflected = int(e.get("seq", 0) or 0)
                new = [e for e in ents
                       if int(e.get("seq", 0) or 0) > last_reflected
                       and e.get("kind") == "message"]
                if len(new) < 4:
                    continue  # not enough substance to be worth a star
                users = [str(e.get("content") or "").strip()
                         for e in new if e.get("role") == "user"]
                assists = [str(e.get("content") or "").strip()
                           for e in new if e.get("role") == "assistant"]
                topic = (users[0][:80] if users else
                         (assists[0][:80] if assists else ""))
                if not topic:
                    continue
                digest_lines = []
                for u in users[:8]:
                    digest_lines.append(f"user: {u[:400]}")
                for a in assists[:8]:
                    if a:
                        digest_lines.append(f"assistant: {a[:400]}")
                content = "\n".join(digest_lines)[:8000]
                mem.l3.create_star(topic=topic, domain="session",
                                   summary=content[:300], content=content)
                distilled += 1
                store.append(sid, {
                    "kind": "reflected", "role": "system",
                    "content": f"[memory reflection recorded: {len(new)} "
                               f"messages distilled to L3]",
                    "reflected_seq": ents[-1].get("seq", 0)})
            except Exception:
                continue
        return distilled

    def _connect_stars(self, mem) -> int:
        edges_added = 0
        by_domain: dict[str, list] = {}
        for star in mem.l3.list_stars():
            by_domain.setdefault(star.domain, []).append(star)
        # ONE query for every existing edge (engine-fix round): the old
        # per-pair edges_of() ran 2 SQL queries per candidate edge —
        # O(2·stars) per cycle on a large vault. Undirected membership in a
        # frozenset preserves the exact (src,dst)/(dst,src) semantics the
        # per-pair check enforced.
        existing: set[frozenset[str]] = {frozenset(e) for e in mem.l3.all_edges()}
        for stars in by_domain.values():
            if len(stars) < 2:
                continue
            stars.sort(key=lambda star: star.interaction_count, reverse=True)
            for left, right in zip(stars, stars[1:]):
                pair = frozenset((left.id, right.id))
                if pair in existing:
                    continue
                mem.l3.add_edge(left.id, right.id, weight=0.5, kind="same_domain")
                existing.add(pair)
                edges_added += 1
        return edges_added


_loop: SubconsciousLoop | None = None


def get_loop() -> SubconsciousLoop:
    global _loop
    if _loop is None:
        _loop = SubconsciousLoop()
    return _loop
