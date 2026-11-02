"""Mandatory capability, consent, resource-scope, and audit gate."""
from __future__ import annotations

import asyncio
import fnmatch
import inspect
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from core.agent.base_agent import Tool
from core.memory import get_memory
from security.audit import log as audit_log
from storage.local import get_storage

DEFAULT_POLICY: dict[str, dict[str, Any]] = {
    "Read": {"consent": "auto"},
    "Write": {"consent": "per_turn"},
    "Bash": {"consent": "per_turn"},
    "network.req": {"consent": "auto"},
    "memory.read": {"consent": "auto"},
    "git.read": {"consent": "auto"},
    "git.write": {"consent": "per_turn"},
    "connector.run": {"consent": "per_turn"},
    "secret.read": {"consent": "explicit"},
}

# The only consent levels the gate understands. Anything else — a typo'd
# Tool(consent=...) or a garbage capability_policy row — is escalated to
# "explicit", never silently allowed (the old fail-open behavior).
VALID_CONSENTS = {"auto", "once", "per_turn", "explicit"}

# ZCode's four permission modes (Round-7 RE: zcode.cjs ExitPlanMode enum
# ["build","edit","yolo","auto"], docs/zcode-re-map.md §2): plan = the
# read-only working set; build = the per-goal consent ladder as declared by
# each tool; edit = writes run without the per-goal gate, commands still
# consent; auto = classifier-lean middle (everyday work flows through,
# risky caps still ask); yolo = everything auto.
# Severity ladder: plan 0 < ask 1 <
# acceptEdits 2 < edit 2 < full 3 — Universe's ask fills the
# ask slot, full fills bypassPermissions.
PERMISSION_MODES = ("ask", "plan", "edit", "full")

# Aliases accepted by set_permission_mode (ZCode's compat table + Universe's
# retired modes so a stored "full"/"ask" keeps working after the switch).
MODE_ALIASES = {
    # canonical → aliases that resolve to it
    "ask-before-changes": "ask", "ask_before_changes": "ask", "build": "ask",
    "default": "ask", "autoedit": "ask", "ask-before": "ask",
    "plan-mode": "plan", "plan_mode": "plan",
    "edit-automatically": "edit", "edit_automatically": "edit",
    "auto_edit": "edit", "auto": "edit", "acceptedits": "edit",
    "full-access": "full", "full_access": "full", "yolo": "full",
    "bypasspermissions": "full", "dontask": "full", "dontAsk": "full",
    # legacy retired names
    "full": "full",  # self
    "ask": "ask",
}


def normalize_permission_mode(mode: str) -> str | None:
    """Canonical mode name for user input, or None when unknown."""
    raw = (mode or "").strip().lower()
    if raw in PERMISSION_MODES:
        return raw
    return MODE_ALIASES.get(raw)

# The plan-mode working set (tool names). Everything else is blocked with
# an honest plan_mode message. network.req is ambiguous at the capability
# level (web_search reads; slack_post_message writes), so the line is
# drawn on tool names: pure reads/research plus the plan-ux tools.
# Static fallback for early import before registry is populated; dynamic check
# prefers Tool.allow_in_plan when registry is available (see _is_plan_allowed).
_PLAN_MODE_STATIC = frozenset({
    "Read", "LS",
    "GitStatus", "GitDiff", "GitLog",
    "MemoryQuery",
    "Skill",
    "TodoWrite", "TodoRead",
    "AskUserQuestion", "ExitPlanMode", "EnterPlanMode",
    "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
    "TaskStop", "TaskOutput",
    "GoalRead", "ReadSessionContext", "ToolSearch",
    "WebSearch", "WebFetch", "WebResearch",
    "wikipedia_search", "arxiv_search",
    "Grep", "Glob",
    "Agent",  # research sub-agents allowed in plan (ZCode parity: 84 occurrences)
})

def _is_plan_allowed(tool_name: str) -> bool:
    """Dynamic plan-mode allowlist: Tool.allow_in_plan wins, else static fallback."""
    try:
        from connectors.builtin import get_registry
        reg = get_registry()
        tool = reg.get(tool_name)
        if tool is not None:
            return bool(getattr(tool, 'allow_in_plan', False))
    except Exception:
        pass
    return tool_name in _PLAN_MODE_STATIC

# Backward compat alias for tests that assert membership
PLAN_MODE_ALLOWED_TOOLS = _PLAN_MODE_STATIC

