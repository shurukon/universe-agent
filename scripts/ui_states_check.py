"""P8 visual+DOM verification of every UI state, on the HTML layer.

Runs against the live dev server (5199) + bridge (8738). My reviewer
cannot view images, so every state is verified programmatically:
DOM assertions, computed styles, pixel sampling of the canvases, console
error collection, and focus checks.

States: welcome · running (galactic impression) · consent modal ·
        offline banner · goal failed · voice buttons · universe page ·
        reduced motion · a11y/contrast spot checks.

The running/consent/reduced-motion states send real goals, so the bridge
needs a real provider configured (Settings → Model provider, or
UNIVERSE_TEST_BASE_URL / UNIVERSE_TEST_API_KEY / UNIVERSE_TEST_MODEL env when
you start it). Without one those states become the honest no-provider
rejection — run this after connecting a provider.

Usage:  python scripts/ui_states_check.py [--json out.json]
"""
from __future__ import annotations

import json
import subprocess
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5199"
BRIDGE = "http://127.0.0.1:8738"
OUT: dict[str, dict] = {}
CONSOLE_ERRORS: list[str] = []


# optional state filter: `--only p2-` runs just the Phase-2 states
ONLY = ""
if "--only" in sys.argv:
    ONLY = sys.argv[sys.argv.index("--only") + 1]


def check(page, state: str, fn) -> None:
    """Run one state's assertions; record pass/fail + notes."""
    if ONLY and not state.startswith(ONLY):
        return
    notes: list[str] = []
    try:
        fn(page, notes)
        OUT[state] = {"ok": True, "notes": notes}
        print(f"PASS {state}")
    except AssertionError as e:
        OUT[state] = {"ok": False, "notes": notes, "error": str(e)[:300]}
        print(f"FAIL {state}: {e}")
    for n in notes:
        print(f"     · {n}")


def bridge_pids() -> set[str]:
    """PIDs listening on 8738 — parsed in python (shell pipes are cmd.exe)."""
    import subprocess
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    pids = set()
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 5 and parts[1].endswith(":8738") and parts[3] == "LISTENING":
            pids.add(parts[4])
    return pids


