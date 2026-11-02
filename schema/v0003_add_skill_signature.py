"""schema/v0003_add_skill_signature.py — index skills by signature for weekly
re-verification by the Subconscious Loop."""
from schema.migrations import register


@register(3, "add skill signature index")
def upgrade(st) -> None:
    st.execute("CREATE INDEX IF NOT EXISTS idx_skills_signature ON skills(signature);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_skills_agent ON skills(target_agent);")
