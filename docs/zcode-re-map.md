# ZCode reverse-engineering map — the app's real internals

Evidence file for the "ZCode parity" build. Everything below was extracted
**statically** from the installed ZCode 3.14.3 desktop app on this machine
(read-only). Runtime observation was not performed; every claim is labeled
`[static]` with its source path. Where the asar could not answer a question,
the section says so instead of guessing.

## Sources

| Source | What it is | How it was read |
|---|---|---|
| `C:\Program Files\ZCode\resources\app.asar` | Electron renderer+main+host+preload bundle (307 MB, 27,281 files) | JSON header parsed at `16 + json_size` (JSON header format, not pickle). Inventory: `docs/re-artifacts/asar-inventory.json` |
| `C:\Program Files\ZCode\resources\glm\zcode.cjs` | Bundled agent runtime (12.6 MB) — slash registry, tool defs, permission enums | read as text, regex extraction → `docs/re-artifacts/zcode-facts.json`, `tool-defs*.txt`, `slash-region.txt` |
| `…\resources\app.asar\out\renderer\assets\` | 2,758 asset chunks incl. **1,713 lucide icon chunks** | per-chunk reads → `docs/re-artifacts/icon-inventory.json`, `renderer-facts.json` |
| 8 harness screenshots (1600×900) + `docs/zcode-live-screens/live-zcode-window.png` | measured geometry (Phase-1 pixel scans) | values quoted in §5 |

Reproduction scripts: `scripts/re/extract_zcode.py`, `scripts/re/extract_icons.py`,
`scripts/re/extract_renderer.py`.

## 1. Slash commands — ZCode's real registry [static]

Extracted verbatim from `glm/zcode.cjs` (20 built-ins; `name`/`summary`/`usage`):

| Command | Summary | Usage |
|---|---|---|
| `/help` | Show this slash command help. | `/help [command]` |
| `/login` | Set up a Coding Plan provider. | `/login [zai-coding-plan\|bigmodel-coding-plan\|…]` |
| `/logout` | Remove the shared Z.ai login credentials. | `/logout` |
| `/compact` | Compact the current conversation with optional instructions. | `/compact [instructions]` |
| `/init` | Create or update workspace AGENTS.md instructions. | `/init [notes]` |
| `/expert` | Run or manage the expert workflow. | `/expert [status\|resume\|stop\|<task>]` |
| `/effort` | Show or switch the current session reasoning effort. | `/effort [list\|<level>]` |
| `/workflow` | Create, validate, or run a script workflow. | `/workflow` |
| `/workflows` | List or inspect workflow runs. | `/workflows [runId]` |
| `/fork` | Fork a new session from a workspace checkpoint. | `/fork [latest\|checkpointId]` |
| `/locale` | Show or switch the UI locale. | `/locale [auto\|en-US\|zh-CN]` |
| `/mcp` | Show or manage configured MCP servers. | `/mcp [list\|status\|connect <srv>\|disconnect <srv>]` |
| `/plugins` | Open the plugin manager. | `/plugins [list\|enable <p>\|disable <p>]` |
| `/mode` | Show or switch the current permission mode. | `/mode [plan\|build\|edit\|yolo]` |
| `/model` | Show or switch the current session model. | `/model [list\|main\|lite\|provider/model]` |
| `/new` | Start a fresh session in the TUI. | `/new` |
| `/resume` | Resume a saved session. | `/resume [sessionId]` |
| `/rewind` | Inspect or restore workspace checkpoints. | `/rewind [latest\|checkpointId]` |
| `/skill` | List skills, or force the next prompt to load one. | `/skill [<skill-name> [task]]` |
| `/session` | Show or set the current session session. | `/session [pause\|resume\|clear\|replace <objective>\|<objective>]` |

**Non-built-in commands** come from `.md` command files (zcode-guide
`diagnosing-commands` skill): discovery order = configured roots →
`~/.zcode/commands` → `~/.agents/commands` → workspace `.zcode/commands`
(walked up to repo root) → `.agents/commands` → plugin command roots. Names
come from file names (subdirs join with `:`, so `review:code` not
`/review/code`); frontmatter keys `description`, `argument-hint`,
`allowed-tools`, `model`, `skills`, `disable-noninteractive`; `$ARGUMENTS`
/ `$1` substitution; names colliding with built-ins or the reserved
`compress` are filtered out of the `/` menu [static: skill doc].

## 2. Permission modes [static]

- Agent-side enum (zcode.cjs): `["plan","build","edit","yolo"]` (+`"auto"`).
- Compatibility table (accepted aliases, zcode.cjs `Kkn`):
  `["default","yolo","plan","edit","acceptEdits","auto","dontAsk","bypassPermissions","autoEdit","build"]`
  — so `acceptEdits`→`edit`, `bypassPermissions`/`dontAsk`→`yolo`,
  `default`/`autoEdit`→`build`.
- Universe's current set is `full|plan|ask` (security/capability.py) and is
  replaced by the ZCode four in Phase 3.

## 3. Tool set [static]

Confirmed present in the agent runtime (occurrence counts in zcode.cjs):
Bash 74 · Read 87 · Write 42 · Edit 41 · Grep 30 · Glob 27 · TodoWrite 18 ·
TodoRead 10 · Agent 84 · Task 21 · Skill 38 · EnterPlanMode 13 ·
ExitPlanMode 14 · AskUserQuestion 21 · CronCreate 20 · CronList 10 ·
CronUpdate 13 · CronDelete 10 · SendMessage 10 · ReadSessionContext 9 ·
TaskOutput 6 · TaskStop 7 · WebFetch 36 · WebSearch 25.

Tool object shape (e.g. `Bash`, from zcode.cjs):
`metadata{name, description, readOnly, destructive, concurrentSafe, timeoutMs,
maxOutputBytes, sideEffectScope, riskLevel, needsApproval}`,
`permission{permission, reason, riskLevel, sideEffectScope, needsApproval,
patternSources[], alwaysAllowPatternSources[], denyPriority}`,
`inputSchema`/`outputSchema` (+ runtime variants).

Computer-Use tool surface (zcode-cua plugin, per harness report-03):
`request_access, list_apps, list_windows, get_app_state, screenshot, zoom,
left_click, right_click, double_click, long_press, swipe, scroll, set_value,
open_application, switch_display`.

## 4. Icons [static]

ZCode's renderer uses **lucide-react per-icon chunks** (same library Universe
already ships, `lucide-react ^0.453.0`) — 1,713 icon chunks present in the
bundle, i.e. names must match **the current lucide names**, which include the
recently renamed ones:

| Old name (Universe uses) | ZCode bundle name |
|---|---|
| `bar-chart-3` | `chart-column` |
| `alert-triangle` | `triangle-alert` |
| `filter` | `funnel` |
| `more-horizontal` | `ellipsis` |

Per-surface import maps (chunk → icons it imports) are in
`docs/re-artifacts/renderer-facts.json`; the shared `styles` chunk carries
198 icons, `catalogTree` 22 (tree chrome), `palette`/`settings`/
`settings-2`/`terminal` re-export their single names. Full list:
`docs/re-artifacts/icon-inventory.json`.

## 5. Measured geometry (harness screenshots + live capture) [measured]

| Surface | Value |
|---|---|
| Title bar height | 48 px, divider `#2E2E2E` 1px at y=48 |
| Left sidebar | x 0…264 (265 wide), `#2B2B2B`, divider hairline at x=265 |
| Chat canvas | x 266…1598, `#161616` |
| Composer card | x 306…1207 (902 w), y 737…842 (≈105 h), fill `#2B2B2B`, radius ≈12, hairline `#414141` |
| Composer chips | **not filled pills**: transparent on card, faint `#7F7F7F`/`#555555` 1px border, `#D4D4D4` label |
| Send button | fill `#363636`, glyph `#D4D4D4` |
| Right rail card | left edge ≈1263, width ≈318–320, radius 8–10 |
| Command palette | `x545…1054` (510 w), top y≈78, radius ≈12, scrim `#090909` @36% |
| Settings sidebar | `#2B2B2B`, sections at ~33px pitch, selected row = `#414141` pill `x8…255` (248 w, ≈31 h) |
| Settings content cards | left edge x=510, width 830 |
| Accents | green `#46BF72` (Update pill), orange `#FF8A30` (Computer Use chip), transparency bar `#F5C518` |

