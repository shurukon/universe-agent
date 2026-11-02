"""config.py — Universe configuration singleton (thread-safe).

§25 Phase 1 ①. Centralizes all paths, defaults, and runtime flags. Thread-safe
singleton so the CLI, the Subconscious Loop, and any background tasks all read
the same configuration. Reads from environment variables and ~/.universe/config.json
(with env vars winning, so tests can override deterministically).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def _default_universe_home() -> Path:
    """Where ~/.universe lives. Overridable via UNIVERSE_HOME for tests.

    Legacy compatibility: the pre-rename GALAXY_HOME env var is still
    honored when UNIVERSE_HOME is unset, and an existing ~/.galaxy data
    home is migrated (copied) to ~/.universe on first resolution — the
    original is kept untouched as a backup."""
    home = os.environ.get("UNIVERSE_HOME") or os.environ.get("GALAXY_HOME")
    if home:
        return Path(home)
    new_home = Path.home() / ".universe"
    legacy = Path.home() / ".galaxy"
    if not new_home.exists() and legacy.exists():
        import shutil
        shutil.copytree(legacy, new_home, symlinks=True)
        # the DB files follow the rename: galaxy.db -> universe.db
        for name in ("galaxy.db",):
            old = new_home / name
            if old.exists():
                old.rename(new_home / "universe.db")
        for bak in new_home.glob("galaxy.db.bak.*"):
            bak.rename(new_home / bak.name.replace("galaxy.db", "universe.db", 1))
    return new_home


# Brand identity (§23)
UNIVERSE_VERSION = "1.0.0"
TAGLINE = "the mind that remembers everything"

_schema_version_cache: int | None = None


def current_schema_version() -> int:
    """Single source of truth: the highest registered migration id.

    The old hand-maintained constant drifted (said 9 while migrations had
    already reached 14). Computed lazily and cached: at module-import time
    schema.migrations imports `get_config` from here, so deriving eagerly
    would hit a half-initialized config and swallow the cycle error."""
    global _schema_version_cache
    if _schema_version_cache is None:
        try:
            from schema.migrations import MIGRATIONS, _load_all
            if not MIGRATIONS:
                _load_all()
            # default=0: an empty MIGRATIONS list raised ValueError from
            # max() — a bare-generator crash, not one of the import-cycle
            # shapes this handler exists for
            _schema_version_cache = max((v for v, _, _ in MIGRATIONS), default=0)
        except (ImportError, AttributeError, KeyError):
            # narrow (engine-fix round): Exception also ate genuine bugs in
            # the migrations module itself and reported them as "schema 0"
            _schema_version_cache = 0
    return _schema_version_cache


def __getattr__(name: str):
    # PEP 562: `from config import SCHEMA_VERSION` works like a normal
    # module attribute but resolves after the import cycle is safe
    if name == "SCHEMA_VERSION":
        return current_schema_version()
    raise AttributeError(f"module 'config' has no attribute {name!r}")

ASCII_HEADER = """
    ·  ✦  ·     ·  ✦  ·     ·  ✦  ·
         UNIVERSE AGENT
    ✦ ─────────── v{ver} ─────────── ✦
      {tagline}
