"""Live check of the v2 wizard: order (Name → Provider → Voice), no model
pickers, auto-advance on verify, expanded voice options. Drives the REAL
wizard in the REAL Electron window via CDP, using the mock LLM server.
"""
import json
import time
import urllib.request
import websocket

HTTP = 'http://127.0.0.1:9222'
BRIDGE = 'http://127.0.0.1:8738'

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  [{detail}]' if detail else ''))
    results.append((name, ok))


def bridge_state() -> dict:
    with urllib.request.urlopen(f'{BRIDGE}/api/setup', timeout=5) as r:
        return json.loads(r.read())


def main() -> None:
    with urllib.request.urlopen(f'{HTTP}/json', timeout=5) as r:
        targets = json.loads(r.read())
    ws_url = next(t['webSocketDebuggerUrl'] for t in targets if t['type'] == 'page')
    ws = websocket.create_connection(ws_url, timeout=30)
    mid = 0

    def call(method, params=None):
        nonlocal mid
        mid += 1
        ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == mid:
                return m.get('result', {})

    def ev(expr: str, wait: float = 0.3):
        if wait:
            time.sleep(wait)
        r = call('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
        return r.get('result', {}).get('value')

    def click(selector_expr: str) -> None:
        call('Runtime.evaluate', {'expression': selector_expr})

    # wait for the window + wizard
    for _ in range(40):
        v = ev('document.querySelector(".wiz") ? 1 : 0', 0)
        if v:
            break
        time.sleep(0.5)
    else:
        print('FAIL  wizard never appeared')
        return

    st = bridge_state()
    if st['setup_complete']:
        print('SKIP: setup already complete — reset first for a true run')
        return

    # 1) order: step 1 must be NAME
    steps = ev('''[...document.querySelectorAll('.wiz-step .ws-label')].map(e => e.textContent)''')
    check('step order = Name, Provider, Voice', steps == ['Name', 'Provider', 'Voice'], str(steps))

    check('page loads at Name step (name input visible)',
          bool(ev("document.querySelector('input.name-input')")), f"steps={steps}")

    # name typing → wake preview
    ev('''(() => {
      const i = document.querySelector('input.name-input')
      i.value = ''
      i.dispatchEvent(new Event('input', {bubbles: true}))
    })()''')
    ev('''(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      const i = document.querySelector('input.name-input')
      setter.call(i, 'Nova')
      i.dispatchEvent(new Event('input', {bubbles: true}))
    })()''')
    preview = ev("document.querySelector('.s-note.preview')?.textContent || ''")
    check('wake preview follows the name', 'nova' in preview.lower(), preview[:60])

    # 2) continue to provider — no model picker anywhere on step 2
    click('''[...document.querySelectorAll('.wiz-foot button')].find(b => b.textContent.includes('Continue'))?.click()''')
    time.sleep(1.0)
    card_count = ev('document.querySelectorAll(".wiz-card").length')
    check('step 2 = provider catalog with 19 cards', card_count == 19, f'cards={card_count}')

    # no model dropdowns in the wizard at all
    select_count = ev('[...document.querySelectorAll(".wiz-body select")].length')
    check('no built-in model picker on provider step', not select_count, f'selects={select_count}')

    # 3) open a custom provider, verify against the mock → auto-advance to voice
    click("document.querySelectorAll('.wiz-body .cm-btn').forEach(b => { if (b.textContent.includes('Add custom provider')) b.click() })")
    time.sleep(0.5)

    def fill(sel: str, val: str) -> None:
        ev(f'''(() => {{
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
          const i = document.querySelector('{sel}')
          setter.call(i, {json.dumps(val)})
          i.dispatchEvent(new Event('input', {{bubbles: true}}))
        }})()''')

    fill('[aria-label="Custom provider name"]', 'MockLLM')
    fill('[aria-label="Custom provider base URL"]', 'http://127.0.0.1:8399/v1')
    fill('[aria-label="Custom provider API key"]', 'sk-mock-123')
    click('''[...document.querySelectorAll('.wiz-body button')].find(b => b.textContent.includes('Verify & connect'))?.click()''')

    # auto-advance: within ~10s we must land on the VOICE step without
    # pressing Continue. NOTE: side-labels are CSS-uppercased — compare
    # case-insensitively.
    advanced = False
    for _ in range(20):
        time.sleep(0.5)
        if ev("document.body.innerText.toLowerCase().includes('speech-to-text')"):
            advanced = True
            break
    check('auto-advance to Voice after key accepted', advanced)

    # 4) voice step: expanded options
    body = ev('document.body.innerText')
    check('STT offers Groq', 'Groq' in body)
    check('STT offers local faster-whisper', 'faster-whisper' in body)
    check('TTS offers Fish Audio', 'Fish Audio' in body)
    check('TTS offers ElevenLabs', 'ElevenLabs' in body)
    check('TTS offers OpenAI', 'OpenAI' in body)
    check('TTS offers Edge TTS', 'Edge TTS' in body)

    # pick Groq STT → model dropdown shows whisper models
    ev('''(() => {
      const radio = document.querySelector('input[name="stt"][value="groq"]')
      radio.click()
    })()''')
    time.sleep(0.4)
    groq_models = ev('''[...document.querySelectorAll('.wiz-body select option')].map(o => o.value)''')
    check('Groq STT model options live in the dropdown',
          'whisper-large-v3' in groq_models, str(groq_models)[:90])

    # pick Fish Audio TTS → key field appears (native click; the earlier
    # evaluate-based click raced the React re-render)
    ev('''(() => {
      const radio = document.querySelector('input[name="tts"][value="fish-audio"]')
      radio.click()
      radio.dispatchEvent(new Event('change', {bubbles: true}))
    })()''')
    fish_key = False
    for _ in range(10):
        time.sleep(0.4)
        if ev('''!!document.querySelector('[aria-label="Text to speech API key"]')'''):
            fish_key = True
            break
    check('Fish Audio key field appears', fish_key)

    # 5) launch with defaults (skip voice config) — but that completes setup,
    #    so only do it if this run is allowed to finish. For verification we
    #    stop here: the user should click through themselves.
    provs = None
    with urllib.request.urlopen(f'{BRIDGE}/api/providers', timeout=5) as r:
        provs = json.loads(r.read())
    check('bridge has MockLLM installed with auto-picked model',
          any(p['name'] == 'MockLLM' for p in provs['providers']) and provs['has_provider'],
          f"serving={provs['currently_serving']}")

    with urllib.request.urlopen(f'{BRIDGE}/api/providers', timeout=5) as r:
        names = [p['name'] for p in json.loads(r.read())['providers']]
    if 'MockLLM' in names:
        req = urllib.request.Request(f'{BRIDGE}/api/providers/MockLLM', method='DELETE')
        urllib.request.urlopen(req, timeout=5)
        print('cleanup: MockLLM removed — first-run wizard stays for the user')

    ws.close()
    fails = [n for n, ok in results if not ok]
    print(f'\n{len(results) - len(fails)}/{len(results)} checks passed')
    if fails:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