## 6. Universe → ZCode rename table (Phase 2 execution contract)

Legend: `=` same semantics, `+` new tool to build, `~` Universe extra kept in
ZCode's PascalCase convention.

| Universe tool (current) | ZCode name | |
|---|---|---|
| `file.read` | `Read` | |
| `file.write` | `Write` | |
| `file.edit` | `Edit` | |
| `file.delete` | `Delete` | ~ |
| `file.list` | `LS` | ~ dir listing |
| *(none)* | `Grep` | **+** content search (ripgrep-backed) |
| *(none)* | `Glob` | **+** glob-pattern file search |
| `shell.exec` | `Bash` | |
| `shell_exec_unsandboxed` | `BashUnsandboxed` | ~ |
| `web_search` | `WebSearch` | |
| `web_fetch` | `WebFetch` | |
| `web_research` | `WebResearch` | ~ |
| `git.status` `git.diff` `git.log` `git.commit` `git.push` | `GitStatus` `GitDiff` `GitLog` `GitCommit` `GitPush` | |
| `docker.build` `docker.run` `docker.ps` | `DockerBuild` `DockerRun` `DockerPs` | |
| `memory_query` | `MemoryQuery` | ~ |
| `todo.write` | `TodoWrite` | |
| `todo.read` | `TodoRead` | |
| `spawn` | `Agent` | |
| `skill.search` `skill.read` `skill.activate` | `Skill` | consolidated single tool |
| `plan.submit` | `ExitPlanMode` | |
| `question.ask` | `AskUserQuestion` | |
| `cron.create` `cron.list` `cron.update` `cron.delete` | `CronCreate` `CronList` `CronUpdate` `CronDelete` | |
| *(none)* | `SendMessage` | **+** agent-to-agent message |
| `browser_navigate` … `browser_connect` | `BrowserNavigate` … `BrowserConnect` | |
| `capability_catalog` | `CapabilityCatalog` | ~ |
| `computer.*` (12) | `Screenshot` `LeftClick` `RightClick` `DoubleClick` `LongPress` `Swipe` `Scroll` `SetValue` `OpenApplication` `SwitchDisplay` `RequestAccess` `ListApps` `ListWindows` `GetAppState` `Zoom` | names from the CUA plugin list |
| `mcp_add_server` `mcp_list_servers` `mcp_remove_server` `mcp_call_tool` | `McpAddServer` `McpListServers` `McpRemoveServer` `McpCallTool` | |
| misc/data/vision/devops families (test_runner, code_analyzer, static_analysis, vuln_scanner, svg_gen, figma_mcp, sql, charting, ssh, ci_cd, pdf, docx, xlsx, pptx, http_client, openapi_parser, code_execute, coverage_report, archive, image_ocr, graphql_client, secret_scanner, dependency_audit, k8s, log_tail, pandas_query, data_validate, css_tools, color_contrast_check, vision_analyze) | PascalCase equivalents (`TestRunner`, `CodeAnalyzer`, …) | ~ no ZCode counterpart |
| 30 thirdparty connectors (`github_repo_info`, `slack_post_message`, …) | unchanged | they mirror external API operations, not ZCode core tools |

