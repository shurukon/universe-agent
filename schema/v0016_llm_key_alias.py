"""v16: llm_calls gains the key alias + failure kind.

m4 accounting fix — providers/client.py records key_alias in the JSONL log
but the DB INSERT dropped it (no column), so /cost-style aggregate views
could not attribute spend to a key. The same migration adds error_kind so
terminal failures are accounted in the DB too (finish_reason='error'),
instead of existing only as an exception nobody can audit.
Forward-only; existing rows get ''.
"""
from schema.migrations import register


@register(16, "llm_calls.key_alias + error_kind for honest accounting")
def upgrade(st) -> None:
    cols = [r["name"] for r in st.query_all("PRAGMA table_info(llm_calls);")]
    if "key_alias" not in cols:
        st.execute(
            "ALTER TABLE llm_calls ADD COLUMN key_alias TEXT NOT NULL DEFAULT '';"
        )
    if "error_kind" not in cols:
        st.execute(
            "ALTER TABLE llm_calls ADD COLUMN error_kind TEXT NOT NULL DEFAULT '';"
        )
