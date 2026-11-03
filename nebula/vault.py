"""nebula/vault.py — Obsidian-like markdown vault (plain files, real links).

A note is a .md file with a tiny front-matter block:
    ---
    id: nb-<12hex>
    title: Note title
    tags: [tag1, tag2]
    created: ISO8601   updated: ISO8601
    ---
    body with [[wikilinks]] and #tags

Links resolve by title (case-insensitive). Backlinks are computed by
scanning the vault — no index to drift. distill() condenses a session
transcript (Dust) into a linked note (Stars): LLM summary through the
normal provider chain when one is configured, otherwise an honest raw
excerpt the user can refine.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from config import get_config

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
TAG_RE = re.compile(r"(?<![\w#])#([A-Za-z0-9][\w/\-]*)")


def vault_dir() -> Path:
    d = get_config().home / "nebula"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", title.strip()).strip().replace(" ", "-")
    return (s[:48] or "note").lower()


def _write_note(note_id: str, title: str, tags: list[str], created: str,
                content: str) -> None:
    path = vault_dir() / f"{note_id}.md"
    fm = (f"---\nid: {note_id}\ntitle: {title}\n"
          f"tags: [{', '.join(tags)}]\ncreated: {created}\n"
          f"updated: {_iso(time.time())}\n---\n")
    path.write_text(fm + content, encoding="utf-8")


def _parse(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    m = FM_RE.match(raw)
    body = raw[m.end():] if m else raw
    meta: dict[str, Any] = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    tags: list[str] = []
    raw_tags = meta.get("tags", "")
    if raw_tags:
        inner = raw_tags.strip("[]")
        tags = [t.strip() for t in inner.split(",") if t.strip()]
    for t in TAG_RE.findall(body):
        if t not in tags:
            tags.append(t)
    note_id = meta.get("id") or path.stem
    return {
        "id": note_id,
        "title": meta.get("title") or path.stem,
        "tags": tags,
        "created": meta.get("created") or "",
        "updated": meta.get("updated") or "",
        "content": body,
        "path": str(path),
    }


def _all_notes() -> list[dict[str, Any]]:
    notes = []
    for p in sorted(vault_dir().glob("*.md")):
        n = _parse(p)
        if n:
            notes.append(n)
    return notes


def _title_index(notes: list[dict[str, Any]]) -> dict[str, str]:
    return {n["title"].strip().lower(): n["id"] for n in notes}


def _link_titles(content: str) -> list[str]:
    return [t.strip() for t in WIKILINK_RE.findall(content)]


def _decorate(note: dict[str, Any], notes: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach resolved links/backlinks + counts to a parsed note."""
    idx = _title_index(notes)
    links_to = [idx[t.lower()] for t in _link_titles(note["content"])
                if t.lower() in idx]
    backlinks_from = [n["id"] for n in notes
                      if n["id"] != note["id"] and any(
                          t.lower() == note["title"].strip().lower()
                          for t in _link_titles(n["content"]))]
    out = {k: v for k, v in note.items() if k != "path"}
    out["links_to"] = links_to
    out["backlinks_from"] = backlinks_from
    out["links"] = len(links_to)
    out["backlinks"] = len(backlinks_from)
    return out


def list_notes() -> list[dict[str, Any]]:
    notes = _all_notes()
    metas = []
    for n in notes:
        d = _decorate(n, notes)
        d.pop("content", None)
        metas.append(d)
    metas.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return metas


def get_note(note_id: str) -> dict[str, Any] | None:
    notes = _all_notes()
    for n in notes:
        if n["id"] == note_id:
            return _decorate(n, notes)
    return None


def create_note(title: str, content: str, tags: list[str] | None = None) -> dict[str, Any]:
    note_id = f"{_slug(title)}-{uuid.uuid4().hex[:6]}"
    _write_note(note_id, title.strip()[:120] or "Untitled",
                tags or [], _iso(time.time()), content or "")
    got = get_note(note_id)
    assert got is not None
    return got


def update_note(note_id: str, content: str) -> dict[str, Any] | None:
    got = get_note(note_id)
    if got is None:
        return None
    path = vault_dir() / f"{note_id}.md"
    raw = path.read_text(encoding="utf-8")
    m = FM_RE.match(raw)
    fm = m.group(0) if m else ""
    if m:
        fm = re.sub(r"updated: .*", f"updated: {_iso(time.time())}", fm)
    path.write_text(fm + content, encoding="utf-8")
    return get_note(note_id)


def delete_note(note_id: str) -> bool:
    path = vault_dir() / f"{note_id}.md"
    if not path.exists():
        return False
    path.unlink()
    return True


def graph() -> dict[str, Any]:
    notes = _all_notes()
    idx = _title_index(notes)
    nodes = []
    edges: list[dict[str, str]] = []
    degree: dict[str, int] = {}
    seen_edges: set[tuple[str, str]] = set()
    for n in notes:
        for t in _link_titles(n["content"]):
            dst = idx.get(t.lower())
            if dst and dst != n["id"]:
                pair = (n["id"], dst)
                if pair not in seen_edges:
                    seen_edges.add(pair)
                    edges.append({"src": n["id"], "dst": dst})
                    degree[n["id"]] = degree.get(n["id"], 0) + 1
                    degree[dst] = degree.get(dst, 0) + 1
    for n in notes:
        nodes.append({"id": n["id"], "title": n["title"],
                      "degree": degree.get(n["id"], 0)})
    return {"nodes": nodes, "edges": edges}


# ---- Dust → Stars -------------------------------------------------------------

async def distill(session_id: str | None = None) -> dict[str, Any]:
    """Condense a session transcript into a linked Nebula note."""
    from core.session.store import SessionStore
    ss = SessionStore()
    sessions = ss.list_sessions(limit=20)
    if not sessions:
        raise ValueError("no sessions to distill yet")
    target = None
    if session_id:
        target = next((s for s in sessions if s.get("id") == session_id), None)
        if target is None:
            raise ValueError(f"session {session_id!r} not found")
    else:
        target = sessions[0]
    sid = target["id"]
    msgs = ss.history_messages(sid)
    tail = msgs[-40:]
    transcript = "\n".join(
        f"**{m.get('role', 'user')}**: {str(m.get('content', ''))[:800]}"
        for m in tail if m.get("content"))
    summary = ""
    try:
        from providers.client import get_llm_client
        summary = str(await get_llm_client().complete(
            agent="nebula",
            messages=[
                {"role": "system",
                 "content": ("Distill this agent session into dense memory "
                             "notes. Output markdown: a one-line title "
                             "suggestion, 3-8 bullet facts worth remembering, "
                             "and a short ## Open threads section. No "
                             "preamble.")},
                {"role": "user", "content": transcript[:12000]}],
        ) or "").strip()
    except Exception:
        summary = ""
    if not summary:
        summary = ("> Raw distill (no provider key configured — this is the "
                   "uncondensed session tail; refine it into stars).\n\n"
                   + transcript[-2500:])
    created = create_note(
        title=f"Distilled — {target.get('title') or sid[:12]}",
        content=(f"{summary}\n\n## Source\nDistilled from session `{sid}` "
                 f"({len(msgs)} messages). See [[Stars]] for distilled "
                 "facts.\n"),
        tags=["distilled", "nebula"])
    return {"created": [created["title"]], "note_id": created["id"]}
