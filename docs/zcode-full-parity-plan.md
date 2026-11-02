# ZCode Full-Parity Plan — make Universe's UI indistinguishable from ZCode's

> Written 2026-10-21 (Shurukon). Status: **EXECUTED (chat-timeline + status scope) — 2026-10-22.**
> Baseline switched to ZCode **3.14.3.7762** (installed 3.11.2 was purged mid-run;
> user approved proceeding on 3.14.3 — see Execution log P1).
> Method: REA playbook (`reverse-engineer-anything` skill). Target routed as
> **ASAR/JavaScript tree** → repo `scripts/re/` static asar extraction +
> targeted member reads of `C:\Program Files\ZCode\resources\app.asar`
> (read-only). All static claims carry an Evidence ID (`E##`); runtime
> behavior was NOT observed and is marked accordingly.
>
> Execution log (2026-10-22) — what ACTUALLY shipped (seam note: the P2/P3
> component names below (MessageStreamTimeline/ToolCallRow/StatusBarPanel)
> were implemented under their final names ZTList/rows + StatusPanel in
> `components/flow/ZCodeFlow.tsx` + `lib/flow/ztimeline.ts`:
> - **P1 fold** `lib/flow/ztimeline.ts`: the ZCode fold (rows, tool families,
>   ZCode duration strings incl. "a few seconds"/"under a second", agent
>   branches, context/compaction rows). Smoke-tested standalone (28/28).
>   Real bug found+fixed: agent_complete must honor success=false.
> - **P2 UI** `components/flow/ZCodeFlow.tsx` (rows + StatusPanel + zcode-flow.css
>   with E15 tokens #161616/#2b2b2b/#414141/#d4d4d4/#999/#ff8a30); AgentFlow.tsx +
>   StatusStrip.tsx rewritten IN PLACE (same export signatures; ChatMessages
>   integration untouched). Stop wired to cancelGoal(goalId).
> - **P5** dead loaders removed from `lib/universe/generate.ts`; `memory_view.py`
>   docstrings made honest; `resources/router.py` trimmed (route() dead code);
>   `context/file_select.py` git-rm'd (zero importers); `cli/repl.py` fixed
>   (tests/test_repl_slash.py 3 passed); voice.py docstring corrected.
> - **Verification** P6 executed LIVE with ZERO cost: new bridge dev route
>   POST /api/dev/synthesize (env-gated UNIVERSE_DEV_SIM=1) streams a scripted
>   event timeline for a synthetic session; session rows persisted as real
>   kind='task' DB rows so the sidebar/history behave like real runs.
>   Live-captured on the real desktop window (screenshots): full timeline
>   (thought/tool/terminal rows with ZCode duration strings, SubAgent branch
>   with failure styling, artifact chip, "Worked for …s" badge, done state,
>   footer stats), right-rail status panel (plan + agents + tools + files +
>   recap "done 16s"), LIVE run state (composer steer mode, live context
>   meter 21,456/200,000 fed by context_usage, stop button). Recap duration
>   decimal fixed during pass (ActivityPane → whole seconds).
> - **Bugs fixed in the pass**: thinking_level contract restored (GoalRequest
>   pattern fast|balanced|max + settings guard/default aligned to the
>   Settings picker vocabulary — the picker had silently never persisted);
>   goal_detail bridge branch now returns result fields (reply/conversation/
>   success/error/steps/artifacts) for non-DB goals so finalize renders the
>   answer; deep-link `--session=<id>` second-instance handler added to
>   electron/main.ts (tooling hook; the row-click path verified as the
>   reliable open route).
> - **NOT done (tracked, not silently skipped)**: P0 i18n/rail/token extractor
>   scripts, P3 full-chrome rebuild (sidebar/ProjectBar/Settings/icons/fonts),
>   P4's deletion sweep + spacing EXACT deletion list pending user approval,
>   e2e data-testid updates, parity_diff.py harness, zh labels table.
>   The chat timeline + status surfaces — the user's core ask — are done and
>   live-verified.

---

## 1. Session

Universe Agent's desktop UI must become the **same UI** as ZCode's desktop
app — pixel-level chrome, identical status updates, identical timeline,
identical labels — to the point the user cannot tell them apart.
Scope decisions encoded here:

- **Replace** Universe's status updates + timeline UI with ZCode's exact
  equivalents (delete Universe's variants; ZCode's surfaces win).
- **Remove** everything extra beyond ZCode's chrome; **add** everything
  missing.
- Bugs found in the 2026-10-21 full scan are fixed in the same campaign.

## 2. Target identity (resolved)

| Fact | Value | Evidence |
|---|---|---|
| Installed app | `C:\Program Files\ZCode\ZCode.exe` | FS |
| **Real installed version** | **3.11.2, build 6792** (`FileVersion`/`ProductVersion`) | E01 |
| Registry uninstall entry | `ZCode 3.11.2` (consistent) | E01 |
| Pending update (NOT installed) | `@zcodedesktop-updater\pending\ZCode-3.14.3-win-x64.exe` (178 MB) + `update-info.json` | E02 |
| Previous RE note said "3.14.3" | **Wrong label** — version was taken from the pending installer's filename; asar target is 3.11.2. `docs/zcode-re-map.md` header gets a correction note in P1. | E01/E02 |

> **Decision point for the user:** RE against installed **3.11.2** (default),
> or apply the pending 3.14.3 installer first and RE the newer build.
> Applying updates to the user's machine requires explicit approval.

## 3. Evidence ledger (already extracted, static)

| ID | What | Where |
|---|---|---|
| E01 | exe version 3.11.2.6792 | `VersionInfo` of ZCode.exe |
| E02 | pending 3.14.3 installer + sha512 | `%LOCALAPPDATA%\@zcodedesktop-updater\pending\update-info.json` |
| E03 | asar inventory: 27,281 files, `/out/*`, node_modules | `docs/re-artifacts/asar-inventory.json` |
| E04 | slash registry (20 commands, verbatim summaries/usages) | `docs/re-artifacts/zcode-facts.json` (extraction: `glm/zcode.cjs` 12.6 MB) |
| E05 | tool surface + metadata shape; permission enum `plan/build/edit/yolo`(+`auto`, aliases incl. `acceptEdits`,`dontAsk`,`bypassPermissions`) | E04 + zcode.cjs |
| E06 | 1,713 lucide icon chunks + per-surface import maps + rename table (chart-column, triangle-alert, ellipsis; `funnel` blocked by old lucide — npm now reachable, upgrade allowed) | `docs/re-artifacts/icon-inventory.json`, `renderer-facts.json`; re-map §4/§7 |
| E07 | session-event state machine: `agent_thought_chunk → reasoningStep`; `agent_message_chunk → generationStep` (skipped when event has `zcodeTimeline` or `parentToolUseId`); `tool_call(+_update) → toolStepsById`, phase→`completed/failed/in_progress`; terminal states `completed,failed,interrupted`; permission waits → `waitingMs` | styles-DyAcaLKy.js @530k-577k |
| E08 | synthetic timeline entry shape `{version:1, kind:"synthetic", type:"context_compaction", operationId, status, trigger, display:"separator", reason, summaryMessageId, preCompactTokenCount, postCompactTokenCount}` | styles chunk @576,607 |
| E09 | tool-call row DOM hooks: `data-tool-call-id`, `data-tool-name`, `data-status`, `data-zcode-tool-stream-animate`; kind labels `chat.toolCall.kind.write/delete/edit` + progressive `edit.writing/editing/deleting`; `status.failed`; `source.subAgent`; expand/collapse ARIA; multi-file count; snapshot previewBytes/fullBytes | styles chunk @2.06M, 1.83-1.94M |
| E10 | reasoning row markup: "Thought · Lasted {seconds}s" (`chat.reasoning.thought`, `durationFewSeconds`, `durationSeconds`), classes `group/reasoning inline-flex max-…` | styles chunk @2.12M + IntlProvider |
| E11 | status panel sections + labels (`chat.statusPanel.*`): environment="Git tools", changes=Changes, branch, commitPush="Commit / Push", clean=Clean, session=Session (with `data-session-elapsed-seconds`), sessionPlans=Plans (+openPlan), todo=Progress with folds `{count} completed/earlier/later/waiting`, terminals, agents, runningStatusValue="{count} in background", runningAgentsValue="{count} running", endedAgents, `data-status-section[-trigger]`, `data-status-todo-preview-*`, layout variants (none/mini) | styles chunk @1.79-1.82M + IntlProvider @194k/505k |
| E12 | subagent directory statuses: running/waiting/blocked/success/failed/cancelled/lost | IntlProvider chunk |
| E13 | sidebar/labels: workspaces="Tasks", archivedTasks, organize view (Grouped / By project / **Timeline** `organizeChronologicalList`), sort by created/updated, search tasks, show/hide/resize sidebar, SSH connect, remove-running-workspace confirm | IntlProvider `workspaceSidebar.*` |
| E14 | settings nav: title+breadcrumb+subtitle "Switch major settings…", appearance (theme mode incl. `zai-dark`, UI font size, code font size, preview light/dark), usageTitle "Usage stats", modelProviderTitle "Model settings", mcpTitle "MCP Servers" | IntlProvider `settings.*` |
| E15 | measured geometry + palette (titlebar 48px, sidebar 265px, canvas #161616, panels #2b2b2b, topbar #262626, pills #414141, rows #363636, ink #d4d4d4/#999/#909090/#5e5e5e, green #46bf72, orange #ff8a30, radii, scrim) | `docs/zcode-tokens.md` + re-map §5 (live-verified 2026-10-08/09) |
| E16 | Session card fields incl. `status` from live projection, lastError, mode, model, thoughtLevel, provider `glm`, runtimeEpoch, migrationSource | styles chunk @310k |
| E17 | prior E2E logs: full suite 463 passed/26 skipped (2026-10-07), MCP re-runs green, Electron dev session healthy 2026-10-20 | `p3_*.log`, `.electron_manual.log` |
| E18 | asar unpacked-native integrity: `node_modules/node-pty/prebuilds/win32-x64/conpty.node` declared sha256 ≠ calculated (packaging tools commonly sign/strip unpacked natives post-write) → REA run uses `--integrity-policy record-and-continue`; native bytes treated untrusted | rea-agents 6.7.0 CLI, 2026-10-21 |

**Inference (labeled):** ZCode's "timeline" the user refers to = the chat
message-stream event timeline (reasoning/generation/tool rows + synthetic
compaction separators) + the collapsible status panel over the conversation.
Static evidence for both is strong (E07-E12); their exact runtime rendering
was not observed — P6 verification covers that gap.

**Unknowns to close in P0:** right-rail tab inventory (Universe guesses
Activity/Preview/Browser/Files/Progress/Terminal — must be verified from
chunks), full i18n dump to a table Universe can import, exact CSS variable
names ZCode uses (`--color-*` seen inline in E09/E11; full set needs
extraction), window preferred size/default zoom, catalogTree semantics.

## 4. Campaign phases (execution order)

### P0 — Evidence completion (read-only, REA-led)
- [x] REA CLI fallback acquired: `rea-agents 6.7.0` isolated install at
      `D:\ClaudeDesktop\Temp\opencode\rea` (npx cache entry was corrupt:
      ERR_MODULE_NOT_FOUND @clack/prompts; npm Sam stub via system32 — use
      full `npm.cmd` path, lesson recorded).
- [ ] REA full-asar run did NOT complete: attempt-3 (`--integrity-policy
      record-and-continue`) ran 26+ min then died with the shell timeout —
      27k-module/307 MB asar is beyond practical single-pass budget here.
      **Fix path:** extract only `/out/**` + `package.json` to a temp tree
      (`npx asar extract-file` loop or node script over the inventory) and
      run `analyze-javascript-application` on the smaller tree, then merge
      with the repo's already-built `asar-inventory.json` (E03) which
      already covers the full file set.
- [ ] `scripts/re/extract_i18n.py`: dump `IntlProvider-*` label table (en+zh)
      to `docs/zcode-parity-spec/i18n-en.json`, used verbatim by the client.
- [ ] `scripts/re/extract_rail.py`: identify right-rail tabs + their chunks
      (targeted reads of rail-ish chunks + `data-rail*`/tab labels).
- [ ] `scripts/re/extract_tokens.py`: mine inline `--color-*` /
      `text-ui-*`/`bg-*` token usage from the styles chunk →
      `docs/zcode-parity-spec/zcode-semantics.css` (class-string parity map).
- [ ] Update `docs/zcode-re-map.md` header: version correction 3.11.2.6792,
      REA evidence note.
- Optional (needs approval, skip if CLI lands): REA MCP registration via
  `rea-agents setup --dry-run` review flow.

### P1 — Data model parity (bridge + reducer)
- [ ] Extend/reshape Universe's event stream to ZCode's model without touching
      the security posture:
      - Session events → `timeline` entries with `status` lifecycle
        (`in_progress → completed/failed/interrupted`), `waitingMs`
        (permission-wait durations — Universe has consents today; measure
        requestedAt→decidedAt), `parentToolUseId` for swarm children
        (Agent tool sub-steps nest exactly as ZCode's subagent nesting),
        `zcodeTimeline` flag semantics suppressed-message markers.
      - Emit synthetic `context_compaction` entries from Universe's compaction
        step (core/context/compaction.py) with `display:"separator"`,
        pre/post token counts (Universe already tracks 32k budget).
      - Session states: map Universe's session lifecycle to ZCode's enumerated
        `idle/running/waiting/paused` (+ terminal set E07).
- [ ] Replace `lib/flow/fold.ts` (18.1 KB custom isolation) with a
      `zcodeTimelineReducer` implementing E07/E08 exactly; keep the bridge
      WS contract (no auth changes). Deliver
      `client/src/lib/zcode/timeline.ts` + tests, including exact field
      names (toolStepsById, reasoningStep, generationStep, usage tokens).
- [ ] Delete in plan: `lib/flow/{fold,agentTint,railStore}` after the new
      reducer ships (git archives them; listed for P4 approval).

### P2 — Timeline + status UI replacement (the user's core ask)
New components under `components/zcode/` (names mirror ZCode semantics):
- [ ] `MessageStreamTimeline.tsx` — in-chat timeline: reasoning rows
      (**"Thought · Lasted {seconds}s"**, E10 class strings verbatim),
      generation rows, tool-call rows (E09: `data-tool-call-id/name/status`,
      animate flag, kind+status labels, subAgent source tag, expand/collapse,
      copy error), synthetic compaction separators (E08).
- [ ] `ToolCallRow.tsx` with ZCode's exact folds (multi-file chips
      `edit.multipleFiles`, snapshot notice, planFile chip, progressive
      "Writing/Deleting/Editing" labels).
- [ ] `StatusBarPanel.tsx` — collapsible status sections over the
      conversation (E11): Environment/Git tools row (branch, changes
      +/-, Commit/Push, Clean), Session (elapsed seconds live counter),
      Plans (open plan link), Progress (todo folds with exact count labels),
      Terminals, Agents ({count} in background / running / Ended).
- [ ] Status updates coin flip: **Universe old statuses die here** —
      `StatusStrip.tsx` (universe-orb strip), `AgentFlow.tsx` brain/tool rows,
      `ActivityPane.tsx`, `ProgressPane.tsx`, `TrailingUniverse.tsx` in the
      chat row context, `flow.css` — removed/replaced; the strip's
      provider-halt honesty (D8 red bar) is preserved inside the new
      status panel's session section.
- [ ] Labels verbatim from the i18n dump (E11-E14); en + zh tables shipped;
      `chat.toolCall.*`, `chat.reasoning.*`, `chat.statusPanel.*`, 
      `subagentDirectory.status.*` namespaces.
- [ ] `data-testid` mirrors (`Tte()` computed ids) so Universe's e2e can assert
      ZCode-identical markup.

### P3 — Chrome parity completion
- [ ] Tokens: exact re-map v2 from P0 extraction; single token file;
      delete the 4-layer cascade (`design-system.css` → `desktop.css` →
      `redesign.css` → `zcode-parity.css` + `settings-linear.css` +
      `shell.css`) → one `zcode-tokens.css` + per-surface files.
- [ ] Sidebar rebuild to E13: Tasks/Archived groups, View menu
      (Grouped / By project / Timeline sort), search-in-sidebar, SSH entry
      detail, resize handle behavior, empty/error states text (E13 verbatim).
- [ ] ProjectBar, TopBar (48px, #262626), Composer (chips transparent +
      hairline, square send button #d4d4d4 glyph, radius 12, hairline
      #414141/#4b4b4b), palette (510w, scrim 36%) — measured §5 numbers as
      Tailwind arbitrary values.
- [ ] Settings rebuild to E14: nav sections (Appearance/General/Usage/
      Model settings/MCP Servers/…), `zai-dark` theme mode naming,
      UI/code font-size settings, preview light/dark cards — keep Universe's
      provider/keys implementations behind the new shell (ZCode layout).
- [ ] Icons: `npm i lucide-react@latest` — registry now reachable;
      then apply ZCode names (`funnel` etc.) and prune unused per E06.
- [ ] Fonts: `Segoe UI` system stack @13px UI (E15); mono stays JetBrains
      (no ZCode bundled mono observed).
- [ ] app://universe protocol + window titlebar: keep Electron sandbox posture
      (main.ts unchanged except optional `--zai-dark` naming), `background`
      #161616 (already).

### P4 — Removal sweep ("delete everything extra / add everything missing")
Explicit list (all git-tracked, still needs `git rm` at execution):
- [ ] CSS layer-cake files replaced (P3) — remove from tree.
- [ ] Old flow/timeline/status components (P2) — remove.
- [ ] Universe-only chrome ZCode lacks, user said remove: 3D memory-universe
      visuals OUT of the chat/marketing chrome — the mini orb
      (`TrailingUniverse`), the universe-glm WebChat canvas inside the
      MainActivity — decision: **hide from chat entirely**; keep the full
      3D universe ONLY if user wants it as an optional view (question Q3).
- [ ] `memory_view.py` static `universe-memory.json` feed → live endpoint
      (bugfix doubles here).
- [ ] `voice.py` docstring stale openwakeword claim → correct text (trivial).
- [ ] Dead code: `resources/router.py:route()`, `context/file_select.py`,
      `core/agent/phase_contract.py` retired specialist names — annotate or
      delete (per-file confirmation).
- [ ] Repo-root hygiene: ~55 stray `p3_*.log`, `shot.png`, `payload.txt`,
      `_headless_edge.png` etc. → move to `docs/re-artifacts/` or delete
      (delete list requires approval).
- [ ] Registry `3.11.2` vs docs claim note (P0 covered).

### P5 — Bug-fix batch (from full scan 2026-10-21)
- [ ] **`cli/repl.py:145`** — `cmd = get_command(cmd_name)`: NameError → use
      `name`; keep the 146-148 retry for base-command fallback; add a
      regression test in `tests/test_repl_slash.py` covering a direct slash
      invoke through `_handle_slash` (currently untested — gap noted).
- [ ] **`memory_view.py`** — serve the 3D page from live bridge state
      instead of static `universe-memory.json` (reuses P4 endpoint move).
- [ ] **Providers** — verify `resources/router.py` usage against
      `providers/client.py:245`; remove router.route() if truly uncalled.
- [ ] Optional quality: split `pages/Settings.tsx` (3,042 ln monolith)
      during the P3 rebuild — natural occasion, tracked as its own task.

### P6 — Verification (definition of done)
1. `npm run check` (tsc+eslint) + `npm run build` zero errors.
2. `pytest` full suite ≥ previous 463 green baseline (E17), plus new
   timeline reducer tests.
3. **Pixel parity harness** `scripts/verify/parity_diff.py`: drive both
   windows (ZCode + Universe, not minimized; **window rect > -1000 guard per
   lessons**) at 1440×900, capture identical regions, report mismatches
   channel-by-channel; target palette/geometry exact match; repeat with
   live run (session executing) for the timeline/status renders.
4. e2e scripts (`client/tests/e2e/`) re-run: onboarding, settings_v2,
   sidebar_workspace, wizard_v2_live — updated to ZCode-identical
   data-testids.
5. Manual acceptance: user opens both apps, side-by-side, "cannot tell
   which is which" on: main window, settings, palette, running timeline,
   status panel, sidebar, composer.

## 5. Deliverables map
```
docs/zcode-parity-spec/          — i18n-en.json, zcode-semantics.css, rail.md, corrections
client/src/lib/zcode/            — timeline.ts (reducer), labels.ts (imported i18n)
client/src/components/zcode/     — MessageStreamTimeline, ToolCallRow, ReasoningRow,
                                   CompactionSeparator, StatusBarPanel (+ css single file)
client/src/components/desktop/   — Slimmed chrome wired to the above; old flow files removed
scripts/re/extract_{i18n,rail,tokens}.py, scripts/verify/parity_diff.py
docs/zcode-full-parity-plan.md   — this file; execution log appended per phase
```

## 6. Decision points requiring the user (blocking)
1. **ZCode version basis** — RE against installed 3.11.2.6792 (default) or
   apply pending 3.14.3 installer first?
2. **3D universe fate** — hide it from the chat timeline entirely (ZCode
   parity as demanded) but keep as optional full-screen view, or remove
   `universe-glm/` codebase altogether?
3. **Deletion approvals** — P4's explicit remove list (CSS/old components/
   stray logs/dead modules) — approve at execution as one ticket.
4. **REA MCP** — skip if CLI evidence lands; else approve scoped setup.

## 7. Risks
- Static-only runtime inference for stream semantics (mitigation: P6 live
  verification + honest labels).
- npm packaging history of ECONNRESET (mitigation: pin versions in lockfile;
  vendor the extracted spec JSON so builds don't re-fetch).
- Minified identifier churn across versions — extraction scripts pin to
  intact string literals (i18n keys, data-* attributes: stable API surface).
- Universe bridge/auth constants stay unchanged (security posture intact).
