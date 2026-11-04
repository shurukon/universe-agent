"""
E2E v2: Settings (Linear rebuild) against the live bridge — the CURRENT
component classes (sx-rail / sx-card / key rows / default-for-all).

Replaces the stale six-section script (that UI no longer exists).

Covers:
  - grouped left rail (Phase 3): 3 report-02 groups, 15 sections, deep
    link ?section=providers activates Model settings, back/user card/
    window menu/onboard chrome present
  - Providers: instant paint (skeleton or cards, not blank), catalog cards,
    NO model field on install forms, Custom provider card with optional
    Model ID, per-key rows + Add key for an installed provider
  - General: brand name display + explicit Edit -> Save flow (+ Cancel)
  - Agents: Default-for-all card (provider+model inputs, Apply), per-agent
    free-text model combobox (input+datalist)
  - Voice: wake word lives HERE (moved from General), STT/TTS provider
    chips, saved-key masked hint renders
  - General must NOT contain the wake word anymore

Env: BRIDGE_TOKEN for a token-protected bridge (desktop-run); leave unset
for a plain `python -m universe_desktop_bridge` dev bridge. Run the vite dev
server (:5199) + bridge (:8738) first:
  python client/tests/e2e/settings_v2.py
"""
import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:5199'
BRIDGE = os.environ.get('BRIDGE_URL', 'http://127.0.0.1:8738')
TOKEN = os.environ.get('BRIDGE_TOKEN', '')


