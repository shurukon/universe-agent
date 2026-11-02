"""connectors/builtin/thirdparty.py — native, direct third-party connectors.

ROLE CLARIFIED post-hoc (2026-07) against §6 of the master spec: Composio
(connectors/composio.py) is the PRIMARY third-party integration layer for
v1 — "the user brings their own Composio API key" is the documented model,
covering Gmail, Slack, Notion, Stripe, and 995+ more through one SDK
wrapper. The 30 tools in this file are a FALLBACK tier for when a user
hasn't configured Composio yet (or skipped that step during onboarding):
a small set of the most-requested integrations that work immediately via
each user's own free-tier credential for that one service, no Composio
account required. Not a replacement for Composio, not a competing
architecture — a stopgap so Universe isn't empty-handed on day one for
Slack/GitHub/Notion/etc. before a user has gone through Composio setup.

Batch 1 of this fallback tier (30 tools). Every tool here talks DIRECTLY to
each service's own public REST/GraphQL API using the user's own free-tier
credential for that one service — mirroring the same "no proprietary
middleman" decision already made for web_search (DuckDuckGo HTML, not the
paid Z.ai SDK).

WHAT "FREE" MEANS HERE (two tiers, both honest, neither hidden):
  - Zero-setup tier (6 tools): pypi_package_info, npm_package_info,
    crates_package_info, wikipedia_search, arxiv_search, coingecko_price.
    No credential of any kind, for anyone, ever. Genuinely frictionless.
  - Bring-your-own-free-credential tier (the rest): the service itself is
    free to sign up for (Slack, GitHub, Notion, Trello, ...), but each user
    must supply their OWN token for their OWN account. No architecture can
    avoid this for anything that touches a private account — it is not a
    Universe limitation, it is how authentication works everywhere (Zapier,
    n8n, and Make's free tiers all require the same per-user setup).

CREDENTIAL RESOLUTION (same order for every tool that needs one):
  1. An env var, e.g. UNIVERSE_SLACK_TOKEN — simplest path, works everywhere.
  2. An encrypted row in the `connectors` table (kind='thirdparty'), set via
     store_thirdparty_credential() — wired for a future interactive setup
     wizard (the composio.py wizard in cli/wizards/__init__.py is the
     template; that wizard step has NOT been built yet for these 30 tools —
     flagging this honestly rather than implying it's done).

CONSENT DESIGN — this is the real payoff of the capability-gate fix:
  Every tool below is capability=network.req, but consent is declared PER
  TOOL (security/capability.py now reads Tool.consent as authoritative):
    - read-only / idempotent calls (list, get, search, query)  -> per_turn
    - anything that sends, creates, deletes, or moves money    -> explicit
    - genuinely public, no-credential data                     -> auto

VERIFICATION NOTE: auth schemes for Telegram, Trello, Stripe, Notion, Jira,
and Linear were confirmed against live documentation while building this
batch (2026-07) because they're the most idiosyncratic (Telegram embeds the
token in the URL path; Trello uses query-string key+token, not a header;
Stripe uses HTTP Basic Auth with a blank password and form-encoded bodies,
not JSON; Notion requires a dated Notion-Version header; Jira Cloud uses
Basic Auth with base64(email:api_token); Linear's personal API keys go
bare in the Authorization header with NO "Bearer" prefix — easy to get
wrong, since Linear's own OAuth docs use Bearer for a *different* credential
type). The rest follow long-stable, well-known REST conventions.

github_repo_info / pypi_package_info / npm_package_info / crates_package_info
are additionally LIVE-tested against the real public APIs in
tests/test_thirdparty.py, since those four domains need no credentials and
are safe to hit from CI. Every other tool is covered by mocked-HTTP tests
(request construction + response parsing) — the standard way to test
third-party API integrations without depending on live secrets in CI.

NOT included in this batch (deferred, not silently dropped):
  - No OAuth2 *flow* is implemented anywhere. google_drive_list_files
    assumes the user already has a valid Google OAuth2 access token — same
    "bring your own token" model as every other tool here. Universe does not
    yet walk the user through Google's consent screen.
  - Gmail, Google Calendar, ExchangeRate-API, NewsAPI, Dropbox upload,
    Airtable writes, Bitbucket, Discord bot (vs. webhook), Salesforce,
    and ~165 more of the "top 200" are intentionally left for later batches
    (phased delivery, same as the enforcement-before-expansion approach
    already used for the rest of Universe).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from xml.etree import ElementTree as ET

from core.agent.base_agent import Tool, new_id
from connectors.builtin import ToolRegistry
from storage.local import get_storage


def _make(name: str, capability: str, desc: str, fn, consent: str = "per_turn",
          resources=None):
    return Tool(name=name, capability=capability, description=desc, handler=fn,
                consent=consent, resources=resources or [])


# ---------------------------------------------------------------------------
# Credential resolution — shared by every tool below
# ---------------------------------------------------------------------------

def _get_credentials(service: str, *env_vars: str) -> dict[str, str]:
    """Resolve one or more named credential pieces for `service`. Checks env
    vars first; falls back to an encrypted `connectors` row for whatever is
    still missing. Returns {env_var_name: value} — missing ones are simply
    absent from the dict, never an empty string, so callers can use
    straightforward `.get(...)` truthiness checks."""
    out: dict[str, str] = {}
    missing = []
    for ev in env_vars:
        v = os.environ.get(ev)
        if v:
            out[ev] = v
        else:
            missing.append(ev)
    if not missing:
        return out
    try:
        st = get_storage()
        row = st.query_one("SELECT * FROM connectors WHERE kind='thirdparty' AND name=?;",
                           (service,))
        if row:
            from security.secrets_fallback import DecryptionError, decrypt_secret
            cfg = json.loads(row["config"] or "{}")
            for ev in missing:
                enc = cfg.get(ev, "")
                if enc:
                    try:
                        out[ev] = decrypt_secret(enc)
                    except DecryptionError:
                        # leave the credential missing — the tool's own
                        # needs_setup path reports it honestly; never fall
                        # back to sending the ciphertext
                        continue
    except Exception:
        pass  # connectors table may be absent on very old schemas
    return out


def store_thirdparty_credential(service: str, **fields: str) -> dict:
    """Store credential field(s) for `service`, AES-encrypted (mirrors
    ComposioConnector.connect() in connectors/composio.py). Not registered
    as an agent tool on purpose: this GRANTS access, it doesn't USE it, so
    it belongs in an onboarding wizard, not the agent-callable tool surface.
    A future CLI wizard step (see cli/wizards/__init__.py for the composio
    precedent) should call this — that wizard step is not yet built."""
    from security.secrets_fallback import encrypt_secret
    cfg = {k: encrypt_secret(v) for k, v in fields.items() if v}
    st = get_storage()
    cid = new_id("thirdparty-")
    with st.transaction() as conn:
        conn.execute(
            "INSERT INTO connectors(id,kind,name,config,connected_at) "
            "VALUES(?,?,?,?,?);",
            (cid, "thirdparty", service, json.dumps(cfg), time.time()),
        )
    return {"ok": True, "service": service, "id": cid}


def _missing(service: str, *env_vars: str, hint: str = "") -> dict:
    names = " and ".join(env_vars)
    suffix = f" {hint}" if hint else ""
    return {"ok": False, "error": f"{service} not configured — set {names}.{suffix}",
            "needs_setup": True, "service": service}


# ---------------------------------------------------------------------------
# Shared HTTP helper — mirrors http_client's pattern in misc.py, including
# the [UNTRUSTED:web] tag (§10): any data originating outside Universe must be
# tagged so it can never be mistaken for an instruction by an agent's LLM.
# ---------------------------------------------------------------------------

def _http(method: str, url: str, *, headers: dict | None = None,
          params: dict | None = None, json_body: Any = None,
          data: Any = None, auth: tuple | None = None,
          timeout: float = 15.0) -> dict:
    import httpx
    try:
        r = httpx.request(method, url, headers=headers or {}, params=params,
                          json=json_body, data=data, auth=auth,
                          timeout=timeout, follow_redirects=True)
        try:
            parsed = r.json()
            body = json.dumps(parsed)[:8000]
        except Exception:
            body = r.text[:3000]
        return {"ok": 200 <= r.status_code < 300, "status": r.status_code,
                "data": f"[UNTRUSTED:web] {body}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def register(reg: ToolRegistry) -> None:
    # =========================================================================
    # DEV / PACKAGE REGISTRIES (6) — github_repo_info, pypi/npm/crates are
    # also live-tested; PyPI/npm/crates need no credential at all.
    # =========================================================================

    def github_repo_info(owner: str, repo: str) -> dict:
        creds = _get_credentials("github", "UNIVERSE_GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github+json"}
        if "UNIVERSE_GITHUB_TOKEN" in creds:
            headers["Authorization"] = f"Bearer {creds['UNIVERSE_GITHUB_TOKEN']}"
        return _http("GET", f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
    reg.register(_make("github_repo_info", "network.req",
                       "Get a GitHub repo's metadata (stars, issues, etc). "
                       "Works without a token for public repos (lower rate limit); "
                       "set UNIVERSE_GITHUB_TOKEN for higher limits or private repos.",
                       github_repo_info, "per_turn", ["url:https://api.github.com/*"]))

    def github_create_issue(owner: str, repo: str, title: str, body: str = "") -> dict:
        creds = _get_credentials("github", "UNIVERSE_GITHUB_TOKEN")
        if "UNIVERSE_GITHUB_TOKEN" not in creds:
            return _missing("github", "UNIVERSE_GITHUB_TOKEN",
                            hint="Generate one at https://github.com/settings/tokens (repo scope).")
        headers = {"Accept": "application/vnd.github+json",
                  "Authorization": f"Bearer {creds['UNIVERSE_GITHUB_TOKEN']}"}
        return _http("POST", f"https://api.github.com/repos/{owner}/{repo}/issues",
                     headers=headers, json_body={"title": title, "body": body})
    reg.register(_make("github_create_issue", "network.req", "Create a GitHub issue",
                       github_create_issue, "explicit", ["url:https://api.github.com/*"]))

    def gitlab_project_info(project: str) -> dict:
        creds = _get_credentials("gitlab", "UNIVERSE_GITLAB_TOKEN")
        headers = {}
        if "UNIVERSE_GITLAB_TOKEN" in creds:
            headers["PRIVATE-TOKEN"] = creds["UNIVERSE_GITLAB_TOKEN"]
        from urllib.parse import quote
        return _http("GET", f"https://gitlab.com/api/v4/projects/{quote(project, safe='')}",
                     headers=headers)
    reg.register(_make("gitlab_project_info", "network.req",
                       "Get a GitLab project's metadata. Works without a token for "
                       "public projects; set UNIVERSE_GITLAB_TOKEN for private ones.",
                       gitlab_project_info, "per_turn", ["url:https://gitlab.com/*"]))

    def pypi_package_info(package: str) -> dict:
        return _http("GET", f"https://pypi.org/pypi/{package}/json")
    reg.register(_make("pypi_package_info", "network.req",
                       "Get a PyPI package's metadata (version, summary, deps). "
                       "Fully public, no credential ever needed.",
                       pypi_package_info, "auto", ["url:https://pypi.org/*"]))

    def npm_package_info(package: str) -> dict:
        return _http("GET", f"https://registry.npmjs.org/{package}")
    reg.register(_make("npm_package_info", "network.req",
                       "Get an npm package's metadata. Fully public, no credential needed.",
                       npm_package_info, "auto", ["url:https://registry.npmjs.org/*"]))

    def crates_package_info(crate: str) -> dict:
        # crates.io requires a descriptive User-Agent or it returns 403.
        headers = {"User-Agent": "universe-agent (https://github.com/universe-agent)"}
        return _http("GET", f"https://crates.io/api/v1/crates/{crate}", headers=headers)
    reg.register(_make("crates_package_info", "network.req",
                       "Get a Rust crate's metadata from crates.io. "
                       "Fully public, no credential needed.",
                       crates_package_info, "auto", ["url:https://crates.io/*"]))

    # =========================================================================
    # COMMUNICATION (4)
    # =========================================================================

    def slack_post_message(channel: str, text: str) -> dict:
        creds = _get_credentials("slack", "UNIVERSE_SLACK_TOKEN")
        if "UNIVERSE_SLACK_TOKEN" not in creds:
            return _missing("slack", "UNIVERSE_SLACK_TOKEN",
                            hint="Create a bot token at https://api.slack.com/apps "
                                 "(OAuth & Permissions -> Bot Token, needs chat:write scope).")
        return _http("POST", "https://slack.com/api/chat.postMessage",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_SLACK_TOKEN']}"},
                     json_body={"channel": channel, "text": text})
    reg.register(_make("slack_post_message", "network.req", "Post a message to a Slack channel",
                       slack_post_message, "explicit", ["url:https://slack.com/*"]))

    def slack_list_channels() -> dict:
        creds = _get_credentials("slack", "UNIVERSE_SLACK_TOKEN")
        if "UNIVERSE_SLACK_TOKEN" not in creds:
            return _missing("slack", "UNIVERSE_SLACK_TOKEN")
        return _http("GET", "https://slack.com/api/conversations.list",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_SLACK_TOKEN']}"})
    reg.register(_make("slack_list_channels", "network.req", "List Slack channels",
                       slack_list_channels, "per_turn", ["url:https://slack.com/*"]))

    def discord_webhook_post(content: str, webhook_url: str = "") -> dict:
        creds = _get_credentials("discord", "UNIVERSE_DISCORD_WEBHOOK_URL")
        url = webhook_url or creds.get("UNIVERSE_DISCORD_WEBHOOK_URL", "")
        if not url:
            return _missing("discord", "UNIVERSE_DISCORD_WEBHOOK_URL",
                            hint="Create one in a channel's Integrations -> Webhooks settings.")
        return _http("POST", url, json_body={"content": content})
    reg.register(_make("discord_webhook_post", "network.req",
                       "Post a message via a Discord incoming webhook (no bot setup needed)",
                       discord_webhook_post, "explicit", ["url:https://discord.com/*"]))

    def telegram_send_message(chat_id: str, text: str) -> dict:
        creds = _get_credentials("telegram", "UNIVERSE_TELEGRAM_TOKEN")
        if "UNIVERSE_TELEGRAM_TOKEN" not in creds:
            return _missing("telegram", "UNIVERSE_TELEGRAM_TOKEN",
                            hint="Message @BotFather on Telegram to create a bot and get a token.")
        token = creds["UNIVERSE_TELEGRAM_TOKEN"]
        return _http("POST", f"https://api.telegram.org/bot{token}/sendMessage",
                     json_body={"chat_id": chat_id, "text": text})
    reg.register(_make("telegram_send_message", "network.req", "Send a Telegram message via a bot",
                       telegram_send_message, "explicit", ["url:https://api.telegram.org/*"]))

    # =========================================================================
    # PRODUCTIVITY / PM (10)
    # =========================================================================

    def notion_query_database(database_id: str, filter_json: str = "") -> dict:
        creds = _get_credentials("notion", "UNIVERSE_NOTION_TOKEN")
        if "UNIVERSE_NOTION_TOKEN" not in creds:
            return _missing("notion", "UNIVERSE_NOTION_TOKEN",
                            hint="Create an internal integration at https://www.notion.so/my-integrations "
                                 "and share the database with it.")
        headers = {"Authorization": f"Bearer {creds['UNIVERSE_NOTION_TOKEN']}",
                  "Notion-Version": "2022-06-28"}
        body = json.loads(filter_json) if filter_json else {}
        return _http("POST", f"https://api.notion.com/v1/databases/{database_id}/query",
                     headers=headers, json_body=body)
    reg.register(_make("notion_query_database", "network.req", "Query a Notion database",
                       notion_query_database, "per_turn", ["url:https://api.notion.com/*"]))

    def notion_create_page(parent_database_id: str, title: str, content: str = "") -> dict:
        creds = _get_credentials("notion", "UNIVERSE_NOTION_TOKEN")
        if "UNIVERSE_NOTION_TOKEN" not in creds:
            return _missing("notion", "UNIVERSE_NOTION_TOKEN")
        headers = {"Authorization": f"Bearer {creds['UNIVERSE_NOTION_TOKEN']}",
                  "Notion-Version": "2022-06-28"}
        body = {
            "parent": {"database_id": parent_database_id},
            "properties": {"title": {"title": [{"text": {"content": title}}]}},
        }
        if content:
            body["children"] = [{"object": "block", "type": "paragraph",
                                 "paragraph": {"rich_text": [{"text": {"content": content}}]}}]
        return _http("POST", "https://api.notion.com/v1/pages", headers=headers, json_body=body)
    reg.register(_make("notion_create_page", "network.req", "Create a page in a Notion database",
                       notion_create_page, "explicit", ["url:https://api.notion.com/*"]))

    def _linear_graphql(creds: dict, query: str, variables: dict) -> dict:
        # Personal API keys go BARE in Authorization (no "Bearer" prefix) —
        # unlike Linear's own OAuth2 access tokens, which do use Bearer.
        return _http("POST", "https://api.linear.app/graphql",
                     headers={"Authorization": creds["UNIVERSE_LINEAR_KEY"],
                             "Content-Type": "application/json"},
                     json_body={"query": query, "variables": variables})

    def linear_list_issues(team_key: str = "", limit: int = 20) -> dict:
        creds = _get_credentials("linear", "UNIVERSE_LINEAR_KEY")
        if "UNIVERSE_LINEAR_KEY" not in creds:
            return _missing("linear", "UNIVERSE_LINEAR_KEY",
                            hint="Settings -> Account -> Security & Access -> Personal API keys.")
        q = """query($first: Int) { issues(first: $first) {
                 nodes { id identifier title state { name } } } }"""
        return _linear_graphql(creds, q, {"first": limit})
    reg.register(_make("linear_list_issues", "network.req", "List Linear issues",
                       linear_list_issues, "per_turn", ["url:https://api.linear.app/*"]))

    def linear_create_issue(team_key: str, title: str, description: str = "") -> dict:
        creds = _get_credentials("linear", "UNIVERSE_LINEAR_KEY")
        if "UNIVERSE_LINEAR_KEY" not in creds:
            return _missing("linear", "UNIVERSE_LINEAR_KEY")
        q = """mutation($teamId: String!, $title: String!, $description: String) {
                 issueCreate(input: {teamId: $teamId, title: $title, description: $description}) {
                   success issue { id identifier } } }"""
        return _linear_graphql(creds, q, {"teamId": team_key, "title": title, "description": description})
    reg.register(_make("linear_create_issue", "network.req", "Create a Linear issue",
                       linear_create_issue, "explicit", ["url:https://api.linear.app/*"]))

    def trello_list_boards() -> dict:
        creds = _get_credentials("trello", "UNIVERSE_TRELLO_KEY", "UNIVERSE_TRELLO_TOKEN")
        if "UNIVERSE_TRELLO_KEY" not in creds or "UNIVERSE_TRELLO_TOKEN" not in creds:
            return _missing("trello", "UNIVERSE_TRELLO_KEY", "UNIVERSE_TRELLO_TOKEN",
                            hint="Get both at https://trello.com/app-key.")
        return _http("GET", "https://api.trello.com/1/members/me/boards",
                     params={"key": creds["UNIVERSE_TRELLO_KEY"], "token": creds["UNIVERSE_TRELLO_TOKEN"]})
    reg.register(_make("trello_list_boards", "network.req", "List the user's Trello boards",
                       trello_list_boards, "per_turn", ["url:https://api.trello.com/*"]))

    def trello_create_card(list_id: str, name: str, desc: str = "") -> dict:
        creds = _get_credentials("trello", "UNIVERSE_TRELLO_KEY", "UNIVERSE_TRELLO_TOKEN")
        if "UNIVERSE_TRELLO_KEY" not in creds or "UNIVERSE_TRELLO_TOKEN" not in creds:
            return _missing("trello", "UNIVERSE_TRELLO_KEY", "UNIVERSE_TRELLO_TOKEN")
        return _http("POST", "https://api.trello.com/1/cards",
                     params={"key": creds["UNIVERSE_TRELLO_KEY"], "token": creds["UNIVERSE_TRELLO_TOKEN"],
                            "idList": list_id, "name": name, "desc": desc})
    reg.register(_make("trello_create_card", "network.req", "Create a Trello card",
                       trello_create_card, "explicit", ["url:https://api.trello.com/*"]))

    def asana_list_tasks(project_gid: str) -> dict:
        creds = _get_credentials("asana", "UNIVERSE_ASANA_TOKEN")
        if "UNIVERSE_ASANA_TOKEN" not in creds:
            return _missing("asana", "UNIVERSE_ASANA_TOKEN",
                            hint="My Settings -> Apps -> Developer apps -> Personal access token.")
        return _http("GET", f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_ASANA_TOKEN']}"})
    reg.register(_make("asana_list_tasks", "network.req", "List tasks in an Asana project",
                       asana_list_tasks, "per_turn", ["url:https://app.asana.com/*"]))

    def asana_create_task(project_gid: str, name: str, notes: str = "") -> dict:
        creds = _get_credentials("asana", "UNIVERSE_ASANA_TOKEN")
        if "UNIVERSE_ASANA_TOKEN" not in creds:
            return _missing("asana", "UNIVERSE_ASANA_TOKEN")
        return _http("POST", "https://app.asana.com/api/1.0/tasks",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_ASANA_TOKEN']}"},
                     json_body={"data": {"projects": [project_gid], "name": name, "notes": notes}})
    reg.register(_make("asana_create_task", "network.req", "Create a task in an Asana project",
                       asana_create_task, "explicit", ["url:https://app.asana.com/*"]))

    def jira_search_issues(jql: str, max_results: int = 20) -> dict:
        creds = _get_credentials("jira", "UNIVERSE_JIRA_DOMAIN", "UNIVERSE_JIRA_EMAIL", "UNIVERSE_JIRA_TOKEN")
        need = ["UNIVERSE_JIRA_DOMAIN", "UNIVERSE_JIRA_EMAIL", "UNIVERSE_JIRA_TOKEN"]
        if not all(k in creds for k in need):
            return _missing("jira", *need,
                            hint="Token at https://id.atlassian.com/manage-profile/security/api-tokens; "
                                 "domain is the <x> in https://<x>.atlassian.net.")
        return _http("GET", f"https://{creds['UNIVERSE_JIRA_DOMAIN']}.atlassian.net/rest/api/3/search",
                     params={"jql": jql, "maxResults": max_results},
                     auth=(creds["UNIVERSE_JIRA_EMAIL"], creds["UNIVERSE_JIRA_TOKEN"]))
    reg.register(_make("jira_search_issues", "network.req", "Search Jira issues with JQL",
                       jira_search_issues, "per_turn", ["url:https://*.atlassian.net/*"]))

    def jira_create_issue(project_key: str, summary: str, issue_type: str = "Task",
                          description: str = "") -> dict:
        creds = _get_credentials("jira", "UNIVERSE_JIRA_DOMAIN", "UNIVERSE_JIRA_EMAIL", "UNIVERSE_JIRA_TOKEN")
        need = ["UNIVERSE_JIRA_DOMAIN", "UNIVERSE_JIRA_EMAIL", "UNIVERSE_JIRA_TOKEN"]
        if not all(k in creds for k in need):
            return _missing("jira", *need)
        body = {"fields": {"project": {"key": project_key}, "summary": summary,
                           "issuetype": {"name": issue_type},
                           "description": {"type": "doc", "version": 1, "content": [
                               {"type": "paragraph",
                                "content": [{"type": "text", "text": description or summary}]}]}}}
        return _http("POST", f"https://{creds['UNIVERSE_JIRA_DOMAIN']}.atlassian.net/rest/api/3/issue",
                     json_body=body, auth=(creds["UNIVERSE_JIRA_EMAIL"], creds["UNIVERSE_JIRA_TOKEN"]))
    reg.register(_make("jira_create_issue", "network.req", "Create a Jira issue",
                       jira_create_issue, "explicit", ["url:https://*.atlassian.net/*"]))

    # =========================================================================
    # STORAGE (2)
    # =========================================================================

    def dropbox_list_files(path: str = "") -> dict:
        creds = _get_credentials("dropbox", "UNIVERSE_DROPBOX_TOKEN")
        if "UNIVERSE_DROPBOX_TOKEN" not in creds:
            return _missing("dropbox", "UNIVERSE_DROPBOX_TOKEN",
                            hint="Create an app + generated access token at https://www.dropbox.com/developers/apps.")
        return _http("POST", "https://api.dropboxapi.com/2/files/list_folder",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_DROPBOX_TOKEN']}"},
                     json_body={"path": path})
    reg.register(_make("dropbox_list_files", "network.req", "List files in a Dropbox folder",
                       dropbox_list_files, "per_turn", ["url:https://api.dropboxapi.com/*"]))

    def google_drive_list_files(query: str = "", page_size: int = 20) -> dict:
        creds = _get_credentials("google", "UNIVERSE_GOOGLE_ACCESS_TOKEN")
        if "UNIVERSE_GOOGLE_ACCESS_TOKEN" not in creds:
            return _missing("google", "UNIVERSE_GOOGLE_ACCESS_TOKEN",
                            hint="Requires an OAuth2 access token from Google's own consent flow — "
                                 "Universe does not yet run that flow itself. See NOTE in thirdparty.py.")
        params = {"pageSize": page_size}
        if query:
            params["q"] = query
        return _http("GET", "https://www.googleapis.com/drive/v3/files",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_GOOGLE_ACCESS_TOKEN']}"},
                     params=params)
    reg.register(_make("google_drive_list_files", "network.req",
                       "List Google Drive files (needs a pre-obtained OAuth2 access token — "
                       "Universe does not run the Google consent flow itself yet)",
                       google_drive_list_files, "per_turn", ["url:https://www.googleapis.com/*"]))

    # =========================================================================
    # PAYMENTS / BUSINESS (3)
    # =========================================================================

    def stripe_list_charges(limit: int = 10) -> dict:
        creds = _get_credentials("stripe", "UNIVERSE_STRIPE_KEY")
        if "UNIVERSE_STRIPE_KEY" not in creds:
            return _missing("stripe", "UNIVERSE_STRIPE_KEY",
                            hint="Secret key from the Stripe Dashboard -> Developers -> API keys.")
        return _http("GET", "https://api.stripe.com/v1/charges", params={"limit": limit},
                     auth=(creds["UNIVERSE_STRIPE_KEY"], ""))
    reg.register(_make("stripe_list_charges", "network.req", "List recent Stripe charges",
                       stripe_list_charges, "per_turn", ["url:https://api.stripe.com/*"]))

    def stripe_create_charge(amount: int, currency: str, source: str, description: str = "") -> dict:
        """NOTE: uses the legacy Charges API (simpler for a single connector
        call). Stripe's current recommended flow for new integrations is
        PaymentIntents, which needs client-side confirmation and is a poor
        fit for a single server-side tool call — flagging the tradeoff
        rather than silently picking one."""
        creds = _get_credentials("stripe", "UNIVERSE_STRIPE_KEY")
        if "UNIVERSE_STRIPE_KEY" not in creds:
            return _missing("stripe", "UNIVERSE_STRIPE_KEY")
        # Stripe expects x-www-form-urlencoded, NOT JSON.
        return _http("POST", "https://api.stripe.com/v1/charges",
                     data={"amount": amount, "currency": currency, "source": source,
                          "description": description},
                     auth=(creds["UNIVERSE_STRIPE_KEY"], ""))
    reg.register(_make("stripe_create_charge", "network.req",
                       "Create a Stripe charge (legacy Charges API — moves real money if using a live key)",
                       stripe_create_charge, "explicit", ["url:https://api.stripe.com/*"]))

    def hubspot_create_contact(email: str, firstname: str = "", lastname: str = "") -> dict:
        creds = _get_credentials("hubspot", "UNIVERSE_HUBSPOT_TOKEN")
        if "UNIVERSE_HUBSPOT_TOKEN" not in creds:
            return _missing("hubspot", "UNIVERSE_HUBSPOT_TOKEN",
                            hint="Create a private app token in Settings -> Integrations -> Private Apps.")
        props = {"email": email}
        if firstname:
            props["firstname"] = firstname
        if lastname:
            props["lastname"] = lastname
        return _http("POST", "https://api.hubapi.com/crm/v3/objects/contacts",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_HUBSPOT_TOKEN']}"},
                     json_body={"properties": props})
    reg.register(_make("hubspot_create_contact", "network.req", "Create a HubSpot CRM contact",
                       hubspot_create_contact, "explicit", ["url:https://api.hubapi.com/*"]))

    # =========================================================================
    # DATA (1)
    # =========================================================================

    def airtable_list_records(base_id: str, table_name: str, max_records: int = 20) -> dict:
        creds = _get_credentials("airtable", "UNIVERSE_AIRTABLE_TOKEN")
        if "UNIVERSE_AIRTABLE_TOKEN" not in creds:
            return _missing("airtable", "UNIVERSE_AIRTABLE_TOKEN",
                            hint="Create a personal access token at https://airtable.com/create/tokens.")
        from urllib.parse import quote
        return _http("GET", f"https://api.airtable.com/v0/{base_id}/{quote(table_name, safe='')}",
                     headers={"Authorization": f"Bearer {creds['UNIVERSE_AIRTABLE_TOKEN']}"},
                     params={"maxRecords": max_records})
    reg.register(_make("airtable_list_records", "network.req", "List records in an Airtable table",
                       airtable_list_records, "per_turn", ["url:https://api.airtable.com/*"]))

    # =========================================================================
    # GENUINELY FREE, ZERO-SETUP (4) — no credential of any kind, ever
    # =========================================================================

    def wikipedia_search(query: str, limit: int = 5) -> dict:
        return _http("GET", "https://en.wikipedia.org/w/api.php",
                     params={"action": "query", "list": "search", "srsearch": query,
                            "srlimit": limit, "format": "json"})
    reg.register(_make("wikipedia_search", "network.req",
                       "Search Wikipedia. Fully public, no credential ever needed.",
                       wikipedia_search, "auto", ["url:https://en.wikipedia.org/*"]))

    def arxiv_search(query: str, max_results: int = 10) -> dict:
        # https (not http): the registered gate resource is
        # url:https://export.arxiv.org/* and the static XML parser below
        # expects the Atom feed exactly as served over TLS.
        r = _http("GET", "https://export.arxiv.org/api/query",
                  params={"search_query": f"all:{query}", "max_results": max_results})
        if not r["ok"]:
            return r
        # arXiv returns Atom XML, not JSON — parse it into a plain list so
        # the agent gets usable data instead of raw XML soup.
        raw = r["data"]
        if raw.startswith("[UNTRUSTED:web] "):
            raw = raw[len("[UNTRUSTED:web] "):]
        try:
            ns = {"a": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(raw)
            entries = []
            for e in root.findall("a:entry", ns):
                entries.append({
                    "title": (e.findtext("a:title", default="", namespaces=ns) or "").strip(),
                    "summary": (e.findtext("a:summary", default="", namespaces=ns) or "").strip()[:500],
                    "id": (e.findtext("a:id", default="", namespaces=ns) or "").strip(),
                })
            return {"ok": True, "status": r["status"],
                    "data": f"[UNTRUSTED:web] {json.dumps(entries)[:8000]}"}
        except ET.ParseError:
            return r  # fall back to the raw tagged text if parsing fails
    reg.register(_make("arxiv_search", "network.req",
                       "Search arXiv papers. Fully public, no credential ever needed.",
                       arxiv_search, "auto", ["url:https://export.arxiv.org/*"]))

    def coingecko_price(coin_id: str = "bitcoin", vs_currency: str = "usd") -> dict:
        return _http("GET", "https://api.coingecko.com/api/v3/simple/price",
                     params={"ids": coin_id, "vs_currencies": vs_currency})
    reg.register(_make("coingecko_price", "network.req",
                       "Get a cryptocurrency's current price via CoinGecko. "
                       "Fully public, no credential ever needed.",
                       coingecko_price, "auto", ["url:https://api.coingecko.com/*"]))

    def openweathermap_current(city: str, units: str = "metric") -> dict:
        creds = _get_credentials("openweathermap", "UNIVERSE_OPENWEATHERMAP_KEY")
        if "UNIVERSE_OPENWEATHERMAP_KEY" not in creds:
            return _missing("openweathermap", "UNIVERSE_OPENWEATHERMAP_KEY",
                            hint="Free tier key at https://openweathermap.org/api.")
        return _http("GET", "https://api.openweathermap.org/data/2.5/weather",
                     params={"q": city, "units": units,
                            "appid": creds["UNIVERSE_OPENWEATHERMAP_KEY"]})
    reg.register(_make("openweathermap_current", "network.req",
                       "Get current weather for a city (needs a free OpenWeatherMap key)",
                       openweathermap_current, "per_turn", ["url:https://api.openweathermap.org/*"]))

