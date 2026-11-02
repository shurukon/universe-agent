"""connectors/builtin/__init__.py — built-in connectors + tool registry.

§6, §25 Phase 5 ⑳. The built-in tools every agent can use: file system,
shell, web, git, docker, memory query. Each tool declares its capability
(verb on a resource) so the Capability Gate (security/capability.py) can
enforce policy at a single chokepoint.

The ToolRegistry is the single place tools are registered; agents reference
tools by name, and the Orchestrator resolves names -> implementations here.
Every call goes through the capability gate (set via set_gate()).
"""
from __future__ import annotations

from typing import Any

from core.agent.base_agent import Tool
from security.audit import log as audit_log


class ToolRegistry:
    """Global registry of tool implementations. Every agent's tool whitelist
    resolves through here, and every call routes through the capability gate."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._gate: Any = None  # security.capability.CapabilityGate, set in Phase 8

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            # a duplicate used to overwrite SILENTLY — a typo'd second
            # registration could replace a working tool with nobody noticing
            from loguru import logger
            logger.warning("registry: tool {n!r} registered twice — "
                           "overwriting the earlier registration",
                           n=tool.name)
        self._tools[tool.name] = tool
        try:
            from observability.tool_lifecycle import register_catalog_entry
            register_catalog_entry(name=tool.name, kind="builtin", description=tool.description,
                                  source="builtin_registry", status="registered",
                                  details={"capability": tool.capability, "consent": tool.consent})
        except Exception:
            pass

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def set_gate(self, gate: Any) -> None:
        self._gate = gate

    async def call(self, name: str, *, agent: str, turn_id: str,
                   args: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool through the mandatory Capability Gate."""
        from observability.events import emit
        tool = self._tools.get(name)
        from observability.tool_lifecycle import record_tool_lifecycle
        # raw args used to be persisted verbatim — http_client headers with
        # Authorization tokens landed in cleartext in universe.db AND the sql
        # agent tool could SELECT them back out (audit 2026-09). Redact at
        # the boundary instead; the in-memory event stream already redacts.
        from security.secret_filter import redact_dict
        record_tool_lifecycle(turn_id=turn_id, agent=agent, name=name, phase="invoked",
                              status="requested", source="registry",
                              details={"args": redact_dict(dict(args))})
        emit("tool_requested", turn_id=turn_id, agent=agent, tool=name, args=args)
        if tool is None:
            record_tool_lifecycle(turn_id=turn_id, agent=agent, name=name, phase="failed",
                                  status="unknown_tool", source="registry")
            emit("tool_failed", turn_id=turn_id, agent=agent, tool=name,
                 error=f"unknown tool {name!r}")
            return {"ok": False, "error": f"unknown tool {name!r}"}
        if self._gate is None:
            from security.capability import get_gate
            self._gate = get_gate()
        call_args = dict(args)
        if name in ("TodoWrite", "TodoRead", "Skill", "TaskCreate",
                    "TaskUpdate", "TaskGet", "TaskList", "TaskStop",
                    "TaskOutput", "GoalRead", "ReadSessionContext"):
            # runtime context is AUTHORITATIVE: overwrite, never setdefault —
            # a model-supplied turn_id/agent is a forgery that would write
            # another goal's todo list or activate under a false identity
            call_args["turn_id"] = turn_id
            if name == "Skill":
                call_args["agent"] = agent
        elif name == "Agent":
            # spawn's "agent" arg is the spawn TARGET (model-facing); only
            # turn_id is runtime context
            call_args["turn_id"] = turn_id
        import time as _t
        _start = _t.monotonic()
        # redact BEFORE stringifying: once args flatten into one opaque
        # string, key-based redaction can no longer see a Bearer header
        _safe_args = redact_dict(dict(args))
        args_summary = " ".join(
            f"{k}={str(v)[:60]}" for k, v in list(_safe_args.items())[:4])[:160]
        emit("tool_running", turn_id=turn_id, agent=agent, tool=name,
             capability=tool.capability, args_summary=args_summary)
        # SECURITY verdicts precede the UX hook layer: a fact-forcing hook
        # must never mask a blackhole / fail-closed unknown-agent / plan-mode
        # block (test_security's precedence invariants). check_denied covers
        # the pure deny phase; consent + handler still happen in enforce().
        gate_deny = self._gate.check_denied(tool, agent=agent, turn_id=turn_id,
                                            args=call_args)
        if gate_deny is not None:
            record_tool_lifecycle(turn_id=turn_id, agent=agent, name=name,
                                  phase="failed", status="gate_denied",
                                  source="registry",
                                  details={"blocked_by": gate_deny.get("blocked_by")})
            emit("tool_blocked", turn_id=turn_id, agent=agent, tool=name,
                 error=str(gate_deny.get("error", ""))[:200],
                 blocked_by=str(gate_deny.get("blocked_by", "gate")),
                 duration_ms=int((_t.monotonic() - _start) * 1000))
            return gate_deny
        # hook layer (GateGuard twin): pre-checks run before the gate's
        # consent/handler phase; a blocked call never runs the handler.
        # Shell-adjacent tools all fire pre_shell (audit 2026-10: only the
        # tool literally named "Bash" used to fire it, so PowerShell,
        # CodeExecute, Monitor and the unsandboxed shell bypassed user
        # pre_shell hooks and the builtin fact-guard entirely).
        _SHELL_TOOLS = {"Bash", "BashUnsandboxed", "PowerShell",
                        "CodeExecute", "Monitor"}
        _WRITE_TOOLS = {"Write", "Edit", "ApplyPatch", "NotebookEdit"}
        hook_event = ("pre_shell" if name in _SHELL_TOOLS
                      else "pre_write" if name in _WRITE_TOOLS
                      else "pre_tool")
        from hooks import get_hook_manager
        hook_block = get_hook_manager().check(
            hook_event, name, agent=agent, turn_id=turn_id, args=call_args)
        if hook_block is not None:
            # a hook-blocked call still goes through the AUDIT chain — the
            # gate's "every call is audited" invariant (test_security) holds
            # for hook interceptions exactly as for gate blocks
            audit_log(actor=agent, action=f"tool:{tool.capability}",
                      args=call_args,
                      result=f"blocked:hook:{hook_block.hook_id}",
                      turn_id=turn_id,
                      duration_ms=int((_t.monotonic() - _start) * 1000))
            record_tool_lifecycle(turn_id=turn_id, agent=agent, name=name,
                                  phase="failed", status="hook_blocked",
                                  source="hooks",
                                  details={"hook_id": hook_block.hook_id})
            emit("hook_intercepted", turn_id=turn_id,
                 hook_id=hook_block.hook_id, event=hook_block.event,
                 action=hook_block.action, tool=name, agent=agent,
                 message=hook_block.message[:400])
            emit("tool_blocked", turn_id=turn_id, agent=agent, tool=name,
                 error=hook_block.message[:200], blocked_by="hook",
                 duration_ms=int((_t.monotonic() - _start) * 1000))
            return {"ok": False, "error": hook_block.message,
                    "blocked_by": "hook", "hook_id": hook_block.hook_id,
                    "capability": tool.capability, "tool": name}
        result = await self._gate.enforce(tool, agent=agent, turn_id=turn_id, args=call_args)
        _dur_ms = int((_t.monotonic() - _start) * 1000)
        if result.get("ok"):
            record_tool_lifecycle(turn_id=turn_id, agent=agent, name=name, phase="verified",
                                  status="success", source="registry", details={"result_type": type(result.get("result")).__name__})
            import json as _json
            inner = result.get("result") if isinstance(result.get("result"), dict) else {}
            detail = _json.dumps(
                {k: v for k, v in inner.items()
                 if k in ("ok", "path", "bytes", "url", "title", "count",
                          "tab", "error")},
                ensure_ascii=False)[:160] or None
            emit("tool_finished", turn_id=turn_id, agent=agent, tool=name,
                 duration_ms=_dur_ms, detail=detail if detail and detail != "null" else None)
            # post_tool hooks: observe-only tap after a successful call
            get_hook_manager().observe("post_tool", name, agent=agent,
                                       turn_id=turn_id, args=call_args)
            # a successful file write/edit is a live artifact — the chat
            # surface gets its chip the moment the file lands, not at goal end
            # (any Write-capability tool: Write/Edit/ApplyPatch/NotebookEdit/SvgGen)
            if tool.capability == "Write" or name in ("Write", "Edit", "ApplyPatch", "NotebookEdit", "SvgGen", "CssTools"):
                inner = result.get("result")
                if isinstance(inner, dict) and (inner.get("path") or inner.get("file") or args.get("path") or args.get("file")):
                    _apath = str(inner.get("path") or inner.get("file") or args.get("path") or args.get("file") or "")[:400]
                    if _apath:
                        emit("artifact_created", turn_id=turn_id, agent=agent, tool=name,
                             path=_apath,
                             bytes=int(inner.get("bytes") or inner.get("size") or 0))
        else:
            record_tool_lifecycle(turn_id=turn_id, agent=agent, name=name, phase="failed",
                                  status="error", source="registry", details={"error": result.get("error", "")})
            blocked = bool(result.get("needs_consent") or result.get("blocked_by"))
            emit("tool_blocked" if blocked else "tool_failed",
                 turn_id=turn_id, agent=agent, tool=name,
                 error=str(result.get("error", ""))[:200],
                 blocked_by=result.get("blocked_by", "consent" if result.get("needs_consent") else ""),
                 duration_ms=_dur_ms)
        return result


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_builtins(_registry)
    return _registry


