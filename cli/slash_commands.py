"""cli/slash_commands.py — all /commands registration (§8).

Every command in §8's full command reference is registered here. [wizard]
commands dispatch to cli/wizards/. The REPL looks up commands by name and
either runs them inline or launches the wizard flow.

Commands are split into the §8 categories: Tasks, Memory, Agents, Rules,
Connectors, Channels, Providers & Models, Resources, Observability,
Evaluation, Data, Profile, System.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable


@dataclass
class Command:
    name: str
    category: str
    description: str
    wizard: bool
    handler: Callable[..., Awaitable[Any]] | Callable[..., Any]


_REGISTRY: dict[str, Command] = {}


def command(name: str, category: str, description: str, wizard: bool = False):
    def deco(fn):
        _REGISTRY[name] = Command(name=name, category=category,
                                   description=description, wizard=wizard, handler=fn)
        return fn
    return deco


def get_command(name: str) -> Command | None:
    return _REGISTRY.get(name)


def all_commands() -> list[Command]:
    return sorted(_REGISTRY.values(), key=lambda c: (c.category, c.name))


def commands_by_category() -> dict[str, list[Command]]:
    out: dict[str, list[Command]] = {}
    for c in all_commands():
        out.setdefault(c.category, []).append(c)
    return out


# ---- register all commands (handlers defined in cli/wizards/ and inline) ----
def register_all() -> None:
    from cli import wizards as w
    from cli.handlers import register_inline
    # Tasks
    command("task", "Tasks", "Start a new task directly / wizard", wizard=True)(w.goal_wizard)
    command("goal", "Tasks", "Alias for /task (the goal era is over)", wizard=True)(w.goal_wizard)
    command("status", "Tasks", "Active tasks with management options", wizard=True)(w.status_wizard)
    command("stop", "Tasks", "Select task to stop", wizard=True)(w.stop_wizard)
    command("history", "Tasks", "Browse history, re-run tasks", wizard=True)(w.history_wizard)
    command("resume", "Tasks", "Resume an interrupted task from its last checkpoint", wizard=True)(w.resume_wizard)
    # Sessions (the README-promised trio + the switcher)
    command("new", "Sessions", "Start a new empty session", wizard=False)(w.new_session_cmd)
    command("fork", "Sessions", "Fork the current session into an independent copy", wizard=False)(w.fork_session_cmd)
    command("compact", "Sessions", "Compact the current session's context", wizard=False)(w.compact_session_cmd)
    command("sessions", "Sessions", "List sessions (pass an id to switch)", wizard=False)(w.sessions_cmd)
    # Memory
    command("memory", "Memory", "Overview of entire Universe state", wizard=False)(w.memory_overview)
    command("vault", "Memory", "Open the Markdown vault in an editor", wizard=False)(w.open_vault)
    command("forget", "Memory", "Delete a specific memory", wizard=True)(w.forget_wizard)
    command("forget-all", "Memory", "Nuclear option — double confirmation required", wizard=True)(w.forget_all_wizard)
    # Agents
    command("agents", "Agents", "List/manage all agents (Core + Moons)", wizard=True)(w.agents_wizard)
    command("agent", "Agents", "Create a custom Moon agent / agent details", wizard=True)(w.agent_wizard)
    # Rules
    command("rules", "Rules", "View/manage all rules", wizard=True)(w.rules_wizard)
    command("blackhole", "Rules", "Add an absolute blocker", wizard=True)(w.blackhole_wizard)
    command("whitehole", "Rules", "Add an environmental rule", wizard=True)(w.whitehole_wizard)
    command("wormhole", "Rules", "Add a system-specific rule", wizard=True)(w.wormhole_wizard)
    # Connectors
    command("connectors", "Connectors", "View/manage connected services", wizard=True)(w.connectors_wizard)
    command("connect", "Connectors", "Connect a new service (natural language)", wizard=True)(w.connect_wizard)
    command("mcp", "Connectors", "Add an MCP server", wizard=True)(w.mcp_wizard)
    command("skills", "Connectors", "Manage pre-loaded and community skills", wizard=True)(w.skills_wizard)
    command("quarantine", "Connectors", "Review/approve community skills", wizard=True)(w.quarantine_wizard)
    command("skill", "Connectors", "Author a custom skill / pin a version", wizard=True)(w.skill_wizard)
    # Channels
    command("channels", "Channels", "View/manage all active channels", wizard=True)(w.channels_wizard)
    command("channel", "Channels", "Connect/remove/configure a channel", wizard=True)(w.channel_wizard)
    # Providers & Models
    command("providers", "Providers & Models", "Full provider management dashboard", wizard=True)(w.providers_wizard)
    command("provider", "Providers & Models", "Add a provider / manage keys", wizard=True)(w.provider_wizard)
    command("model", "Providers & Models", "Assign provider/key/model per agent", wizard=True)(w.model_wizard)
    command("fallback", "Providers & Models", "Set the fallback chain per agent", wizard=True)(w.fallback_wizard)
    # Observability
    command("debug", "Observability", "Peek at live L1-L5 state", wizard=False)(w.debug_cmd)
    command("log", "Observability", "Tail structured logs", wizard=False)(w.log_cmd)
    command("trace", "Observability", "Replay a goal's agent timeline", wizard=False)(w.trace_cmd)
    command("metrics", "Observability", "Throughput, latency, error rate", wizard=False)(w.metrics_cmd)
    # Evaluation
    command("eval", "Evaluation", "Run the v1 smoke-test suite", wizard=False)(w.eval_cmd)
    # Data
    command("export", "Data", "Export the full Universe state as a portable archive", wizard=False)(w.export_cmd)
    command("import", "Data", "Restore from an export", wizard=False)(w.import_cmd)
    # Profile
    command("profile", "Profile", "View/edit the Cosmic Orbit", wizard=True)(w.profile_wizard)
    command("orbit", "Profile", "View Local Orbits per solar system", wizard=True)(w.orbit_wizard)
    # System
    command("setup", "System", "Full onboarding wizard", wizard=True)(w.setup_wizard)
    command("version", "System", "Universe version + schema version", wizard=False)(w.version_cmd)
    command("migrate", "System", "Run pending SQLite migrations", wizard=False)(w.migrate_cmd)
    command("help", "System", "Help (general or command-specific)", wizard=False)(w.help_cmd)
    # inline handlers (cost, limits — short outputs)
    register_inline(command)