`EnterPlanMode` has no Universe tool counterpart — the permission-mode switch
itself covers it (Phase 3).

## 7. Icon-name constraint found during application (2026-10-19)

Universe ships `lucide-react ^0.453.0`, which does NOT export the newest
canonical names ZCode's bundle carries (`funnel` exists in ZCode, absent
here; Filter is the alias that does). The npm registry is unreachable from
this machine (ECONNRESET), so the dependency cannot be upgraded to ZCode's
version. Applied renames: `AlertTriangle→TriangleAlert`,
`BarChart3→ChartColumn`, `MoreHorizontal→Ellipsis`,
`Loader2→LoaderCircle`. `Filter` stays (same glyph, alias name) until the
network allows the upgrade — recorded here rather than guessed around.

## 8. Limitations / unknowns

- REA MCP/CLI (`npx rea-agents analyze-javascript-application`) could not
  run: the npm registry is unreachable from this machine (ECONNRESET). All
  extraction above is direct asar/zcode.cjs parsing instead — the Evidence
  ledger format is adapted, not the real tool's output.
- ZCode's exact i18n label table was not fully dumped (renderer chunk scan
  found 3 string chunks only); labels for Phase 5 come from the harness
  reports + measured pixels.
- Runtime behavior (what a mode actually gates) was not observed; Phase 3
  semantics are derived from the static enums + Universe's existing security
  model.
- `Task`/`ReadSessionContext`/`TaskOutput`/`TaskStop` exist in ZCode but
  Universe's swarm surface maps them onto goals/events; only `SendMessage` is
  built as a new tool in Phase 2.