def bridge(path: str, method: str = 'GET', body: dict | None = None) -> dict:
    req = urllib.request.Request(f'{BRIDGE}{path}', method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={'Content-Type': 'application/json'}
                                 if body else {})
    if TOKEN:
        req.add_header('Authorization', f'Bearer {TOKEN}')
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = '') -> None:
        print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  [{detail}]' if detail else ''))
        if not ok:
            failures.append(name)

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(viewport={'width': 1440, 'height': 900}).new_page()
        errs: list[str] = []
        page.on('pageerror', lambda e: errs.append(str(e)[:150]))

        # ---- deep link lands on Model settings in the GROUPED rail (Phase 3)
        page.goto(f'{BASE}/settings?section=providers', wait_until='domcontentloaded')
        page.wait_for_selector('.sx-rail-btn', timeout=25000)
        groups = page.locator('.sx-rail-label').all_inner_texts()
        got = [g.strip().lower() for g in groups]
        check('rail groups match report-02',
              got == ['basics', 'agent capabilities', 'data and statistics'],
              str(groups))
        rail = page.locator('.sx-rail-btn')
        check('rail has 15 sections', rail.count() == 15, str(rail.count()))
        labels = [rail.nth(i).inner_text() for i in range(rail.count())]
        check('Basics order',
              labels[:6] == ['General', 'Appearance', 'Model settings',
                             'Browser Use', 'Computer Use', 'Voice'],
              str(labels[:6]))
        check('providers deep link activates Model settings',
              'on' in (rail.nth(2).get_attribute('class') or ''),
              rail.nth(2).get_attribute('class') or '')
        check('back/user card/window menu present',
              page.locator('.sx-backrow').count() == 1
              and page.locator('.sx-user-card').count() == 1
              and page.locator('.sx-winmenu-btn').count() == 1
              and page.locator('.sx-onboard').count() == 1, '')

        # the page must NOT be blank: either skeleton or real cards render
        painted = page.wait_for_selector('.sx-skel, .sx-prov', timeout=4000)
        check('providers paint without 4s blank', painted is not None)
        page.wait_for_selector('.sx-prov', timeout=15000)
        check('catalog cards present (>=30 providers)',
              page.locator('.sx-prov').count() >= 30,
              str(page.locator('.sx-prov').count()))
        # no model input on install (not-installed) cards: expand one
        card = page.locator('.sx-prov', has_text='Groq').first
        if card.count() and 'installed' not in (card.get_attribute('class') or ''):
            card.locator('button:has-text("Add API key")').click()
            page.wait_for_selector('.sx-prov input[type="password"]', timeout=5000)
            has_model_field = page.locator(
                '.sx-prov input[aria-label="Default model"]').count()
            check('no Model field on catalog install form', has_model_field == 0)
            card.locator('button:has-text("Cancel")').click()

        # custom provider card exists with optional Model ID
        custom = page.locator('button:has-text("Add custom provider")')
        check('custom provider card present', custom.count() >= 1)
        if custom.count():
            custom.first.click()
            check('custom card has optional Model ID field',
                  page.locator('.sx-custom input[aria-label="Custom provider model ID"]').count() == 1)
            page.locator('.sx-custom button:has-text("Cancel")').click()

        # installed provider rows show key inventory + Add key
        provs = bridge('/api/providers')['providers']
        if provs:
            name = provs[0]['name']
            inst = page.locator('.sx-prov.installed', has_text=name).first
            if inst.count():
                check('installed card shows + Add key',
                      inst.locator('button:has-text("Add key")').count() >= 1)
                check('key rows render',
                      inst.locator('.sx-keyrow').count() >= 1)

        # ---- General: brand display + Edit flow
        page.locator('.sx-rail-btn', has_text='General').first.click()
        page.wait_for_selector('.sx-idname', timeout=10000)
        setup0 = bridge('/api/setup')
        brand = page.locator('.sx-idname').inner_text()
        cur_name = setup0['assistant_name']
        # the display is the brand lockup: glyph + the configured name
        # uppercased (the name is user-owned — "L" included) + COMPUTER suffix
        check('brand name displays styled',
              '✦' in brand and cur_name.upper() in brand.upper()
              and 'COMPUTER' in brand.upper(), brand.replace('\n', ' '))
        check('name not directly editable',
              page.locator('.sx-idedit').count() == 0)
        page.locator('button:has-text("Edit")').first.click()
        page.wait_for_selector('.sx-idedit input', timeout=5000)
        page.locator('.sx-idedit input').fill('Nova')
        page.locator('.sx-idedit button:has-text("Cancel")').click()
        check('Cancel discards rename',
              'nova' not in page.locator('.sx-idname').inner_text().lower())
        page.locator('button:has-text("Edit")').first.click()
        page.locator('.sx-idedit input').fill('Nova')
        page.locator('.sx-idedit button:has-text("Save")').click()
        # poll the bridge: the store PUT resolves async — a fixed timeout
        # raced it (the earlier false FAIL)
        persisted = False
        for _ in range(20):
            page.wait_for_timeout(500)
            if bridge('/api/setup')['assistant_name'] == 'Nova':
                persisted = True
                break
        check('Edit->Save persists name', persisted,
              bridge('/api/setup')['assistant_name'])
        # restore whatever the user had configured
        page.locator('button:has-text("Edit")').first.click()
        page.wait_for_selector('.sx-idedit input', timeout=5000)
        page.locator('.sx-idedit input').fill(cur_name)
        page.locator('.sx-idedit button:has-text("Save")').click()
        page.wait_for_timeout(800)

        # work folder: Choose button present (native dialog; not clicked —
        # a modal would block the headless run)
        check('workspace card has Choose button',
              page.locator('button:has-text("Choose")').count() >= 1)
        # response language select wired
        check('behavior: response language select',
              page.locator('select#sx-resp-lang').count() == 1)
        check('general: wake word gone from General',
              page.locator('input[aria-label="Wake word"]').count() == 0)

        # ---- Agents: default-for-all + combobox model fields
        page.locator('.sx-rail-btn', has_text='Agents').first.click()
        page.wait_for_selector('.sx-default-all', timeout=10000)
        check('agents: default-for-all card',
              page.locator('button:has-text("Apply to all")').count() == 1)
        page.locator('.sx-row .sx-link', has_text='Configure').first.click()
        page.wait_for_selector('input[list^="sx-agent-models"]', timeout=8000)
        check('agents: model field is free-text combobox',
              page.locator('input[list^="sx-agent-models"]').count() >= 1)

        # ---- Voice: wake word lives here now
        page.locator('.sx-rail-btn', has_text='Voice').first.click()
        page.wait_for_selector('input[aria-label="Wake word"]', timeout=10000)
        check('voice: wake word moved here',
              page.locator('input[aria-label="Wake word"]').count() == 1)
        check('voice: STT provider chips',
              page.locator('[aria-label="STT provider"] button').count() >= 3)
        check('voice: TTS provider chips',
              page.locator('[aria-label="TTS provider"] button').count() >= 4)
        st = bridge('/api/settings')['settings']
        if st.get('stt_api_key'):
            check('voice: saved STT key indicator', 'Key saved' in page.content())

        check('zero page errors', len(errs) == 0, '; '.join(errs[:3]))
        b.close()

    print(f'\n{len(failures)} failure(s)' if failures else '\nALL PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