# auto mode (ZCode enum + CC classifier-lean semantics): everyday file
# writes flow without the per-goal prompt — exactly CC's auto sitting
# between acceptEdits and bypassPermissions. These capability sets express
# the classifier in Universe terms: capability "Write" covers the file
# write/edits (Write/Edit/SvgGen…); named destructive tools stay on the
# build ladder; every command/network/connector/dangerous-explicit tool
# consents exactly as it would in build mode.
AUTO_MODE_LIFTED_CAPS = frozenset({"Write"})
AUTO_MODE_NEVER_LIFT = frozenset({"Delete", "Archive", "VulnScanner"})


# Dynamic consent escalation (audit 2026-10). Some tools' real risk depends
# on RUNTIME STATE or argument values a static consent= declaration cannot
# express — e.g. browser tools operating the user's REAL browser after a
# CDP attach, or a docker run that disables its advertised quarantine
# flags (network="host" / readonly=False). An escalator returns True when
# THIS call must be treated as per_turn regardless of the declared
# consent. Escalation NEVER downgrades: an explicit tool stays explicit.
_consent_escalators: dict[str, Callable[[dict[str, Any]], bool]] = {}


def register_consent_escalator(
        tool_name: str,
        predicate: Callable[[dict[str, Any]], bool]) -> None:
    """Register a per-call consent escalator for a tool name."""
    _consent_escalators[tool_name] = predicate


def _escalate(tool_name: str, args: dict[str, Any]) -> bool:
    fn = _consent_escalators.get(tool_name)
    if fn is None:
        return False
    try:
        return bool(fn(args))
    except Exception:
        return True  # fail-closed: a broken escalator asks, never trusts


# Per-call handler timeout (audit 2026-10): nothing bounded a tool
# handler's execution, so one hung handler (browser page op, ssh) stalled
# the whole 48-round turn forever. The gate is the single chokepoint, so
# the watchdog lives here. A to_thread worker cannot be killed on timeout —
# its result is discarded and the turn continues (documented, honest).
GATE_TOOL_TIMEOUT_S = 600.0


def _glob_match(value: str, pattern: str) -> bool:
    """Separator-aware glob matching with REAL `**` semantics.

    `**` matches zero or more path segments; `*` never crosses `/`.
    fnmatch's `*` crosses `/`, which let `..` escapes match `./**`.
    Contract (audit 2026-10): any `..` segment in the value never matches
    a scoped glob; the segment walk below is memoized so pathological
    paths stay linear-ish. This replaces the old equal-length segment
    matcher whose `**/*` only ever matched depth-2 paths — depth-3+
    and root-level paths were silently denied, proving the declared
    scope semantics were never regression-tested end-to-end.
    """
    if ".." in value.split("/"):
        return False  # a relative escape never matches a scoped glob
    if "/" not in pattern:
        return fnmatch.fnmatch(value, pattern)
    v_parts = value.split("/")
    p_parts = pattern.split("/")
    memo: dict[tuple[int, int], bool] = {}

    def rec(vi: int, pi: int) -> bool:
        key = (vi, pi)
        if key in memo:
            return memo[key]
        if pi == len(p_parts):
            res = vi == len(v_parts)
        elif p_parts[pi] == "**":
            # `**` matches zero segments, or consumes one and stays alive
            res = rec(vi, pi + 1) or (vi < len(v_parts) and rec(vi + 1, pi))
        elif vi >= len(v_parts):
            res = False
        else:
            res = (fnmatch.fnmatch(v_parts[vi], p_parts[pi])
                   and rec(vi + 1, pi + 1))
        memo[key] = res
        return res

    return rec(0, 0)


# Context-window sizes by model family for the context meter. The provider
# catalog carries no window field (audit 2026-09), so the meter works from
# this family heuristic and flags every value as an estimate — the UI must
# show it as "~" never as a hard limit.
_CONTEXT_WINDOWS = (  # (substring in model name, window tokens)
    ("gemini", 1_000_000), ("claude", 200_000), ("gpt", 400_000),
    ("llama", 128_000), ("mixtral", 32_000), ("mistral", 32_000),
    ("qwen", 128_000), ("deepseek", 64_000),
)
_CONTEXT_WINDOW_FALLBACK = 32_000


