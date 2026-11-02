# ZCode ↔ Universe Agent — the parity table

The single source of truth for "is Universe exactly like ZCode?" — surface
by surface, with the evidence trail. Anything NOT listed here as parity
is documented as an intentional difference or out of scope; nothing is
silently missing.

References: reports 01–04
(`C:\Users\MKT\.zcode\workspace\default\zcode-harness-docs\`), the
measured palette (docs/zcode-tokens.md), the frozen event contract
(docs/events-v1.md), and the live-verification scripts
(`scripts/verify_universe_live.py`, `scripts/verify_phase_c_live.py`) whose
captures live in `docs/zcode-live-screens/`.

Legend: ✅ parity · 🟡 parity with a documented difference · ⛔ deliberately
different (a decision, not a gap) · phases reference the rebuild commits
(`c742001` P1 · `1fd2d7f` P2 · `3c3bd86` P3 · `9c8927d` B · `5f546d8` C ·
`2cfb4af` D).

## Shell chrome

| Surface | ZCode (evidence) | Universe | Status |
|---|---|---|---|
| Canvas | `#161616` (ref 01, live) | `--canvas #161616` | ✅ B, live-verified |
| Panels/sidebar/cards | `#2b2b2b` | `--ds-surface-1` | ✅ B, live-verified |
| Topbar over chat | `#262626` | `--tb-bg` | ✅ B, live-verified |
| Topbar over sidebar (two-tone) | `#2b2b2b` left segment | `.tb-left` | ✅ B, live-verified |
| Active pill / hover rows | `#414141` / `#363636` | `--ds-surface-3/2` | ✅ B |
| kbd chips | bg `#404040`, ink `#d4d4d4` | `.side-nav-k` | ✅ B |
| Card hairlines | `#414141` / `#4b4b4b` | `--ds-line(-strong)` | ✅ B |
| Ink scale | `#d4d4d4`→`#5e5e5e` | `--ds-ink…ink-4` | ✅ B |
| Accent (green) | `#46bf72` status only | `--ds-accent` | ✅ B |
| Permission chip (orange) | `#ff8a30` | Switch-mode chip | ✅ B |
| Font | Segoe system stack | same stack | ✅ B |
| WebGL universe in empty state | — (ZCode has none) | kept as the brand touch | ⛔ plan-approved identity |
| Old teal chrome | — | fully removed (0px live) | ✅ B |

## Sidebar / navigation

| Surface | ZCode | Universe | Status |
|---|---|---|---|
| New task row + Ctrl+N chip | first row | first row, wired | ✅ P2/C |
| Search row + Ctrl+K | palette | palette | ✅ P2 |
| Automations row | page | `/automations` page | ✅ C, live-verified |
| Marketplace row | page | deferred (documented) | ⛔ out of scope by plan |
| Session list / tasks | threads | real goals (SQLite) | ✅ P2 |
| Projects | — | projects CRUD | 🟡 Universe extra |

## Composer & chat

| Surface | ZCode | Universe | Status |
|---|---|---|---|
| Composer card + border | `#2b2b2b`/`#4b4b4b` | same | ✅ B |
| Send button (square, `#d4d4d4`) | ✓ | tri-state send/stop | ✅ B |
| "/" inline autocomplete | commands popup | popup: built-ins + custom | ✅ C, live-verified |
| Custom commands (user-defined) | files | `custom_commands[]` + expansion | 🟡 storage is settings, not files |
| Switch mode (plan/build/edit/yolo) | ✓ | PUT /api/permission-mode | ✅ P1/P2 |
| Max chip (thinking level) | ✓ | fast/balanced/max budgets | ✅ P2/P3 |
| Context usage meter | ✓ | `context_usage` events | ✅ P1 |
| Queued follow-ups (queue mode) | ✓ | steer/queue behavior | ✅ P1 |
| Ask-user structured questions | AskUserQuestion | ask_user + banner | ✅ P1 |
| Attachments "+ Add context" | ✓ | text file paths v1 | 🟡 v1 scope |

## Right rail

| Surface | ZCode | Universe | Status |
|---|---|---|---|
| Preview pane | file previews | text/md/img/audio/video/PDF | ✅ (M2) |
| Files pane | artifacts | real artifacts of finished goals | ✅ (M2) |
| Progress pane + capsule | live todos | `todo_updated` events | ✅ P1/P2 |
| Browser pane | screencast | browser tool live badge | 🟡 placeholder pane, live badge |
| Terminal pane | full PTY | one-shot WS over shell.exec | 🟡 no node-pty (plan rule), cwd protocol |
| Ctrl+J terminal toggle | ✓ | key + TopBar button + palette item | ✅ C (see note 1) |

## Pages / Settings

| Surface | ZCode | Universe | Status |
|---|---|---|---|
| Settings layout (left sections) | Linear style | `sx-*` | ✅ P3 |
| Automations page | list + switches + form | full CRUD over real scheduler | ✅ C, live-verified |
| Skills page | inventory + toggles | real L4 corpus + v21 enabled | ✅ C |
| Commands page | custom commands | CRUD + composer integration | ✅ C |
| Model settings / providers | ✓ | full provider CRUD + keys | ✅ P3 |
| MCP Servers | ✓ | all transports CRUD | ✅ P3 |
| Hooks page | rules editor | hooks[] + kill switch | ✅ P1/P3 |
| Memory page | ✓ | live counts | ✅ P3 |
| Subagents | ✓ | read-only roster | ✅ P3 |
| Voice | — | full voice channel | 🟡 Universe extra |
| Plugins / Marketplace | ✓ | deferred | ⛔ out of scope by plan |
| Usage stats / Indexing | ✓ | honest placeholders | ⛔ later phase |

## Harness internals (report-03/04)

| Surface | ZCode | Universe | Status |
|---|---|---|---|
| Todos | TodoWrite/Read | `todo.write/read` + Progress pane | ✅ P1 |
| Hooks (GateGuard-style) | pre/post tool | HookManager + rules | ✅ P1/P3 |
| Permission modes | plan/build/edit/yolo | the ZCode four, alias-normalized
(acceptEdits→edit, bypassPermissions→yolo), enforced by the gate | ✅ P1 |
| ask_user | structured options | question_required/decided | ✅ P1 |
| Follow-up queue | queue | steer_queued | ✅ P1 |
| Automations (cron) | CronCreate/… | cron.* tools + scheduler + page | ✅ P1/C |
| Transparency bar | yellow "…controlling" | TransparencyBar `#f5c518` | ✅ B |
| Consent gate | per-call approvals | consent auto/per_turn/explicit | ✅ (pre-P1) |
| Computer-use broker | cua-helper NDJSON | universe_cua_helper NDJSON | ✅ D |
| Frame binding | frame_id TTL | 15s TTL, out-of-frame rejected | ✅ D |
| a11y-first set_value | AXPress/ValuePattern | UIA ValuePattern | ✅ D |
| Session memory (persistent files) | memories + MEMORY.md | Universe memory layers L1–L5 | 🟡 different model, both real |
| Background tasks | run_in_background | shell.run_background | ✅ P1-era |
| Context auto-compact | summarize window | honest meter, no auto-compact | 🟡 later phase |

## Notes

1. **Keyboard-shortcut verification on the dev machine**: synthetic
   Ctrl+combos are eaten by an injected-input filter on this machine
   (Ctrl+K/B/J/A/C probes all failed while plain keys arrive — see
   `synthetic-input-limits` in the session memory). The Ctrl+J path is
   verified through the TopBar button, which runs the IDENTICAL store
   toggle; the key handler is the same three lines.
2. **The two shell layout bugs fixed in C** (the 38px topbar-offset
   overflow and the rail flex oscillation freeze) were pre-existing
   shell issues surfaced by the terminal pane — both fixed in
   `5f546d8` with regression notes in the commit.
3. **Zero-event-contract churn**: Phases B–D added no new BridgeEvent
   types; the v1 contract (docs/events-v1.md) stays additive-only.


## ZCode reverse-engineering pass (2026-10-19)

Static RE of the installed ZCode 3.14.3 (app.asar + glm/zcode.cjs), evidence
in docs/zcode-re-map.md:

| Surface | ZCode | Universe | Status |
|---|---|---|---|
| Tool names | Read/Write/Edit/Bash/Grep/Glob/TodoWrite/Agent/Skill/ExitPlanMode/AskUserQuestion/Cron*/SendMessage + the CUA set | identical names in the registry (117 tools renamed), Grep/Glob/SendMessage built new | ✅ |
| Slash commands | 20 built-ins + .md files (`$ARGUMENTS`, `:` names, `compress` reserved) | same registry over `GET /api/commands` + `/api/commands/resolve`, composer autocomplete rebuilt on it | ✅ (Universe-only entries keep their actions; ZCode-only ones are honest placeholders) |
| Permission modes | plan/build/edit/yolo (+auto) | identical + alias table (full→yolo, ask→build) | ✅ |
| Icons | lucide canonical names | renames applied (TriangleAlert, ChartColumn, Ellipsis, LoaderCircle); `funnel` needs the newer lucide the registry can't fetch — Filter alias kept | ⚠️ |
| UI strings/labels | i18n table + harness reports | measured tokens already in docs/zcode-tokens.md; geometry fixes pending Phase-5 pixel pass | ⏳ |
