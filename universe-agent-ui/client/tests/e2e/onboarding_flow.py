"""
E2E: first-run onboarding (W3) — the mandatory /welcome gate.

PRECONDITION (true first run):
    python universe-agent-ui/scripts/reset_setup.py
    (restart the bridge, then start this suite)

Exercises the real surfaces:
  1. setup incomplete -> /chat redirects to /welcome (the app-wide gate)
  2. provider step: Continue disabled until a provider verifies
  3. custom OpenAI-compatible provider verified against a real local
     server (MOCK_URL) — the same probe a cloud key goes through
  4. voice step: local defaults checked; skip works
  5. name step: wake-phrase preview; Launch -> /chat; gate re-check passes
  6. the bridge reports setup_complete=true + assistant_name persisted

Run: python client/tests/e2e/onboarding_flow.py
"""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:5199'
BRIDGE = 'http://127.0.0.1:8738'
# a real OpenAI-compatible server must be up here (tests/e2e/mock_llm.py)
MOCK_URL = 'http://127.0.0.1:8399/v1'


def bridge(path: str) -> dict:
    with urllib.request.urlopen(f'{BRIDGE}{path}', timeout=10) as r:
        return json.loads(r.read())


def main() -> int:
    # precondition: this suite must run from a true first-run state
    st = bridge('/api/setup')
    if st['setup_complete']:
        print('PRECONDITION FAILED: setup already complete. Run '
              'scripts/reset_setup.py + restart the bridge first.')
        return 2

    failures = []

    def check(name: str, ok: bool, detail: str = '') -> None:
        print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  [{detail}]' if detail else ''))
        if not ok:
            failures.append(name)

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(viewport={'width': 1440, 'height': 900}).new_page()
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)[:150]))

        # 1) the gate
        page.goto(f'{BASE}/chat', wait_until='domcontentloaded')
        page.wait_for_selector('.wiz', timeout=25000)
        check('gate: /chat redirects to /welcome while incomplete', page.url.endswith('/welcome'))

        # 2) step 1 — Continue must be disabled with no provider
        cont = page.locator('.wiz-foot .cm-btn.ok')
        cont.wait_for(state='visible', timeout=10000)
        check('provider step: Continue disabled (no key yet)', cont.is_disabled())

        # 3) custom provider against the real mock server
        page.locator('button:has-text("Add custom provider")').click()
        page.locator('input[aria-label="Custom provider name"]').fill('MockLLM')
        page.locator('input[aria-label="Custom provider base URL"]').fill(MOCK_URL)
        page.locator('input[aria-label="Custom provider API key"]').fill('sk-mock-123')
        page.locator('input[aria-label="Custom provider model"]').fill('mock-1')
        page.locator('.wc-form.custom .cm-btn.ok').click()
        # the real probe + routing round-trip (first call after a bridge
        # start also pays the runtime-init cost) — wait for the banner
        page.wait_for_selector('.wiz-ok', timeout=30000)
        check('custom provider verified (real probe)', True)
        check('provider step: Continue enabled', not cont.is_disabled())

        # 4) step 2 — voice defaults + skip
        cont.click()
        page.wait_for_selector('input[name="stt"]', timeout=10000)
        check('voice: local STT default', page.locator('input[name="stt"][value="local"]').is_checked())
        check('voice: edge TTS default', page.locator('input[name="tts"][value="edge-tts"]').is_checked())
        page.locator('button:has-text("Skip for now")').click()

        # 5) step 3 — name + launch
        page.wait_for_selector('input[aria-label="Universe name"]', timeout=10000)
        page.locator('input[aria-label="Universe name"]').fill('Nova')
        page.wait_for_timeout(250)
        preview = page.locator('.s-note.preview .mono').inner_text()
        check('wake phrase preview', '"wake up Nova"' in preview, preview)
        page.locator('button:has-text("Launch Universe")').click()
        page.wait_for_selector('.composer textarea', timeout=25000)
        check('launch lands on /chat', page.url.endswith('/chat'))
        check('composer present', page.locator('.composer textarea').count() == 1)

        # 6) bridge state + the completed gate
        st2 = bridge('/api/setup')
        check('bridge: setup_complete=true', st2['setup_complete'] is True)
        check('bridge: assistant_name=Nova', st2['assistant_name'] == 'Nova')
        prov = bridge('/api/providers')
        check('bridge: provider installed', any(p['name'] == 'MockLLM' for p in prov['providers']))

        page.goto(f'{BASE}/welcome', wait_until='domcontentloaded')
        page.wait_for_timeout(2000)
        check('gate: /welcome bounces to /chat when complete', page.url.endswith('/chat'))

        check('zero page errors', len(errs) == 0, '; '.join(errs[:2]))
        b.close()

    print(f'\n{len(failures)} failure(s)' if failures else '\nALL PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