def _context_window_for(model: str) -> tuple[int, bool]:
    """(window_tokens, is_estimate) for a model name. Every value here is a
    heuristic — is_estimate is always True today."""
    m = (model or "").lower()
    for key, window in _CONTEXT_WINDOWS:
        if key in m:
            return window, True
    return _CONTEXT_WINDOW_FALLBACK, True


def _url_match(url: str, pattern: str) -> bool:
    """Allowlist match for url: resources — full-string, no scheme prefixing.

    The old code matched `fnmatch(f"https://{url}", pattern)`, which made
    EVERY url (any scheme, any host) match a `url:https://*` pattern.
    Here the pattern must match the actual url string, and a `*` in the
    host position cannot cross the `://` boundary.
    """
    if not pattern:
        return False
    # file:// is never a network resource: navigating a browser to
    # file:/// read local files with consent=auto, bypassing file.read's
    # project-root scoping entirely (audit 2026-09)
    if url.startswith("file://"):
        return False
    # exact full-string match — `*` matches anything, as intended for
    # `url:*` (the ssh tool's declared universal resource)
    if fnmatch.fnmatch(url, pattern):
        # for scheme-scoped patterns, verify the scheme actually matches:
        # `url:https://*` must not accept `http://...`
        if "://" in pattern:
            scheme = pattern.split("://", 1)[0]
            if not url.startswith(f"{scheme}://"):
                return False
        return True
    return False


# Argument names the gate treats as filesystem locations / network targets.
# Deliberately EXCLUDES ambiguous names (`repo`, `source`, `target`, `file`,
# `root`): GitHub's `repo` is an owner/name string and Stripe's `source` is a
# payment token — treating those as paths blocked legitimate thirdparty calls
# (the tools that DO mean a filesystem path by those names declare it
# explicitly via Tool.path_args). The handler's own signature adds any
# parameter annotated as Path, with a Path default, or named *_path/_dir.
_PATH_ARG_NAMES = {"path", "cwd", "file_path", "dockerfile", "manifest_path",
                   "dir", "directory", "folder", "vault_dir"}
_URL_ARG_NAMES = {"url", "cdp_url", "endpoint", "host", "webhook_url"}


def _signature_arg_names(tool: "Tool") -> tuple[set[str], set[str]]:
    """Split a handler's parameter names into (path-ish, url-ish) sets."""
    paths, urls = set(), set()
    try:
        sig = inspect.signature(tool.handler)
    except (TypeError, ValueError):
        return paths, urls
    for pname, param in sig.parameters.items():
        if param.annotation is Path or isinstance(param.default, Path):
            paths.add(pname)
        if pname.endswith("_path") or pname.endswith("_dir"):
            paths.add(pname)
        if pname.endswith("_url") or pname in ("endpoint", "host"):
            urls.add(pname)
    return paths, urls


def _collect_path_candidates(tool: "Tool", args: dict[str, Any],
                             root: Path) -> list[Path]:
    """Every argument that names a filesystem location, resolved against
    the project root. Non-string values (lists, dicts) are skipped.

    Tool.path_args, when declared, is the authoritative name list for that
    tool; the heuristics only apply to tools that don't declare."""
    if tool.path_args is not None:
        names = set(tool.path_args)
    else:
        names = _PATH_ARG_NAMES | _signature_arg_names(tool)[0]
    out: list[Path] = []
    for name in names:
        value = args.get(name)
        if isinstance(value, Path):
            cand = value
        elif isinstance(value, str) and value.strip():
            cand = Path(value).expanduser()
        else:
            continue
        if not cand.is_absolute():
            cand = root / cand
        out.append(cand.resolve())
    # a `patch` argument carries its file targets INSIDE the text —
    # extract them so the declared path scope actually gets tested
    patch_val = args.get("patch")
    if isinstance(patch_val, str) and patch_val.strip():
        out.extend(_paths_from_patch(patch_val, root))
    return out


def _collect_url_candidates(tool: "Tool", args: dict[str, Any]) -> list[str]:
    """Every argument that names a network target."""
    names = _URL_ARG_NAMES | _signature_arg_names(tool)[1]
    out: list[str] = []
    for name in names:
        value = args.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        if name == "host":
            value = f"http://{value}:{args.get('port', '')}"
        out.append(value)
    return out


