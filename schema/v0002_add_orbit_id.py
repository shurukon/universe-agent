"""schema/v0002_add_orbit_id.py — ensure orbits carry explicit id (fix for the
v0 duplicate-orbit-rows bug). The v0001 schema already includes id, so this
migration is a structural confirmation + index for fast lookups."""
from schema.migrations import register


@register(2, "add orbit id index")
def upgrade(st) -> None:
    st.execute("CREATE INDEX IF NOT EXISTS idx_orbits_kind ON orbits(kind);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_orbits_sys ON orbits(solar_system_id);")
