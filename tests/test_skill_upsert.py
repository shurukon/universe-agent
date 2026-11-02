# Adapted for the 2026-10-31 skills purge: skills/loader.py is an
# empty-catalog stub, so re-ingestion is driven through the product's own
# pipeline (skill.ingestion.ingest_source) over a small temp corpus instead
# of the removed 1,584-row bundled library. The user-state preservation and
# pinned-version semantics these tests pin are unchanged in
# skill/ingestion._preserve_user_state / L4Procedural.pin_version.
"""tests/test_skill_upsert.py — re-ingestion must not wipe user state.

Re-ingestion used to mint a fresh id and INSERT OR REPLACE on the
UNIQUE(name, source) index, deleting the prior row — silently discarding the
Settings enabled toggle, use_count, last_used, and any pinned version
(MEDIUM finding, 2026-10 audit). These tests pin the fixed behavior:

- two ingestion runs keep every row and keep row identity stable;
- a manually-set enabled=0 / use_count=7 survives a re-ingest;
- a pinned version survives a re-ingest AND stays signature-verifiable;
- an approved community skill keeps its approval across a re-ingest.
"""
import pytest


BODIES = {
    "alpha": "Step 1: do the trusted thing.\nStep 2: verify the result.\n",
    "beta": "Step 1: prepare the environment.\nStep 2: run the checks.\n",
}


def _make_corpus(root) -> str:
    """A minimal bundled corpus: one trusted source (ECC), two skills."""
    src = root / "ECC"
    src.mkdir(parents=True, exist_ok=True)
    for name, body in BODIES.items():
        (src / f"{name}.md").write_text(
            "---\n"
            f"name: {name}\n"
            "source: ECC\n"
            'version: "1.0.0"\n'
            "description: test skill\n"
            'tags: ["test"]\n'
            "triggers: []\n"
            "license: MIT\n"
            "target_agent: code\n"
            "category: code_generation\n"
            "---\n" + body,
            encoding="utf-8")
    return str(root)


def _ingest() -> dict:
    from skill.ingestion import ingest_source
    from pathlib import Path
    import os
    return ingest_source(Path(os.environ["UNIVERSE_SKILLS_DATA"]) / "ECC")


def _signature_mismatches() -> list[str]:
    """Verify every TRUSTED row against its own stored signature via the
    live skill.signing function (the loader stub no longer does this)."""
    from skill.signing import sign_skill
    from storage.local import get_storage
    st = get_storage()
    return [
        r["id"] for r in st.query_all(
            "SELECT id, name, source, version, body, signature FROM skills "
            "WHERE status='trusted';")
        if (r["signature"] or "") != sign_skill(r["name"], r["source"],
                                                r["version"], r["body"] or "")
    ]


@pytest.fixture
def tiny_corpus_home(fresh_home, monkeypatch, tmp_path):
    """Fresh home + a 2-skill corpus in a temp skills_data root, with ECC on
    a TEST allowlist (the production tuple is empty post-purge; the tier
    machinery is still live product code). The index is wiped so each test
    rebuilds from the tiny corpus — deterministic table-wide assertions."""
    import config as config_mod
    import skill.ingestion as ingestion_mod
    allow = ("ECC",)
    monkeypatch.setattr(config_mod, "TRUSTED_SKILL_SOURCES", allow)
    monkeypatch.setattr(ingestion_mod, "TRUSTED_SKILL_SOURCES", allow)
    corpus = _make_corpus(tmp_path / "skills_data")
    monkeypatch.setenv("UNIVERSE_SKILLS_DATA", corpus)
    from storage.local import get_storage
    st = get_storage()
    with st.transaction() as conn:
        for table in ("skills", "skill_quarantine", "skill_activations",
                      "skill_taxonomy_audit"):
            conn.execute(f"DELETE FROM {table};")
    return tmp_path / "skills_data"


def test_two_loads_keep_row_count_and_identity(tiny_corpus_home):
    """Two ingestion runs keep every row and every row id."""
    from storage.local import get_storage

    _ingest()
    st = get_storage()
    before = {r["id"]: r["name"] for r in st.query_all("SELECT id, name FROM skills;")}
    assert len(before) == 2
    _ingest()
    after = {r["id"]: r["name"] for r in st.query_all("SELECT id, name FROM skills;")}
    assert after == before