# V4A patch format file headers — the ApplyPatch tool's only argument is
# the patch TEXT itself, and its file targets live INSIDE that text
# (`*** Update File: <path>`). Without this extraction the gate saw zero
# path candidates and ApplyPatch's declared `path:glob:./**` scope was
# vacuous — absolute paths inside a patch wrote or DELETED arbitrary
# locations on disk at per_turn consent (audit 2026-10, verified live).
_V4A_FILE_HEADER = re.compile(
    r"^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$", re.MULTILINE)


def _paths_from_patch(patch: str, root: Path) -> list[Path]:
    """Filesystem targets declared by a V4A patch body, resolved like any
    other path candidate (relative → project root, `~` expanded)."""
    out: list[Path] = []
    for m in _V4A_FILE_HEADER.finditer(patch):
        raw = m.group(1).strip().strip('"').strip("'")
        if not raw:
            continue
        cand = Path(raw).expanduser()
        if not cand.is_absolute():
            cand = root / cand
        out.append(cand.resolve())
    return out

def _attachments_inbox_roots() -> list[Path]:
    """The user-attached-media inbox directories.

    electron/main.ts saves pasted clipboard images under
    `%TMP%/universe-attachments/clipboard-<ts>.png` and the composer hands
    that absolute path to the bridge as an attachment. Those files are
    content the USER explicitly handed to Universe this session — the
    exact thing they asked the agent to look at — so the project-root
    resource gate must not bounce them (the 2026-10-25 live failure:
    a pasted screenshot attachment made every read of it fail with
    "resource outside the active project or allowlist").
    """
    roots: list[Path] = []
    override = os.environ.get("UNIVERSE_ATTACHMENTS_DIR", "").strip()
    if override:
        try:
            roots.append(Path(override).expanduser().resolve())
        except OSError:
            pass
    roots.append((Path(tempfile.gettempdir()) / "universe-attachments").resolve())
    roots.append((Path(tempfile.gettempdir()) / "universe-cua-frames").resolve())
    return roots


def _in_attachments_inbox(cand: Path) -> bool:
    for root in _attachments_inbox_roots():
        try:
            cand.relative_to(root)
            return True
        except ValueError:
            continue
    return False


ConsentHandler = Callable[[Tool, str, str, dict[str, Any]], bool | Awaitable[bool]]


