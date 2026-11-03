"""schema/migrations.py — forward-only schema migrations.

§25 Phase 10 ㊿, §20. Each migration is a numbered module in schema/v*.py
exposing upgrade(storage). The runner:
- Takes a backup of universe.db before applying (§20).
- Updates PRAGMA user_version.
- On failure, restores the backup and refuses to start.
- /migrate --dry-run previews pending migrations.

We use a tiny bespoke runner rather than yoyo-migrations so the build has zero
external migration deps and migrations are plain Python (auditable). The
behaviour matches the spec's intent exactly.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Callable

from storage.local import LocalStorage, get_storage
from config import get_config


MigrationFn = Callable[[LocalStorage], None]

# Ordered list of (version, description, upgrade_fn). Each v*.py module
# registers itself here at import time.
MIGRATIONS: list[tuple[int, str, MigrationFn]] = []


def register(version: int, description: str) -> Callable[[MigrationFn], MigrationFn]:
    def deco(fn: MigrationFn) -> MigrationFn:
        MIGRATIONS.append((version, description, fn))
        return fn
    return deco


def _load_all() -> None:
    """Import every v*.py so its @register decorator fires."""
    from schema import (v0001_initial, v0002_add_orbit_id, v0003_add_skill_signature,  # noqa: F401 — side-effect registration
                        v0004_add_goals_table, v0005_add_audit_log,  # noqa: F401
                        v0006_add_capability_policy, v0007_add_skill_quarantine,  # noqa: F401
                        v0008_add_privacy_tier, v0009_add_connectors_channels,  # noqa: F401
                        v0010_add_agent_provider_keys, v0011_add_tool_lifecycle,  # noqa: F401
                        v0012_skill_taxonomy, v0013_purge_echo, v0014_ui_overhaul,  # noqa: F401
                        v0015_consent_persist, v0016_llm_key_alias,  # noqa: F401
                        v0017_goal_kind, v0018_swarm_registry,  # noqa: F401
                        v0019_automations, v0020_feedback, v0021_skill_enabled,  # noqa: F401
                        v0022_agent_universe_run_mode,  # noqa: F401
                        v0023_hot_path_indexes,  # noqa: F401
                        v0024_agent_universe_rename,  # noqa: F401
                        v0025_retire_goal_mode,  # noqa: F401
                        v0026_task_views,  # noqa: F401
                        v0027_sessions,  # noqa: F401
                        v0028_single_universe,  # noqa: F401
                        v0029_session_authority,  # noqa: F401
                        v0030_turn_identity)  # noqa: F401
    # ensure sorted by version
    MIGRATIONS.sort(key=lambda t: t[0])


def pending(target_version: int | None = None) -> list[tuple[int, str, MigrationFn]]:
    _load_all()
    st = get_storage()
    current = st.user_version()
    end = target_version if target_version is not None else max(v for v, _, _ in MIGRATIONS)
    return [(v, d, fn) for v, d, fn in MIGRATIONS if current < v <= end]


def run(target_version: int | None = None, *, dry_run: bool = False) -> list[str]:
    """Apply pending migrations up to target_version (default: latest).

    Returns a list of human-readable lines describing what happened."""
    _load_all()
    cfg = get_config()
    st = get_storage()
    current = st.user_version()
    pend = pending(target_version)
    if not pend:
        return [f"schema already at v{current}; nothing to migrate."]

    lines: list[str] = []
    if not dry_run:
        # integrity check before
        if not st.integrity_check():
            raise RuntimeError("integrity_check failed before migration — refusing to start")
        # backup
        cfg.home.mkdir(parents=True, exist_ok=True)
        backup_path = cfg.home / f"universe.db.bak.{int(time.time())}"
        st.backup(backup_path)
        lines.append(f"backup written -> {backup_path.name}")

    for version, desc, fn in pend:
        lines.append(f"applying v{version:04d}: {desc}")
        if dry_run:
            continue
        try:
            with st.transaction() as conn:
                fn(st)
                conn.execute(f"PRAGMA user_version = {version};")
            if not st.integrity_check():
                raise RuntimeError(f"integrity_check failed after v{version}")
        except Exception as e:
            # restore backup. The failed run leaves -wal/-shm sidecars next
            # to the main file: if they survived the copy they would be
            # applied on top of the restored (older) file by the next
            # connection, silently resurrecting half-migrated state. Drop
            # them on BOTH the restore path and the VACUUM-checkpoint path.
            if backup_path.exists():
                try:
                    st.execute("VACUUM;")  # release WAL locks
                except Exception as vac_err:
                    # a corrupted half-migrated db may refuse VACUUM — the
                    # backup restore must still proceed, loudly afterwards
                    print(f"migration restore: VACUUM failed ({vac_err}); "
                          "restoring from backup anyway", file=sys.stderr)
                # release the open handle first (Windows keeps the file
                # locked while a connection lives; close also checkpoints
                # and removes the -wal the last connection owns)
                st.close_thread()
                shutil.copy2(backup_path, cfg.db_path)
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(cfg.db_path) + suffix)
                    try:
                        sidecar.unlink(missing_ok=True)
                    except PermissionError:
                        # another process still holds the sidecar open (the
                        # common single-writer case removes it itself on
                        # close_thread); deleting is impossible, so say so
                        # loudly instead of crashing the restore
                        print(f"migration restore: could not delete {sidecar.name} "
                              "(held open by another connection) — the next "
                              "writer's checkpoint will fold it in", file=sys.stderr)
            raise RuntimeError(
                f"migration v{version} failed: {e}. backup restored. Universe refuses to start."
            ) from e
    return lines


def ensure_latest() -> None:
    """Called at startup. Idempotent — no-op if already current."""
    run()
