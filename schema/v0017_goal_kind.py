"""v17: goals.kind — task containers vs. message children.

The pre-v17 model was "every chat message = a new top-level goal row", which
made the sidebar fill with one task per message titled after the message
text. The desktop model moves to real tasks:

  kind='task'     a container row the sidebar lists (created by the New Task
                  button or auto-created on first message; title defaults to
                  "New task", NEVER derived from message text)
  kind='message'  one run inside a task (parent_goal_id -> the task row);
                  it keeps its own event ring, planet, handoffs, checkpoints
                  and final_summary, so the whole per-goal pipeline works
                  unchanged per message.

Existing rows default to 'task' (legacy single-message chats, including
v14 '⑂' branches, stay sidebar-visible). Forward-only.
"""
from schema.migrations import register


@register(17, "goals.kind: task containers + message children")
def upgrade(st) -> None:
    cols = [r["name"] for r in st.query_all("PRAGMA table_info(goals);")]
    if "kind" not in cols:
        st.execute(
            "ALTER TABLE goals ADD COLUMN kind TEXT NOT NULL DEFAULT 'task';"
        )
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(parent_goal_id);"
    )
