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

# ensure the universe-agent package root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def mock_llm():
    """The shared OpenAI-compatible mock server on :8399 — started ONCE per
    session. The previous module-scoped copies raced each other's teardown:
    a module saw the port alive while the previous server was still exiting,
    skipped starting its own, and the next LLM call hit a dead socket."""
    import time as _t
    import urllib.request as _u

    def _alive(port: int) -> bool:
        try:
            with _u.urlopen(f"http://127.0.0.1:{port}/v1/models",
                            timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    if _alive(8399):
        yield None
        return
    from test_honesty_fixes import _MockOpenAI
    srv = _MockOpenAI()
    srv.start()
    yield srv
    srv.stop()


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
def fresh_home_with_skills(fresh_home, monkeypatch, tmp_path):
    """Fresh home with a SMALL SEEDED skills corpus.

    The bundled 1,584-skill library was purged (2026-10-31 — skills/loader.py
    is an empty-catalog stub, config.TRUSTED_SKILL_SOURCES == ()), so this
    fixture seeds a handful of skills through the product's own ingestion
    pipeline (skill.ingestion.ingest_source + skill.taxonomy.rebuild_taxonomy)
    from a temp corpus dir. The trusted-source allowlist is a TEST allowlist
    (the tier machinery in quarantine_check/approve is live product code);
    tests that need the purge reality itself use plain fresh_home.
    """
    import config as config_mod
    import skill.ingestion as ingestion_mod
    allow = ("ECC", "UIUXProMax", "OpenDesign", "AnthropicSkills")
    monkeypatch.setattr(config_mod, "TRUSTED_SKILL_SOURCES", allow)
    monkeypatch.setattr(ingestion_mod, "TRUSTED_SKILL_SOURCES", allow)

    def md(name: str, source: str, desc: str, *, category: str,
           target_agent: str = "") -> str:
        return (
            "---\n"
            f"name: {name}\n"
            f"source: {source}\n"
            'version: "1.0.0"\n'
            f"description: {desc}\n"
            'tags: ["seed"]\n'
            "triggers: []\n"
            "license: MIT\n"
            f"target_agent: {target_agent}\n"
            f"category: {category}\n"
            "---\n"
            f"{desc}. Step 1: prepare. Step 2: verify the result.\n")

    corpus = tmp_path / "skills_data"
    files = {
        "ECC": [
            ("python-debugging", "Debug python tracebacks and fix errors fast",
             "code_generation", "code"),
            ("express-backend-api", "Express backend API with routes and tests",
             "code_generation", "code"),
            ("api-design", "Design a REST API with resources and versioning",
             "api_design", ""),
        ],
        "UIUXProMax": [
            ("web-ui-checklist", "Web UI checklist for accessible interfaces",
             "ui_ux_design", "design"),
        ],
        "OpenDesign": [
            ("design-review", "Design review pass over visual systems",
             "design_system", ""),
        ],
        "AnthropicSkills": [
            ("version-control", "Version control hygiene with git",
             "tech_stack", "code"),
        ],
    }
    for source, skills in files.items():
        src_dir = corpus / source
        src_dir.mkdir(parents=True)
        for name, desc, category, target in skills:
            (src_dir / f"{name}.md").write_text(
                md(name, source, desc, category=category, target_agent=target),
                encoding="utf-8")
        ingestion_mod.ingest_source(src_dir)
    from skill.taxonomy import rebuild_taxonomy
    rebuild_taxonomy()
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