def reset_registry_for_tests() -> ToolRegistry:
    global _registry
    _registry = ToolRegistry()
    _register_builtins(_registry)
    return _registry


def _register_builtins(reg: ToolRegistry) -> None:
    """Register every built-in tool. Called once at registry creation."""
    from connectors.builtin.browser import register as reg_browser
    from connectors.builtin.capabilities import register as reg_capabilities
    from connectors.builtin.computer_use import register as reg_computer_use
    from connectors.builtin.cron_tools import register as reg_cron
    from connectors.builtin.skills import register as reg_skills
    from connectors.builtin.data import register as reg_data
    from connectors.builtin.devops import register as reg_devops
    from connectors.builtin.docker import register as reg_docker
    from connectors.builtin.files import register as reg_files
    from connectors.builtin.git import register as reg_git
    from connectors.builtin.mcp_tools import register as reg_mcp
    from connectors.builtin.memory import register as reg_memory
    from connectors.builtin.ask_user import register as reg_ask_user
    from connectors.builtin.plan_mode import register as reg_plan_mode
    from connectors.builtin.misc import register as reg_misc
    from connectors.builtin.shell import register as reg_shell
    from connectors.builtin.thirdparty import register as reg_thirdparty
    from connectors.builtin.todos import register as reg_todos
    from connectors.builtin.vision import register as reg_vision
    from connectors.builtin.web import register as reg_web
    from core.agent.swarm import register as reg_swarm
    from connectors.builtin.search_tools import register as reg_search
    from connectors.builtin.task_tools import register as reg_task_tools
    from connectors.builtin.parity_tools import register as reg_parity_tools
    reg_files(reg)
    reg_shell(reg)
    reg_web(reg)
    reg_git(reg)
    reg_docker(reg)
    reg_memory(reg)
    reg_misc(reg)
    reg_browser(reg)
    reg_capabilities(reg)
    reg_skills(reg)
    reg_data(reg)
    reg_vision(reg)
    reg_devops(reg)
    reg_thirdparty(reg)
    reg_mcp(reg)
    reg_swarm(reg)
    reg_search(reg)
    reg_todos(reg)
    reg_plan_mode(reg)
    reg_task_tools(reg)
    reg_parity_tools(reg)
    reg_ask_user(reg)
    reg_cron(reg)
    reg_computer_use(reg)
    # ---- ZCode parity: per-tool defaults for plan mode & timeout ----
    _PLAN_ALLOWED = {
        "Read", "LS", "Grep", "Glob", "GitStatus", "GitDiff", "GitLog",
        "MemoryQuery", "Skill", "ToolSearch", "CapabilityCatalog",
        "WebSearch", "WebFetch", "WebResearch", "wikipedia_search", "arxiv_search",
        "TodoWrite", "TodoRead", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
        "TaskCreate", "TaskUpdate", "TaskGet", "TaskList", "TaskStop", "TaskOutput",
        "GoalRead", "ReadSessionContext", "Agent", "BrowserNavigate", "BrowserSnapshot",
        "BrowserExtract", "VisionAnalyze",
    }
    _TIMEOUT_DEFAULTS = {
        # reads: 30s
        "Read": 30, "LS": 30, "Grep": 30, "Glob": 30, "GitStatus": 30, "GitDiff": 30, "GitLog": 30,
        "MemoryQuery": 15, "Skill": 15, "ToolSearch": 15, "CapabilityCatalog": 10,
        "WebSearch": 30, "WebFetch": 30, "WebResearch": 60, "wikipedia_search": 30, "arxiv_search": 30,
        "TodoRead": 10, "TodoWrite": 10, "GoalRead": 10, "ReadSessionContext": 10,
        "AskUserQuestion": 30, "EnterPlanMode": 5, "ExitPlanMode": 5,
        # writes: 60s
        "Write": 60, "Edit": 60, "Delete": 30, "ApplyPatch": 60, "NotebookEdit": 60,
        "GitCommit": 60, "GitPush": 60,
        # exec / network / browser: 60-600s
        "Bash": 600, "PowerShell": 600, "BashUnsandboxed": 600, "CodeExecute": 600, "Monitor": 600,
        "DockerBuild": 600, "DockerRun": 600, "DockerPs": 30,
        "BrowserNavigate": 30, "BrowserClick": 30, "BrowserFill": 30, "BrowserSnapshot": 30,
        "BrowserScreenshot": 30, "BrowserExtract": 30, "BrowserConsole": 30, "BrowserTabs": 30,
        "Agent": 600,
    }
    for _tool in reg._tools.values():
        if not hasattr(_tool, 'timeout_s') or _tool.timeout_s is None:
            _tool.timeout_s = float(_TIMEOUT_DEFAULTS.get(_tool.name, 60))
        if not _tool.allow_in_plan and _tool.name in _PLAN_ALLOWED:
            _tool.allow_in_plan = True

