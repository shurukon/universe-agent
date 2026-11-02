"""tests/test_skill_upsert.py — re-ingestion must not wipe user state.

Re-ingestion used to mint a fresh id and INSERT OR REPLACE on the
UNIQUE(name, source) index, deleting the prior row — silently discarding the
Settings enabled toggle, use_count, last_used, and any pinned version
(MEDIUM finding, 2026-10 audit). These tests pin the fixed behavior:

- two load_all_skills() runs keep every row (1580 bundled rows on the real
  corpus) and keep row identity stable;
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
    src.mkdir(parents=True)
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


@pytest.fixture
def tiny_corpus_home(fresh_home, monkeypatch, tmp_path):
    """Fresh home + a 2-skill bundled corpus in a temp skills_data root.

    fresh_home's own bootstrap already materialized the REAL bundled corpus
    (the env override can only be set after the fixture chain starts), so the
    fixture wipes the materialized index and lets each test rebuild from the
    tiny corpus — deterministic table-wide assertions, fast loads."""
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
    """Two load_all_skills() runs keep every row and every row id."""
    from skills.loader import load_all_skills
    from storage.local import get_storage

    load_all_skills()
    st = get_storage()
    before = {r["id"]: r["name"] for r in st.query_all("SELECT id, name FROM skills;")}
    assert len(before) == 2
    load_all_skills()
    after = {r["id"]: r["name"] for r in st.query_all("SELECT id, name FROM skills;")}
    assert after == before


def test_reingest_preserves_enabled_and_use_count(tiny_corpus_home):
    """A manually-set enabled=0 / use_count=7 survives a re-ingest, along
    with last_used and row identity; the row stays trusted and verifiable."""
    from skills.loader import load_all_skills, verify_all_signatures
    from storage.local import get_storage

    load_all_skills()
    st = get_storage()
    row = st.query_one("SELECT * FROM skills WHERE name='alpha' AND source='ECC';")
    with st.transaction() as conn:
        conn.execute("UPDATE skills SET enabled=0, use_count=7, last_used=123.0 "
                     "WHERE id=?;", (row["id"],))

    load_all_skills()

    row = st.query_one("SELECT * FROM skills WHERE id=?;", (row["id"],))
    assert row is not None                       # identity kept (same id)
    assert row["enabled"] == 0                   # Settings toggle survives
    assert row["use_count"] == 7                 # usage survives
    assert row["last_used"] == 123.0
    assert row["status"] == "trusted"
    assert st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"] == 2
    assert verify_all_signatures()["mismatch"] == 0


def test_reingest_preserves_user_state_on_real_corpus(fresh_home_with_skills):
    """Scale version of the preservation test on the real 1,580-row corpus:
    two load_all_skills() runs keep 1580 rows and preserve enabled=0 /
    use_count=7 on a sampled row."""
    from skills.loader import load_all_skills, verify_all_signatures
    from storage.local import get_storage

    st = get_storage()
    assert st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"] == 1580
    row = st.query_one(
        "SELECT id FROM skills WHERE status='trusted' LIMIT 1;")
    with st.transaction() as conn:
        conn.execute("UPDATE skills SET enabled=0, use_count=7 WHERE id=?;", (row["id"],))

    load_all_skills()

    assert st.query_one("SELECT COUNT(*) AS c FROM skills;")["c"] == 1580
    row = st.query_one("SELECT enabled, use_count, status FROM skills WHERE id=?;",
                       (row["id"],))
    assert row["enabled"] == 0
    assert row["use_count"] == 7
    assert row["status"] == "trusted"
    assert verify_all_signatures()["mismatch"] == 0


def test_pinned_version_survives_reingest_and_stays_verifiable(tiny_corpus_home):
    """Version is user-owned (pin flows write it in place). A re-ingest keeps
    the pinned version and re-signs over it, so the row both keeps its pin
    and passes signature verification."""
    from skills.loader import load_all_skills, verify_all_signatures
    from storage.local import get_storage

    load_all_skills()
    st = get_storage()
    with st.transaction() as conn:
        conn.execute("UPDATE skills SET version='9.9.9-pin' "
                     "WHERE name='alpha' AND source='ECC';")

    load_all_skills()

    row = st.query_one("SELECT * FROM skills WHERE name='alpha' AND source='ECC';")
    assert row["version"] == "9.9.9-pin"
    assert row["status"] == "trusted"
    assert verify_all_signatures()["mismatch"] == 0


def test_approved_community_skill_survives_reingest(fresh_home, tmp_path):
    """An approved community skill (status 'active' at 0.70) must not be
    silently demoted back to quarantine by a re-ingest of the same source."""
    from skill.ingestion import ingest_source
    from skill.quarantine import list_quarantine, approve, pending_count
    from skills.loader import load_all_skills
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
    # sanity: bundled corpus untouched by the community re-ingest
    load_all_skills()
    assert st.query_one(
        "SELECT status FROM skills WHERE name='widget-maker';")["status"] == "active"
