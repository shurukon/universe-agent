"""tests/conftest.py — shared fixtures for the Universe test suite.

LLM-backed tests need a REAL provider: set UNIVERSE_TEST_BASE_URL,
UNIVERSE_TEST_API_KEY and UNIVERSE_TEST_MODEL (e.g. a free Groq key) and they
run; without them they skip honestly. There is no mock model anymore —
Universe Agent only works with real providers.
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

# The secrets fallback FAILS CLOSED without an explicit passphrase (audit
# 2026-10) and CI/sandboxes have no OS keychain. Provide the documented
# user-supplied env source so tests exercise real encrypt/decrypt instead
# of erroring out with SecretsUnavailableError.
os.environ.setdefault("UNIVERSE_PASSPHRASE", "universe-test-passphrase")

# ensure the universe-agent package root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def install_test_provider() -> bool:
    """Wire the real test provider (from env) into the manager exactly the
    way the bridge does. Returns True when all three env vars are present."""
    base = os.environ.get("UNIVERSE_TEST_BASE_URL", "")
    key = os.environ.get("UNIVERSE_TEST_API_KEY", "")
    model = os.environ.get("UNIVERSE_TEST_MODEL", "")
    if not (base and key and model):
        return False
    from providers.manager import get_provider_manager
    from security.secrets_fallback import encrypt_secret
    from core.core_agents.agents import ALL_AGENTS
    pm = get_provider_manager()
    pm.add_provider("Test Provider", base)
    pm.add_key("Test Provider", alias="test", encrypted_secret=encrypt_secret(key),
               tier="paid", status="high")
    for agent in list(ALL_AGENTS) + ["orchestrator"]:
        pm.set_agent_model(agent, "Test Provider", model)
    return True


def pytest_collection_modifyitems(config, items):
    """Skip llm-marked tests honestly when no real key is configured."""
    if os.environ.get("UNIVERSE_TEST_API_KEY"):
        return
    skip = pytest.mark.skip(reason="UNIVERSE_TEST_API_KEY (and BASE_URL/MODEL) not set — "
                                   "LLM tests need a real provider")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    """Sweep ORPHANED temp homes from previous killed runs.

    A full suite mints ~620 gax-test-* dirs; a hard kill (server restart,
    disk death, crash) ends the run without teardown and leaves them all
    behind — 4515 dirs / 57 GB filled D: to zero once (2026-10-28). Any
    dir older than 6h cannot belong to a live run: safe to remove."""
    import time as _time
    root = Path(tempfile.gettempdir())
    now = _time.time()
    for d in root.glob("gax-test-*"):
        try:
            if now - d.stat().st_mtime > 6 * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@pytest.fixture
def fresh_home(monkeypatch):
    """A fresh ~/.universe pointing at a temp dir, with migrations applied."""
    tmp = Path(tempfile.mkdtemp(prefix="gax-test-"))
    from config import get_config
    cfg = get_config()
    cfg.reset_for_tests(tmp)
    cfg.ensure_dirs()
    monkeypatch.setenv("UNIVERSE_HOME", str(tmp))
    # the bridge's auth gate now generates a random token when none is set
    # (audit 2026-09); the suite opts into the documented dev contract here
    monkeypatch.setenv("UNIVERSE_BRIDGE_TOKEN", "dev")
    from storage.local import reset_storage_for_tests
    reset_storage_for_tests(cfg.db_path)
    from schema.migrations import ensure_latest
    ensure_latest()
    # reset all singletons (order matters: storage first, then everything that
    # caches a storage reference at construction time)
    from core.memory.orbits import get_orbits
    import core.memory.orbits as _orbits_mod
    _orbits_mod._orbits = None  # force re-creation against the new storage
    get_orbits()  # warm cache
    from providers.manager import reset_provider_manager_for_tests
    reset_provider_manager_for_tests()
    from providers.client import reset_llm_client_for_tests
    reset_llm_client_for_tests()
    from core.memory import reset_memory_for_tests
    reset_memory_for_tests()
    from core.core_agents.agents import reset_agents_for_tests
    reset_agents_for_tests()
    from core.agent.orchestrator import reset_orchestrator_for_tests
    reset_orchestrator_for_tests()
    from connectors.builtin import reset_registry_for_tests
    reset_registry_for_tests()
    from security.capability import reset_gate_for_tests
    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    import core.memory.subconscious.loop as _loop_mod
    _loop_mod._loop = None
    # Phase-1 harness singletons (todo state is per-goal, no reset needed;
    # hooks/questions/scheduler hold process-global state)
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    from connectors.builtin.ask_user import reset_question_handler_for_tests
    reset_question_handler_for_tests()
    from universe_desktop_bridge.questions import reset_questions_broker_for_tests
    reset_questions_broker_for_tests()
    from scheduling import reset_scheduler_for_tests
    reset_scheduler_for_tests()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def fresh_home_with_skills(fresh_home):
    """Fresh home with the real bundled skills (ECC, UI UX Pro Max, Open
    Design, Anthropic Skills — see SOURCES.md) ingested."""
    from skills.loader import load_all_skills
    load_all_skills()
    return fresh_home


@pytest.fixture
def real_provider(fresh_home):
    """Fresh home + the real test provider installed from env. Test functions
    depending on this run only with UNIVERSE_TEST_* set (mark them @llm too —
    the module-level skip needs the marker)."""
    ok = install_test_provider()
    if not ok:
        pytest.skip("UNIVERSE_TEST_API_KEY (and BASE_URL/MODEL) not set — "
                    "LLM tests need a real provider")
    return fresh_home