class CapabilityGate:
    """The single chokepoint. A missing handler never bypasses the policy."""

    def __init__(self) -> None:
        self._memory = get_memory()
        self._st = get_storage()
        self._granted: set[tuple[str, str]] = set()
        self._once_granted: set[str] = set()
        self._load_persisted_grants()
        self._auto_grant_all = False
        self._consent_handler: ConsentHandler | None = None
        # session-level permission mode (ZCode parity): plan|build|edit|auto|yolo
        self._permission_mode: str = "ask"

    def _load_persisted_grants(self) -> None:
        """Restore consent grants saved by a previous process (m2 wiring of
        the capability_grants table). Per-goal grants are keyed by
        (turn_id, tool_name); "once" grants are keyed by tool_name alone
        (turn_id = ''). Grants with a future expires_at survive only until
        that timestamp; NULL expires_at means no expiry."""
        now = time.time()
        try:
            rows = self._st.query_all(
                "SELECT turn_id, tool, expires_at FROM capability_grants "
                "WHERE (expires_at IS NULL OR expires_at > ?);", (now,))
        except Exception:
            return  # pre-v15 schema or unreadable table — start clean
        for row in rows:
            tool_name = str(row.get("tool") or "")
            if not tool_name:
                continue
            if row.get("turn_id"):
                self._granted.add((str(row["turn_id"]), tool_name))
            else:
                self._once_granted.add(tool_name)

    def _persist_grant(self, turn_id: str, tool_name: str,
                        consent: str) -> None:
        """Write a grant to capability_grants so it survives restarts."""
        try:
            self._st.execute(
                "INSERT INTO capability_grants"
                "(capability, scope, turn_id, granted_at, expires_at, tool) "
                "VALUES(?,?,?,?,?,?);",
                ("", consent, turn_id or "", time.time(), None, tool_name),
            )
        except Exception:
            pass  # persistence is best-effort — the gate still works in-memory

    def _drop_expired_grants(self) -> None:
        now = time.time()
        try:
            self._st.execute(
                "DELETE FROM capability_grants WHERE expires_at IS NOT NULL "
                "AND expires_at <= ?;", (now,))
        except Exception:
            pass

    def set_auto_grant(self, on: bool) -> None:
        """Only test/eval harnesses should enable this explicit bypass."""
        self._auto_grant_all = on

    def set_consent_handler(self, handler: ConsentHandler | None) -> None:
        """Install the interactive consent callback used by CLI/channel frontends."""
        self._consent_handler = handler

    def set_permission_mode(self, mode: str) -> str:
        """Switch the session permission mode. Returns the mode actually
        set — an invalid value keeps the current mode (fail-safe, no
        exception: a bad bridge payload can never crash a call). Aliases
        (acceptEdits, bypassPermissions, the retired full/ask…) normalize
        to the five ZCode modes through MODE_ALIASES."""
        canonical = normalize_permission_mode(mode)
        if canonical is None:
            return self._permission_mode
        mode = canonical
        previous = self._permission_mode
        self._permission_mode = mode
        audit_log(actor="user", action="permission_mode",
                  args={"mode": mode}, result=f"set:{mode}", turn_id="")
        if mode != previous:
            # the UI's mode chip must not go stale when plan.submit (or
            # the bridge endpoint) flips the mode mid-goal
            from observability.events import emit
            emit("permission_mode_changed", mode=mode, previous=previous)
        return self._permission_mode

    def get_permission_mode(self) -> str:
        return self._permission_mode

    # capabilities whose consent can NEVER be downgraded by a policy-row
    # override: plan.submit's explicit prompt IS the plan approval — one
    # row (capability='ExitPlanMode', consent='auto') would otherwise collapse
    # the entire plan-mode read-only guarantee without any prompt
    _PINNED_EXPLICIT = frozenset({"ExitPlanMode"})

    def _policy_for(self, capability: str, tool_consent: str | None = None) -> dict[str, Any]:
        if capability in self._PINNED_EXPLICIT:
            return {"consent": "explicit"}
        row = self._st.query_one(
            "SELECT * FROM capability_policy WHERE capability=?;", (capability,))
        if row:
            # fail-closed: a policy row with an unknown consent value is
            # escalated, not silently honored (it used to bypass the gate)
            consent = str(row["consent"])
            if consent not in VALID_CONSENTS:
                consent = "explicit"
            return {"consent": consent}
        if tool_consent is not None:
            # a tool with a typo'd consent= value is escalated to explicit,
            # never downgraded to "silently allowed"
            consent = str(tool_consent)
            if consent not in VALID_CONSENTS:
                consent = "explicit"
            return {"consent": consent}
        return DEFAULT_POLICY.get(capability, {"consent": "per_turn"})

    def _project_root(self) -> Path:
        try:
            from core.agent.project_context import project_root_ctx
            ctx = project_root_ctx.get()
            if ctx:
                return Path(ctx).expanduser().resolve()
        except Exception:
            pass
        from config import get_config
        configured = get_config().get("project_root", "")
        return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()

    def _resource_allowed(self, tool: Tool, args: dict[str, Any]) -> bool:
        """Evaluate declared path/url/cwd resources against the active project.

        Fail-closed rewrite (audit 2026-09): the old implementation had two
        vacuous branches — a `url:` resource with no URL argument in the call
        returned True immediately (so browser_screenshot's `path` argument
        was never scope-checked and could write any absolute path), and
        empty resource lists returned True regardless of arguments (so
        charting read any local file). New contract:

          * every argument that names a filesystem location must be inside
            the project root AND matched by a declared `path:glob:`/`cwd`
            resource — no exceptions, no early True
          * every argument that names a network target must be matched by a
            declared `url:`/`network` resource
          * a tool with NO declared resources passes only calls that carry
            no filesystem/network surface at all
          * `path:*` is the deliberate any-path declaration for
            explicit-consent tools (mcp_add_server) — the path twin of the
            ssh tool's `url:*`
        """
        root = self._project_root()
        path_cands = _collect_path_candidates(tool, args, root)
        url_cands = _collect_url_candidates(tool, args)

        if not tool.resources:
            # no declared resources: a purely internal call passes; a call
            # that touches the filesystem or network fails closed — the
            # user-attached-media inbox stays sanctioned (see
            # _attachments_inbox_roots)
            inbox_free = [c for c in path_cands
                          if not _in_attachments_inbox(c)]
            return not inbox_free and not url_cands

        path_resources = [r for r in tool.resources
                          if r == "cwd" or r == "path:*"
                          or r.startswith("path:glob:")]
        url_resources = [r for r in tool.resources
                         if r.startswith("url:") or r == "network"]

        for cand in path_cands:
            # the user-attached-media inbox is always in scope (see
            # _attachments_inbox_roots) — user content, sanctioned
            if _in_attachments_inbox(cand):
                continue
            if not any(self._path_matches(cand, res, root)
                       for res in path_resources):
                return False
        for url in url_cands:
            if not any(self._url_matches(url, res)
                       for res in url_resources):
                return False
        return True

    @staticmethod
    def _path_matches(cand: Path, resource: str, root: Path) -> bool:
        if resource == "path:*":
            return True  # deliberate any-path declaration (explicit consent)
        if resource == "cwd":
            # cwd scoping: the root itself and anything under it
            try:
                cand.relative_to(root)
                return True
            except ValueError:
                return False
        if resource.startswith("path:glob:"):
            pattern = resource.removeprefix("path:glob:")
            if cand == root:
                return True  # the project root itself is always in scope
            try:
                rel = cand.relative_to(root).as_posix()
            except ValueError:
                return False  # outside the project root — a scoped glob
                # must not match it (no more `fnmatch(abs, "**/*")`)
            return _glob_match(rel, pattern) or _glob_match(f"./{rel}", pattern)
        return False

    @staticmethod
    def _url_matches(url: str, resource: str) -> bool:
        if resource == "network":
            return urlparse(url).scheme in {"http", "https"}
        if resource.startswith("url:"):
            return _url_match(url, resource.removeprefix("url:"))
        return False

    async def _ask_consent(self, tool: Tool, *, agent: str, turn_id: str,
                           args: dict[str, Any]) -> bool:
        if self._consent_handler is None:
            return False
        decision = self._consent_handler(tool, agent, turn_id, args)
        if inspect.isawaitable(decision):
            decision = await decision
        return bool(decision)

    async def _grant_or_block(self, *, tool: Tool, consent: str, agent: str,
                              turn_id: str, args: dict[str, Any], start: float) -> dict[str, Any] | None:
        key = (turn_id, tool.name)
        already = key in self._granted or (consent == "once" and tool.name in self._once_granted)
        if self._auto_grant_all or already or consent == "auto":
            return None
        # unknown consent values were normalized to "explicit" by
        # _policy_for — this is the fail-closed contract: everything not
        # explicitly auto/already-granted prompts the user
        approved = await self._ask_consent(tool, agent=agent, turn_id=turn_id, args=args)
        if approved:
            if consent == "once":
                self._once_granted.add(tool.name)
            else:
                self._granted.add(key)
            self._persist_grant(turn_id, tool.name, consent)
            # consent decisions used to leave no audit entry — only blocks
            # did (audit 2026-09); a grant is the security-relevant event
            audit_log(actor=agent, action=f"consent:{tool.capability}",
                      args={"tool": tool.name, "consent": consent},
                      result="granted", turn_id=turn_id,
                      duration_ms=int((time.time() - start) * 1000))
            return None
        audit_log(actor=agent, action=f"tool:{tool.capability}", args=args,
                  result="blocked:consent", turn_id=turn_id,
                  duration_ms=int((time.time() - start) * 1000))
        # (failure-diagnosis.md root #3) the bare "requires user consent"
        # could not tell a user DENY from a missed/expired prompt — 40
        # recorded dead goals where the user never learned the operation
        # simply waited 60s for an approval that never came. Say both.
        return {"ok": False,
                "error": f"{tool.name} requires user consent — it was denied "
                         "or no approval arrived within the 60s window; "
                         "re-run and approve from the consent banner",
                "needs_consent": True, "capability": tool.capability,
                "tool": tool.name}

    def _deny_core(self, tool: Tool, *, agent: str,
                   args: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
        """The pure deny phase (blackhole → resource → whitelist →
        plan-mode), shared by check_denied and enforce so the
        security-critical chain can never drift between two copies.
        Returns (kind, error, extra_fields) or None. NO auditing here —
        each caller audits at its own site so a denial lands exactly once."""
        cap = tool.capability
        action_desc = f"{cap} {args.get('cmd', '') or args.get('path', '') or args.get('url', '')}"
        if self._memory.is_blackhooled(action_desc):
            return ("blackhole", "blocked by black hole rule", {})
        if not self._resource_allowed(tool, args):
            return ("resource", "resource outside the active project or allowlist", {})
        # whitelist scoping (m2: fail-closed — an unregistered agent name
        # gets NO whitelist exemption)
        from core.core_agents.agents import ALL_AGENTS, get_agent
        if agent in ALL_AGENTS:
            whitelist = getattr(get_agent(agent), "tool_whitelist_names", None)
            core_skill_tools = {"Skill"}
            if whitelist is not None and tool.name not in whitelist and tool.name not in core_skill_tools:
                return ("whitelist", f"tool {tool.name!r} not in {agent!r} whitelist",
                        {"whitelist": list(whitelist)})
        else:
            return ("unknown_agent",
                    f"unknown agent {agent!r} — tool calls are "
                    f"whitelist-scoped to registered agents", {})
        # permission modes (ZCode parity): plan = read-only working set;
        # build/ask = every policy lifted to explicit is never silent
        if self._permission_mode == "plan" and not _is_plan_allowed(tool.name):
            return ("plan_mode",
                    f"{tool.name} is blocked in plan mode — only "
                    f"read-only research and planning tools run; "
                    f"submit the plan (ExitPlanMode) to switch to "
                    f"build mode",
                    {"capability": cap, "tool": tool.name})
        return None

    def _audit_deny(self, *, agent: str, cap: str, args: dict[str, Any],
                    kind: str, turn_id: str, start: float) -> None:
        audit_log(actor=agent, action=f"tool:{cap}", args=args,
                  result=f"blocked:{kind}", turn_id=turn_id,
                  duration_ms=int((time.time() - start) * 1000))

    def check_denied(self, tool: Tool, *, agent: str, turn_id: str,
                     args: dict[str, Any]) -> dict[str, Any] | None:
        """Registry pre-hook security pass: SECURITY verdicts run before the
        UX hook layer so a fact-forcing hook can never mask a blackhole /
        fail-closed unknown-agent / plan-mode block (test_security
        precedence invariants). Returns the deny dict (audited HERE — the
        registry then returns without calling enforce, so exactly one audit
        entry), or None to proceed to hooks/consent."""
        start = time.time()
        deny = self._deny_core(tool, agent=agent, args=args)
        if deny is None:
            return None
        kind, error, extra = deny
        self._audit_deny(agent=agent, cap=tool.capability, args=args,
                         kind=kind, turn_id=turn_id, start=start)
        return {"ok": False, "error": error, "blocked_by": kind, **extra}

    async def enforce(self, tool: Tool, *, agent: str, turn_id: str,
                      args: dict[str, Any]) -> dict[str, Any]:
        """The single chokepoint. A missing handler never bypasses the
        policy. Deny phase via the shared _deny_core; consent + handler
        dispatch live here only."""
        start = time.time()
        cap = tool.capability
        deny = self._deny_core(tool, agent=agent, args=args)
        if deny is not None:
            kind, error, extra = deny
            self._audit_deny(agent=agent, cap=cap, args=args, kind=kind,
                             turn_id=turn_id, start=start)
            return {"ok": False, "error": error, "blocked_by": kind, **extra}
        policy = self._policy_for(cap, tool.consent)
        mode = self._permission_mode
        if mode == "full" and policy["consent"] != "explicit":
            # yolo: nothing runs silently gated — auto/per_turn are lifted
            # to auto so the run never blocks on a consent prompt
            policy = {"consent": "auto"}
        elif mode == "edit" and policy["consent"] == "per_turn":
            # edit: FILE WRITES run without the per-goal gate; commands,
            # network/connector runs and the named destructive tools keep
            # asking — explicit stays explicit. (Audit 2026-10: this
            # branch used to lift EVERY per_turn tool — Bash, BrowserConsole
            # JS eval, GitPush — contradicting the shipped product copy
            # "writes run without the gate; commands ask". It now uses the
            # same capability-gated lift the auto mode uses.)
            if (cap in AUTO_MODE_LIFTED_CAPS
                    and tool.name not in AUTO_MODE_NEVER_LIFT):
                policy = {"consent": "auto"}

        # runtime-state escalation: a call whose risk the static consent
        # cannot express (CDP-attached browser tools, docker quarantine
        # downgrades) is lifted to per_turn — never downgraded
        if (policy["consent"] == "auto"
                and _escalate(tool.name, args)):
            policy = {"consent": "per_turn"}
        consume_once = policy["consent"] == "once"
        blocked = await self._grant_or_block(tool=tool, consent=policy["consent"],
                                             agent=agent, turn_id=turn_id, args=args,
                                             start=start)
        if blocked:
            return blocked
        # per-call watchdog: per-tool timeout wins, then config, then global.
        # Tool.timeout_s is the ZCode timeoutMs parity (e.g. Read=30s, Bash=600s).
        timeout_s = getattr(tool, 'timeout_s', None)
        if timeout_s is None:
            timeout_s = GATE_TOOL_TIMEOUT_S
            try:
                from config import get_config
                # per-tool override: tool_timeout_<ToolName> e.g. tool_timeout_Bash
                per_tool = get_config().get(f"tool_timeout_{tool.name}")
                if per_tool is not None:
                    timeout_s = max(5.0, float(per_tool))
                else:
                    configured = get_config().get("tool_timeout_s")
                    if configured:
                        timeout_s = max(5.0, float(configured))
            except Exception:
                pass
        else:
            timeout_s = max(5.0, float(timeout_s))
        try:
            if inspect.iscoroutinefunction(tool.handler):
                result = await asyncio.wait_for(
                    tool.handler(**args), timeout=timeout_s)
            else:
                # sync handlers run in a worker thread so a long subprocess
                # (ffmpeg, pip-audit, tesseract…) can never freeze the event
                # loop — the whole app (bridge, WS, voice, cancellation)
                # used to hang for up to 5 minutes behind one blocking call
                result = await asyncio.wait_for(
                    asyncio.to_thread(tool.handler, **args), timeout=timeout_s)
        except asyncio.TimeoutError:
            audit_log(actor=agent, action=f"tool:{cap}", args=args,
                      result=f"timeout:{timeout_s:.0f}s", turn_id=turn_id,
                      duration_ms=int((time.time() - start) * 1000))
            return {"ok": False,
                    "error": f"{tool.name} timed out after {timeout_s:.0f}s"}
        except Exception as exc:
            audit_log(actor=agent, action=f"tool:{cap}", args=args,
                      result=f"error:{exc.__class__.__name__}", turn_id=turn_id,
                      duration_ms=int((time.time() - start) * 1000))
            return {"ok": False, "error": str(exc)}
        finally:
            # "once" consent is SINGLE-USE now (audit 2026-10): the grant
            # used to persist forever keyed by tool name alone, so the
            # first approval silently became a permanent one
            if consume_once:
                self._consume_once_grant(tool.name)
        audit_log(actor=agent, action=f"tool:{cap}", args=args, result="ok",
                  turn_id=turn_id, duration_ms=int((time.time() - start) * 1000))
        # The dispatch envelope stays {"ok": True, "result": …} (call was
        # allowed and ran) — the HANDLER's own ok is the truth about the
        # side effect. A handler that reports {"ok": False, …} must NOT
        # be re-wrapped as a gate success: the envelope's ok mirrors the
        # handler's verdict so failure tracking (attach_tool_events) and
        # every reg.call consumer see the real outcome.
        if isinstance(result, dict) and "ok" in result:
            return {"ok": bool(result.get("ok")), "result": result}
        return {"ok": True, "result": result}

    def _consume_once_grant(self, tool_name: str) -> None:
        """Drop a consumed single-use "once" grant from memory and disk."""
        self._once_granted.discard(tool_name)
        try:
            self._st.execute(
                "DELETE FROM capability_grants WHERE turn_id='' AND tool=?;",
                (tool_name,))
        except Exception:
            pass  # persistence cleanup is best-effort; memory state is truth

    def grant(self, turn_id: str, tool_name: str) -> None:
        self._granted.add((turn_id, tool_name))
        self._persist_grant(turn_id, tool_name, "per_turn")


_gate: CapabilityGate | None = None


def get_gate() -> CapabilityGate:
    global _gate
    if _gate is None:
        _gate = CapabilityGate()
        from connectors.builtin import get_registry
        get_registry().set_gate(_gate)
    return _gate


def reset_gate_for_tests() -> CapabilityGate:
    global _gate
    _gate = CapabilityGate()
    from connectors.builtin import get_registry
    get_registry().set_gate(_gate)
    return _gate
