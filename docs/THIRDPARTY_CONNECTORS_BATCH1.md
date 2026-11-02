# Third-party connectors — fallback tier (30 tools)

**Role, clarified against §6 of the master spec:** Composio
(`connectors/composio.py`) is the primary integration layer for v1 — "the
user brings their own Composio API key" covers Gmail, Slack, Notion,
Stripe, and 995+ more through one SDK wrapper. The 30 tools below are a
**fallback tier** for when a user hasn't configured Composio yet (or
skipped that step during onboarding) — not a replacement, not a competing
architecture. Native, direct — no Composio dependency for these specific
30. Code lives in `connectors/builtin/thirdparty.py`. All 30 are on the
`api` agent's whitelist, capability `network.req`, consent set per-tool.

## Zero-setup — works for every user, no credential ever (6)

| Tool | Notes |
|---|---|
| `pypi_package_info` | live-tested in CI |
| `npm_package_info` | live-tested in CI |
| `crates_package_info` | live-tested in CI (requires a descriptive User-Agent header — handled) |
| `wikipedia_search` | |
| `arxiv_search` | parses Atom XML into plain JSON |
| `coingecko_price` | |

## Works without credentials, better with one (2)

| Tool | Env var (optional) | Effect without it |
|---|---|---|
| `github_repo_info` / `github_create_issue`* | `UNIVERSE_GITHUB_TOKEN` | 60 req/hr instead of 5000; can't touch private repos |
| `gitlab_project_info` | `UNIVERSE_GITLAB_TOKEN` | public projects only |

\*`github_create_issue` always requires the token (it's a write).

## Bring-your-own-free-credential (22)

| Service | Env var(s) | Where to get it |
|---|---|---|
| Slack | `UNIVERSE_SLACK_TOKEN` | api.slack.com/apps → OAuth & Permissions → Bot Token (`chat:write` scope) |
| Discord | `UNIVERSE_DISCORD_WEBHOOK_URL` | Channel → Integrations → Webhooks |
| Telegram | `UNIVERSE_TELEGRAM_TOKEN` | Message @BotFather |
| Notion | `UNIVERSE_NOTION_TOKEN` | notion.so/my-integrations (then share the database with it) |
| Linear | `UNIVERSE_LINEAR_KEY` | Settings → Account → Security & Access |
| Trello | `UNIVERSE_TRELLO_KEY` + `UNIVERSE_TRELLO_TOKEN` | trello.com/app-key |
| Asana | `UNIVERSE_ASANA_TOKEN` | My Settings → Apps → Developer apps |
| Jira | `UNIVERSE_JIRA_DOMAIN` + `UNIVERSE_JIRA_EMAIL` + `UNIVERSE_JIRA_TOKEN` | id.atlassian.com/manage-profile/security/api-tokens |
| Dropbox | `UNIVERSE_DROPBOX_TOKEN` | dropbox.com/developers/apps |
| Google Drive | `UNIVERSE_GOOGLE_ACCESS_TOKEN` | **Needs a full OAuth2 flow Universe does not yet run — see caveat below** |
| Stripe | `UNIVERSE_STRIPE_KEY` | Dashboard → Developers → API keys |
| HubSpot | `UNIVERSE_HUBSPOT_TOKEN` | Settings → Integrations → Private Apps |
| Airtable | `UNIVERSE_AIRTABLE_TOKEN` | airtable.com/create/tokens |
| OpenWeatherMap | `UNIVERSE_OPENWEATHERMAP_KEY` | openweathermap.org/api (free tier) |

Set as environment variables, or store via
`connectors.builtin.thirdparty.store_thirdparty_credential(service, **fields)`
(AES-256-GCM encrypted, same mechanism as `connectors/composio.py`). No
interactive wizard step calls this yet for these 30 — that's the one
onboarding piece still to build (the composio wizard in
`cli/wizards/__init__.py` is the template to follow).

## Known, honest caveats

- **No OAuth2 flow anywhere in this batch.** `google_drive_list_files`
  assumes you already have a valid Google access token from your own OAuth
  setup. Getting that token yourself today requires standing up your own
  OAuth client — Universe doesn't walk you through Google's consent screen.
- **`stripe_create_charge`** uses the legacy Charges API (simpler for one
  tool call). Stripe's current recommended path for new integrations is
  PaymentIntents, which needs client-side confirmation and doesn't fit a
  single server-side call — a deliberate tradeoff, not an oversight.
- Every write/send/money-moving tool requires **explicit** consent
  (per-session grant, asked once); every read-only tool is **per_turn**
  (auto-granted once per session); the 6 zero-setup tools are **auto**.

## Verified vs. inherited

Telegram, Trello, Stripe, Notion, Jira, and Linear's auth conventions were
confirmed against live documentation while building this batch (2026-07) —
they're the most idiosyncratic and easiest to get subtly wrong. Everything
else follows long-stable REST conventions. `tests/test_thirdparty.py` locks
in the exact request shape for all 30 via mocked HTTP, plus real live calls
for the 3 credential-free, CI-safe tools (pypi/npm/crates) and a
gracefully-skipping live test for GitHub.

## Deferred to batch 2 (not silently dropped)

Gmail, Google Calendar, ExchangeRate-API, NewsAPI, Dropbox upload, Airtable
writes, Bitbucket, a real Discord bot (vs. webhook), Salesforce, and the
remaining ~165 of the "top 200" — plus the onboarding wizard for all of
batch 1's credentials.
