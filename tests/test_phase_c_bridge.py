"""tests/test_phase_c_bridge.py — Phase C bridge surfaces.

Automations CRUD over the real scheduler, the skills inventory + v20
enabled toggle (with the L4 search exclusion), the terminal WebSocket
(one-shot runs + the cd protocol), and the custom-commands settings key.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import install_test_provider  # noqa: F401  (pattern parity)


@pytest.fixture
def client(fresh_home):
    from config import get_config
    get_config().set("project_root", str(fresh_home))
    from universe_desktop_bridge import app as app_mod
    from observability.events import reset_event_bus_for_tests
    from universe_desktop_bridge.app import reset_runtime_for_tests
    reset_event_bus_for_tests()
    reset_runtime_for_tests()
    with TestClient(app_mod.app) as c:
        yield c


# ---- automations -------------------------------------------------------------

def test_automations_crud_roundtrip(client):
    # empty list first
    assert client.get("/api/automations").json() == {"automations": []}

    r = client.post("/api/automations", json={
        "name": "Daily standup digest",
        "schedule": "0 9 * * 1-5",
        "prompt": "Summarize yesterday's goals and open questions.",
    })
    assert r.status_code == 200, r.text
    auto = r.json()["automation"]
    assert auto["id"].startswith("auto-")
    assert auto["enabled"] is True
    assert auto["next_run_at"] > 0

    # appears in the list
    rows = client.get("/api/automations").json()["automations"]
    assert [a["id"] for a in rows] == [auto["id"]]

    # patch: rename + disarm
    r = client.patch(f"/api/automations/{auto['id']}",
                     json={"name": "Digest", "enabled": False})
    assert r.status_code == 200
    row = r.json()["automation"]
    assert row["name"] == "Digest" and row["enabled"] is False

    # a disabled row is re-armed and gets a fresh next_run_at
    r = client.patch(f"/api/automations/{auto['id']}", json={"enabled": True})
    assert r.json()["automation"]["enabled"] is True

    # delete + 404 afterwards
    assert client.delete(f"/api/automations/{auto['id']}").json()["ok"] is True
    assert client.get("/api/automations").json() == {"automations": []}
    assert client.delete(f"/api/automations/{auto['id']}").status_code == 404
    assert client.patch(f"/api/automations/{auto['id']}",
                        json={"enabled": True}).status_code == 404


def test_automations_reject_invalid_schedule(client):
    r = client.post("/api/automations", json={
        "name": "bad", "schedule": "61 * * * *", "prompt": "nope"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_schedule"
    r = client.post("/api/automations", json={
        "name": "bad", "schedule": "0 9 * * 1", "prompt": ""})
    assert r.status_code == 422  # pydantic: empty prompt


# ---- skills inventory + the v20 toggle ---------------------------------------

def test_skills_inventory_lists_the_real_corpus(client, fresh_home_with_skills):
    """The inventory serves whatever the index really holds — post-purge
    that is the SEEDED corpus (the bundled library was removed 2026-10-31;
    the conftest fixture ingests 6 skills through the product pipeline)."""
    data = client.get("/api/skills").json()
    assert len(data["skills"]) >= 6
    row = data["skills"][0]
    for key in ("id", "name", "source", "description", "category",
                "status", "use_count", "enabled"):
        assert key in row
    assert {"categories"} <= set(data)
    # search filter matches
    hit = client.get("/api/skills", params={"q": row["name"][:6]}).json()
    assert any(row["id"] == s["id"] for s in hit["skills"])


def test_skill_toggle_excludes_skill_from_l4_search(client, fresh_home_with_skills):
    from core.memory import get_memory
    mem = get_memory()
    skill = mem.l4.list(status="trusted")[0]
    assert skill.enabled is True  # v20 default
    # visible before
    hits = mem.l4.search(skill.name, top_k=20)
    assert any(s.id == skill.id for s in hits)

    r = client.patch(f"/api/skills/{skill.id}", json={"enabled": False})
    assert r.status_code == 200
    # gone from every search…
    hits = mem.l4.search(skill.name, top_k=20)
    assert not any(s.id == skill.id for s in hits)
    # …but still stored (durable corpus)
    assert mem.l4.get(skill.id) is not None
    assert mem.l4.get(skill.id).enabled is False

    # re-arm restores it
    assert client.patch(f"/api/skills/{skill.id}",
                        json={"enabled": True}).status_code == 200
    assert any(s.id == skill.id for s in mem.l4.search(skill.name, top_k=20))

    # unknown id -> honest 404
    assert client.patch("/api/skills/nope", json={"enabled": False}).status_code == 404


# ---- terminal WebSocket --------------------------------------------------------

def test_terminal_ws_runs_a_command(client):
    with client.websocket_connect("/api/terminal?token=dev") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "ready" and hello["cwd"]
        ws.send_json({"type": "run", "cmd": "echo universe-term-ok"})
        assert ws.receive_json()["type"] == "start"  # echo of the accepted line
        out = ws.receive_json()
        assert out["type"] == "result"
        assert "universe-term-ok" in out["stdout"]
        assert out["returncode"] == 0


def test_terminal_ws_cd_protocol_tracks_cwd(client, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    with client.websocket_connect("/api/terminal?token=dev") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "cd", "path": str(tmp_path)})
        msg = ws.receive_json()
        assert msg["type"] == "cwd"
        # relative cd joins the tracked cwd
        ws.send_json({"type": "cd", "path": "sub"})
        msg = ws.receive_json()
        assert msg["type"] == "cwd" and msg["cwd"].endswith("sub")
        # a run lands in the tracked cwd
        ws.send_json({"type": "run", "cmd": "cd"})
        assert ws.receive_json()["type"] == "start"
        out = ws.receive_json()
        assert out["type"] == "result" and out["cwd"].endswith("sub")
        # a bad directory is refused honestly
        ws.send_json({"type": "cd", "path": "no-such-dir-xyz"})
        err = ws.receive_json()
        assert err["type"] == "error" and "no such directory" in err["message"]


def test_terminal_ws_rejects_unknown_message_type(client):
    with client.websocket_connect("/api/terminal?token=dev") as ws:
        ws.receive_json()
        ws.send_json({"type": "bogus"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and "bogus" in msg["message"]


# ---- custom commands (settings key) --------------------------------------------

def test_custom_commands_settings_roundtrip(client):
    r = client.get("/api/settings").json()
    assert r["settings"].get("custom_commands") == []
    cmds = [{"name": "review", "description": "Review the diff",
             "prompt": "Review the working tree diff and list risks."}]
    r = client.put("/api/settings", json={"custom_commands": cmds}).json()
    assert r["settings"]["custom_commands"] == cmds
    assert client.get("/api/settings").json()["settings"]["custom_commands"] == cmds
