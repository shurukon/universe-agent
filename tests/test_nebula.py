"""tests/test_nebula.py — the Memory domain: markdown vault, wikilinks,
backlinks, graph, and Dust→Stars distillation."""
from __future__ import annotations

import asyncio

import pytest

from nebula import vault


def test_note_crud_roundtrip(fresh_home):
    note = vault.create_note("Core ideas", "remember the [[Center]] rule")
    assert note["id"]
    assert note["title"] == "Core ideas"
    got = vault.get_note(note["id"])
    assert got is not None
    assert "remember the" in got["content"]
    assert "[[Center]]" in got["content"]

    vault.update_note(note["id"], "updated body with [[Center]] link")
    got = vault.get_note(note["id"])
    assert got is not None
    assert "updated body" in got["content"]

    assert vault.delete_note(note["id"]) is True
    assert vault.get_note(note["id"]) is None
    assert vault.delete_note(note["id"]) is False


def test_wikilinks_backlinks_and_graph(fresh_home):
    a = vault.create_note("Stars", "facts distilled from [[Dust]]")
    b = vault.create_note("Dust", "raw session grains feeding [[Stars]]")
    c = vault.create_note("Orbits", "skills on repeat, see [[Stars]]")

    got_a = vault.get_note(a["id"])
    assert got_a is not None
    assert got_a["links_to"] == [b["id"]]
    assert got_a["backlinks"] == 2
    assert set(got_a["backlinks_from"]) == {b["id"], c["id"]}

    graph = vault.graph()
    ids = {n["id"] for n in graph["nodes"]}
    assert {a["id"], b["id"], c["id"]} <= ids
    edges = {(e["src"], e["dst"]) for e in graph["edges"]}
    assert (a["id"], b["id"]) in edges
    assert (b["id"], a["id"]) in edges
    degrees = {n["id"]: n["degree"] for n in graph["nodes"]}
    assert degrees[a["id"]] == 3  # out-link to Dust + back-links from Dust and Orbits


def test_tags_from_body(fresh_home):
    note = vault.create_note("Tagged", "this note mentions #memory and #sessions")
    assert "memory" in note["tags"]
    assert "sessions" in note["tags"]


def test_list_notes_sorted_by_updated(fresh_home):
    n1 = vault.create_note("First", "one")
    n2 = vault.create_note("Second", "two")
    vault.update_note(n1["id"], "one (touched)")
    metas = vault.list_notes()
    assert metas[0]["id"] == n1["id"]


def test_distill_requires_sessions(fresh_home):
    with pytest.raises(ValueError):
        asyncio.run(vault.distill(None))


def test_distill_raw_fallback_without_provider(fresh_home):
    """No provider key configured → the honest raw-excerpt distill, not a
    fabricated summary."""
    from core.session.store import SessionStore
    ss = SessionStore()
    s = ss.create(title="smoke distill")
    ss.append(s["id"], {"role": "user", "content": "remember the compass rule"})
    ss.append(s["id"], {"role": "assistant", "content": "the center decides"})
    result = asyncio.run(vault.distill(s["id"]))
    assert result["created"], "a note must be created"
    notes = vault.list_notes()
    assert any(n["title"].startswith("Distilled —") for n in notes)
    target = next(n for n in notes if n["title"] == result["created"][0])
    got = vault.get_note(target["id"])
    assert got is not None
    assert "compass rule" in got["content"]
    assert "uncondensed" in got["content"] or "Raw distill" in got["content"]
