"""connectors/builtin/parity_tools.py — Round-7 verbatim-parity tools.

The exact remaining tool names mined from the shipped Universe engine
(reference shell, 28-name streaming allowlist) and the reference 2.1.277
binary (BUILTIN_TOOL_NAMES + Ct({name:…} registration sites):

- EnterPlanMode            (CC aT: empty input, output {message})
- ToolSearch               (CC Da: {query}, select:<tool_name>, max_results=5)
- Monitor                  (CC Wa: description / timeout_ms>=1000 /
                            persistent / command | ws — "exactly one of
                            command or ws"; ws watch is honestly
                            unsupported here)
- NotebookEdit             (CC): notebook_path / cell_id / new_source /
                            cell_type ["code","markdown"] /
                            edit_mode ["replace","insert","delete"]
- GoalRead                 (Universe allowlist: read the live goal row)
- ReadSessionContext       (Universe: sess_* id, strategy
                            ["relevant","handoff"], maxTokens cap 12000)
- PowerShell               (CC registers PowerShell on Windows:
                            "execute Windows PowerShell commands")
- ApplyPatch               (Universe allowlist; OpenAI codex V4A patch format)

Everything rides the SAME gate as the rest of the registry: write-shaped
tools carry capability "Write" (per_turn consent lifted by the mode
ladders), read-shaped tools stay consent="auto". The registry injects
turn_id as runtime context.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from core.agent.base_agent import Tool
from connectors.builtin.todos import _todos as _todo_ledger

READ_SESSION_MAX_TOKENS = 12000


# ---------------------------------------------------------------- plan door
def _enter_plan_mode(**_: Any) -> dict[str, Any]:
    from security.capability import get_gate
    gate = get_gate()
    before = gate.get_permission_mode()
    if before == "plan":
        return {"ok": True, "changed": False, "mode": "plan",
                "message": "already in plan mode"}
    if before == "full":
        return {"ok": False,
                "message": ("full access bypasses approvals by user choice — "
                            "switching to plan would make tools read-only; "
                            "return from the mode chip yourself if wanted")}
    gate.set_permission_mode("plan")
    return {"ok": True, "changed": True, "mode": "plan",
            "message": ("plan mode set: research read-only, then submit "
                        "the plan with ExitPlanMode")}


# --------------------------------------------------------------- registry
def _tool_search(query: str = "", max_results: int = 5,
                 **_: Any) -> dict[str, Any]:
    raw = str(query or "").strip()
    if raw.lower().startswith("select:"):
        # CC ToolSearch syntax: select:<tool_name> → direct lookup
        name = raw[len("select:"):].strip()
        from connectors.builtin import get_registry
        reg = get_registry()
        names = reg.names()
        hit_name = next((n for n in names
                         if n == name or n.lower() == name.lower()), None)
        return {"ok": True, "matches": ([
            {"name": hit_name,
             "description": reg.get(hit_name).description}
        ] if hit_name else []), "selected": hit_name is not None,
            "total_deferred_tools": len(names)}
    q = raw.lower()
    if not q:
        return {"ok": False, "error": "query required"}
    limit = max(1, min(int(max_results or 5), 25))
    from connectors.builtin import get_registry
    reg = get_registry()
    scored: list[tuple[int, str, str]] = []
    for name in reg.names():
        desc = reg.get(name).description or ""
        name_hit = q in name.lower()
        desc_hit = q in desc.lower()
        if name_hit or desc_hit:
            # name hits outrank description hits (rank 0/1)
            scored.append((0 if name_hit else 1, name, desc))
    scored.sort(key=lambda t: (t[0], t[1]))
    return {"ok": True,
            "matches": [{"name": n, "description": d}
                        for _, n, d in scored[:limit]],
            "total": len(scored),
            "total_deferred_tools": len(reg.names())}


# ----------------------------------------------------------------- monitor
def _monitor(description: str = "", timeout_ms: int = 0, command: str = "",
             persistent: bool = False, ws: dict[str, Any] | None = None,
             **_: Any) -> dict[str, Any]:
    provided = [bool(str(command or "").strip()), ws is not None]
    if not any(provided):
        return {"ok": False,
                "error": "exactly one of command or ws is required"}
    if all(provided):
        return {"ok": False,
                "error": "exactly one of command or ws — got both"}
    if str(description or "").strip() == "":
        return {"ok": False, "error": "description required"}
    if ws is not None:
        return {"ok": False, "unsupported": "ws",
                "message": ("ws watch mode is not implemented in Universe's "
                            "Monitor twin — use command mode")}
    timeout = timeout_ms / 1000.0
    if timeout <= 0:
        return {"ok": False, "error": "timeout_ms must be >= 1000"}
    if timeout > 300:
        # SHELL_TIMEOUT parity: Monitor once allowed 900s while Bash was
        # capped at 300 — same ladder, one cap (audit 2026-10)
        return {"ok": False, "error": "timeout_ms must be <= 300000 (5 min)"}
    from connectors.builtin.shell import _run_sandboxed_dispatch
    out = _run_sandboxed_dispatch(str(command).strip(), cwd=".",
                                  timeout=int(timeout))
    return {"ok": True, "description": description,
            "timed_out": out.get("error") == "timeout",
            "returncode": out.get("returncode"),
            "stdout": out.get("stdout", "")[-10000:],
            "stderr": out.get("stderr", "")[-4000:],
            "elapsed_ms": out.get("elapsed_ms"),
            **({"note": "persistent watch runs collect until timeout only"}
               if persistent else {})}


# ------------------------------------------------------------ notebook edit
def _notebook_edit(notebook_path: str = "", cell_id: str = "",
                   new_source: str = "", cell_type: str = "code",
                   edit_mode: str = "replace", **_: Any) -> dict[str, Any]:
    path = Path(str(notebook_path or "")).expanduser()
    if not path.name.endswith(".ipynb"):
        return {"ok": False, "error": "notebook_path must be a .ipynb file"}
    if not path.is_absolute():
        from connectors.builtin.files import _project_root
        path = _project_root() / path
    if edit_mode not in ("replace", "insert", "delete"):
        return {"ok": False, "error": "edit_mode must be replace|insert|delete"}
    if cell_type not in ("code", "markdown"):
        return {"ok": False, "error": "cell_type must be code|markdown"}
    try:
        nb = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "error": f"notebook not found: {path}"}
    except Exception as exc:
        return {"ok": False, "error": f"invalid notebook json: {exc}"}
    cells = nb.get("cells")
    if not isinstance(cells, list):
        return {"ok": False, "error": "notebook has no cells array"}
    src_lines = str(new_source or "").splitlines(keepends=True)
    if edit_mode == "insert":
        cell = {"cell_type": cell_type, "id": (str(cell_id) if cell_id else
                                              uuid.uuid4().hex[:16]),
                "metadata": {}, "source": src_lines}
        idx = int(cell_id) if str(cell_id).isdigit() else len(cells)
        cells.insert(min(max(idx, 0), len(cells)), cell)
        try:
            path.write_text(json.dumps(nb, indent=1, ensure_ascii=False),
                            encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": f"write failed: {exc}"}
        return {"ok": True, "action": "insert",
                "cell_id": cell["id"], "cells": len(cells)}
    match = [i for i, c in enumerate(cells)
             if c.get("id") == cell_id or str(i) == str(cell_id)]
    if not match:
        return {"ok": False, "error": f"cell not found: {cell_id!r}"}
    idx = match[0]
    if edit_mode == "delete":
        cells.pop(idx)
        return {"ok": True, "action": "delete", "cells": len(cells)}
    cells[idx]["source"] = src_lines
    if cell_type and cells[idx].get("cell_type") != cell_type:
        cells[idx]["cell_type"] = cell_type
    try:
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False),
                        encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"write failed: {exc}"}
    return {"ok": True, "action": "replace", "cell_id": str(cell_id),
            "cells": len(cells)}


# ---------------------------------------------------------------- goal read
def _goal_read(turn_id: str = "", **_: Any) -> dict[str, Any]:
    if not turn_id:
        return {"ok": False, "note": "no active goal context"}
    from storage.local import get_storage
    st = get_storage()
    row = st.query_one(
        "SELECT id, text, status, created_at, final_summary "
        "FROM goals WHERE id=?;", (turn_id,))
    if row is None:
        return {"ok": False, "error": f"goal not found: {turn_id}"}
    todos = list(_todo_ledger.get(turn_id, []))
    return {"ok": True, "goal": {"id": row["id"], "text": row["text"],
                                 "status": row["status"],
                                 "created_at": row.get("created_at"),
                                 "final_summary": row.get("final_summary")},
            "todos": todos, "todo_count": len(todos)}


# ------------------------------------------------------- session context
def _read_session_context(session_id: str = "", strategy: str = "relevant",
                          max_tokens: int = READ_SESSION_MAX_TOKENS,
                          turn_id: str = "", **_: Any) -> dict[str, Any]:
    if strategy not in ("relevant", "handoff"):
        return {"ok": False, "error": "strategy must be relevant|handoff"}
    try:
        cap = max(1, min(int(max_tokens or READ_SESSION_MAX_TOKENS),
                         READ_SESSION_MAX_TOKENS))
    except Exception:
        cap = READ_SESSION_MAX_TOKENS
    target = str(session_id or "").strip() or turn_id
    if not target:
        return {"ok": False, "note": "no session context to read"}
    from storage.local import get_storage
    st = get_storage()
    rows = st.query_all(
        "SELECT id, text, created_at FROM goals "
        "WHERE owner_session_id=? AND kind='message' "
        "ORDER BY created_at ASC;", (target,))
    if not rows:
        return {"ok": True, "session_id": target, "messages": [],
                "note": "thread has no messages"}
    if strategy == "handoff":
        out = [{"role": "user", "text": (r["text"] or "")[:1600]}
               for r in rows[-40:]]
    else:
        out = [{"role": "user", "text": (r["text"] or "")[:4000]}
               for r in rows]
    # token budget: ~4 chars/token, tail-side trim
    budget = cap * 4
    picked: list[dict[str, str]] = []
    used = 0
    for m in reversed(out):
        size = len(m["text"])
        if used + size > budget and picked:
            break
        picked.append(m)
        used += size
    picked.reverse()
    return {"ok": True, "session_id": target, "strategy": strategy,
            "messages": picked, "count": len(picked),
            "approx_tokens": used // 4}


# -------------------------------------------------------------- powershell
def _powershell(command: str = "", timeout: int = 120,
                **_: Any) -> dict[str, Any]:
    cmd = str(command or "").strip()
    if not cmd:
        return {"ok": False, "error": "command required"}
    # -EncodedCommand carries the script as base64(UTF-16LE): NO quoting
    # layer can be injected through it (the old argv "-Command" form was
    # injection-safe but quoting-fragile; encoded is both).
    import base64
    encoded = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
    from connectors.builtin.shell import _run_sandboxed_dispatch
    cmd_line = (f"powershell.exe -NoProfile -NonInteractive "
                f"-EncodedCommand {encoded}")
    try:
        seconds = max(1, min(int(timeout or 120), 300))
    except Exception:
        seconds = 120
    out = _run_sandboxed_dispatch(cmd_line, cwd=".", timeout=seconds)
    return {**out, "tool": "PowerShell"}


# -------------------------------------------------------------- applypatch
def _apply_patch(patch: str = "", **_: Any) -> dict[str, Any]:
    """OpenAI codex V4A patch format (the format Universe's apply_patch tool
    carries): *** Begin Patch / Update File / Add File / Delete File /
    End Patch. Anchored context apply — files not found or '^'-anchor
    misses are reported per file, honest."""
    text = str(patch or "")
    if "*** Begin Patch" not in text:
        return {"ok": False, "error": "patch must start with *** Begin Patch"}
    files: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line in ("*** Begin Patch", "*** End Patch", "*** Begin Patch:") :
            continue
        if line.startswith("*** Update File: "):
            current = {"op": "update", "path": line[len("*** Update File: "):].strip(), "hunks": [], "lines": []}
            files[current["path"]] = current
            continue
        if line.startswith("*** Add File: "):
            current = {"op": "add", "path": line[len("*** Add File: "):].strip(), "lines": []}
            files[current["path"]] = current
            continue
        if line.startswith("*** Delete File: "):
            current = {"op": "delete", "path": line[len("*** Delete File: "):].strip()}
            files[current["path"]] = current
            current = None
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            current.setdefault("hunks", []).append(
                {"ctx": line[2:].strip(), "lines": []})
            continue
        if current.get("hunks"):
            current["hunks"][-1]["lines"].append(line)
        else:
            current.setdefault("add_lines", []).append(line)
    if not files:
        return {"ok": False, "error": "patch carries no file sections"}
    applied: list[dict[str, Any]] = []
    errors: list[str] = []
    # Scope discipline BEFORE any write/delete (audit 2026-10): the patch
    # text used to be the only source of truth for paths and honored
    # absolute paths + `~` expansion — one per_turn approval for "apply a
    # patch to the workspace" silently covered unlimited out-of-workspace
    # writes/deletes. Workspace-relative paths ONLY; the gate now also
    # sees these paths (patch-header extraction), this is the
    # handler-side second lock.
    from connectors.builtin.files import _within_scope
    for path in sorted(files):
        if (".." in Path(path).parts or Path(path).is_absolute()
                or not _within_scope(path)):
            errors.append(f"{path}: rejected — patches accept workspace-"
                          "relative paths only (no absolute paths, no '..')")
    rejected = {e.split(":")[0] for e in errors}
    for path in sorted(files):
        if path in rejected:
            continue
        entry = files[path]
        try:
            from connectors.builtin.files import _project_root
            target = _project_root() / path
            if entry["op"] == "add":
                if target.exists():
                    errors.append(f"{path}: add — file already exists")
                    continue
                body = "".join(ln[1:] if ln[:1] == "+" else ln
                               for ln in entry.get("add_lines", []))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
                applied.append({"path": path, "op": "add"})
                continue
            if entry["op"] == "delete":
                if not target.exists():
                    errors.append(f"{path}: delete — file not found")
                    continue
                target.unlink()
                applied.append({"path": path, "op": "delete"})
                continue
            # update: apply hunks by context anchor (V4A semantics:
            # '-old' lines are replaced by '+new' lines; with NO minus
            # lines the '+new' lines are INSERTED after the '@@' anchor or
            # the first context line — a hunk that neither removes nor
            # inserts anything is an anchor miss, not a silent no-op)
            old = target.read_text(encoding="utf-8", errors="replace")
            text_old = old
            ok_all = True
            for hunk in entry.get("hunks", []):  # type: ignore[union-attr]
                minus: list[str] = []
                plus: list[str] = []
                ctx: list[str] = []
                for ln in hunk["lines"]:
                    if ln.startswith("-"):
                        minus.append(ln[1:])
                    elif ln.startswith("+"):
                        plus.append(ln[1:])
                    elif ln.startswith(" "):
                        ctx.append(ln[1:])
                anchor = (hunk.get("ctx") or "").strip()
                anchor_idx = _find_anchor(text_old, anchor) if anchor else -1
                if anchor_idx < 0 and ctx:
                    anchor_idx = _find_anchor(text_old, ctx[0])
                if minus:
                    block = "\n".join(minus)
                    idx = text_old.find(block)
                    if idx < 0:
                        ok_all = False
                        break
                    text_old = (text_old[:idx] + "\n".join(plus)
                                + text_old[idx + len(block):])
                    continue
                if not plus:
                    ok_all = False
                    break
                # plus-only insertion: right after the anchor (or context)
                # line; no anchor and no context ⇒ hunk cannot be placed
                if anchor_idx < 0:
                    ok_all = False
                    break
                parts = text_old.splitlines(True)
                ins_at = sum(len(p) for p in parts[:anchor_idx + 1])
                text_old = (text_old[:ins_at] + "\n".join(plus) + "\n"
                            + text_old[ins_at:])
            if not ok_all:
                errors.append(f"{path}: anchors not found — no changes")
                continue
            if not entry.get("hunks"):  # type: ignore[union-attr]
                errors.append(
                    f"{path}: Update File requires @@ anchor hunks "
                    "(Add File is for whole-file content)")
                continue
            target.write_text(text_old, encoding="utf-8")
            applied.append({"path": path, "op": "update"})
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return {"ok": not errors and bool(applied),
            "applied": applied, "errors": errors}


def _find_anchor(text: str, anchor: str) -> int:
    """Line-tolerant anchor search for '@@ <text>' markers."""
    for i, line in enumerate(text.splitlines()):
        if anchor in line:
            return i
    return -1


def register(reg) -> None:
    reg.register(Tool(
        name="EnterPlanMode", capability="EnterPlanMode",
        description=(
            "Switch this session into plan mode: read-only research while "
            "you design the approach. Empty input. Once the plan is ready, "
            "request approval with ExitPlanMode."
        ),
        handler=_enter_plan_mode, consent="auto"))
    reg.register(Tool(
        name="ToolSearch", capability="ToolSearch",
        description=(
            "Search the registered tool catalog by keyword "
            "(a 'select:<tool_name>' query selects one tool directly). "
            "Use it before guessing a tool name — the registry is broad."
        ),
        handler=_tool_search, consent="auto"))
    reg.register(Tool(
        name="Monitor", capability="Bash",
        description=(
            "Run a command and watch its output until timeout_ms "
            "(min 1000). Exactly one of command or ws. The result carries "
            "timed_out plus the collected stdout/stderr."
        ),
        handler=_monitor, consent="per_turn", resources=["cwd"]))
    reg.register(Tool(
        name="NotebookEdit", capability="Write",
        description=(
            "Edit a Jupyter notebook: replace/insert/delete one cell by "
            "cell_id (cell_type code|markdown; edit_mode"
            " replace|insert|delete). Insert accepts an integer cell_id as "
            " an index."
        ),
        handler=_notebook_edit, consent="per_turn",
        resources=["path:glob:./**"]))
    reg.register(Tool(
        name="GoalRead", capability="GoalRead",
        description=(
            "Read the live task row: text, status, "
            "final summary, and the current todo list."
        ),
        handler=_goal_read, consent="auto"))
    reg.register(Tool(
        name="ReadSessionContext", capability="Read",
        description=(
            "Read this task's thread messages. strategy 'handoff' returns "
            "the tail (post-compaction re-grounding); 'relevant' returns "
            "the latest messages within the token budget (max 12000)."
        ),
        handler=_read_session_context, consent="auto"))
    reg.register(Tool(
        name="PowerShell", capability="Bash",
        description="Execute Windows PowerShell commands via "
                    "powershell.exe -NoProfile -NonInteractive.",
        handler=_powershell, consent="per_turn", resources=["cwd"]))
    reg.register(Tool(
        name="ApplyPatch", capability="Write",
        description=(
            "Apply a patch to the workspace in the V4A format: "
            "*** Begin Patch … *** Update File: path + @@ anchor context "
            "with -old/+new lines … *** End Patch. Add File and Delete "
            "File sections supported. Files not matching their anchors "
            "are reported per file and left unchanged."
        ),
        handler=_apply_patch, consent="per_turn",
        resources=["path:glob:./**"]))
