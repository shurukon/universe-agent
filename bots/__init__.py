"""bots/ — Universe Bots: persistent task agents (Grok-Bot-style, Muse runtime).

One bot, one job: the name is for the user, the description holds the job,
sources and boundaries. Lifecycle: TASK → SKILL → ROUTINE — run the job once,
save the method as a skill, then let the scheduler run it on a schedule.
Bots run on Meta's Muse Code CLI when available (the real bots runtime),
with Universe's own LLM engine as the honest fallback, and route risky
steps (send / publish / purchase …) through the approvals queue.
"""
