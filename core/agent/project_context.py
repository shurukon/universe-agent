"""Per-turn project root ContextVar — threads project selection from bridge to tools."""
from __future__ import annotations

from contextvars import ContextVar

project_root_ctx: ContextVar[str | None] = ContextVar("project_root", default=None)
