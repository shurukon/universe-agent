"""Regression test for the CLI slash handler NameError (found 2026-10-21).

cli/repl.py `_handle_slash` called get_command(cmd_name) — cmd_name was never
defined, so EVERY slash command raised NameError before dispatch. The fix
dispatches on `name`.

NOTE: the registry only fills via register_all(); these tests call it
explicitly and assert the REAL handler ran (not the "Unknown command"
fallback) so the coverage cannot be vacuous.
"""
import asyncio
import sys

sys.path.insert(0, ".")  # run from the repo root (pytest testpaths=tests)

from cli import repl  # noqa: E402
from cli.slash_commands import get_command, register_all  # noqa: E402


class _Environ:
    """ConsoleIO stand-in that just records printed lines."""

    def __init__(self):
        self.lines = []

    def print(self, text=""):
        self.lines.append(str(text))


def _dispatch(line: str) -> _Environ:
    register_all()
    io = _Environ()
    asyncio.run(repl._handle_slash(line, io))
    return io


def test_slash_help_dispatches_for_real():
    io = _dispatch("/help")
    joined = "\n".join(io.lines)
    assert "NameError" not in joined
    assert not any("Unknown command" in line for line in io.lines), (
        "command must resolve — /help is registered once register_all() ran"
    )
    assert io.lines


def test_slash_unknown_command_reports_cleanly():
    io = _dispatch("/definitely-not-a-command-xyz")
    assert any("Unknown command" in line for line in io.lines)


def test_registry_expected_core_commands_present():
    register_all()
    for name in ("goal", "status", "history", "help"):
        assert get_command(name) is not None, name