""".format(ver=UNIVERSE_VERSION, tagline=TAGLINE)

# Color palette (§23) — exposed for any TTY rendering that wants raw hex.
PALETTE = {
    "deep_space": "#0A0A14",
    "void": "#1A0A2E",
    "nebula": "#6B21A8",
    "pulsar": "#A855F7",
    "quasar": "#E879F9",
    "starlight": "#F0E6FF",
    "stardust": "#C084FC",
    "dark_matter": "#4C1D95",
    "signal": "#22D3EE",
    "active": "#4ADE80",
    "warning": "#F59E0B",
}

# Sliding context-budget percentages (§13).
CONTEXT_BUDGET = {
    "system_prompt": 0.05,
    "l4_skills": 0.15,
    "l3_stars": 0.10,
    "conversation": 0.50,
    "file_contents": 0.10,
    "buffer": 0.10,
}

# Gravity Score thresholds (§3).
GRAVITY_THRESHOLDS = {
    "nebula": 0.30,        # <0.30 = nebula — low value; deleted after 14 days by the Forgetting Engine
    "asteroid": 0.60,      # 0.30–0.60 asteroid (kept)
    "planet_to_l3": 0.85,  # 0.60–0.85 promoted to L3
    "star_permanent": 0.85,  # 0.85+ permanent L3 Star
}
SUBCONSCIOUS_PROMOTE_AT = 0.45  # idle-time promotion threshold (§3)

# Skill confidence lifecycle (§18).
SKILL_CONFIDENCE = {
    "trusted_load": 0.95,
    "trusted_load_lower": 0.90,
    "idle_decay_to": 0.70,
    "idle_decay_days": 90,
    "success_bump_to": 0.95,
    "failure_drop_to": 0.50,
    "needs_review_below": 0.55,
}

# Moon probation (§4).
MOON_PROBATION_GOALS = 3
MAX_MOONS_PER_PLANET = 4  # backpressure (§14)

# Retention (§3 foundational hardening).
ASTEROID_MIN_RETENTION_DAYS = 7

# Subconscious Loop bounds (§3).
SUBCONSCIOUS_INTERVAL_SECONDS = 30 * 60  # 30 min
SUBCONSCIOUS_MAX_CYCLE_SECONDS = 5 * 60  # 5 min per cycle

# The trusted skill sources (§5) — the complete allowlist. Extended
# 2026-07 to cover agents the original four left at zero (browser, file,
# data) or near-zero (write): each verified directly against its own
# official upstream repo's LICENSE, not inferred from any aggregator/index
# (see docs/SKILLS_EXPANSION.md for the full per-source verification and
# the sources deliberately excluded on licensing grounds — trailofbits/skills
# is CC-BY-SA-4.0, incompatible with the MIT/Apache-2.0-only policy below,
# same reasoning as the existing OpenHuman/GPL-3.0 exclusion).
TRUSTED_SKILL_SOURCES: tuple[str, ...] = ()  # purged 2026-10-31 — 0 skills, corpus deleted per user


class UniverseConfig:
    """Thread-safe configuration singleton.

    All paths are resolved lazily from UNIVERSE_HOME so tests can point at a
    temp directory. The singleton is intentionally simple — no hot-reload of
    config.json beyond construction — because configuration changes that
    matter (provider keys, models) go through the live slash-command wizards, not
    by editing config.json at runtime.
    """

    _instance: "UniverseConfig | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "UniverseConfig":
        # the documented contract is lazy re-resolution from UNIVERSE_HOME;
        # a singleton built before a fixture's setenv must never survive,
        # or every later cfg.set("project_root", tmp) lands in the REAL
        # user home (2026-10-25: a pytest run wrote a deleted pytest temp
        # dir in as project_root and every shell cwd died with WinError
        # 267 until it was found and fixed).
        env_home = _default_universe_home()
        stale = cls._instance is not None and cls._instance.home != env_home
        if stale:
            with cls._lock:
                if cls._instance is not None and cls._instance.home != env_home:
                    cls._instance = None
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self.home: Path = _default_universe_home()
        self.db_path: Path = self.home / "universe.db"
        self.vault_dir: Path = self.home / "memory_vault"
        self.skills_dir: Path = self.home / "skills"
        self.orbits_dir: Path = self.home / "orbits"
        self.connectors_dir: Path = self.home / "connectors"
        self.checkpoints_dir: Path = self.home / "checkpoints"
        self.maps_dir: Path = self.home / "maps"
        self.audit_log: Path = self.home / "audit.log"
        self.llm_log: Path = self.home / "llm_calls.jsonl"
        self.config_file: Path = self.home / "config.json"
        self.timeline_dir: Path = self.home / "timelines"
        self.eval_history: Path = self.home / "eval-history.json"
        # Runtime flags (env-driven, never persisted — these are session concerns)
        self.debug: bool = os.environ.get("GAX_DEBUG", "") == "1"
        self.offline: bool = os.environ.get("GAX_OFFLINE", "") == "1"
        self.otel_endpoint: str | None = os.environ.get("GAX_OTEL_ENDPOINT")
        # User-editable overrides loaded from config.json (empty until /setup writes it)
        self._overrides: dict[str, Any] = {}
        self._load_overrides()

    def _load_overrides(self) -> None:
        if not self.config_file.exists():
            return
        try:
            self._overrides = json.loads(self.config_file.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            # the atomic write (see _persist) should make this unreachable,
            # but a hand-edited config.json with a syntax error used to
            # reset EVERY override silently — warn loudly instead
            from loguru import logger
            logger.warning("config.json is corrupt ({}); starting with "
                           "defaults until it is fixed", exc)
            self._overrides = {}

    @staticmethod
    def _coerce_env(env_val: str, default: Any) -> Any:
        """Type-aware env override (audit L5): the raw string "0" used to
        read as a truthy value for boolean keys and "1" as "1" for int
        keys — coerce against the default's type instead."""
        if isinstance(default, bool):
            return env_val.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(env_val.strip())
            except ValueError:
                return env_val
        if isinstance(default, float):
            try:
                return float(env_val.strip())
            except ValueError:
                return env_val
        return env_val

    def get(self, key: str, default: Any = None) -> Any:
        # env wins, then override, then default
        env_val = os.environ.get("GAX_" + key.upper())
        if env_val is not None:
            return self._coerce_env(env_val, default)
        return self._overrides.get(key, default)

    def set(self, key: str, value: Any) -> None:
        # under the class lock: two concurrent set() calls (CLI thread +
        # bridge thread) could interleave _overrides mutation and _persist,
        # writing one update twice or clobbering the other's change
        with UniverseConfig._lock:
            self._overrides[key] = value
            self._persist()

    def _persist(self) -> None:
        # atomic write (audit 2026-09): a crash mid-write used to truncate
        # config.json, and _load_overrides then swallowed the JSONDecodeError
        # and silently reset the WHOLE config to {} (losing language,
        # desktop prefs, project_root). tempfile + fsync + os.replace means
        # readers only ever see the old or the new complete file.
        self.home.mkdir(parents=True, exist_ok=True)
        import os as _os
        import tempfile as _tempfile
        fd, tmp = _tempfile.mkstemp(prefix=".config.", suffix=".tmp",
                                    dir=str(self.home))
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self._overrides, indent=2))
                fh.flush()
                _os.fsync(fh.fileno())
            _os.replace(tmp, self.config_file)
        finally:
            if _os.path.exists(tmp):
                _os.unlink(tmp)

    def ensure_dirs(self) -> None:
        """Create every directory Universe expects to exist. Idempotent."""
        for d in (
            self.home, self.vault_dir, self.skills_dir, self.orbits_dir,
            self.connectors_dir, self.checkpoints_dir, self.maps_dir,
            self.timeline_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def reset_for_tests(self, home: Path) -> None:
        """Repoint the singleton at a fresh temp home (tests only)."""
        self.home = home
        self.db_path = home / "universe.db"
        self.vault_dir = home / "memory_vault"
        self.skills_dir = home / "skills"
        self.orbits_dir = home / "orbits"
        self.connectors_dir = home / "connectors"
        self.checkpoints_dir = home / "checkpoints"
        self.maps_dir = home / "maps"
        self.audit_log = home / "audit.log"
        self.llm_log = home / "llm_calls.jsonl"
        self.config_file = home / "config.json"
        self.timeline_dir = home / "timelines"
        self.eval_history = home / "eval-history.json"
        self._overrides = {}
        self.ensure_dirs()


def get_config() -> UniverseConfig:
    """Module-level accessor used everywhere in the codebase."""
    return UniverseConfig()
