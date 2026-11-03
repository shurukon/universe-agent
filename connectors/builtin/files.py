"""Capability-gated project-scoped file tools."""
from __future__ import annotations

from pathlib import Path

from config import get_config
from connectors.builtin import ToolRegistry
from core.agent.base_agent import Tool

MAX_READ = 50_000
MAX_LINES = 5_000  # hard line ceiling for one Read page


def _project_root() -> Path:
    try:
        from core.agent.project_context import project_root_ctx
        ctx = project_root_ctx.get()
        if ctx:
            return Path(ctx).expanduser().resolve()
    except Exception:
        pass
    configured = get_config().get("project_root", "")
    return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()


def _resolved(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = _project_root() / candidate
    return candidate.resolve()


# Windows reserved device names (audit 2026-10, verified live): opening
# `dir\CON` or `dir\NUL` for writing reaches the DEVICE, not a file — the
# call reports ok while the bytes go to the console/null. Deny them.
_RESERVED_DEVICES = {"CON", "PRN", "AUX", "NUL",
                     *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}


def _is_reserved_device(p: Path) -> bool:
    last = p.name.split("/", 1)[-1]
    stem = last.split(".", 1)[0].upper()
    return stem in _RESERVED_DEVICES and last.upper() == stem


def _within_scope(path: str) -> bool:
    p = _resolved(path)
    if _is_reserved_device(p):
        return False
    root = _project_root()
    try:
        p.relative_to(root)
        return True
    except ValueError:
        pass
    # the user-attached-media inbox is sanctioned content the USER handed
    # to Universe this session (pasted screenshots etc.) — the gate already
    # treats it as in-scope, so the handler-level check must agree or
    # every attachment read dies at the handler after passing the gate
    try:
        from security.capability import _in_attachments_inbox
        if _in_attachments_inbox(p):
            return True
    except Exception:
        pass
    for entry in get_config().get("file_allowlist", []):
        try:
            p.relative_to(Path(entry).expanduser().resolve())
            return True
        except (ValueError, TypeError):
            continue
    return False


def file_read(path: str, offset: int = 1, limit: int = 1000) -> dict:
    """Read a page of a text file in LINE units (reference parity).

    Args:
        path: absolute path (or project-relative) of the file to read.
        offset: first line to return, 1-based (0 is tolerated as 1).
        limit: maximum number of LINES to return (default 1000). The page
            is additionally capped at MAX_READ characters — the smaller
            bound wins and sets truncated=true.
    """
    # forgiving: clamp instead of failing on first call
    if limit <= 0:
        limit = 1000
    if limit > MAX_LINES:
        limit = MAX_LINES
    if offset < 0:
        offset = 0
    if offset == 0:
        offset = 1
    if not _within_scope(path):
        from config import get_config as _gc
        _pr = _gc().get("project_root", "")
        return {"ok": False, "error": f"path outside scope: {path} (PROJECT ROOT={_pr}; hint: use relative path)"}
    p = _resolved(path)
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not found: {path} (hint: create it with Write first)"}
    size = p.stat().st_size
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return {"ok": False, "error": f"unreadable: {exc}"}
    total = len(lines)
    start = max(offset, 1) - 1  # 1-based → 0-based; offset=0 tolerated as 1
    page_lines = lines[start:start + limit]
    content = "".join(page_lines)  # each line keeps its own newline
    char_capped = len(content) > MAX_READ
    if char_capped:
        content = content[:MAX_READ]
    end_line = start + len(page_lines)  # 1-based, inclusive
    truncated = char_capped or end_line < total
    return {"ok": True, "path": str(p), "start_line": start + 1,
            "end_line": end_line, "total_lines": total,
            "lines": len(page_lines), "size": size, "truncated": truncated,
            "content": content}


def _atomic_write_text(p: Path, text: str) -> None:
    """Write text via temp-file + os.replace in the SAME directory: a crash
    mid-write can no longer leave a half-written file at the target."""
    import os
    import tempfile as _tempfile
    fd, tmp_name = _tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.",
                                     suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def file_write(path: str, content: str, append: bool = False) -> dict:
    """Write a file atomically (temp file + os.replace).

    Args:
        path: target file path (must be inside the active project root).
        content: the full text to write (replaces any existing content
            unless append=true).
        append: when true, append to the file instead of replacing it.
    """
    if not _within_scope(path):
        return {"ok": False, "error": f"path outside scope: {path}"}
    p = _resolved(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if append:
        # append keeps the plain open-append (atomic replace would rewrite
        # the whole file and race concurrent appenders differently)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content)
    else:
        _atomic_write_text(p, content)
    return {"ok": True, "path": str(p), "bytes": len(content)}


def file_edit(path: str, old_string: str, new_string: str,
              replace_all: bool = False) -> dict:
    """Partial edit via exact text match. Fails unless old_string matches
    exactly once (or replace_all=True), so a stale match can never silently
    hit the wrong spot.

    Args:
        path: target file path (must be inside the active project root).
        old_string: the exact text to replace — quote it verbatim from the
            Read output (no line-number prefixes, no paraphrase).
        new_string: the replacement text (must differ from old_string).
        replace_all: replace every occurrence instead of failing on
            multiple matches.
    """
    if not _within_scope(path):
        return {"ok": False, "error": f"path outside scope: {path}"}
    if not old_string:
        return {"ok": False, "error": "file.edit requires non-empty `old_string`"}
    if old_string == new_string:
        return {"ok": False, "error": "old_string and new_string are identical"}
    p = _resolved(path)
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": "not found"}
    # binary and undecodable files must not reach an exact-match text edit:
    # errors="replace" would silently rewrite undecodable bytes into U+FFFD
    # on write-back, corrupting the file. Strict decode fails fast instead.
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        return {"ok": False,
                "error": "file is not valid UTF-8 text; file.edit refuses "
                         "to rewrite bytes it cannot decode"}
    except OSError as e:
        return {"ok": False, "error": f"cannot read file: {e}"}
    count = text.count(old_string)
    if count == 0:
        return {"ok": False, "error": "old_string not found in file"}
    if count > 1 and not replace_all:
        return {"ok": False,
                "error": f"old_string matches {count} times; pass replace_all=true or add more context"}
    new_text = text.replace(old_string, new_string) if replace_all \
        else text.replace(old_string, new_string, 1)
    _atomic_write_text(p, new_text)
    return {"ok": True, "path": str(p), "replacements": count if replace_all else 1,
            "bytes": len(new_text)}


def file_delete(path: str) -> dict:
    """Delete a file inside the active project (explicit consent).

    Args:
        path: the file to delete (must be inside the active project root).
    """
    if not _within_scope(path):
        return {"ok": False, "error": f"path outside scope: {path}"}
    p = _resolved(path)
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": "not found"}
    p.unlink()
    return {"ok": True, "path": str(p)}


def file_list(path: str = ".", pattern: str = "*") -> dict:
    """List a directory's immediate children.

    Args:
        path: directory to list (must be inside the active project root).
        pattern: glob applied to the children (default '*' = all).
    """
    if not _within_scope(path):
        return {"ok": False, "error": f"path outside scope: {path}"}
    p = _resolved(path)
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "not found"}
    entries = []
    for child in sorted(p.glob(pattern)):
        entries.append({"path": str(child), "is_dir": child.is_dir(),
                        "size": child.stat().st_size if child.is_file() else 0})
    return {"ok": True, "entries": entries}


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(name="Read", capability="Read",
                      description="Read a file page. offset = first line "
                                  "(1-based), limit = max LINES (default "
                                  "1000; page capped at 50K chars). Returns "
                                  "content plus start_line/end_line/"
                                  "total_lines/truncated.",
                      handler=file_read, consent="auto", resources=["path:glob:**/*"]))
    reg.register(Tool(name="Write", capability="Write",
                      description="Write or append to a file within the project",
                      handler=file_write, consent="per_turn", resources=["path:glob:./**"]))
    reg.register(Tool(name="Edit", capability="Write",
                      description="Edit a file by exact text match: old_string is replaced with new_string; fails on zero or multiple matches unless replace_all=true",
                      handler=file_edit, consent="per_turn", resources=["path:glob:./**"]))
    reg.register(Tool(name="Delete", capability="Write",
                      description="Delete a file within the project",
                      handler=file_delete, consent="explicit", resources=["path:glob:./**"]))
    reg.register(Tool(name="LS", capability="Read",
                      description="List files in a directory",
                      handler=file_list, consent="auto", resources=["path:glob:**/*"]))
