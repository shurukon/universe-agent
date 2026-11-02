"""tests/test_thirdparty.py — tests for connectors/builtin/thirdparty.py
(batch 1 of the native, non-Composio third-party connectors).

Three kinds of coverage, matching how third-party API integrations are
tested industry-wide:
  1. LIVE tests (pypi/npm/crates skip with a reason when the transport
     can't reach the host — offline CI; github skips gracefully on
     rate-limit) — the only 4 tools whose domains need no credential and
     are safe to hit from CI.
  2. Mocked-HTTP tests for every credentialed tool — verify the exact
     request Universe constructs (URL, method, auth header/param/body shape)
     against real API conventions, without depending on live secrets.
  3. Structural/regression tests — registry + whitelist + consent-level
     wiring, so a future edit can't silently break registration again.
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# 1. LIVE tests — only for the 4 tools whose domains need zero credentials
# ---------------------------------------------------------------------------

def _skip_if_transport_unreachable(r: dict, host: str) -> None:
    """Same offline-skip convention as the crates/github live tests below:
    a transport failure (DNS/connect/TLS — the inner envelope carries an
    `error` and NO `status`) is an environment condition and skips with
    the reason; a real HTTP status (even an error one) fails the test
    honestly, because that means the request reached the host."""
    inner = r.get("result") if isinstance(r.get("result"), dict) else {}
    if "status" not in inner:
        pytest.skip(f"{host} unreachable from this network (offline/transient): "
                    f"{str(inner.get('error') or r.get('error'))[:160]}")


@pytest.mark.asyncio
async def test_pypi_package_info_live(fresh_home):
    from connectors.builtin import get_registry
    r = await get_registry().call("pypi_package_info", agent="universe", turn_id="g1",
                                  args={"package": "requests"})
    _skip_if_transport_unreachable(r, "pypi.org")
    assert r["ok"] is True
    assert r["result"]["ok"] is True
    assert r["result"]["status"] == 200
    assert '"requests"' in r["result"]["data"] or "requests" in r["result"]["data"]


@pytest.mark.asyncio
async def test_npm_package_info_live(fresh_home):
    from connectors.builtin import get_registry
    r = await get_registry().call("npm_package_info", agent="universe", turn_id="g1",
                                  args={"package": "express"})
    _skip_if_transport_unreachable(r, "registry.npmjs.org")
    assert r["ok"] is True
    assert r["result"]["ok"] is True
    assert r["result"]["status"] == 200


@pytest.mark.asyncio
async def test_crates_package_info_live(fresh_home):
    """Also verifies the required User-Agent header: crates.io returns 403
    without one (confirmed while building this batch). A transient external
    failure (DNS/TLS/upstream 5xx — observed on shared IPs) skips with the
    reason; a 403 (missing UA) still fails the test honestly."""
    from connectors.builtin import get_registry
    r = await get_registry().call("crates_package_info", agent="universe", turn_id="g1",
                                  args={"crate": "serde"})
    _skip_if_transport_unreachable(r, "crates.io")  # DNS/connect/TLS = offline
    inner = r.get("result") if isinstance(r.get("result"), dict) else {}
    if not r.get("ok") and "403" not in str(r.get("error", "")):
        pytest.skip(f"crates.io transiently unreachable (external): "
                    f"{str(r.get('error'))[:160]}")
    assert r["ok"] is True, f"registry call failed: {str(r)[:250]}"
    assert inner.get("ok") is True, f"crates.io call failed: {inner}"
    assert inner["status"] == 200


@pytest.mark.asyncio
async def test_github_repo_info_live(fresh_home):
    """Live test — skipped gracefully if this shared IP has exhausted
    GitHub's 60/hour unauthenticated rate limit (a real, external, transient
    condition unrelated to the code; same skip-with-reason convention as
    the existing Docker/DDG tests in this suite)."""
    from connectors.builtin import get_registry
    r = await get_registry().call("github_repo_info", agent="universe", turn_id="g1",
                                  args={"owner": "python", "repo": "cpython"})
    _skip_if_transport_unreachable(r, "api.github.com")  # DNS failure = offline
    inner = r["result"]
    if not inner["ok"] and inner.get("status") == 403:
        pytest.skip("GitHub unauthenticated rate limit exhausted on this IP; "
                   "not a code issue (verify with UNIVERSE_GITHUB_TOKEN set)")
    assert inner["ok"] is True
    assert inner["status"] == 200


# ---------------------------------------------------------------------------
# 2. Mocked-HTTP tests — request construction, per tool
# ---------------------------------------------------------------------------

def _mock_response(status=200, json_data=None, text=""):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data if json_data is not None else {}
    m.text = text or json.dumps(json_data or {})
    return m


# --- missing-credential path: every credentialed tool must fail closed,
#     with no network call attempted, when its credential is absent. -------

CREDENTIALED_TOOLS = [
    ("github_create_issue", {"owner": "a", "repo": "b", "title": "t"}),
    ("slack_post_message", {"channel": "#x", "text": "hi"}),
    ("slack_list_channels", {}),
    ("discord_webhook_post", {"content": "hi"}),
    ("telegram_send_message", {"chat_id": "1", "text": "hi"}),
    ("notion_query_database", {"database_id": "d"}),
    ("notion_create_page", {"parent_database_id": "d", "title": "t"}),
    ("linear_list_issues", {}),
    ("linear_create_issue", {"team_key": "t", "title": "x"}),
    ("trello_list_boards", {}),
    ("trello_create_card", {"list_id": "l", "name": "n"}),
    ("asana_list_tasks", {"project_gid": "p"}),
    ("asana_create_task", {"project_gid": "p", "name": "n"}),
    ("jira_search_issues", {"jql": "project=X"}),
    ("jira_create_issue", {"project_key": "X", "summary": "s"}),
    ("dropbox_list_files", {}),
    ("google_drive_list_files", {}),
    ("stripe_list_charges", {}),
    ("stripe_create_charge", {"amount": 100, "currency": "usd", "source": "tok_x"}),
    ("hubspot_create_contact", {"email": "a@b.com"}),
    ("airtable_list_records", {"base_id": "b", "table_name": "t"}),
    ("openweathermap_current", {"city": "Algiers"}),
]
# NOTE: github_repo_info and gitlab_project_info are deliberately excluded
# here — both work WITHOUT a credential for public data (token is optional,
# only raises rate limits / unlocks private data). See
# test_gitlab_works_unauthenticated_for_public_projects below.


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,args", CREDENTIALED_TOOLS)
async def test_missing_credential_fails_closed_no_network_call(fresh_home, monkeypatch, tool_name, args):
    """Every credentialed tool must return needs_setup=True (not raise, not
    silently proceed unauthenticated) when its credential is absent, and
    must NOT attempt any network call in that case."""
    # scrub every UNIVERSE_*_TOKEN/KEY/DOMAIN/EMAIL/etc env var so no leftover
    # value from the host environment accidentally satisfies a credential
    for var in list(__import__("os").environ):
        if var.startswith("UNIVERSE_") and var not in ("UNIVERSE_HOME", "UNIVERSE_PASSPHRASE"):
            monkeypatch.delenv(var, raising=False)
    from connectors.builtin import get_registry
    with patch("httpx.request") as mock_req:
        r = await get_registry().call(tool_name, agent="universe", turn_id="g1", args=args)
        # m1 truth-envelope: the handler's own ok IS the envelope's ok now.
        # A credential-less call is a DISPATCH success (the gate allowed it)
        # whose inner verdict is the honest needs_setup failure.
        inner = r.get("result", r)
        assert inner.get("needs_setup") is True, f"{tool_name}: {r}"
        assert inner.get("ok") is False, f"{tool_name} should fail without credentials: {inner}"
        mock_req.assert_not_called()


# --- request construction: simple single-Bearer-token tools ---------------

SIMPLE_BEARER_TOOLS = [
    ("github_create_issue", "UNIVERSE_GITHUB_TOKEN", {"owner": "o", "repo": "r", "title": "t"},
     "https://api.github.com/repos/o/r/issues"),
    ("slack_post_message", "UNIVERSE_SLACK_TOKEN", {"channel": "#c", "text": "hi"},
     "https://slack.com/api/chat.postMessage"),
    ("slack_list_channels", "UNIVERSE_SLACK_TOKEN", {},
     "https://slack.com/api/conversations.list"),
    ("notion_query_database", "UNIVERSE_NOTION_TOKEN", {"database_id": "d"},
     "https://api.notion.com/v1/databases/d/query"),
    ("asana_list_tasks", "UNIVERSE_ASANA_TOKEN", {"project_gid": "p"},
     "https://app.asana.com/api/1.0/projects/p/tasks"),
    ("dropbox_list_files", "UNIVERSE_DROPBOX_TOKEN", {},
     "https://api.dropboxapi.com/2/files/list_folder"),
    ("hubspot_create_contact", "UNIVERSE_HUBSPOT_TOKEN", {"email": "a@b.com"},
     "https://api.hubapi.com/crm/v3/objects/contacts"),
    ("google_drive_list_files", "UNIVERSE_GOOGLE_ACCESS_TOKEN", {},
     "https://www.googleapis.com/drive/v3/files"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,env_var,args,expected_url", SIMPLE_BEARER_TOOLS)
async def test_bearer_token_sent_correctly(fresh_home, monkeypatch, tool_name, env_var, args, expected_url):
    monkeypatch.setenv(env_var, "test-token-123")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"ok": True})) as mock_req:
        r = await get_registry().call(tool_name, agent="universe", turn_id="g1", args=args)
        assert r["result"]["ok"] is True, r["result"]
        mock_req.assert_called_once()
        call_args, call_kwargs = mock_req.call_args
        assert call_args[1] == expected_url, f"wrong URL: {call_args[1]}"
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-token-123", headers


# --- the idiosyncratic ones, individually, matching the verified docs -----

@pytest.mark.asyncio
async def test_telegram_token_embedded_in_url_path(fresh_home, monkeypatch):
    """Telegram has NO auth header — the token lives in the URL path itself."""
    monkeypatch.setenv("UNIVERSE_TELEGRAM_TOKEN", "123:ABC")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"ok": True})) as mock_req:
        r = await get_registry().call("telegram_send_message", agent="universe", turn_id="g1",
                                      args={"chat_id": "42", "text": "hi"})
        assert r["result"]["ok"] is True
        call_args, call_kwargs = mock_req.call_args
        assert call_args[1] == "https://api.telegram.org/bot123:ABC/sendMessage"
        assert call_kwargs.get("json") == {"chat_id": "42", "text": "hi"}


@pytest.mark.asyncio
async def test_trello_key_and_token_as_query_params(fresh_home, monkeypatch):
    """Trello passes key+token as query params, NOT headers."""
    monkeypatch.setenv("UNIVERSE_TRELLO_KEY", "mykey")
    monkeypatch.setenv("UNIVERSE_TRELLO_TOKEN", "mytoken")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, [])) as mock_req:
        r = await get_registry().call("trello_list_boards", agent="universe", turn_id="g1", args={})
        assert r["result"]["ok"] is True
        call_args, call_kwargs = mock_req.call_args
        assert call_args[1] == "https://api.trello.com/1/members/me/boards"
        params = call_kwargs.get("params", {})
        assert params == {"key": "mykey", "token": "mytoken"}


@pytest.mark.asyncio
async def test_stripe_basic_auth_blank_password_form_encoded(fresh_home, monkeypatch):
    """Stripe uses HTTP Basic Auth with the secret key as username and a
    BLANK password, and form-encoded (not JSON) request bodies."""
    monkeypatch.setenv("UNIVERSE_STRIPE_KEY", "sk_test_abc")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"id": "ch_1"})) as mock_req:
        r = await get_registry().call("stripe_create_charge", agent="universe", turn_id="g1",
                                      args={"amount": 500, "currency": "usd", "source": "tok_visa"})
        assert r["result"]["ok"] is True
        call_args, call_kwargs = mock_req.call_args
        assert call_kwargs.get("auth") == ("sk_test_abc", "")
        assert call_kwargs.get("json") is None, "Stripe must NOT receive a JSON body"
        assert call_kwargs.get("data") == {"amount": 500, "currency": "usd",
                                           "source": "tok_visa", "description": ""}


@pytest.mark.asyncio
async def test_notion_version_header_present(fresh_home, monkeypatch):
    """Notion requires a Notion-Version header on every request."""
    monkeypatch.setenv("UNIVERSE_NOTION_TOKEN", "secret_abc")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"results": []})) as mock_req:
        r = await get_registry().call("notion_query_database", agent="universe", turn_id="g1",
                                      args={"database_id": "d1"})
        assert r["result"]["ok"] is True
        _, call_kwargs = mock_req.call_args
        headers = call_kwargs.get("headers", {})
        assert "Notion-Version" in headers
        assert headers["Authorization"] == "Bearer secret_abc"


@pytest.mark.asyncio
async def test_jira_basic_auth_email_and_token(fresh_home, monkeypatch):
    """Jira Cloud uses HTTP Basic Auth with (email, api_token), against a
    per-workspace *.atlassian.net domain."""
    monkeypatch.setenv("UNIVERSE_JIRA_DOMAIN", "mycompany")
    monkeypatch.setenv("UNIVERSE_JIRA_EMAIL", "me@example.com")
    monkeypatch.setenv("UNIVERSE_JIRA_TOKEN", "atok123")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"issues": []})) as mock_req:
        r = await get_registry().call("jira_search_issues", agent="universe", turn_id="g1",
                                      args={"jql": "project=X"})
        assert r["result"]["ok"] is True
        call_args, call_kwargs = mock_req.call_args
        assert call_args[1] == "https://mycompany.atlassian.net/rest/api/3/search"
        assert call_kwargs.get("auth") == ("me@example.com", "atok123")


@pytest.mark.asyncio
async def test_linear_api_key_has_no_bearer_prefix(fresh_home, monkeypatch):
    """Linear personal API keys go BARE in the Authorization header — no
    'Bearer' prefix (unlike Linear's own OAuth2 access tokens, which do use
    Bearer). Easy to get wrong; verified against live docs while building
    this batch."""
    monkeypatch.setenv("UNIVERSE_LINEAR_KEY", "lin_api_xyz")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"data": {}})) as mock_req:
        r = await get_registry().call("linear_list_issues", agent="universe", turn_id="g1", args={})
        assert r["result"]["ok"] is True
        _, call_kwargs = mock_req.call_args
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "lin_api_xyz", (
            f"Linear key must be bare, got: {headers.get('Authorization')!r}")


@pytest.mark.asyncio
async def test_discord_webhook_url_is_the_credential(fresh_home, monkeypatch):
    """Discord's incoming-webhook URL itself contains the secret — there's
    no separate auth header."""
    monkeypatch.setenv("UNIVERSE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/abc")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(204, {})) as mock_req:
        r = await get_registry().call("discord_webhook_post", agent="universe", turn_id="g1",
                                      args={"content": "hello"})
        assert r["result"]["ok"] is True
        call_args, call_kwargs = mock_req.call_args
        assert call_args[1] == "https://discord.com/api/webhooks/1/abc"
        assert call_kwargs.get("json") == {"content": "hello"}


@pytest.mark.asyncio
async def test_discord_webhook_explicit_arg_overrides_env(fresh_home, monkeypatch):
    """An explicitly-passed webhook_url must take priority over the env var."""
    monkeypatch.setenv("UNIVERSE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/env")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(204, {})) as mock_req:
        await get_registry().call("discord_webhook_post", agent="universe", turn_id="g1",
                                  args={"content": "hi", "webhook_url": "https://discord.com/api/webhooks/2/explicit"})
        call_args, _ = mock_req.call_args
        assert call_args[1] == "https://discord.com/api/webhooks/2/explicit"


@pytest.mark.asyncio
async def test_gitlab_private_token_header(fresh_home, monkeypatch):
    """GitLab uses a PRIVATE-TOKEN header, not Authorization: Bearer."""
    monkeypatch.setenv("UNIVERSE_GITLAB_TOKEN", "glpat-abc")
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"id": 1})) as mock_req:
        r = await get_registry().call("gitlab_project_info", agent="universe", turn_id="g1",
                                      args={"project": "group/repo"})
        assert r["result"]["ok"] is True
        _, call_kwargs = mock_req.call_args
        assert call_kwargs.get("headers", {}).get("PRIVATE-TOKEN") == "glpat-abc"


@pytest.mark.asyncio
async def test_gitlab_works_unauthenticated_for_public_projects(fresh_home, monkeypatch):
    """Unlike most others, GitLab (and GitHub) work with NO credential at
    all for public data — only per_turn-gated, not blocked outright."""
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"id": 1})) as mock_req:
        r = await get_registry().call("gitlab_project_info", agent="universe", turn_id="g1",
                                      args={"project": "group/repo"})
        assert r["result"]["ok"] is True
        _, call_kwargs = mock_req.call_args
        assert "PRIVATE-TOKEN" not in call_kwargs.get("headers", {})


@pytest.mark.asyncio
async def test_arxiv_search_parses_atom_xml_into_json(fresh_home):
    """arXiv returns Atom XML, not JSON — verify Universe parses it into a
    plain list rather than handing the agent raw XML soup."""
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1234.5678</id>
        <title>A Great Paper</title>
        <summary>This paper is about testing.</summary>
      </entry>
    </feed>"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    resp.text = atom
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=resp):
        r = await get_registry().call("arxiv_search", agent="universe", turn_id="g1",
                                      args={"query": "testing"})
        assert r["result"]["ok"] is True
        data = r["result"]["data"]
        assert "A Great Paper" in data
        assert "1234.5678" in data


@pytest.mark.asyncio
async def test_wikipedia_and_coingecko_need_no_credential(fresh_home, monkeypatch):
    """Zero-setup tier: must work with NO env vars set at all."""
    for var in list(__import__("os").environ):
        if var.startswith("UNIVERSE_"):
            monkeypatch.delenv(var, raising=False)
    from connectors.builtin import get_registry
    reg = get_registry()
    with patch("httpx.request", return_value=_mock_response(200, {"query": {"search": []}})):
        r1 = await reg.call("wikipedia_search", agent="universe", turn_id="g1", args={"query": "x"})
        assert r1["result"]["ok"] is True
    with patch("httpx.request", return_value=_mock_response(200, {"bitcoin": {"usd": 50000}})):
        r2 = await reg.call("coingecko_price", agent="universe", turn_id="g1", args={})
        assert r2["result"]["ok"] is True


# ---------------------------------------------------------------------------
# 3. Structural / regression tests
# ---------------------------------------------------------------------------

ALL_30 = [t for t, _ in CREDENTIALED_TOOLS] + [
    "pypi_package_info", "npm_package_info", "crates_package_info",
    "wikipedia_search", "arxiv_search", "coingecko_price",
    "github_repo_info", "gitlab_project_info",
]
ALL_30 = sorted(set(ALL_30))


def test_all_30_tools_registered_exactly_once():
    from connectors.builtin import get_registry
    reg = get_registry()
    names = reg.names()
    assert len(ALL_30) == 30, f"expected exactly 30 unique tool names, got {len(ALL_30)}"
    for t in ALL_30:
        assert t in names, f"{t} is not registered"


def test_all_30_tools_in_api_agent_whitelist():
    from core.core_agents.agents import get_agent
    wl = set(get_agent("universe").tool_whitelist_names)
    missing = [t for t in ALL_30 if t not in wl]
    assert not missing, f"missing from api agent whitelist: {missing}"


def test_all_30_tools_are_network_req_capability():
    from connectors.builtin import get_registry
    reg = get_registry()
    for t in ALL_30:
        assert reg.get(t).capability == "network.req", t


def test_write_and_money_moving_tools_require_explicit_consent():
    from connectors.builtin import get_registry
    reg = get_registry()
    explicit_expected = [
        "github_create_issue", "slack_post_message", "discord_webhook_post",
        "telegram_send_message", "notion_create_page", "linear_create_issue",
        "trello_create_card", "asana_create_task", "jira_create_issue",
        "stripe_create_charge", "hubspot_create_contact",
    ]
    for t in explicit_expected:
        assert reg.get(t).consent == "explicit", f"{t} should require explicit consent"


def test_zero_setup_tools_are_auto_consent():
    from connectors.builtin import get_registry
    reg = get_registry()
    for t in ["pypi_package_info", "npm_package_info", "crates_package_info",
             "wikipedia_search", "arxiv_search", "coingecko_price"]:
        assert reg.get(t).consent == "auto", f"{t} should be auto (no credential, public data)"


@pytest.mark.asyncio
async def test_store_and_resolve_thirdparty_credential_roundtrip(fresh_home, monkeypatch):
    """store_thirdparty_credential() -> DB fallback path in _get_credentials()
    works end to end (the env-var path is covered by every test above)."""
    for var in ["UNIVERSE_SLACK_TOKEN"]:
        monkeypatch.delenv(var, raising=False)
    from connectors.builtin.thirdparty import store_thirdparty_credential
    result = store_thirdparty_credential("slack", UNIVERSE_SLACK_TOKEN="xoxb-stored-token")
    assert result["ok"] is True
    from connectors.builtin import get_registry
    with patch("httpx.request", return_value=_mock_response(200, {"ok": True})) as mock_req:
        r = await get_registry().call("slack_list_channels", agent="universe", turn_id="g1", args={})
        assert r["result"]["ok"] is True, r["result"]
        _, call_kwargs = mock_req.call_args
        assert call_kwargs["headers"]["Authorization"] == "Bearer xoxb-stored-token"
