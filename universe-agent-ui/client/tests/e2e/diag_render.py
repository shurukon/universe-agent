"""
Diagnostic: what does the app actually render at http://127.0.0.1:5199
right now? Mirrors the Electron window (same URL vite serves), collects
console errors, page errors, failed requests, then dumps the visible
text tree + a screenshot so we can see exactly what the user saw.
"""
import json
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5199'

console_msgs: list[str] = []
page_errors: list[str] = []
bad_requests: list[str] = []

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_context(viewport={'width': 1440, 'height': 900}).new_page()
    page.on('console', lambda m: console_msgs.append(f'{m.type}: {m.text[:200]}')
            if m.type in ('error', 'warning') else None)
    page.on('pageerror', lambda e: page_errors.append(str(e)[:300]))
    page.on('requestfailed', lambda r: bad_requests.append(
        f'{r.url[:120]} -> {r.failure}'))

    page.goto(BASE, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(6000)  # let lazy chunks + WS connect settle

    print('URL now :', page.url)
    print('title   :', page.title())

    # root element + what wouter thinks
    info = page.evaluate('''() => ({
        appRootChildren: document.querySelector('.app-root')?.children.length ?? null,
        bodyChildren: document.body.children.length,
        bodyClass: document.body.className,
        htmlLang: document.documentElement.lang,
        rootHtml: (document.querySelector('#root')?.innerHTML || '').slice(0, 300),
    })''')
    print('state   :', json.dumps(info, indent=2))

    # visible text
    text = page.evaluate('() => document.body.innerText')
    print('--- visible text (first 1200 chars) ---')
    print(text[:1200])
    print('--- end visible text ---')

    page.screenshot(path='diag_render.png')

    print(f'console errors/warnings: {len(console_msgs)}')
    for m in console_msgs[:15]:
        print('  ', m)
    print(f'page errors: {len(page_errors)}')
    for e in page_errors[:10]:
        print('  ', e)
    print(f'failed requests: {len(bad_requests)}')
    for r in bad_requests[:10]:
        print('  ', r)

    b.close()

print('screenshot: diag_render.png')
