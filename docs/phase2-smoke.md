# Phase 2 — live smoke scenario (ZCode shell rebuild)

The DoD scenario for the Phase-2 UI shell, per the approved plan: a real
session with a local mock provider where palette opens, todos update, tool
rows show their event counter, consent shows, and plan mode blocks a
write.

## Part A — provider-free shell states (automated)

`scripts/ui_states_check.py` gained six Phase-2 states that need NO LLM —
they drive the REAL store ingestion path (`window.__universeTest`, the same
`ingestEvent` the WebSocket uses):

| state | verifies |
|---|---|
| `p2-topbar` | TopBar mounts, no dead window buttons in a plain browser, back/forward start disabled, Ctrl+B folds/unfolds the sidebar |
| `p2-palette` | Ctrl+K and `/` (empty composer) both open the palette; All/Actions/Tasks/Files tabs; the Recent/Suggested/Panels/Configure/Application sections |
| `p2-sidebar-nav` | Search/Automations/Marketplace rows, Search opens the palette, Group/Project view chips |
| `p2-question-banner` | an injected `question_required` renders options + free-text and round-trips an answer |
| `p2-progress-pane` | the palette's "Show Progress" opens the Rail's fourth tab with its honest empty state |
| `p2-composer-chips` | Switch mode (Full access/Plan/Ask), Max (Fast/Balanced/Max), attachments button enabled |

Run:

```bash
# terminal 1 — the bridge
python -m universe_desktop_bridge

# terminal 2 — the dev client (the __universeTest seam exists in DEV only)
cd universe-agent-ui && npm run dev:client:desktop

# terminal 3
python scripts/ui_states_check.py --json ui_states_phase2.json
```

The older states (`welcome`, `running`, `consent`, …) send real goals and
need a provider (env `UNIVERSE_TEST_BASE_URL` / `UNIVERSE_TEST_API_KEY` /
`UNIVERSE_TEST_MODEL`, or Settings → Model provider). Note two of them
(`running`, `reduced`) still watch the retired `.memory-pulse` canvas —
they are pre-Phase-2 drift and read as failures until re-pointed at
`.trailing-universe`; the six `p2-*` states are the Phase-2 contract.

## Part B — full-pipeline scenario (needs a provider)

With any provider connected (the mock-provider env vars above, or a real
key), walk the plan's DoD by hand in the Electron app
(`npm run desktop`):

1. **Palette** — Ctrl+K opens; "New task" (Ctrl+N) focuses the composer.
2. **A real session** — ask for something that plans + writes a file
   (e.g. *"create a short file named smoke_p2.md listing three nebulae,
   then review it"*).
3. **Consent** — the first `Write` raises the inline consent line
   above the composer; approve it.
4. **Tool rows** — while running, the Explore/Terminal/Changes groups
   fold consecutive calls, each head shows its events counter and the
   spinner/duration badge; the Hooks badge appears if a hook intercepts
   (hooks are configured on the `desktop.hooks[]` settings key — the
   Settings → Hooks page edits the rules and gates the whole layer with
   the real enable/disable switch (Phase 3); `PUT /api/settings` still
   works).
5. **Todo updates** — if the agent calls `TodoWrite`, the Rail's
   Progress tab shows the live `N/M` list (open via the palette →
   "Show Progress"); collapse to capsule and back.
6. **Plan mode** — Switch mode → Plan; send a session that tries to write a
   file: the write is refused with an honest blocked row, `ExitPlanMode`
   approval flips the mode back to Full and the chip follows live
   (`permission_mode_changed`).
7. **Queue behavior** — set `interaction_behavior: "queue"` (Settings →
   General → Interaction behavior, Phase 3 — or via `PUT /api/settings`),
   then send a
   follow-up while a session runs: the composer placeholder switches to
   "Keep typing to queue follow-up changes" and the queued count appears;
   the follow-up runs when the session ends.
8. **Context usage** — the bottom-bar meter appears once the first
   `context_usage` event lands (a session with 2+ rounds).
9. **Working-for** — while the session runs the assistant header shows
   "working for …", freezing to "worked for …" at the terminal event.
10. **Message rows** — user: Edit (branches + prefills) and Copy;
    assistant: Copy, Like/Dislike (rows land in `goal_feedback`), Fork,
    Hooks popover when intercepts exist.

## What "green" means here

- `p2-*` states all PASS in the JSON report.
- tsc + eslint + vite build green (CI gate).
- pytest bridge suites green (68 passed in the Phase-2 run:
  test_desktop_bridge, test_harness_foundations, test_events,
  test_automations, test_m5_bridge_contracts, test_task_threading).
