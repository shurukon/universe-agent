"""universe_desktop_bridge/slash.py — ZCode's slash-command surface for Universe.

Extracted verbatim from the installed ZCode runtime (docs/zcode-re-map.md
section 1): the 20 built-ins, their aliases, and the .md command-file
discovery order + frontmatter contract. Commands with a Universe counterpart
report an `action` the desktop client can run; commands ZCode has that
Universe does not report a `supported: false` placeholder instead of pretending.

The bridge owns the registry so the desktop UI and the CLI share one truth.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SlashCommand:
    name: str
    summary: str
    usage: str
    action: str  # client action id, or "" when unsupported
    aliases: tuple[str, ...] = ()
    argument_hint: str = ""
    allowed_tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "usage": self.usage,
            "action": self.action,
            "aliases": list(self.aliases),
            "argument_hint": self.argument_hint,
            "allowed_tools": list(self.allowed_tools),
            "supported": bool(self.action),
        }


BUILTINS: tuple[SlashCommand, ...] = (
    SlashCommand("help", "Show this slash command help.", "/help [command]", "help"),
    SlashCommand("login", "Set up a Coding Plan provider.", "/login [zai-coding-plan|bigmodel-coding-plan|...]", ""),
    SlashCommand("logout", "Remove the shared Z.ai login credentials.", "/logout", ""),
    SlashCommand("compact", "Compact the current conversation with optional instructions.", "/compact [instructions]", "compact"),
    SlashCommand("init", "Create or update workspace AGENTS.md instructions.", "/init [notes]", ""),
    SlashCommand("expert", "Run or manage the expert workflow.", "/expert [status|resume|stop|<task>]", ""),
    SlashCommand("effort", "Show or switch the current session reasoning effort.", "/effort [list|<level>]", "effort"),
    SlashCommand("workflow", "Create, validate, or run a script workflow.", "/workflow", ""),
    SlashCommand("workflows", "List or inspect workflow runs.", "/workflows [runId]", ""),
    SlashCommand("fork", "Fork a new session from a workspace checkpoint.", "/fork [latest|checkpointId]", "fork"),
    SlashCommand("locale", "Show or switch the UI locale.", "/locale [auto|en-US|zh-CN]", "locale"),
    SlashCommand("mcp", "Show or manage configured MCP servers.", "/mcp [list|status|connect <srv>|disconnect <srv>]", "settings:mcp"),
    SlashCommand("plugins", "Open the plugin manager.", "/plugins [list|enable <p>|disable <p>]", ""),
    SlashCommand("mode", "Show or switch the current permission mode.", "/mode [ask|plan|edit|full]", "mode"),
    SlashCommand("model", "Show or switch the current session model.", "/model [list|main|lite|provider/model]", "model"),
    SlashCommand("new", "Start a fresh session.", "/new", "new"),
    SlashCommand("resume", "Resume a saved session.", "/resume [sessionId]", "resume", aliases=("continue",)),
    SlashCommand("rewind", "Inspect or restore workspace checkpoints.", "/rewind [latest|checkpointId]", "rewind"),
    SlashCommand("skill", "List skills, or force the next prompt to load one.", "/skill [<skill-name> [task]]", "settings:skills"),
    # /goal is DELETED (2026-10-28): one turn engine runs every task —
    # there is no long-horizon goal mode to switch into.
    # Universe surfaces that existed as local composer actions before the
    # ZCode-parity registry: kept as built-ins so /settings etc. stay live
    SlashCommand("settings", "Open Settings", "/settings", "settings", aliases=("setting",)),
    SlashCommand("automations", "Manage scheduled goals", "/automations", "automations"),
    SlashCommand("terminal", "Toggle the terminal pane", "/terminal", "terminal"),
    # ---- Round-7 Claude Code parity additions (claude binary category
    # map/actions; summaries are the RE'd descriptions where captured).
    # Only commands with a REAL Universe surface are wired with an action;
    # no fake placeholders.
    SlashCommand("agents", "Manage agent configurations", "/agents", "agents"),
    SlashCommand("memory", "Show memory bank status and stored knowledge", "/memory", "memory"),
    SlashCommand("hooks", "List configured hooks", "/hooks", "hooks"),
    SlashCommand("permissions", "Show or switch the permission mode", "/permissions [plan|build|edit|auto|yolo]", "permission"),
    SlashCommand("status", "Show version, model, provider, and DB status", "/status", "status"),
    SlashCommand("context", "Show a context usage breakdown for this task", "/context", "context"),
    SlashCommand("clear", "Clear the input and start a fresh task", "/clear", "new"),
    SlashCommand("export", "Save the current task transcript to a markdown file", "/export", "export"),
    SlashCommand("plan", "Show or switch the plan mode", "/plan [on|off]", "planmode"),
    SlashCommand("skills", "List available skills", "/skills", "skill"),
    SlashCommand("doctor", "Check the connection health", "/doctor", "doctor"),
    SlashCommand("version", "Show the Universe desktop version", "/version", "version"),
    # NOTE: no builtin /review — user .md command files own that name the
    # same way ZCode lets a custom command shadow a default (resolve()
    # tries builtins first, so a builtin here would hide real custom files).
)

_BY_NAME = {c.name: c for c in BUILTINS}
for _c in BUILTINS:
    for _a in _c.aliases:
        _BY_NAME.setdefault(_a, _c)

# ZCode's reserved name — filtered from the live menu even if a .md file
# uses it (the runtime owns it).
_RESERVED = {"compress"}


@dataclass
class CommandFile:
    """A discovered .md command (frontmatter contract from the zcode-guide
    diagnosing-commands skill)."""
    name: str                # file stem, subdirs join with ":"
    description: str
    argument_hint: str
    allowed_tools: tuple[str, ...]
    model: str
    skills: tuple[str, ...]
    disable_noninteractive: bool
    body: str                # the prompt; $ARGUMENTS/$1 substitute at run time
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "summary": self.description, "usage": self.usage(),
            "action": "custom", "argument_hint": self.argument_hint,
            "allowed_tools": list(self.allowed_tools), "supported": True,
            "kind": "custom", "source": self.source,
        }

    def usage(self) -> str:
        hint = f" {self.argument_hint}" if self.argument_hint else ""
        return f"/{self.name}{hint}"

    def prompt_for(self, args_text: str) -> str:
        body = self.body
        parts = args_text.split() if args_text else []
        body = body.replace("$ARGUMENTS", args_text)
        for i, p in enumerate(parts, 1):
            body = body.replace(f"${i}", p)
        return body.strip()


_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def _command_roots() -> list[Path]:
    """ZCode's discovery order: the user dirs, then the workspace dirs
    walked up from the repo."""
    roots: list[Path] = []
    home = Path(os.path.expanduser("~"))
    for base in (home / ".zcode" / "commands", home / ".agents" / "commands"):
        if base.is_dir():
            roots.append(base)
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        for name in (".zcode/commands", ".agents/commands"):
            cand = parent / name
            if cand.is_dir() and cand not in roots:
                roots.append(cand)
    plugin_root = os.environ.get("ZCODE_COMMANDS_ROOT", "")
    if plugin_root and Path(plugin_root).is_dir() and Path(plugin_root) not in roots:
        roots.append(Path(plugin_root))
    return roots


def discover_command_files() -> list[CommandFile]:
    """Every .md command on disk, filtered by ZCode's rules (built-in and
    reserved names never shadow the live menu)."""
    out: list[CommandFile] = []
    for root in _command_roots():
        for path in sorted(root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = _parse_frontmatter(text)
            rel = path.relative_to(root).with_suffix("")
            name = ":".join(rel.parts)
            if not name or name in _BY_NAME or name in _RESERVED:
                continue
            tools = tuple(t.strip() for t in meta.get("allowed-tools", "").split(",") if t.strip())
            skills = tuple(s.strip() for s in meta.get("skills", "").split(",") if s.strip())
            out.append(CommandFile(
                name=name,
                description=meta.get("description", name),
                argument_hint=meta.get("argument-hint", ""),
                allowed_tools=tools, model=meta.get("model", ""),
                skills=skills,
                disable_noninteractive=meta.get("disable-noninteractive", "").lower() in ("1", "true", "yes"),
                body=body.strip(), source=str(path),
            ))
    return out


def list_commands() -> list[dict[str, Any]]:
    """The live menu: built-ins + discovered .md files."""
    items = [c.to_dict() for c in BUILTINS]
    items.extend(f.to_dict() for f in discover_command_files())
    return items


def resolve(text: str) -> dict[str, Any] | None:
    """Resolve one /token [args] input to its command + expanded prompt.

    Returns None when the input is not a slash command (typed paths like
    "C:/x" never match — the leading token must be the whole first word).
    """
    m = re.match(r"^/([a-zA-Z][a-zA-Z0-9:_-]*)(?:\s+([\s\S]*))?$", text.strip())
    if not m:
        return None
    token, args_text = m.group(1), (m.group(2) or "").strip()
    cmd = _BY_NAME.get(token)
    if cmd is not None:
        return {"kind": "builtin", "name": cmd.name, "command": cmd.to_dict(),
                "args": args_text}
    for f in discover_command_files():
        if f.name == token:
            return {"kind": "custom", "name": f.name,
                    "command": f.to_dict(), "args": args_text,
                    "prompt": f.prompt_for(args_text)}
    return None
