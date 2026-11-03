"""cli/repl.py — main REPL loop (Chat mode + slash commands).

§9, §25 Phase 6 ㉓. Chat mode is pure conversation; every slash command
from §8 is registered via cli/slash_commands.py (the goal mode is retired
— 2026-10-28 — one turn engine runs every task).

Entry point: `python -m cli` or the `universe` console script.
"""
from __future__ import annotations

import asyncio

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from config import ASCII_HEADER, get_config
from schema.migrations import ensure_latest


class ConsoleIO:
    """IO adapter so wizards can be driven interactively or programmatically."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._scripted_inputs: list[str] = []
        self._outputs: list[str] = []

    def print(self, *args, **kwargs) -> None:
        line = " ".join(str(a) for a in args)
        self._outputs.append(line)
        self.console.print(line, **kwargs)

    def input(self, prompt: str = "") -> str:
        if self._scripted_inputs:
            val = self._scripted_inputs.pop(0)
            self.console.print(f"{prompt}{val}")
            return val
        return Prompt.ask(prompt) if prompt else input()

    def confirm(self, prompt: str) -> bool:
        if self._scripted_inputs:
            return self._scripted_inputs.pop(0).lower() in ("y", "yes")
        return Prompt.ask(f"{prompt} [y/N]", choices=["y", "n"], default="n") == "y"

    def pause(self, prompt: str = "Press Enter to continue...") -> None:
        if not self._scripted_inputs:
            input(prompt)

    def feed(self, *lines: str) -> None:
        """Programmatic input for tests/eval."""
        self._scripted_inputs.extend(lines)

    def outputs(self) -> list[str]:
        return list(self._outputs)


async def repl_loop(io: ConsoleIO | None = None) -> None:
    """The main REPL. Reads input, dispatches to chat or slash commands."""
    from cli.slash_commands import register_all
    from core.agent.orchestrator import get_orchestrator
    register_all()
    io = io or ConsoleIO()
    from core.memory.subconscious.loop import get_loop
    from security.capability import get_gate
    subconscious = get_loop()
    subconscious.start()
    # configured channels boot with the REPL (audit H4: TelegramChannel was
    # complete but nothing ever called start() — the channel never came up).
    # A channel that fails to boot is logged and skipped, never fatal.
    from channels import start_channels, stop_channels
    try:
        booted_channels = await start_channels()
        if booted_channels:
            io.print(f"Channels live: {', '.join(booted_channels)}")
    except Exception as _ch_err:
        io.print(f"[dim]Channels did not start: {_ch_err}[/dim]")

    async def consent_handler(tool, agent: str, turn_id: str, args: dict) -> bool:
        # full redacted argument vector, not a one-line preview: a truncated
        # summary hid injected shell text inside docker/k8s args (audit 2026-09)
        import json as _json
        from security.secret_filter import redact_dict
        target = args.get("path") or args.get("url") or args.get("cmd") or ""
        io.print(f"Consent required: {tool.name} ({agent}) {target}")
        io.print(f"  args: {_json.dumps(redact_dict(args), ensure_ascii=False, default=str)[:4000]}")
        return io.confirm("Approve this tool for the current goal?")

    get_gate().set_consent_handler(consent_handler)
    io.console.print(ASCII_HEADER)
    # ensure first-launch setup
    cfg = get_config()
    if not cfg.get("setup_complete", False):
        io.console.print("[dim]First launch detected. Run /setup to configure, or just start typing.[/dim]")
    while True:
        try:
            line = io.input("\nuniverse> ")
        except (EOFError, KeyboardInterrupt):
            io.print("\nGoodbye. The mind that remembers everything.")
            await stop_channels()
            subconscious.stop()
            break
        line = line.strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            io.print("Goodbye. The mind that remembers everything.")
            await stop_channels()
            subconscious.stop()
            break
        if line.startswith("/"):
            await _handle_slash(line, io)
        else:
            # chat mode
            try:
                orch = get_orchestrator()
                from cli.session_state import current_session_id
                reply = await orch.chat(line, session_id=current_session_id())
                io.console.print(Panel(reply, title="Universe", border_style="purple"))
            except Exception as e:
                io.print(f"[red]Error:[/red] {e}")


def _split_args(raw: str) -> list[str]:
    """Quote-aware whitespace split for slash-command arguments.

    `/import "C:\\My Files\\archive.zip"` used to pass C:\\My to the wizard
    and silently operate on the wrong path. shlex with posix=True eats the
    backslashes Windows paths need, so this is a minimal double-quote-
    aware splitter: tokens inside "..." stay one argument, quotes are
    stripped, a lone closing quote is tolerated (treated as literal text).
    """
    parts: list[str] = []
    cur: list[str] = []
    in_quotes = False
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch.isspace() and not in_quotes:
            if cur:
                parts.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


async def _handle_slash(line: str, io: ConsoleIO) -> None:
    from cli.slash_commands import get_command
    parts = _split_args(line[1:])
    if not parts:
        return
    name = parts[0]
    args = parts[1:]
    cmd = get_command(name)
    if cmd is None:
        # try matching /agent <name>, /channel <name> as the base command
        cmd = get_command(name)
    if cmd is None:
        io.print(f"Unknown command: /{name}. Type /help for the list.")
        return
    try:
        result = cmd.handler(args, io)
        if asyncio.iscoroutine(result):
            result = await result
        if result and isinstance(result, str):
            io.print(result)
    except Exception as e:
        io.print(f"[red]Command failed:[/red] {e}")
        if get_config().debug:
            import traceback
            traceback.print_exc()


def main() -> None:
    """Entry point."""
    cfg = get_config()
    cfg.ensure_dirs()
    ensure_latest()
    # apply the documented 90-day audit retention (was never wired —
    # audit 2026-09) and verify the hash chain's seam cheaply
    from security.audit import prune_at_startup, verify_chain
    pruned = prune_at_startup()
    chain = verify_chain(limit=500)
    if pruned or not chain["ok"]:
        print(f"audit: pruned {pruned} old entries; chain ok={chain['ok']}")
    # load skills on first launch
    from skills.loader import load_all_skills, skill_counts_by_source
    if not skill_counts_by_source():
        load_all_skills()
    asyncio.run(repl_loop())


if __name__ == "__main__":
    main()