def bridge_restart() -> None:
    import subprocess
    subprocess.Popen("python -m universe_desktop_bridge", shell=True,
                     cwd="D:/Universecomputer/Universe-compter",
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def snap_console(page) -> None:
    page.on("console", lambda m: CONSOLE_ERRORS.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: CONSOLE_ERRORS.append(f"pageerror: {e}"))


def ensure_idle(page) -> None:
    """Cancel any running goal so the Send button (not Stop) is back.
    Tolerates orphaned tasks (bridge restart) — those cancel to a
    finalized state now, so the composer recovers either way."""
    for _ in range(3):
        stop = page.locator("button[aria-label='Stop the running goal']")
        if not stop.count():
            return
        stop.first.click()
        try:
            page.wait_for_function(
                "() => !document.querySelector("
                "\"button[aria-label='Stop the running goal']\")", timeout=30000)
            page.wait_for_timeout(600)
        except Exception:
            continue


def pixel_churn(shot1: bytes, shot2: bytes) -> int:
    """Count differing bytes between two page screenshots (rough churn)."""
    if len(shot1) != len(shot2):
        return abs(len(shot1) - len(shot2))
    return sum(a != b for a, b in zip(shot1, shot2))


def provider_ready() -> bool:
    """True when the bridge has a real LLM provider configured."""
    import httpx
    try:
        r = httpx.get(f"{BRIDGE}/api/providers", timeout=5)
        return bool(r.json().get("has_provider"))
    except Exception:
        return False


def main() -> int:
    if not provider_ready():
        print("NOTE: bridge has no LLM provider — the goal-driven states")
        print("(running / consent / reduced-motion) will hit the honest")
        print("no-provider rejection. Connect one and re-run for the full pass.")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream",
                                          "--use-fake-device-for-media-stream"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        snap_console(page)
        page.goto(BASE, wait_until="networkidle")

        # ---------- welcome -------------------------------------------------
        def welcome(page, notes):
            page.goto(BASE, wait_until="networkidle")
            ta = page.locator("textarea[aria-label='Goal input']")
            ta.wait_for(timeout=8000)
            assert ta.is_visible(), "composer textarea visible"
            txt = page.locator(".welcome h1").first
            if txt.count():
                notes.append(f"greeting: {txt.inner_text()[:60]!r}")
            starters = page.locator(".starter").count()
            notes.append(f"starter cards: {starters}")
            assert starters >= 2, "starter suggestions present"
            # sidebar + topbar + starfield canvas
            assert page.locator("canvas#stars").count() == 1, "starfield canvas"
            # identity check on the token that paints every surface
            bg = page.evaluate(
                "getComputedStyle(document.documentElement)"
                ".getPropertyValue('--background').trim()")
            notes.append(f"--background token {bg!r}")
            assert "0b0d11" in bg.lower(), f"near-black identity token, got {bg}"
            # composer present and honest: Send (idle) or Stop (a goal carried
            # over from a previous session is legitimately still running)
            has_send = page.locator("button[aria-label='Send goal']").count()
            has_stop = page.locator("button[aria-label='Stop the running goal']").count()
            assert bool(has_send or has_stop), "composer action button present"
        check(page, "welcome", welcome)

        # ---------- running: the galactic impression ------------------------
        def running(page, notes):
            page.goto(BASE, wait_until="networkidle")
            ta = page.locator("textarea[aria-label='Goal input']")
            ta.wait_for(timeout=8000)
            ensure_idle(page)
            # long enough that the goal is still working while we sample:
            # research + write keeps several agents busy for a while
            ta.fill("create a file named p8_probe.md explaining how "
                    "galaxies form, then review it")
            page.locator("button[aria-label='Send goal']").click()
            # wait for the running card / mini-universe to appear
            page.wait_for_selector(".msg-agent", timeout=10000)
            # this goal writes a file → the consent modal will pop mid-run;
            # approve it like the user would so the universe keeps working
            modal = page.locator(".modal-root")
            if modal.count() and modal.is_visible():
                modal.locator("button.cm-btn.ok").click()
                notes.append("consent approved mid-run (per_turn file.write)")
            page.wait_for_selector(".memory-pulse canvas", timeout=15000)
            page.wait_for_timeout(1200)  # let agents + tools fire
            pulse = page.locator(".memory-pulse canvas").count()
            notes.append(f"MemoryPulse canvas present: {bool(pulse)}")
            assert pulse, "mini-universe (thinking indicator) mounted while running"
            # pixel churn proof: the universe is ALIVE, not a static image
            c1 = page.locator(".memory-pulse canvas").first.screenshot()
            page.wait_for_timeout(500)
            c2 = page.locator(".memory-pulse canvas").first.screenshot()
            churn = pixel_churn(c1, c2)
            notes.append(f"pulse pixel churn over 500ms: {churn}")
            assert churn > 200, f"universe is animating (churn={churn})"
            # constellation line + aria-live narration
            assert page.locator(".event-constellation").count() >= 1, "event constellation"
            live = page.locator("[aria-live='polite']").count()
            notes.append(f"aria-live regions: {live}")
            assert live >= 1, "screen reader narration present"
            # stop button state while running
            stop = page.locator("button[aria-label='Stop the running goal']")
            if stop.count():
                assert stop.first.is_enabled(), "stop enabled while running"
                notes.append("stop button live")
        check(page, "running", running)

        # ---------- consent modal (forced via capability gate) --------------
        def consent(page, notes):
            # file.write policy is per_turn: the first write of a goal always
            # prompts — the modal is the gate, no bypass in the UI
            page.goto(BASE, wait_until="networkidle")
            ta = page.locator("textarea[aria-label='Goal input']")
            ta.wait_for(timeout=8000)
            ensure_idle(page)
            ta.fill("create a file named consent_probe.py containing print('ok')")
            page.locator("button[aria-label='Send goal']").click()
            try:
                page.wait_for_selector(".modal-root", timeout=30000)
                m = page.locator(".modal-root")
                assert m.get_attribute("role") == "alertdialog", "alertdialog role"
                assert m.get_attribute("aria-modal") == "true", "aria-modal"
                body = m.locator(".cm-body").inner_text()
                notes.append(f"modal body: {body[:80]!r}")
                assert "file" in body.lower(), "names the requesting tool"
                # Escape denies — the modal must close
                page.keyboard.press("Escape")
                page.wait_for_selector(".modal-root", state="detached", timeout=8000)
                notes.append("Escape → denied, modal closed")
                # denial surfaces — a terminal flag appears, not silence
                page.wait_for_selector(".a-flag", timeout=150000)
                flags = page.locator(".a-flag").all_inner_texts()
                notes.append(f"outcome flags after deny: {flags[-1:]}")
                page.wait_for_timeout(400)
            except Exception as e:
                raise AssertionError(str(e))
        check(page, "consent", consent)

        # ---------- offline banner (kill the bridge) ------------------------
        def offline(page, notes):
            pids = bridge_pids()
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, text=True)
            notes.append(f"killed bridge pids {pids}")
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_selector(".offbanner.off", timeout=15000)
            banner = page.locator(".offbanner.off")
            assert banner.is_visible(), "offline banner visible"
            txt = banner.inner_text()
            notes.append(f"banner: {txt!r}")
            # no silent failure: a goal attempted against the dead bridge
            # becomes a visible failed task (or the composer is disabled —
            # either is an honest path, a silent no-op is not)
            ta = page.locator("textarea[aria-label='Goal input']")
            ta.wait_for(timeout=8000)
            ta.fill("prove the offline recovery path")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2500)
            failed = page.evaluate("""() => {
              const items = [...document.querySelectorAll('.task-item')];
              const failedRow = items.some((el) =>
                (el.getAttribute('aria-label') || '').startsWith('Task failed'));
              return failedRow
                  || document.querySelector("textarea[aria-label='Goal input']")?.disabled
                  || false; }""")
            assert failed, "goal attempt against dead backend → visible failed task"
            notes.append("attempt while backend down → failed task in sidebar")
            # recovery: restart the bridge, the banner must clear
            bridge_restart()
            page.wait_for_selector(".offbanner", state="detached", timeout=45000)
            notes.append("bridge back → banner cleared (recovery path works)")
            page.wait_for_function(
                "() => document.querySelector(\"textarea[aria-label='Goal input']\")"
                ".disabled === false", timeout=20000)
            assert not ta.is_disabled(), "composer re-enabled after recovery"
        check(page, "offline", offline)

        # ---------- voice buttons -------------------------------------------
        def voice(page, notes):
            page.goto(BASE, wait_until="networkidle")
            ensure_idle(page)
            mic = page.locator("button[aria-label='Start voice input']")
            mic.wait_for(timeout=8000)
            assert mic.is_visible(), "mic button visible"
            # fake device streams silence — the channel should open, not error
            mic.click()
            page.wait_for_timeout(1500)
            live = page.locator("button[aria-label='Stop voice input']")
            assert live.count() == 1, "mic toggles to live state"
            aria = live.get_attribute("aria-pressed")
            notes.append(f"aria-pressed={aria}")
            assert aria == "true", "pressed state exposed"
            live.click()
            page.wait_for_timeout(600)
            assert page.locator("button[aria-label='Start voice input']").count() == 1, \
                "mic toggles back off"
        check(page, "voice", voice)

        # ---------- universe page ---------------------------------------------
        def universe(page, notes):
            page.goto(f"{BASE}/universe", wait_until="networkidle")
            page.wait_for_timeout(2500)  # three.js warm-up
            canvases = page.locator("canvas").count()
            notes.append(f"canvases on /universe: {canvases}")
            assert canvases >= 1, "3D universe canvas mounted"
            # the memory data must be real (1580 skills / 11 systems)
            mem = page.evaluate("""async () => {
              try { const r = await fetch('http://127.0.0.1:8738/api/memory');
                    const j = await r.json(); return j.counts; }
              catch { return null; } }""")
            notes.append(f"live memory counts: {mem}")
            assert mem and mem.get("skills", 0) > 1000, "real memory data loaded"
            # live goal should have lit skill stars — presence of MemoryStars
            assert page.locator("canvas").first.is_visible()
        check(page, "universe", universe)

        # ---------- reduced motion ------------------------------------------
        def reduced(page, notes):
            page.goto(BASE, wait_until="networkidle")
            ta = page.locator("textarea[aria-label='Goal input']")
            ta.wait_for(timeout=8000)
            ensure_idle(page)
            # long enough that the goal is still mid-flight at sampling time
            ta.fill("create a file named reduced_probe.md about "
                    "stellar formation, then review it")
            page.locator("button[aria-label='Send goal']").click()
            page.wait_for_selector(".memory-pulse canvas", timeout=10000)
            c1 = page.locator(".memory-pulse canvas").first.screenshot()
            page.wait_for_timeout(600)
            c2 = page.locator(".memory-pulse canvas").first.screenshot()
            normal_churn = pixel_churn(c1, c2)
            notes.append(f"normal churn: {normal_churn}")
            ctx2 = browser.new_context(viewport={"width": 1440, "height": 900},
                                       reduced_motion="reduce")
            p2 = ctx2.new_page()
            snap_console(p2)
            p2.goto(BASE, wait_until="networkidle")
            ta2 = p2.locator("textarea[aria-label='Goal input']")
            ta2.wait_for(timeout=8000)
            ensure_idle(p2)  # cancel the normal-mode goal still on the bridge
            ta2.fill("create a file named reduced_probe2.md about "
                     "stellar death, then review it")
            p2.locator("button[aria-label='Send goal']").click()
            p2.wait_for_selector(".memory-pulse canvas", timeout=10000)
            p2.wait_for_timeout(400)  # one frame then still
            r1 = p2.locator(".memory-pulse canvas").first.screenshot()
            p2.wait_for_timeout(800)
            r2 = p2.locator(".memory-pulse canvas").first.screenshot()
            red_churn = pixel_churn(r1, r2)
            notes.append(f"reduced-motion churn: {red_churn}")
            assert red_churn < normal_churn * 0.3, \
                f"animation stops for reduced-motion ({red_churn} vs {normal_churn})"
            ctx2.close()
        check(page, "reduced", reduced)

        # ---------- a11y + contrast spot checks ------------------------------
        def a11y(page, notes):
            page.goto(BASE, wait_until="networkidle")
            # every icon-only button has an accessible name
            unnamed = page.evaluate("""() => {
              const bad = [];
              document.querySelectorAll('button').forEach((b) => {
                const name = (b.getAttribute('aria-label') || b.innerText || '').trim();
                if (!name && b.querySelector('svg')) bad.push(b.className);
              });
              return bad; }""")
            assert not unnamed, f"icon buttons without names: {unnamed}"
            notes.append("all icon-only buttons named")
            # focus ring is the photon cyan
            page.locator("textarea[aria-label='Goal input']").focus()
            page.keyboard.press("Tab")
            ring = page.evaluate("""() => {
              const el = document.activeElement;
              return getComputedStyle(el).outlineColor; }""")
            notes.append(f"focus outline color: {ring}")
            # ink3 token AA verified server-side; check it's what's applied
            ink3 = page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--ink3')")
            notes.append(f"ink3 token: {ink3}")
            assert "8b96a5" in ink3, f"AA ink3 applied, got {ink3}"
        check(page, "a11y", a11y)

        # ---------- PHASE-2 SHELL (ZCode parity - provider-free) ------------
        # The chrome states below need NO LLM: navigation and the question
        # surface are driven through window.__universeTest - the REAL store
        # ingestion path (the same ingestEvent the WS uses).

        def topbar(page, notes):
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_selector(".topbar", timeout=8000)
            assert page.locator(".topbar").is_visible(), "topbar mounted"
            assert "UNIVERSE" in page.locator(".tb-logo").inner_text()
            # window controls exist only inside Electron (preload); a plain
            # browser must NOT render dead buttons
            assert page.locator(".tb-win").count() == 0, \
                "no window buttons without the preload"
            # a fresh load may hold the "/" -> /chat redirect as one back
            # step (legitimate history); forward is ALWAYS empty though
            fwd = page.locator("button[aria-label='Go forward']")
            assert fwd.is_disabled(), "forward starts empty"
            # Ctrl+B toggles the sidebar both ways
            sb = page.locator("aside.sidebar")
            page.keyboard.press("Control+b")
            page.wait_for_timeout(400)
            assert "closed" in (sb.get_attribute("class") or ""), "sidebar folds"
            page.keyboard.press("Control+b")
            page.wait_for_timeout(400)
            assert "closed" not in (sb.get_attribute("class") or ""), "sidebar returns"
            notes.append("topbar + Ctrl+B sidebar toggle verified")
        check(page, "p2-topbar", topbar)

        def palette(page, notes):
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_selector(".topbar", timeout=8000)
            # Ctrl+K opens the command palette
            page.keyboard.press("Control+k")
            page.wait_for_selector(".cmdk-overlay", timeout=8000)
            tabs = page.locator(".cmdk-tab").all_inner_texts()
            assert tabs == ["All", "Actions", "Tasks", "Files"], f"tabs {tabs}"
            # the ZCode sections are all present on the All tab
            secs = page.locator(".cmdk-sec, [cmdk-group-heading]").all_inner_texts()
            for want in ("Recent", "Suggested", "Panels", "Configure", "Application"):
                assert any(want.lower() in s.lower() for s in secs), \
                    f"section {want} missing ({secs})"
            page.keyboard.press("Escape")
            page.wait_for_selector(".cmdk-overlay", state="detached", timeout=8000)
            # "/" from the EMPTY composer opens it too (ZCode behavior)
            ta = page.locator("textarea[aria-label='Goal input']")
            ta.click()
            page.keyboard.press("/")
            page.wait_for_selector(".cmdk-overlay", timeout=8000)
            page.keyboard.press("Escape")
            page.wait_for_selector(".cmdk-overlay", state="detached", timeout=8000)
            notes.append("Ctrl+K and / both open the palette; sections complete")
        check(page, "p2-palette", palette)

        def sidebar_nav(page, notes):
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_selector(".side-nav", timeout=8000)
            btns = page.locator(".side-nav-btn").all_inner_texts()
            for want in ("Search", "Automations"):
                assert any(want in b for b in btns), f"nav row missing {want}"
            # the Search row opens the palette
            page.locator(".side-nav-btn", has_text="Search").click()
            page.wait_for_selector(".cmdk-overlay", timeout=8000)
            page.keyboard.press("Escape")
            # view chips exist
            chips = page.locator(".side-chip").all_inner_texts()
            assert any("Group" in c for c in chips) and \
                any("Project" in c for c in chips), f"chips {chips}"
            notes.append("sidebar nav rows + Group/Project chips verified")
        check(page, "p2-sidebar-nav", sidebar_nav)

        def question_banner(page, notes):
            # a parked ask_user question renders as the consent line sibling
            # - injected through the REAL ingestEvent seam
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(600)
            page.evaluate("""() => {
              window.__universeTest.getState().ingestEvent({
                type: 'question_required',
                ts: Date.now() / 1000,
                turn_id: 'g-smoke-q',
                seq: 1,
                pending_id: 'question-smoke-1',
                question: 'Which color scheme should the report use?',
                options: ['Dark', 'Light', 'Auto'],
                allow_free_text: true,
                timeout_s: 300,
              });
            }""")
            page.wait_for_selector(".q-banner", timeout=8000)
            opts = page.locator(".q-opt").all_inner_texts()
            assert "Dark" in opts and "Light" in opts, f"options rendered ({opts})"
            assert page.locator(".q-free input").count() == 1, \
                "free-text input for allow_free_text questions"
            # choosing an option resolves it through the real answer path
            page.locator(".q-opt", has_text="Dark").first.click()
            page.wait_for_selector(".q-banner", state="detached", timeout=8000)
            notes.append("question_required banner round-trips an answer")
        check(page, "p2-question-banner", question_banner)

        def progress_pane(page, notes):
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_selector(".topbar", timeout=8000)
            # open through the palette like a user would: Show Progress
            page.keyboard.press("Control+k")
            page.wait_for_selector(".cmdk-overlay", timeout=8000)
            # cmdk does not autofocus its input - focus it explicitly
            page.locator(".cmdk-card input").first.click()
            page.keyboard.type("progress")
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_selector(".rail", timeout=8000)
            page.wait_for_selector(".prog-empty", timeout=8000)
            empty = page.locator(".prog-empty").inner_text()
            assert "todo" in empty.lower(), f"empty progress copy ({empty!r})"
            notes.append("Progress tab opens via the palette, honest empty state")
        check(page, "p2-progress-pane", progress_pane)

        def composer_chips(page, notes):
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_selector(".cx-chip", timeout=8000)
            # the mode chip carries the live permission mode
            mode = page.locator(".cx-chip", has_text="Full access")
            assert mode.count() >= 1, "mode chip shows Full access"
            # its menu offers the three ZCode modes
            mode.first.click()
            page.wait_for_selector(".cx-menu", timeout=8000)
            labels = page.locator(".cx-menu button").all_inner_texts()
            joined = " ".join(labels)
            for want in ("Full access", "Plan", "Ask"):
                assert want in joined, f"mode menu missing {want} ({labels})"
            page.keyboard.press("Escape")
            # the Max chip starts balanced and lists all three levels
            maxc = page.locator(".cx-chip", has_text="Max")
            assert maxc.count() >= 1, "Max chip present"
            maxc.first.click()
            page.wait_for_selector(".cx-menu", timeout=8000)
            lv = " ".join(page.locator(".cx-menu button").all_inner_texts())
            for want in ("Fast", "Balanced", "Max"):
                assert want in lv, f"level {want} missing ({lv})"
            page.keyboard.press("Escape")
            # the attachments button is enabled (Phase 2 v1: text paths)
            attach = page.locator("button[aria-label='Add context files']")
            assert attach.count() == 1 and attach.is_enabled(), \
                "attachments enabled"
            notes.append("mode + Max menus verified; attachments enabled")
        check(page, "p2-composer-chips", composer_chips)

        # ---------- Phase 3: the report-02 Settings page ---------------------
        import urllib.request

        def bridge_get(path: str) -> dict:
            with urllib.request.urlopen(f"{BRIDGE}{path}", timeout=10) as r:
                return json.loads(r.read())

        def p3_settings_structure(page, notes):
            page.goto(f"{BASE}/settings", wait_until="networkidle")
            page.wait_for_selector(".sx-rail-btn", timeout=10000)
            groups = page.locator(".sx-rail-label").all_inner_texts()
            got = [g.strip().lower() for g in groups]
            assert got == ["basics", "agent capabilities",
                           "data and statistics"], f"groups {groups}"
            rail = page.locator(".sx-rail-btn")
            assert rail.count() == 15, f"15 sections, got {rail.count()}"
            # report §1: Model settings opens by default
            page.wait_for_selector(".sx-prov, .sx-skel", timeout=10000)
            active = page.locator(".sx-rail-btn.on").inner_text()
            assert "Model settings" in active, f"default pane {active!r}"
            # report chrome: back, user card, window menu, Onboard
            for sel in (".sx-backrow", ".sx-user-card", ".sx-winmenu-btn",
                        ".sx-onboard"):
                assert page.locator(sel).count() == 1, sel
            page.locator(".sx-winmenu-btn").click()
            page.wait_for_selector(".sx-winmenu-pop", timeout=3000)
            items = page.locator(".sx-winmenu-pop button").all_inner_texts()
            assert len(items) == 3, f"window menu items {items}"
            page.locator(".sx-winmenu-btn").click()  # close before the shot
            page.wait_for_selector(".sx-winmenu-pop", state="detached",
                                   timeout=3000)
            page.screenshot(
                path="docs/phase3-screens/01-settings-structure.png")
            notes.append("grouped rail (3 groups / 15 sections), Model "
                         "settings default, user card + window menu")
        check(page, "p3-settings-structure", p3_settings_structure)

        def p3_settings_hooks(page, notes):
            page.goto(f"{BASE}/settings?section=hooks",
                      wait_until="networkidle")
            page.wait_for_selector(".sx-card", timeout=10000)
            sw = page.locator(
                "button[role='switch'][aria-label='Enable hooks']")
            sw.wait_for(timeout=8000)
            assert sw.get_attribute("aria-checked") == "true", \
                "hooks on by default"
            sw.click()
            # the switch is the REAL kill switch — the bridge must persist it
            for _ in range(20):
                if bridge_get("/api/settings")["settings"][
                        "hooks_enabled"] is False:
                    break
                page.wait_for_timeout(250)
            assert bridge_get("/api/settings")["settings"][
                "hooks_enabled"] is False, "kill switch persisted"
            page.screenshot(
                path="docs/phase3-screens/02-hooks-killswitch.png")
            # restore (leave the environment as found)
            page.locator(
                "button[role='switch'][aria-label='Enable hooks']").click()
            for _ in range(20):
                if bridge_get("/api/settings")["settings"][
                        "hooks_enabled"] is True:
                    break
                page.wait_for_timeout(250)
            assert bridge_get("/api/settings")["settings"][
                "hooks_enabled"] is True, "hooks re-enabled"
            notes.append("hooks kill switch round-trips through "
                         "PUT /api/settings (HookManager reloads on save)")
        check(page, "p3-settings-hooks", p3_settings_hooks)

        def p3_general_flags(page, notes):
            page.goto(f"{BASE}/settings?section=general",
                      wait_until="networkidle")
            page.wait_for_selector(".sx-card", timeout=10000)
            # report §2.4 mirrors: behavior select, permission-mode segment,
            # default thinking level — all present on the General pane
            assert page.locator(
                "[aria-label='Interaction behavior']").count() == 1, \
                "interaction behavior select"
            assert page.locator(".sx-seg button").count() == 3, \
                "permission mode segment"
            assert page.locator(
                "[aria-label='Default thinking level']").count() == 1, \
                "default thinking level select"
            # report §2.5: the grouping toggle is REAL — persist it
            sw = page.locator("button[role='switch']"
                              "[aria-label='Group terminal commands']")
            sw.wait_for(timeout=8000)
            assert sw.get_attribute("aria-checked") == "true", \
                f"group-terminal on ({sw.get_attribute('aria-checked')!r})"
            sw.click()
            for _ in range(20):
                if bridge_get("/api/settings")["settings"][
                        "group_terminal_tools"] is False:
                    break
                page.wait_for_timeout(250)
            assert bridge_get("/api/settings")["settings"][
                "group_terminal_tools"] is False, "grouping flag persisted"
            # attachments flag: off must disable the composer's + Add context
            page.locator(
                "button[role='switch'][aria-label='Attachments']").click()
            for _ in range(20):
                if bridge_get("/api/settings")["settings"][
                        "attachments_enabled"] is False:
                    break
                page.wait_for_timeout(250)
            page.goto(f"{BASE}/chat", wait_until="networkidle")
            attach = page.locator("button[aria-label='Add context files']")
            attach.wait_for(timeout=8000)
            assert not attach.is_enabled(), \
                "attachments off disables the composer button"
            # restore all flags to their default-on state
            page.goto(f"{BASE}/settings?section=general",
                      wait_until="networkidle")
            page.wait_for_selector(".sx-card", timeout=10000)
            page.locator("button[role='switch']"
                         "[aria-label='Group terminal commands']").click()
            page.locator("button[role='switch']"
                         "[aria-label='Attachments']").click()
            for _ in range(20):
                s = bridge_get("/api/settings")["settings"]
                if (s["group_terminal_tools"] is True
                        and s["attachments_enabled"] is True):
                    break
                page.wait_for_timeout(250)
            s = bridge_get("/api/settings")["settings"]
            assert s["group_terminal_tools"] is True, "restored"
            assert s["attachments_enabled"] is True, "restored"
            page.screenshot(
                path="docs/phase3-screens/03-general-flags.png")
            notes.append("grouping + attachments flags persist; composer "
                         "mirrors attachments_enabled")
        check(page, "p3-general-flags", p3_general_flags)

        browser.close()

    # ---------- console errors (final gate) --------------------------------
    # ERR_CONNECTION_REFUSED WS lines are the offline check doing its job
    # (we kill the bridge on purpose); everything else must be silent
    real_errors = [e for e in CONSOLE_ERRORS
                   if "favicon" not in e and "404" not in e
                   and "Failed to load resource" not in e
                   and "ERR_CONNECTION_REFUSED" not in e]
    OUT["console"] = {"ok": not real_errors,
                      "notes": [f"{len(real_errors)} real console errors"]}
    if real_errors:
        for e in real_errors[:10]:
            print(f"  console: {e[:200]}")
    print()
    all_ok = all(v.get("ok") for v in OUT.values())
    print("=" * 50)
    print("ALL PASS" if all_ok else "SOME FAILURES")
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) else "ui_states.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(OUT, f, indent=2, ensure_ascii=False)
        print(f"report: {path}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
