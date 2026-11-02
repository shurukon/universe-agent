"""core/memory/layers/l5_meta.py — Dark Matter (meta memory).

§3, §25 Phase 2 ⑪. Self-knowledge layer: a live mirror of the universe. The v0
bug (error_rate = 1 - gravity_score, making every domain look 60% failed) is
fixed: error_rate is computed from a WINDOWED sample of actual task_success
flags — last 100 tasks per domain.
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.memory import UniverseMemory

from storage.local import get_storage

WINDOW_SIZE = 100  # last 100 tasks per domain (§3)


class L5Meta:
    def __init__(self) -> None:
        self._st = get_storage()

    # ---- raw key/value mirror -------------------------------------------
    def set(self, key: str, value: Any) -> None:
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dark_matter(key,value,updated_at) VALUES(?,?,?);",
                (key, json.dumps(value), time.time()),
            )

    def set_many(self, items: dict[str, Any]) -> None:
        """Write several mirror keys in ONE transaction (engine-fix round):
        refresh_mirror committed 6 keys as 6 separate transactions, so a
        concurrent reader (bridge UI) could observe a half-refreshed mirror
        — e.g. a new star_count next to the PREVIOUS last_refresh. One
        transaction is one consistent snapshot write."""
        if not items:
            return
        now = time.time()
        with self._st.transaction() as conn:
            for key, value in items.items():
                conn.execute(
                    "INSERT OR REPLACE INTO dark_matter(key,value,updated_at) VALUES(?,?,?);",
                    (key, json.dumps(value), now),
                )

    def get(self, key: str, default: Any = None) -> Any:
        row = self._st.query_one("SELECT value FROM dark_matter WHERE key=?;", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def all(self) -> dict[str, Any]:
        rows = self._st.query_all("SELECT key,value FROM dark_matter;")
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                out[r["key"]] = r["value"]
        return out

    # ---- task outcome tracking (windowed) -------------------------------
    def record_outcome(self, *, domain: str, success: bool, turn_id: str = "") -> None:
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT INTO task_outcomes(ts,domain,success,turn_id) VALUES(?,?,?,?);",
                (time.time(), domain, 1 if success else 0, turn_id),
            )
            # prune to window per domain
            conn.execute(
                "DELETE FROM task_outcomes WHERE id NOT IN ("
                "  SELECT id FROM task_outcomes WHERE domain=? ORDER BY ts DESC LIMIT ?"
                ") AND domain=?;",
                (domain, WINDOW_SIZE, domain),
            )

    def error_rate(self, domain: str) -> float:
        """Windowed error rate from real task_success flags (§3 fix)."""
        rows = self._st.query_all(
            "SELECT success FROM task_outcomes WHERE domain=? ORDER BY ts DESC LIMIT ?;",
            (domain, WINDOW_SIZE),
        )
        if not rows:
            return 0.0
        failures = sum(1 for r in rows if not r["success"])
        return failures / len(rows)

    def domain_stats(self) -> dict[str, dict[str, Any]]:
        rows = self._st.query_all(
            "SELECT domain, COUNT(*) AS n, SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS fails "
            "FROM task_outcomes GROUP BY domain;",
        )
        out = {}
        for r in rows:
            n = int(r["n"])
            fails = int(r["fails"])
            out[r["domain"]] = {
                "samples": n,
                "failures": fails,
                "error_rate": (fails / n) if n else 0.0,
            }
        return out

    # ---- reflective mirror updates --------------------------------------
    def refresh_mirror(self, memory: "UniverseMemory") -> None:
        """Rebuild the L5 snapshot from the other layers. Called after task
        completion and by the Subconscious Loop."""
        l2 = memory.l2
        l3 = memory.l3
        l4 = memory.l4
        recent = l2.list_recent(limit=200)
        # COUNT(*) instead of materializing 10,000 full asteroid rows and
        # JSON-parsing every one, after every goal (audit 2026-09)
        try:
            row = self._st.query_one("SELECT COUNT(*) AS n FROM asteroids;")
            asteroid_count = int(row["n"]) if row else 0
        except Exception:
            asteroid_count = len(recent)
        # top errors
        errs: dict[str, int] = {}
        for a in recent:
            if not a.task_success:
                errs[a.domain] = errs.get(a.domain, 0) + 1
        # one transaction for the whole mirror snapshot (set_many)
        self.set_many({
            "mirror.asteroid_count": asteroid_count,
            "mirror.star_count": len(l3.list_stars()),
            "mirror.skill_count": len(l4.list()),
            "mirror.domain_stats": self.domain_stats(),
            "mirror.top_error_domains": errs,
            "mirror.last_refresh": time.time(),
        })
