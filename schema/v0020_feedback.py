"""v20: goal feedback — the Like/Dislike row (ZCode chat message parity).

One row per rating a user files on a finished goal's reply. The desktop
message buttons (Like / Dislike) write here; nothing else reads it yet —
it is the dataset a later quality/eval pass can mine, exactly like the
LLM labelling flows ZCode exposes on its chat rows.

Ratings are restricted at the API layer to 'like' | 'dislike'; the column
is TEXT so future kinds (e.g. 'report') stay additive.
"""
from schema.migrations import register


@register(20, "goal feedback (like/dislike) table")
def upgrade(st) -> None:
    st.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_feedback (
            goal_id TEXT NOT NULL,
            rating  TEXT NOT NULL,          -- 'like' | 'dislike'
            ts      REAL NOT NULL
        );
        """
    )
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_goal_feedback_goal "
        "ON goal_feedback(goal_id);"
    )
