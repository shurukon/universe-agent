"""cli/handlers.py — inline (non-wizard) command handlers."""
from __future__ import annotations


def register_inline(command) -> None:
    @command("audit", "Observability", "Inspect the audit log", wizard=False)
    async def audit_cmd(args, io):
        from security.audit import tail_audit
        return tail_audit(io, limit=int(args[0]) if args else 20)
