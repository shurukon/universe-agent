"""core/core_agents/agents.py — the UNIVERSE agent + its specialist subagents.

S1 architecture (2026-09), renamed (2026-10, user's standing order): Universe
Computer IS the universe agent — one doer that owns every tool and skill and
completes most work solo, committing a TodoWrite plan only when work is
genuinely complex. It can spawn a bounded
swarm of specialist subagents — in parallel for read work
(research/review/security), sequentially for writers (code/design) — via
the `Agent` tool (core/agent/swarm.py), plus unspecialized `worker` runs
that share its full toolset.

Registry: universe (sole doer, owns every tool). All historical
specialists (research, code, review, security, design) have been removed
(user standing order 2026-10-31); their toolsets merged into universe.
"""
from __future__ import annotations

import json
from typing import Any

from core.agent.base_agent import BaseAgent, HandoffPackage, attach_tool_events


# ---- shared parsing helper ------------------------------------------------
def _parse_handoff(text: str, agent_name: str, elapsed_ms: int = 0) -> HandoffPackage:
    """Parse an LLM response into a safe, schema-normalized HandoffPackage."""
    payload: dict[str, Any] = {}

    def string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, (str, int, float))]

    def artifact_list(value: Any) -> list[dict[str, Any]]:
        # Model text is not proof of a side effect. Real artifacts are added by
        # attach_tool_events only after a successful tool result.
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def memory_context(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {"summary": str(value)[:500]} if value is not None else {}

    def boolean(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1"}:
                return True
            if lowered in {"false", "no", "0"}:
                return False
        return default
    # try fenced JSON
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            payload = json.loads(m.group(1))
        except Exception:
            payload = {}
    elif text.strip().startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = {}
    parse_failed = not bool(payload)
    if parse_failed:
        # m3 truth rule: PROSE IS NOT SUCCESS. A response with no valid
        # handoff JSON cannot claim task_success — the old
        # bool(text.strip()) marked any non-empty prose as success and the
        # (removed) rescue path then kept it. A parse failure is a failed
        # step carrying the raw excerpt for diagnosis.
        payload = {
            "what_was_done": text[:500],
            "key_decisions": [],
            "artifacts_created": [],
            "avoid_these": [],
            "next_agent_focus": "Return a valid handoff after verifying the work",
            "context_for_memory": {"parse_error": True, "raw_response_excerpt": text[:300]},
            "decision_confidence": 0.45 if text.strip() else 0.1,
            "is_knowledge_based": False,
            "task_success": False,
        }
    try:
        confidence = max(0.0, min(1.0, float(payload.get("decision_confidence", 0.65))))
    except (TypeError, ValueError):
        confidence = 0.65
    what = str(payload.get("what_was_done", ""))[:1000]
    context = memory_context(payload.get("context_for_memory", {"summary": what[:300]}))
    for field_name in ("capability_gap", "required_capabilities", "acceptance_criteria", "tool_lifecycle"):
        if field_name in payload:
            context[field_name] = payload[field_name]
    return HandoffPackage(
        what_was_done=what,
        key_decisions=string_list(payload.get("key_decisions")),
        artifacts_created=artifact_list(payload.get("artifacts_created")),
        avoid_these=string_list(payload.get("avoid_these")),
        next_agent_focus=str(payload.get("next_agent_focus", ""))[:500],
        context_for_memory=context,
        elapsed_ms=elapsed_ms,
        decision_confidence=confidence,
        is_knowledge_based=boolean(payload.get("is_knowledge_based"), False),
        task_success=boolean(payload.get("task_success"), False),
        agent=agent_name,
        next_agent=str(payload["next_agent"]) if payload.get("next_agent") else None,
    )


async def _skill_preflight(self: BaseAgent, *, goal_text: str, classification: dict[str, str],
                           instruction: str, turn_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Mandatory, auditable L4 procedure lookup for every specialist.

    This is deliberately executed through the same registry/gate as model tool
    calls. It ensures an agent cannot silently skip its assigned skill group;
    the later activation records whether the procedure-supported step succeeded.
    """
    from connectors.builtin import get_registry
    from skill.taxonomy import AGENT_OWNERSHIP
    registry = get_registry()
    system, orbit = AGENT_OWNERSHIP.get(self.name, ("general_fallback", "needs_review"))
    # target_agent is the primary ownership group. Do not hard-filter by the
    # nominal orbit here: some agents intentionally own cross-system skills
    # (review spans software and browser QA), and legacy skills may occupy a
    # neighboring orbit inside the same trusted group.
    args = {"query": f"{goal_text} {instruction}", "category": classification.get("category", ""),
            "target_agent": self.name, "top_k": 3}
    events: list[dict[str, Any]] = []
    from observability.events import emit as _emit
    search = await registry.call("Skill", agent=self.name, turn_id=turn_id, args={**args, "action": "search"})
    events.append({"tool": "Skill", "args": args, "result": search, "call_id": "preflight-search"})
    skills = (search.get("result") or {}).get("skills", []) if search.get("ok") else []
    selected = skills[0] if skills else {}
    _emit("skill_searched", turn_id=turn_id, agent=self.name,
          skill=selected.get("name", ""), matches=len(skills))
    read_args = {"skill_id": selected.get("id", "")}
    read = ({"ok": False, "result": {"error": "no matching skill"}}
            if not selected.get("id")
            else await registry.call("Skill", agent=self.name, turn_id=turn_id,
                                     args={**read_args, "action": "read"}))
    events.append({"tool": "Skill", "args": read_args, "result": read, "call_id": "preflight-read"})
    body = ((read.get("result") or {}).get("skill") or {}).get("body", "") if read.get("ok") else ""
    read_ok = bool(read.get("ok"))
    _emit("skill_read", turn_id=turn_id, agent=self.name,
          skill=selected.get("name", ""), solar_system=system, orbit=orbit,
          read_ok=read_ok)
    return events, {"skill_id": selected.get("id", ""),
                    # build_messages reads name/source/solar_system_id/
                    # orbit_id/description/tags — the old keys
                    # (skill_name/solar_system/orbit) rendered an empty
                    # identity block in every prompt (audit 2026-09)
                    "name": selected.get("name", ""),
                    "source": selected.get("source", ""),
                    "solar_system_id": system,
                    "orbit_id": orbit,
                    "description": selected.get("description", ""),
                    "tags": selected.get("tags", []),
                    "taxonomy_confidence": selected.get("taxonomy_confidence",
                                                         selected.get("confidence", "")),
                    "body": body, "read_ok": read_ok}


async def _run_specialist_step(self: BaseAgent, *, memory, llm_client,
                               goal_text: str, classification: dict[str, str],
                               handoff: HandoffPackage | None,
                               context: dict[str, Any], capability_gate,
                               turn_id: str, instruction: str) -> HandoffPackage:
    # a spawned mission's brief overrides the class's canned instruction —
    # subagents exist to run the mission, not their default routine
    instruction = str(context.get("mission_instruction") or instruction)
    phase = context.get("phase") or {}
    preflight_events, preflight_skill = await _skill_preflight(
        self, goal_text=goal_text, classification=classification,
        instruction=instruction, turn_id=turn_id)
    context["skill_preflight"] = preflight_skill
    if preflight_skill.get("body"):
        context.setdefault("l4_skills", [])
        context["l4_skills"] = [preflight_skill] + [
            s for s in context["l4_skills"]
            if s.get("id") != preflight_skill.get("skill_id")]
    messages = self.build_messages(context, goal_text, instruction)
    # The Universe turn engine runs every loop (2026-10-28): unbounded steps,
    # the rapid-refill breaker + the operator cap replace the complexity
    # round budgets. phase.required_tools still scopes the whitelist.
    from core.agent.base_agent import EFFORT_DEFAULT_MAX_TOKENS, EFFORT_MAX_TOKENS
    _token_budget = EFFORT_MAX_TOKENS.get(
        str(context.get("effort") or ""), EFFORT_DEFAULT_MAX_TOKENS)
    from core.agent.turn_engine import run_turn
    response, events, _turn_out = await run_turn(
        llm_client=llm_client, messages=messages, agent=self.name,
        turn_id=turn_id, capability_gate=capability_gate,
        response_format={"type": "json_object"}, max_tokens=_token_budget,
        allowed_tools=[str(name) for name in phase.get("required_tools", [])] or None,
        drain_steer=context.get("_drain_steer"),
    )
    # Preflight events (skill.search/read/activate) are plumbing, not the
    # agent's work: they must not count toward tools_used, "confirmed N real
    # tool calls", or any success rescue. Only real tool-loop events do.
    package = _parse_handoff(response.text, self.name, response.latency_ms)
    # One bounded re-ask on a prose reply: free models on gateways without
    # response_format support (Kilo, OpenCode Zen — both
    # supports_response_format=False) regularly answer "done!" in prose even
    # after real tool work. Without this, EVERY such step is a parse_error
    # → failed handoff → failed goal, on exactly the free providers. One
    # JSON-only follow-up recovers the handoff honestly; if the second
    # answer still isn't JSON, the m3 truth rule stands (prose is not
    # success).
    if package.context_for_memory.get("parse_error"):
        try:
            reask = await llm_client.complete(
                agent=self.name,
                messages=[
                    *messages,
                    {"role": "assistant", "content": (response.text or "")[:4000]},
                    {"role": "user", "content": (
                        "Your last reply was not the handoff JSON. Reply with "
                        "ONLY the single valid JSON object described in the "
                        "protocol — no prose, no code fences, no explanation.")},
                ],
                max_tokens=4096, temperature=0.0,
                response_format={"type": "json_object"},
            )
            retry_pkg = _parse_handoff(reask.text, self.name, reask.latency_ms)
            if not retry_pkg.context_for_memory.get("parse_error"):
                package = retry_pkg
        except Exception:
            pass  # the re-ask is opportunistic; the original parse_error stands
    if phase:
        package.context_for_memory["phase_id"] = str(phase.get("phase_id", self.name))
        package.context_for_memory["phase_acceptance"] = phase.get("acceptance", [])
        package.context_for_memory["phase_result"] = package.what_was_done[:1000]
        if str(phase.get("phase_id")) == "method_research":
            package.context_for_memory["research_result"] = {
                "what_was_done": package.what_was_done,
                "key_decisions": package.key_decisions,
                "artifacts_created": package.artifacts_created,
                "next_agent_focus": package.next_agent_focus,
            }
    package.context_for_memory["skill_preflight"] = {
        "skill_id": preflight_skill.get("skill_id", ""),
        "skill_name": preflight_skill.get("name", ""),
        "solar_system": preflight_skill.get("solar_system_id", ""),
        "orbit": preflight_skill.get("orbit_id", ""),
        "read_ok": bool(preflight_skill.get("read_ok")),
        "body_chars": len(str(preflight_skill.get("body", ""))),
    }
    activation_key = preflight_skill.get("skill_id") or preflight_skill.get("name")
    if activation_key:
        from connectors.builtin import get_registry
        activation_args = ({"skill_id": preflight_skill["skill_id"]}
                           if preflight_skill.get("skill_id") else
                           {"skill_name": preflight_skill["name"]})
        activation_args["outcome"] = "success" if package.task_success else "failure"
        activation = await get_registry().call("Skill", agent=self.name,
                                                turn_id=turn_id,
                                                args={**activation_args, "action": "activate"})
        events.append({"tool": "Skill", "args": activation_args,
                       "result": activation, "call_id": "preflight-activate"})
        # the skill's galactic coordinates travel with the event so the UI can
        # light up the exact star in its real solar system
        from observability.events import emit as _emit
        _emit("skill_activated", turn_id=turn_id, agent=self.name,
              skill=str(activation_key)[:120],
              skill_name=preflight_skill.get("name", ""),
              solar_system=preflight_skill.get("solar_system_id", ""),
              orbit=preflight_skill.get("orbit_id", ""),
              outcome=str(activation_args.get("outcome")))
    # Preflight events (skill.search/read/activate) are plumbing, not the
    # agent's work (the comment above said so; the code below then included
    # them anyway — a failed preflight activation like {"ok": false,
    # "skill not found"} marked a COMPLETED step failed and inflated
    # "Confirmed N real tool call(s)". audit 2026-09). attach_tool_events
    # never sees them; they stay in the event stream for the UI only.
    real_events = [e for e in events
                   if not str(e.get("call_id", "")).startswith("preflight-")]
    package = attach_tool_events(package, real_events)
    # m3 truth rule: prose is not success, and successful plumbing is not a
    # completed task. The old parse_error rescue (any-ok-tools flips prose
    # into task_success=True) and the planning override (failed planning
    # flipped to success because its tools ran) both laundered narrated
    # work into verified success. A parse_error stays a failure with its
    # raw excerpt; a planning failure stays a failure. Only the model's
    # explicit task_success, backed by real verified tool events in
    # attach_tool_events, decides the outcome.
    if package.context_for_memory.get("parse_error") and events:
        # tools DID run and succeed — record that fact honestly, but the
        # step still failed to produce a valid handoff verdict
        succeeded = [e for e in events if (e.get("result") or {}).get("ok", False)]
        if succeeded:
            package.context_for_memory["tools_succeeded_before_parse_error"] = len(succeeded)
    return package


# ---- the UNIVERSE agent (the doer formerly known as "general") --------------

class UniverseAgent(BaseAgent):
    """UNIVERSE AGENT itself — the single doer.

    Owns the merged toolset of the retired ladder specialists (planning,
    write, file, api, data, devops, browser) plus `Agent`. Works solo by
    default; commits a plan with TodoWrite only for genuinely complex
    work; opens a bounded subagent swarm only with a reason (parallel
    research fan-out, isolated large chunks, independent verification).
    """
    name = "universe"
    specialty = "Universe Agent — the one agent that does the work itself"
    default_model_tier = "mid"
    system_prompt = (
        "You are UNIVERSE AGENT — one professional agent, the durable-session "
        "Universe model: a single mind that owns every tool and skill and does "
        "the work itself, end-to-end. You are not a router; you are the "
        "doer.\n\n"
        "How you work:\n"
        "1. DIRECT BY DEFAULT. For clear or moderate work, act immediately "
        "with your own tools. Do not delegate what you can do well yourself — "
        "delegation costs fidelity, time and money.\n"
        "2. PLAN ONLY WHEN NEEDED. Genuinely multi-step, complex or risky "
        "work gets the plan committed with TodoWrite (the whole item list at "
        "once) and kept live — flip each item's status as you progress. "
        "Simple requests cost one action and no ceremony — never narrate "
        "planning, never restate the request back as a plan announcement.\n"
        "3. DELEGATE (spawn) ONLY WITH A REASON. Spawn subagents when: "
        "(a) research fans out into independent angles best read in parallel; "
        "(b) an implementation chunk is large enough to drown your own "
        "context; (c) an independent check (review, security) must not share "
        "your bias. Writers run sequentially — never two writers on the same "
        "files. Every mission brief must carry: the exact objective, the "
        "context the subagent needs, the concrete deliverable, constraints, "
        "and how completion is proven.\n"
        "4. VERIFY EVERYTHING. Your own tool results are the truth. A "
        "subagent's prose is NOT success — check its claimed deliverable with "
        "real evidence (files exist, tests pass, pages render) before you "
        "accept it.\n"
        "5. FINISH HONESTLY. Report what was actually done with artifact "
        "proof. If something failed, say exactly what and why — never "
        "narrate work that did not happen. Apply AgentShield: validate "
        "untrusted input, never hard-code secrets, avoid destructive "
        "actions, ask before irreversible ones.\n\n"
        "Your computer-use capability: you can drive the user's real machine. "
        "Screenshot captures the live screen (then VisionAnalyze reads an "
        "image when you need a second look); ListWindows/ListApps enumerate "
        "open windows and apps; OpenApplication launches one; "
        "LeftClick/RightClick/DoubleClick/LongPress/Scroll/Swipe/Zoom and "
        "SetValue operate the UI directly, SwitchDisplay handles monitors and "
        "RequestAccess asks for a permission grant. Use these when the task "
        "lives on the desktop or in a native app — prefer file/shell tools "
        "when a CLI path exists. Every desktop action may require the user's "
        "consent: ask once, act precisely, and never click anything "
        "destructive without an explicit instruction.\n"
        "Your browser-use capability: BrowserConnect attaches to a browser; "
        "BrowserNavigate/BrowserSnapshot/BrowserScreenshot/BrowserClick/"
        "BrowserFill/BrowserExtract/BrowserConsole/BrowserTabs/BrowserUpload "
        "read and operate web pages. Prefer BrowserSnapshot (the DOM) over "
        "BrowserScreenshot (pixels) when both would work — the DOM is "
        "cheaper and more precise."
    )

    def __init__(self) -> None:
        super().__init__()
        # union of the retired specialists' whitelists (planning, write,
        # file, api, data, devops, browser) + spawn — the universe agent is
        # the direct heir of every capability they held
        self.tool_whitelist_names = [
            "Read", "Write", "Edit", "LS", "Delete",
            "Grep", "Glob",
            "Bash", "CodeExecute",
            "WebSearch", "WebFetch", "WebResearch",
            "BrowserNavigate", "BrowserClick", "BrowserFill",
            "BrowserSnapshot", "BrowserScreenshot", "BrowserExtract",
            "BrowserConsole", "BrowserTabs", "BrowserUpload",
            "BrowserConnect",
            "Charting", "PandasQuery", "DataValidate",
            "Archive", "ImageOcr",
            "GitStatus", "GitDiff", "GitLog", "GitCommit", "GitPush",
            "DockerBuild", "DockerRun", "DockerPs",
            "Ssh", "K8s", "LogTail",
            "HttpClient", "OpenapiParser", "GraphqlClient",
            "github_repo_info", "github_create_issue", "gitlab_project_info",
            "pypi_package_info", "npm_package_info", "crates_package_info",
            "slack_post_message", "slack_list_channels", "discord_webhook_post",
            "telegram_send_message", "notion_query_database", "notion_create_page",
            "linear_list_issues", "linear_create_issue", "trello_list_boards",
            "trello_create_card", "asana_list_tasks", "asana_create_task",
            "jira_search_issues", "jira_create_issue", "dropbox_list_files",
            "google_drive_list_files", "stripe_list_charges",
            "stripe_create_charge", "hubspot_create_contact",
            "airtable_list_records", "wikipedia_search", "arxiv_search",
            "coingecko_price", "openweathermap_current",
            "McpAddServer", "McpListServers", "McpRemoveServer",
            "McpCallTool",
            "MemoryQuery", "CapabilityCatalog",
            "Agent", "SendMessage",
            "TodoWrite", "TodoRead",
            "ExitPlanMode", "EnterPlanMode", "AskUserQuestion",
            "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
            "TaskStop", "TaskOutput",
            "GoalRead", "ReadSessionContext", "ToolSearch",
            "NotebookEdit", "ApplyPatch", "PowerShell", "Monitor",
            "Skill",
            "CronCreate", "CronList", "CronUpdate", "CronDelete",
            # computer use — the agent drives the real desktop (the VL
            # model sees the screen; the gate escalates these to per_turn
            # consent so the user approves each desktop action)
            "Screenshot", "LeftClick", "RightClick", "DoubleClick",
            "LongPress", "Scroll", "Swipe", "Zoom",
            "ListWindows", "ListApps", "OpenApplication",
            "SwitchDisplay", "RequestAccess", "GetAppState", "SetValue",
            "VisionAnalyze",
            # Universe Studio — the real tool surfaces behind the Studio
            # tool cards (AVE MCP, open-design daemon, Blender addon socket)
            "StudioAVE", "StudioDesign", "StudioBlender",
        ]

    async def _execute(self, *, memory, llm_client, goal_text, classification, handoff, context, capability_gate, turn_id="", **_):
        return await _run_specialist_step(
            self, memory=memory, llm_client=llm_client, goal_text=goal_text,
            classification=classification, handoff=handoff, context=context,
            capability_gate=capability_gate, turn_id=turn_id,
            instruction=(
                "Execute this work end-to-end yourself with your tools. "
                "Commit a TodoWrite plan first only if the work is genuinely "
                "complex or risky; spawn subagents only for parallelizable "
                "research, isolated large chunks, or independent verification. "
                "Verify every claim with real evidence before the final handoff."
            ))


ALL_AGENTS = {
    "universe": UniverseAgent,
}

_instances: dict[str, BaseAgent] = {}


def get_agent(name: str) -> BaseAgent:
    if name not in _instances:
        cls = ALL_AGENTS.get(name)
        if cls is None:
            raise KeyError(f"unknown agent {name!r}")
        _instances[name] = cls()
    return _instances[name]


def get_all_agents() -> dict[str, BaseAgent]:
    for n in ALL_AGENTS:
        get_agent(n)
    return _instances


def reset_agents_for_tests() -> None:
    _instances.clear()