def test_reingest_preserves_enabled_and_use_count(tiny_corpus_home):
    """A manually-set enabled=0 / use_count=7 survives a re-ingest, along
    with last_used and row identity; the row stays trusted and verifiable."""
    from storage.local import get_storage

    _ingest()
    st = get_storage()
    row = st.query_one("SELECT * FROM skills WHERE name='alpha' AND source='ECC';")
    with st.transaction() as conn:
        conn.execute("UPDATE skills SET enabled=0, use_count=7, last_used=123.0 "
                     "WHERE id=?;", (row["id"],))

    _ingest()

    row = st.query_one("SELECT * FROM skills WHERE id=?;", (row["id"],))
    assert row is not None                       # identity kept (same id)
    assert row["enabled"] == 0                   # Settings toggle survives
    assert row["use_count"] == 7                 # usage survives
    assert row["last_used"] == 123.0
    assert row["status"] == "trusted"
    assert st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"] == 2
    assert _signature_mismatches() == []


def test_reingest_preserves_user_state_on_seeded_corpus(fresh_home_with_skills,
                                                        tmp_path):
    """Same preservation contract on the SEEDED multi-source corpus (the
    1,584-row bundled corpus is purged): a second ingestion pass through
    the product pipeline keeps every row and preserves enabled=0 /
    use_count=7 on a sampled row. (A raw L4 upsert with a fresh id would
    REPLACE the row on the UNIQUE(name, source) index — the pipeline's
    _preserve_user_state is what must prevent that.)"""
    from skill.ingestion import ingest_source
    from storage.local import get_storage

    st = get_storage()
    total = st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"]
    assert total >= 6  # the conftest seeded corpus
    row = st.query_one(
        "SELECT id FROM skills WHERE status='trusted' LIMIT 1;")
    with st.transaction() as conn:
        conn.execute("UPDATE skills SET enabled=0, use_count=7 WHERE id=?;", (row["id"],))

    # re-run the product's ingestion over the SAME corpus files the fixture
    # seeded (conftest writes them under tmp_path/skills_data)
    corpus = tmp_path / "skills_data"
    for src_dir in sorted(p for p in corpus.iterdir() if p.is_dir()):
        ingest_source(src_dir)

    assert st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"] == total
    row = st.query_one("SELECT enabled, use_count, status FROM skills WHERE id=?;",
                       (row["id"],))
    assert row is not None                       # identity kept (same id)
    assert row["enabled"] == 0
    assert row["use_count"] == 7
    assert row["status"] == "trusted"


def test_pinned_version_survives_reingest_and_stays_verifiable(tiny_corpus_home):
    """Version is user-owned (pin flows write it in place). A re-ingest keeps
    the pinned version and re-signs over it, so the row both keeps its pin
    and passes signature verification."""
    from storage.local import get_storage

    _ingest()
    st = get_storage()
    with st.transaction() as conn:
        conn.execute("UPDATE skills SET version='9.9.9-pin' "
                     "WHERE name='alpha' AND source='ECC';")

    _ingest()

    row = st.query_one("SELECT * FROM skills WHERE name='alpha' AND source='ECC';")
    assert row["version"] == "9.9.9-pin"
    assert row["status"] == "trusted"
    assert _signature_mismatches() == []


def test_approved_community_skill_survives_reingest(fresh_home, tmp_path):
    """An approved community skill (status 'active' at 0.70) must not be
    silently demoted back to quarantine by a re-ingest of the same source."""
    from skill.ingestion import ingest_source
    from skill.quarantine import list_quarantine, approve, pending_count
    from storage.local import get_storage

    src = tmp_path / "community"
    src.mkdir()
    (src / "widget.md").write_text(
        "---\nname: widget-maker\nsource: RandomForum\n"
        'version: "0.1.0"\ndescription: community skill\ntags: []\n'
        "triggers: []\nlicense: unknown\ntarget_agent: code\n"
        "category: general\n---\nBody of the community skill.\n",
        encoding="utf-8")
    ingest_source(src)
    items = list_quarantine()
    skill = approve(items[0]["id"])
    assert skill.status == "active"

    ingest_source(src)

    st = get_storage()
    row = st.query_one(
        "SELECT * FROM skills WHERE name='widget-maker' AND source='RandomForum';")
    assert row["status"] == "active"             # approval kept
    assert row["confidence"] == pytest.approx(0.70)
    assert pending_count() == 0                  # no duplicate review row
    # a re-ingest of a DIFFERENT (bundled) source never touches the
    # community row — re-parse the same source; approval still stands
    ingest_source(src)
    assert st.query_one(
        "SELECT status FROM skills WHERE name='widget-maker';")["status"] == "active"
