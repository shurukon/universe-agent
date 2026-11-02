"""Acceptance-contract verdict override tests (live round-4 findings).

The full-stack validation goal WROTE server.py + index.html and the
acceptance contract verified the real tool evidence (success=True), yet the
goal read 'failed' because the FINAL review step returned model prose — a
free model emitted a non-JSON narration blob as its text answer instead of
the handoff JSON. complete_task now lets the stamped acceptance contract
carry the goal verdict while the step record stays truthfully failed
(honesty rule preserved: a failed step never REWRITES to success).

pytest-collected only (testpaths=['tests']); no production importer.
Data: fresh_home temp DB - asteroids.row.task_success (INTEGER), goals,
planets; timestamps epoch-float. No secrets, no network.
"""
from __future__ import annotations

# a model answer that is prose, not the handoff JSON (parse_error scenario)
PROSE_REPLY = ("I ran the shell command and everything looks great, the app "
               "is complete and working — no JSON here, just narration.")


class TestAcceptanceVerdictOverride:
    def test_acceptance_overrides_prose_mangled_final_step(self, fresh_home):
        from core.memory import get_memory
        mem = get_memory()
        planet = mem.l1.create_planet(
            turn_id="g-prose", goal_text="build it",
            classification={"category": "code_generation", "domain": "python",
                            "intent": "write", "complexity": "low"},
            language="en", owner_session_id="s")
        handoffs = [
            {"agent": "code", "task_success": True,
             "what_was_done": "wrote server.py + index.html",
             "tools_used": ["Write"]},
            {"agent": "review", "task_success": False,
             "what_was_done": PROSE_REPLY,
             "tools_used": [],
             "context_for_memory": {"parse_error": True,
                                    "acceptance_success": True}},
        ]
        ast = mem.complete_task(planet=planet, handoffs=handoffs)
        assert ast.task_success is True, (
            "a verified acceptance must outweigh a prose-mangled final step")

    def test_no_override_without_acceptance_stamp(self, fresh_home):
        """Without the acceptance stamp the final-step verdict stands:
        a genuinely failed last step is NOT laundered into success."""
        from core.memory import get_memory
        mem = get_memory()
        planet = mem.l1.create_planet(
            turn_id="g-plain-fail", goal_text="build it",
            classification={"category": "code_generation", "domain": "python",
                            "intent": "write", "complexity": "low"},
            language="en", owner_session_id="s")
        handoffs = [
            {"agent": "code", "task_success": True,
             "what_was_done": "tried", "tools_used": ["Write"]},
            {"agent": "review", "task_success": False,
             "what_was_done": "Acceptance failed: no artifact",
             "tools_used": [], "context_for_memory": {}},
        ]
        ast = mem.complete_task(planet=planet, handoffs=handoffs)
        assert ast.task_success is False

    def test_no_override_when_no_step_succeeded(self, fresh_home):
        """Acceptance stamp present but EVERY step failed (including the
        earlier ones) => no success: the override requires at least one
        genuinely successful handoff as evidence."""
        from core.memory import get_memory
        mem = get_memory()
        planet = mem.l1.create_planet(
            turn_id="g-all-fail", goal_text="build it",
            classification={"category": "code_generation", "domain": "python",
                            "intent": "write", "complexity": "low"},
            language="en", owner_session_id="s")
        handoffs = [
            {"agent": "code", "task_success": False,
             "what_was_done": "failed", "tools_used": []},
            {"agent": "review", "task_success": False,
             "what_was_done": PROSE_REPLY,
             "tools_used": [],
             "context_for_memory": {"parse_error": True,
                                    "acceptance_success": True}},
        ]
        ast = mem.complete_task(planet=planet, handoffs=handoffs)
        assert ast.task_success is False
