"""tests/test_skills_corpus.py — the Procedural tier ships EMPTY, and that is
the documented state.

Audit 2026-11-10: `skills/loader.py` is a stub that returns zero counts,
`config.TRUSTED_SKILL_SOURCES` is an empty tuple (the corpus was purged on
2026-10-31) and `skills_data/` does not exist — yet the README, the SECURITY
doc and the pyproject wheel list all implied a corpus. This file pins the
honest state so nobody can "restore" a catalog by accident and so the docs
cannot drift away from it again. When a real corpus lands, THIS file is the
checklist to flip (see docs/SKILLS.md).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_trusted_skill_sources_is_empty():
    from config import TRUSTED_SKILL_SOURCES
    assert TRUSTED_SKILL_SOURCES == (), (
        "a corpus is configured again — update docs/SKILLS.md and re-enable "
        "the Skill tool before shipping")


def test_loader_reports_an_empty_catalog():
    from skills import loader
    counts = loader.load_all_skills()
    assert all(v == 0 for v in counts.values()), counts
    boot = loader.ensure_skills_bootstrapped()
    assert boot["bootstrapped"] is False
    assert boot["existing_trusted"] == 0


def test_skill_tool_is_disabled_and_says_so():
    from connectors.builtin import get_registry
    reg = get_registry()
    skill = reg.get("Skill")
    assert skill is not None, "the Skill tool must stay registered (it is whitelisted)"
    out = skill.handler(action="search", query="csv")
    assert out["count"] == 0 and out["skills"] == []
    read = skill.handler(action="read", skill_name="anything")
    assert read["ok"] is False
    assert "disabled" in str(read.get("error", "")).lower()


def test_no_missing_skills_data_package_is_shipped():
    """The wheel must not list a directory that does not exist."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"skills_data"' not in pyproject, (
        "pyproject ships skills_data again — restore the corpus or drop it")


def test_skills_doc_exists_and_states_the_truth():
    doc = ROOT / "docs" / "SKILLS.md"
    assert doc.exists(), "docs/SKILLS.md must document the empty-catalog state"
    text = doc.read_text(encoding="utf-8")
    assert "EMPTY" in text
