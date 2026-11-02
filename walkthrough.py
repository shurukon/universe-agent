"""walkthrough.py — produces the §STEP 6 '5-minute first goal' transcript.

Simulates a fresh user: clean ~/.universe → /setup (3 steps) → /goal that
engages 3+ Core Agents with a real handoff chain → final summary. Prints a
real transcript to stdout.

Requires a real provider (UNIVERSE_TEST_BASE_URL / UNIVERSE_TEST_API_KEY /
UNIVERSE_TEST_MODEL) — there is no built-in model anymore.

Everything lives inside main(): importing this module has NO side effects
(m2) — the old import-time HOME wipe + DB reset fired on `import
walkthrough`, which poisoned any test or tool that merely imported it.
Run it as a script: `python walkthrough.py`.
"""
import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))


def _reset_to_fresh_install():
    """Point every Universe subsystem at a throwaway HOME and re-init it.
    Only runs when the walkthrough actually executes."""
    home = os.environ.get(
        "UNIVERSE_WALKTHROUGH_HOME",
        os.path.join(tempfile.gettempdir(), "gax_walkthrough"))
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home, exist_ok=True)
    os.environ["UNIVERSE_HOME"] = home

    from config import get_config
    cfg = get_config()
    cfg.reset_for_tests(Path(home))
    cfg.ensure_dirs()
    from storage.local import reset_storage_for_tests
    reset_storage_for_tests(cfg.db_path)
    from schema.migrations import ensure_latest
    ensure_latest()
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
    reset_gate_for_tests().set_auto_grant(True)


def _install_test_provider():
    """Wire the real test provider exactly the way the bridge does."""
    test_base = os.environ.get("UNIVERSE_TEST_BASE_URL", "")
    test_key = os.environ.get("UNIVERSE_TEST_API_KEY", "")
    test_model = os.environ.get("UNIVERSE_TEST_MODEL", "")
    if not (test_base and test_key and test_model):
        return False
    from providers.manager import get_provider_manager
    from security.secrets_fallback import encrypt_secret
    from core.core_agents.agents import ALL_AGENTS
    pm = get_provider_manager()
    pm.add_provider("Test Provider", test_base)
    pm.add_key("Test Provider", alias="walkthrough",
               encrypted_secret=encrypt_secret(test_key), tier="paid")
    for agent in list(ALL_AGENTS):
        pm.set_agent_model(agent, "Test Provider", test_model)
    return True


class TranscriptIO:
    def __init__(self):
        self.inputs = []
        self._idx = 0

    def print(self, *a, **k):
        print(*a, **k)

    def input(self, prompt=""):
        if self._idx < len(self.inputs):
            val = self.inputs[self._idx]
            self._idx += 1
            print(f"{prompt}{val}")
            return val
        return ""

    def confirm(self, prompt):
        return self.input(f"{prompt} [y/N] ").lower() in ("y", "yes")

    def pause(self, prompt=""):
        pass

    def feed(self, *lines):
        self.inputs.extend(lines)


async def main():
    _reset_to_fresh_install()
    if not _install_test_provider():
        print("=" * 70)
        print("WALKTHROUGH REQUIRES A REAL PROVIDER")
        print("=" * 70)
        print("Universe Agent has no built-in model. Set these and re-run:")
        print("  UNIVERSE_TEST_BASE_URL  e.g. https://api.groq.com/openai/v1")
        print("  UNIVERSE_TEST_API_KEY   your real key")
        print("  UNIVERSE_TEST_MODEL     e.g. llama-3.3-70b-versatile")
        return 2
    io = TranscriptIO()
    # --- /setup inputs ---
    io.feed(
        "en",        # language
        "n",         # add a provider via the wizard? no — pre-wired above
        "Ada",       # name
        "Developer", # profession
        "n",         # Free Mode?
        "n",         # Subconscious Loop?
    )
    from cli.setup import run_setup
    print("\n" + "=" * 70)
    print("TRANSCRIPT: Fresh install → /setup → /goal (5-minute first goal)")
    print("=" * 70)
    print("\n$ python -m cli")
    print("universe> /setup\n")
    await run_setup(io)

    # --- /goal ---
    print("\n" + "=" * 70)
    print("universe> /goal write a python function to read a csv file and print the rows")
    print("=" * 70 + "\n")
    from cli.slash_commands import register_all, get_command
    register_all()
    cmd = get_command("goal")
    start = time.time()
    await cmd.handler(["write a python function to read a csv file and print the rows"], io)
    elapsed = time.time() - start
    print(f"\n[walkthrough complete in {elapsed:.1f}s]")

    # --- /memory to show what was learned ---
    print("\n" + "=" * 70)
    print("universe> /memory")
    print("=" * 70 + "\n")
    cmd = get_command("memory")
    await cmd.handler([], io)

    # --- /version ---
    print("\n" + "=" * 70)
    print("universe> /version")
    print("=" * 70 + "\n")
    cmd = get_command("version")
    await cmd.handler([], io)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
