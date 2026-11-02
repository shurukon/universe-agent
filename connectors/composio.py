"""connectors/composio.py — Composio SDK wrapper.

§6, §25 Phase 5 ⑲. Universe uses Composio's Python SDK directly (no dependency
on OpenHuman's backend). The user brings their own Composio API key. 1000+
integrations (Gmail, Calendar, Drive, GitHub, Slack, Notion, Linear, etc.).
"""
from __future__ import annotations

import json

from storage.local import get_storage


AVAILABLE_TOOLS = [
    "gmail", "google_calendar", "google_drive", "google_sheets",
    "github", "gitlab", "notion", "linear", "jira", "asana",
    "slack", "discord", "telegram", "stripe", "hubspot", "salesforce", "figma",
]


class ComposioConnector:
    """Onboarding surface for Composio tools: list what's available and
    store per-tool credentials AES-encrypted. HONEST LIMITATION: action
    EXECUTION was never wired (no SDK client is instantiated at runtime,
    and no agent tool was registered for it) — the execute()/set_api_key()
    scaffolding was removed rather than shipped as unreachable code."""

    def __init__(self) -> None:
        self._connected: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        st = get_storage()
        rows = st.query_all("SELECT * FROM connectors WHERE kind='composio';")
        for r in rows:
            try:
                self._connected[r["name"]] = json.loads(r["config"] or "{}")
            except Exception:
                self._connected[r["name"]] = {}

    def list_available(self) -> list[str]:
        return list(AVAILABLE_TOOLS)

    def connect(self, name: str, config: dict | None = None) -> dict:
        """Register a Composio tool as connected. The actual OAuth/API token
        is stored AES-encrypted via security/secrets_fallback."""
        from security.secrets_fallback import encrypt_secret
        from core.agent.base_agent import new_id
        cfg = config or {}
        token = cfg.pop("token", "")
        if token:
            cfg["encrypted_token"] = encrypt_secret(token)
        st = get_storage()
        cid = new_id("composio-")
        with st.transaction() as conn:
            conn.execute(
                "INSERT INTO connectors(id,kind,name,config,connected_at) "
                "VALUES(?,?,?,?,?);",
                (cid, "composio", name, json.dumps(cfg), __import__("time").time()),
            )
        # connectors table may not exist in older schemas; ignore if so
        self._connected[name] = cfg
        return {"ok": True, "name": name, "id": cid}

    def list_connected(self) -> list[str]:
        return list(self._connected.keys())


_composio: ComposioConnector | None = None


def get_composio() -> ComposioConnector:
    global _composio
    if _composio is None:
        _composio = ComposioConnector()
    return _composio
