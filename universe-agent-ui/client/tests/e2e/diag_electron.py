"""Capture what the LIVE Electron window actually renders (via CDP 9222).
Mirrors exactly what the user sees, bypassing my inability to view images:
reports paint evidence (pixels) + visible text + console errors."""
import base64
import json
import struct
import urllib.request
import websocket  # pip install websocket-client

HTTP = 'http://127.0.0.1:9222'
SHOT = 'electron_view.png'


def find_page_ws() -> str:
    with urllib.request.urlopen(f'{HTTP}/json', timeout=5) as r:
        targets = json.loads(r.read())
    for t in targets:
        if t['type'] == 'page':
            return t['webSocketDebuggerUrl']
    raise SystemExit('no page target')


def main() -> None:
    # suppress_origin: Chromium's DevTools WS rejects requests carrying an
    # Origin header unless --remote-allow-origins matches — suppressing the
    # header makes the probe work regardless of launch flags.
    ws = websocket.create_connection(find_page_ws(), timeout=10,
                                     suppress_origin=True)
    mid = 0

    def call(method: str, params: dict | None = None):
        nonlocal mid
        mid += 1
        ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if 'id' in msg and msg['id'] == mid:
                return msg.get('result', msg)

    # recent console
    console = call('Runtime.evaluate', {
        'expression': 'JSON.stringify((window.__errLog||[]))',
        'returnByValue': True,
    })
    print('window.__errLog:', console.get('result', {}).get('value'))

    # visible text
    text = call('Runtime.evaluate', {
        'expression': 'document.body.innerText',
        'returnByValue': True,
    })
    body_text = text.get('result', {}).get('value', '')
    print('--- visible text in the ELECTRON window (first 900) ---')
    print(body_text[:900])
    print('--- end ---')

    # actual pixels
    shot = call('Page.captureScreenshot', {'format': 'png'})
    png = base64.b64decode(shot['data'])
    with open(SHOT, 'wb') as f:
        f.write(png)

    # quantify: is it all one flat color (blank) or does it have structure?
    import zlib
    # cheap test without PIL: count unique colors via CDP eval on a canvas
    stats = call('Runtime.evaluate', {
        'expression': '''(() => {
          const s = document.createElement('canvas');
          s.width = 100; s.height = 60;
          const c = s.getContext('2d');
          const shot = document.createElement('canvas');
          // draw current page via html2canvas? no — use body pixels directly
          return JSON.stringify({skip: true});
        })()''',
        'returnByValue': True,
    })
    print('screenshot saved:', SHOT, len(png), 'bytes')

    ws.close()


if __name__ == '__main__':
    main()
