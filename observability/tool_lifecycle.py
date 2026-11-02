"""Durable tool/capability lifecycle evidence.

The lifecycle is intentionally separate from prose memory. A capability is only
considered acquired after a registered or external tool has a successful real
invocation and a verification event.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from storage.local import get_storage


VALID_PHASES = {"proposed", "discovered", "installed", "registered", "invoked", "verified", "failed"}


def record_tool_lifecycle(*, turn_id: str = "", agent: str = "", name: str,
                          phase: str, status: str, source: str = "runtime",
                          details: dict[str, Any] | None = None) -> None:
    """Persist one auditable lifecycle transition, best-effort during bootstrap."""
    if phase not in VALID_PHASES:
        raise ValueError(f"unknown lifecycle phase: {phase}")
    # defense in depth: callers redact at the boundary, but any details dict
    # reaching this durable table is redacted again — raw tool args (e.g.
    # http_client headers carrying Authorization tokens) must never sit in
    # cleartext in universe.db (audit 2026-09)
    from security.secret_filter import redact_dict
    payload = json.dumps(redact_dict(details or {}), ensure_ascii=False, default=str)
    try:
        get_storage().execute(
            "INSERT INTO tool_lifecycle(id,turn_id,agent,name,phase,status,source,details,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?);",
            (str(uuid.uuid4()), turn_id, agent, name, phase, status, source, payload, time.time()),
        )
    except Exception:
        # Lifecycle must never make a real tool call fail during an older-home
        # migration window. The migration test catches missing persistence.
        return


def register_catalog_entry(*, name: str, kind: str, description: str,
                           source: str, status: str = "registered",
                           details: dict[str, Any] | None = None) -> None:
    """Upsert the durable catalog entry for a registered/discovered capability."""
    try:
        get_storage().execute(
            "INSERT INTO tool_catalog(name,kind,description,source,status,details,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "kind=excluded.kind, description=excluded.description, source=excluded.source, "
            "status=excluded.status, details=excluded.details, updated_at=excluded.updated_at;",
            (name, kind, description, source, status,
             json.dumps(details or {}, ensure_ascii=False, default=str), time.time()),
        )
    except Exception:
        return


def catalog_snapshot() -> list[dict[str, Any]]:
    try:
        return get_storage().query_all("SELECT * FROM tool_catalog ORDER BY name;")
    except Exception:
        return []


def lifecycle_snapshot(turn_id: str = "") -> list[dict[str, Any]]:
    try:
        if turn_id:
            return get_storage().query_all(
                "SELECT * FROM tool_lifecycle WHERE turn_id=? ORDER BY created_at;", (turn_id,))
        return get_storage().query_all("SELECT * FROM tool_lifecycle ORDER BY created_at;")
    except Exception:
        return []
