"""scripts/s1d_orchestrator.py — one-shot S1d transformation of
core/agent/orchestrator.py (the general agent + swarm rewrite).

Guarded with asserts on every marker; refuses to write anything unless ALL
anchors are found. Idempotence: run against the pre-S1d file only.
"""
import io

P = "core/agent/orchestrator.py"
raw = io.open(P, encoding="utf-8", newline="").read()
# EOL-aware: the file may be CRLF (Windows repo) while our markers are LF.
# Normalize to LF for the transformation, restore the original ending on write.
crlf = "\r\n" in raw
s = raw.replace("\r\n", "\n") if crlf else raw

# ---------- 1) replace [default_plan .. acceptance) with the single general run
START = "            plan = self._default_plan(meta, goal_text)\n"
END = "            # acceptance runs ffprobe/rglob/stat"
i0 = s.find(START)
i1 = s.find(END)
assert i0 > 0 and i1 > i0, ("ladder start/end not found", i0, i1)

NEW = '''            # S1d: the fixed ladder is dead — the general agent IS the plan,
            # the executor and the verifier. One run, tools in its hands.
            try:
                from config import get_config
                project_root = str(get_config().get("project_root", "") or "")
            except Exception:
                project_root = ""
            phase_contract: list[dict[str, Any]] = []
            if is_media_goal(meta.category, goal_text):
                phase_contract = media_phase_plan()
            from core.agent.steer import take_steer as _take_steer
            context = {"phase_contract": phase_contract, "project_root": project_root,
                       "l4_skills": [s.to_dict() for s in self.memory.search_l4(
                            goal_text, category=meta.category, domain=meta.domain, top_k=5)],
                       "l3_stars": [s.to_dict() for s in self.memory.search_l3(goal_text, top_k=5)],
                       "active_rules": self.memory.active_rules(), "handoff": None,
                       "classification": classification, "untrusted": [],
                       "_drain_steer": lambda: [str(m.get("text", ""))
                                               for m in _take_steer(turn_id)]}
            checkpoint(turn_id, state={"status": "executing", "goal_text": goal_text,
                                       "language": language, "owner_session_id": owner_session_id,
                                       "classification": classification, "planet_id": planet.id,
                                       "handoffs": [], "remaining_plan": []})
            timeline_record(turn_id, agent="general", event="agent_start",
                            detail=goal_text[:200])
            if on_step:
                await self._maybe_call(on_step, {"phase": "agent_start", "agent": "general",
                                                 "step": 0, "instruction": goal_text[:200]})
            pkg = await get_agent("general").run(
                memory=self.memory, llm_client=self.llm, goal_text=goal_text,
                classification=classification, handoff=None, context=context,
                capability_gate=gate, turn_id=turn_id)
            handoffs.append(pkg.model_dump())
            timeline_record(turn_id, agent="general", event="agent_complete",
                            detail=json.dumps({"success": pkg.task_success,
                                               "tools": pkg.tools_used}))
            if on_handoff:
                await self._maybe_call(on_handoff, pkg.model_dump())
            remaining_plan_now = []
            if is_media_goal(meta.category, goal_text) and str(project_root or ""):
                enforcement = await ensure_media_execution(
                    turn_id=turn_id, project_root=project_root,
                    agent="general", capability_gate=gate)
                timeline_record(turn_id, agent="orchestrator", event="media_enforcer",
                                detail=json.dumps(enforcement, ensure_ascii=False, default=str)[:4000])
                if enforcement.get("ok"):
                    pkg.context_for_memory["media_enforcer"] = enforcement
                else:
                    pkg.task_success = False
                    pkg.what_was_done = (pkg.what_was_done + " Media enforcer: "
                                         + str(enforcement.get("failure", "failed")))[:1000]
'''
s = s[:i0] + NEW + s[i1:]

# ---------- 2) LLMError handler before the generic except in run_goal
CANCEL = "        except asyncio.CancelledError:"
GEN = "        except Exception as exc:"
ic = s.find(CANCEL)
ig = s.find(GEN, ic)
assert ic > 0 and ig > ic, ("except anchors not found", ic, ig)
LLMH = '''        except LLMError as exc:
            # S1d: a terminal provider failure halts the goal honestly — the
            # old in-loop sanity guard lived inside the deleted ladder loop
            reason = f"Provider failure ({getattr(exc, 'kind', 'unknown')}): {exc}"[:500]
            return record_provider_halt(
                turn_id, reason, error=str(exc)[:500],
                goal_text=goal_text, language=language,
                owner_session_id=owner_session_id)
'''
s = s[:ig] + LLMH + s[ig:]

# ---------- 3) import LLMError
OLD_IMP = "from providers.client import LLMClient, get_llm_client"
assert s.count(OLD_IMP) == 1, "client import anchor changed"
s = s.replace(OLD_IMP, "from providers.client import LLMClient, LLMError, get_llm_client")

# ---------- 4) swarm bookkeeping cleared with the steer queue in finally
STEER_CLEAR = ("                from core.agent.steer import clear as _clear_steer\n"
               "                _clear_steer(turn_id)")
assert s.count(STEER_CLEAR) == 1, "steer-clear anchor not unique"
s = s.replace(
    STEER_CLEAR,
    "                from core.agent.steer import clear as _clear_steer\n"
    "                _clear_steer(turn_id)\n"
    "                from core.agent.swarm import clear as _clear_swarm\n"
    "                _clear_swarm(turn_id)")

# ---------- 5) resume compat: legacy step names map to their heirs
RES_OLD = ('            for i, step in enumerate(plan):\n'
           '                agent_name = step["agent"]')
assert s.count(RES_OLD) == 1, "resume loop anchor not unique"
RES_NEW = ('            for i, step in enumerate(plan):\n'
           '                # legacy checkpoints carry retired agent names — map them\n'
           '                raw_name = str(step.get("agent") or "general")\n'
           '                from core.core_agents.agents import ALL_AGENTS as _ROSTER\n'
           '                from skill.taxonomy import LEGACY_AGENT_ALIASES as _ALIASES\n'
           '                agent_name = (raw_name if raw_name in _ROSTER\n'
           '                              else _ALIASES.get(raw_name, "general"))')
s = s.replace(RES_OLD, RES_NEW)


def cut(a: str, b: str) -> None:
    global s
    ia = s.find(a)
    ib = s.find(b)
    assert ia > 0 and ib > ia, ("cut anchors missing", a[:34], ia, ib)
    s = s[:ia] + s[ib:]


# ---------- 6) delete _replan + _default_plan + _recovery_steps
cut("    async def _replan(self,",
    "    # ---- default plan -----------------------------------------------------")
cut("    def _default_plan(self, meta: UniverseMeta, goal_text: str)",
    "    def _recovery_steps(")
ia = s.find("    def _recovery_steps(")
ib = s.find("    def _apply_acceptance(")
dec = s.rfind("    @staticmethod", ia, ib)
if ia > 0 and dec > ia and ib - dec < 60:
    ib = dec
assert ia > 0 and ib > ia, ("recovery cut anchors", ia, ib)
s = s[:ia] + s[ib:]

# ---------- 7) remaining labels: the orchestrator identity IS the general agent
n = s.count('agent="orchestrator"')
assert n >= 3, f"routing/timeline labels vanished (found {n})"
s = s.replace('agent="orchestrator"', 'agent="general"')

if crlf:
    s = s.replace("\n", "\r\n")
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("orchestrator.py: S1d transformation applied OK")
