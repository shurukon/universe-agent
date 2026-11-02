"""scripts/wipe_history.py — erase every conversation / goal the system has
stored, for a truly clean slate.

Deletes (user history only):
- DB rows: goals, planets (conversation sessions), asteroids (per-goal task
  learnings), handoffs, task_outcomes, stars + star_edges (L2 episodic
  memories written from goals), dark_matter mirror counters, plus legacy
  "Universe Echo" rows from any table
- Files: ~/.universe/timelines/*, ~/.universe/checkpoints/*, ~/.universe/llm_calls.jsonl,
  ~/.universe/memory_vault/*, ~/.universe/audit.log

NEVER deletes: universe.db.bak.* migration backups (§20) — the runner may
need them to restore a corrupted schema. Only a manual `del` removes those.

Keeps (product knowledge, not user history):
- skills / skill_* tables (the 1,580-skill taxonomy + solar systems),
  tool_catalog / tool_lifecycle (98 tools), capability_policy, moons,
  orbits, providers / keys.

Usage:
  python scripts/wipe_history.py            # interactive confirm, then wipe
  python scripts/wipe_history.py --yes      # non-interactive wipe
  python scripts/wipe_history.py --dry-run  # show what would be deleted
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import get_config  # noqa: E402

WIPE_TABLES = [
    "planets", "asteroids", "handoffs", "task_outcomes",
    "stars", "star_edges", "goals",
]
WIPE_KEYS = ["mirror.asteroid_count", "mirror.star_count"]
WIPE_DIRS = ["timelines", "checkpoints", "memory_vault"]
WIPE_FILES = ["llm_calls.jsonl", "audit.log"]


def _home_dir() -> Path:
    cfg = get_config()
    return cfg.home


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true",
                    help="confirm the destructive wipe (non-interactive use)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be deleted, delete nothing")
    args = ap.parse_args(argv)
    home = _home_dir()
    db = home / "universe.db"
    if not db.exists():
        print(f"no database at {db} — nothing to wipe")
        return 0

    con = sqlite3.connect(db)
    counts = {}
    for t in WIPE_TABLES:
        try:
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            counts[t] = "n/a"
    # legacy Echo rows anywhere they might linger
    echo_counts = {}
    for t in ("providers", "provider_keys", "agent_models", "agent_fallbacks",
              "agent_provider_keys"):
        try:
            echo_counts[t] = con.execute(
                f"SELECT COUNT(*) FROM {t} WHERE provider = 'Universe Echo'"
                if t != "providers" else
                "SELECT COUNT(*) FROM providers WHERE name = 'Universe Echo'"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            echo_counts[t] = "n/a"

    file_targets = []
    for d in WIPE_DIRS:
        p = home / d
        if p.exists():
            file_targets.append((p, sum(1 for _ in p.rglob("*") if _.is_file())))
    for f in WIPE_FILES:
        p = home / f
        if p.exists():
            file_targets.append((p, 1))
    bak_count = len(list(home.glob("universe.db.bak*")))

    print(f"home: {home}")
    for t, c in counts.items():
        print(f"  table {t}: {c} rows")
    for t, c in echo_counts.items():
        if c not in (0, "n/a"):
            print(f"  legacy echo rows in {t}: {c}")
    for p, n in file_targets:
        print(f"  files {p.name}: {n}")
    print(f"  migration backups kept (never deleted): {bak_count}")

    if args.dry_run:
        print("\n(dry run — nothing deleted)")
        return 0

    # interactive confirm unless --yes (mirrors reset_setup.py's style)
    if not args.yes:
        try:
            answer = input("Delete ALL conversations, goals, timelines and "
                           "episodic memories? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("aborted — pass --yes to confirm.")
            return 2

    with con:
        for t in WIPE_TABLES:
            try:
                con.execute(f"DELETE FROM {t}")
            except sqlite3.OperationalError:
                # table absent (never migrated / already dropped) — nothing
                # there to wipe; the count display above already said "n/a"
                print(f"  (table {t} absent — skipped)")
        for k in WIPE_KEYS:
            try:
                con.execute("DELETE FROM dark_matter WHERE key = ?", (k,))
            except sqlite3.OperationalError:
                pass
        for t in ("provider_keys", "agent_models", "agent_fallbacks",
                  "agent_provider_keys"):
            try:
                con.execute(f"DELETE FROM {t} WHERE provider = 'Universe Echo'")
            except sqlite3.OperationalError:
                pass
        try:
            con.execute("DELETE FROM providers WHERE name = 'Universe Echo'")
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()

    for p, _n in file_targets:
        if p.is_dir():
            for child in list(p.rglob("*")):
                if child.is_file():
                    child.unlink()
        elif p.exists():
            p.unlink()

    print("\nwiped: all conversations, goals, timelines, checkpoints, "
          "episodic stars, LLM call log, vault, audit log, legacy Echo rows")
    print("kept: skill taxonomy, solar systems, tool catalog, providers/keys, "
          "capability policy, moons, universe.db.bak.* migration backups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
