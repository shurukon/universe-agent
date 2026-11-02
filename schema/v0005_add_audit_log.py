"""schema/v0005_add_audit_log.py — the audit log itself is append-only JSONL
on disk (security/audit.py); this migration adds the LLM call log table used
by /cost for aggregate views when the JSONL isn't convenient."""
from schema.migrations import register


@register(5, "add llm call log table")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            agent TEXT,
            provider TEXT,
            model TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            prompt_hash TEXT,
            finish_reason TEXT
        );
    """)
