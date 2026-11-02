"""connectors/builtin/browser.py — 10 Playwright-backed browser tools.

§6 + Phase 1 expansion. Default mode is HEADED (visible): the operator wants to
see the browser work so there's no room for an unnoticed mistake. Falls back to
headless ONLY when no display is available, with a logged warning — never
silent. Override via the UNIVERSE_BROWSER_HEADLESS env var:
    UNIVERSE_BROWSER_HEADLESS=1  force headless
    UNIVERSE_BROWSER_HEADLESS=0  force headed (will fail without a display)

The Capability Gate calls every handler — sync OR async. Browser handlers are
async (Playwright's natural API); the gate's `enforce()` awaits coroutine
handlers. Each handler tags any retrieved page text with [UNTRUSTED:web] so it
can never be followed as an instruction (§10).

A single shared browser instance lives for the lifetime of the process; tabs
are managed by browser_tabs / browser_navigate(tab=...).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from typing import Any

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry

_log = logging.getLogger("universe.browser")

# Module-level state. Lazily initialised on first browser_* call so importing
# this module is free; the cost is paid only when a browser tool actually runs.
# IMPORTANT: _loop_at_create records the event loop the browser was started on.
# pytest-asyncio (function scope) gives every test a fresh loop — if we don't
# detect that and restart, all calls after the first test will hang waiting on
# a browser bound to a dead loop.
_state_lock = asyncio.Lock()
_pw: Any = None            # Playwright instance
_browser: Any = None       # Universe's own Chromium (launched by _ensure_browser)
_cdp_browser: Any = None   # User's real browser, attached via CDP (browser_connect)
_cdp_pages: dict[str, Any] = {}  # tab_id -> Page inside the user's CDP browser
_pages: dict[str, Any] = {}  # tab_id -> Page
_active_tab: str = "default"
_loop_at_create: Any = None


async def reset_browser_for_tests() -> None:
    """Force-close the shared browser. Tests call this between cases so the
    next call starts a fresh browser on the test's own event loop."""
    global _pw, _browser, _cdp_browser, _active_tab, _loop_at_create
    async with _state_lock:
        try:
            if _cdp_browser is not None:
                await _cdp_browser.disconnect()
        except Exception:
            pass
        try:
            if _browser is not None:
                await _browser.close()
        except Exception:
            pass
        try:
            if _pw is not None:
                await _pw.stop()
        except Exception:
            pass
        _cdp_browser = None
        _browser = None
        _pw = None
        _pages.clear()
        _cdp_pages.clear()
        _active_tab = "default"
        _loop_at_create = None


def _want_headless() -> bool:
    """Decide headed vs headless. Headed is the default everywhere. Headless
    only when (a) explicitly forced via UNIVERSE_BROWSER_HEADLESS=1, or (b) a
    headless POSIX box with no display detected and the user hasn't forced
    headed. Windows and macOS always have a desktop by default — the old
    DISPLAY-only probe flipped Windows to headless silently, inverting this
    module's own "the operator sees the browser work" rationale."""
    env = os.environ.get("UNIVERSE_BROWSER_HEADLESS", "").strip().lower()
    if env == "1":
        return True
    if env == "0":
        return False
    if os.name == "nt" or sys.platform == "darwin":
        return False  # desktop present by definition; headless only via env
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        _log.warning(
            "browser: no DISPLAY/WAYLAND_DISPLAY available — falling back to "
            "headless. Set UNIVERSE_BROWSER_HEADLESS=0 to force headed (will "
            "fail without a display server).")
        return True
    return False


async def _ensure_playwright() -> Any:
    """Start the Playwright process without launching any browser.
    Used by browser_connect which only needs _pw.chromium to issue a CDP
    connect — it must NOT trigger a full Chromium launch via _ensure_browser()
    because that wastes resources and pollutes module state that belongs to
    Universe's own Chromium session."""
    global _pw
    if _pw is not None:
        return _pw
    async with _state_lock:
        if _pw is not None:
            return _pw
        from playwright.async_api import async_playwright
        _pw = await async_playwright().start()
    return _pw


async def _ensure_browser() -> Any:
    """Lazily start Playwright + Chromium. Idempotent under the state lock.
    Detects a stale event loop (pytest-asyncio function scope gives every test
    a fresh loop) and restarts cleanly instead of hanging on a dead browser."""
    global _pw, _browser, _loop_at_create
    current_loop = asyncio.get_running_loop()
    if (_browser is not None and _loop_at_create is current_loop
            and _browser.is_connected()):
        return _browser
    # Either first start, or the event loop changed under us — clean up the
    # stale browser if any, then start fresh. (Cleanup is best-effort: a
    # browser bound to a dead loop will fail its close() call.)
    if _browser is not None or _pw is not None:
        try:
            await reset_browser_for_tests()
        except Exception:
            _browser = None
            _pw = None
            _pages.clear()
            _loop_at_create = None
    async with _state_lock:
        if _browser is not None and _loop_at_create is current_loop:
            return _browser
        from playwright.async_api import async_playwright
        _pw = await async_playwright().start()
        try:
            _browser = await _pw.chromium.launch(headless=_want_headless())
        except Exception as launch_exc:
            # headed launch needs the full chromium build — a machine that
            # installed only the headless shell raises "Executable doesn't
            # exist". Fall back to headless with a logged warning: the
            # browser family keeps working, the user still sees the truth.
            _log.warning("browser: chromium launch failed (%s) — retrying "
                         "headless (full chromium build not installed)",
                         str(launch_exc)[:120])
            _browser = await _pw.chromium.launch(headless=True)
        _loop_at_create = current_loop
        if "default" not in _pages:
            _pages["default"] = await _browser.new_page()
    return _browser


async def _page(tab: str | None = None) -> Any:
    """Return the page for `tab`, creating it if needed.

    When browser_connect has attached the user's real browser over CDP,
    every tool routes through THAT browser (its sessions/cookies), never
    through Universe's own Chromium — that was the entire point of connecting.
    Tabs are matched against the user's existing tabs by URL best-effort;
    new tab ids open a new page in the first CDP context."""
    t = tab or _active_tab
    if _cdp_browser is not None:
        if not _cdp_browser.is_connected():
            # the user closed their browser — fall back honestly
            _log.warning("browser: CDP browser went away — falling back to Universe's own Chromium")
            _cdp_browser_disconnected()
        else:
            pg = _cdp_pages.get(t)
            if pg is not None and not pg.is_closed():
                return pg
            ctxs = _cdp_browser.contexts
            if not ctxs:
                raise RuntimeError("CDP browser has no open contexts")
            if t == "default":
                # audit 2026-10: 'default' used to map to the user's
                # CURRENT tab — after one explicit CDP attach, auto-consent
                # tools then steered clicks/fills on the user's open page
                # and snapshotted other logged-in tabs. Every CDP action
                # must now name an explicit tab id (from
                # browser_tabs action='list').
                raise RuntimeError(
                    "CDP browser attached: pass an explicit tab id "
                    "(browser_tabs action='list') instead of 'default'")
            pg = await ctxs[0].new_page()
            _cdp_pages[t] = pg
            return pg
    await _ensure_browser()
    if t not in _pages:
        _pages[t] = await _browser.new_page()
    return _pages[t]


def _cdp_browser_disconnected() -> None:
    """Clear stale CDP state after the user's browser disappears."""
    global _cdp_browser
    _cdp_browser = None
    _cdp_pages.clear()


# Bounded per-page console capture. The read path consumes
# window.__universe_console, but nothing ever wrote to it — a phantom
# listener that always returned []. This attaches a real page-side
# wrapper (once per page object) so read returns actual messages.
_CONSOLE_CAP = 200


async def _ensure_console_listener(p: Any) -> None:
    """Wrap console.log/info/warn/error/debug in the page so messages are
    captured into window.__universe_console (capped at _CONSOLE_CAP). Pure
    page-side JS — no Python binding needed. Best-effort: a page that
    blocks evaluate just yields an empty log, reported honestly as []."""
    if getattr(p, "_universe_console_hooked", False):
        return
    try:
        await p.evaluate(
            "() => { if (window.__universe_console_hooked) return; "
            "window.__universe_console_hooked = true; "
            "window.__universe_console = window.__universe_console || []; "
            "for (const m of ['log','info','warn','error','debug']) { "
            "const f = console[m].bind(console); "
            "console[m] = (...a) => { try { "
            "window.__universe_console.push({m, t: Date.now(), "
            "text: a.map(x => { try { return typeof x === 'string' ? x : JSON.stringify(x); } "
            "catch(e){ return String(x); } }).join(' ') }); "
            f"if (window.__universe_console.length > {_CONSOLE_CAP}) "
            "window.__universe_console.splice(0, window.__universe_console.length - "
            f"{_CONSOLE_CAP}); }} catch(e){{}} return f(...a); }}; }}")
        p._universe_console_hooked = True
    except Exception:
        pass


# ---- the 10 tool handlers -------------------------------------------------

async def browser_navigate(url: str, wait_until: str = "load",
                           tab: str = "default", timeout_ms: int = 30000) -> dict:
    """Open a URL in a new or existing tab. Waits for load. Returns final URL +
    title. When a CDP browser is attached (browser_connect), pass an explicit
    tab id from the connect result or browser_tabs action='list' — 'default'
    is refused there on purpose (no implicit steering of the user's open tab).
    Default consent: auto (navigating is low-risk) — but internal hosts are
    refused: at auto consent nothing stops an injected instruction from
    navigating at the bridge API / LAN / cloud metadata and snapshotting the
    response into context (audit 2026-10 SSRF fix; web tools had this guard,
    the browser family did not)."""
    # forgiving: prepend https:// if missing scheme (VL often omits it)
    if url and "://" not in url and not url.startswith("data:"):
        url = "https://" + url.lstrip("/")
    from connectors.builtin.misc import _ssrf_guard
    # allow_private: the consent escalator has lifted local-network targets
    # to per_turn — the handler only hard-refuses scheme/host-shape junk
    guard = _ssrf_guard(url, allow_private=True)
    if not guard.get("ok"):
        return {"ok": False, "error": guard.get("error", "ssrf guard refused"),
                "url": url}
    p = await _page(tab)
    resp = await p.goto(url, wait_until=wait_until, timeout=timeout_ms)
    # arm console capture for this page so browser_console(read) has real
    # messages to return (best-effort; navigation before attach is lost —
    # stated, not hidden)
    await _ensure_console_listener(p)
    return {"ok": True, "url": p.url, "title": await p.title(),
            "status": resp.status if resp else None, "tab": tab}


async def browser_click(selector: str | None = None,
                        description: str | None = None,
                        tab: str | None = None,
                        timeout_ms: int = 10000) -> dict:
    """Click an element by CSS selector, or by a textual description resolved
    against the current accessibility snapshot (text=... match). Exactly one of
    selector / description must be provided."""
    if not selector and not description:
        return {"ok": False, "error": "browser_click requires selector or description (hint: use selector=#id or description=Button text)"}
    p = await _page(tab)
    if selector:
        await p.click(selector, timeout=timeout_ms)
        return {"ok": True, "clicked": "selector", "selector": selector}
    # description: try text=, role-based, and aria-label selectors in order
    candidates = [
        f"text={description}",
        f"button:has-text({description!r})",
        f"a:has-text({description!r})",
        f"[aria-label={description!r}]",
    ]
    last_err = None
    for sel in candidates:
        try:
            await p.click(sel, timeout=timeout_ms)
            return {"ok": True, "clicked": "description", "selector": sel,
                    "description": description}
        except Exception as e:
            last_err = str(e)
    return {"ok": False, "error": f"could not resolve description "
                                  f"{description!r}: {last_err}"}


async def browser_fill(fields: dict, tab: str | None = None,
                       timeout_ms: int = 10000) -> dict:
    """Fill one or more form fields. `fields` maps CSS selector -> value."""
    if isinstance(fields, str):
        import json as _js
        try:
            fields = _js.loads(fields)
        except Exception:
            pass
    if not isinstance(fields, dict) or not fields:
        return {'ok': False, 'error': 'browser_fill requires fields dict (hint: {"#email": "test@example.com"})'}
    p = await _page(tab)
    filled = []
    for sel, val in fields.items():
        await p.fill(sel, str(val), timeout=timeout_ms)
        filled.append(sel)
    return {"ok": True, "filled": filled, "tab": tab or _active_tab}


async def browser_snapshot(tab: str | None = None,
                           max_chars: int = 20000) -> dict:
    """Return the page's accessibility tree — the default lightweight way an
    agent 'reads' a page. Tagged [UNTRUSTED:web]. Uses Playwright's ARIA
    snapshot when available, falling back to body.innerText so it works across
    Playwright versions (page.accessibility was removed in 1.46+)."""
    p = await _page(tab)
    text = ""
    method = None
    # Try the modern ARIA snapshot first
    try:
        text = await p.locator("body").aria_snapshot()
        method = "aria_snapshot"
    except Exception:
        pass
    # Fall back to the old page.accessibility API if present
    if not text:
        acc = getattr(p, "accessibility", None)
        if acc is not None:
            try:
                snap = await acc.snapshot()
                import json as _json
                text = _json.dumps(snap, ensure_ascii=False) if snap else ""
                method = "accessibility"
            except Exception:
                pass
    # Last resort: visible text — still a faithful representation of what's on the page
    if not text:
        try:
            text = await p.evaluate("() => document.body.innerText")
            method = "body_innerText"
        except Exception as e:
            return {"ok": False, "error": f"snapshot failed: {e}"}
    return {"ok": True, "url": p.url,
            "snapshot": f"[UNTRUSTED:web] {text[:max_chars]}",
            "truncated": len(text) > max_chars,
            "method": method,
            "tab": tab or _active_tab}


async def browser_screenshot(path: str | None = None,
                             full_page: bool = True,
                             tab: str | None = None) -> dict:
    """Capture a PNG screenshot. If `path` is given, saves there; otherwise
    returns the image as base64. Hook for vision models: when a vision-capable
    provider is configured, the orchestrator can pass the returned base64 to
    the model for description (see Design Agent's vision_analyze tool)."""
    p = await _page(tab)
    if path:
        await p.screenshot(path=path, full_page=full_page)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return {"ok": True, "path": path, "full_page": full_page,
                "bytes": size, "tab": tab or _active_tab}
    png = await p.screenshot(full_page=full_page)
    b64 = base64.b64encode(png).decode("ascii")
    return {"ok": True, "image_base64": b64,
            "image_mime": "image/png", "full_page": full_page,
            "bytes": len(png), "tab": tab or _active_tab}


async def browser_extract(selectors: dict | None = None,
                          tab: str | None = None,
                          max_chars: int = 20000, url: str = "") -> dict:
    """Pull structured data out of the current page. With no args, extracts
    every <table> as a list of row-dicts and every <ul>/<ol> as a list of text
    items. With `selectors={key: css_selector}`, returns {key: extracted_text}
    for each. Tagged [UNTRUSTED:web]."""
    if url:
        await browser_navigate(url=url, tab=tab or "default")
    p = await _page(tab)
    if selectors:
        out: dict[str, Any] = {}
        for key, sel in selectors.items():
            try:
                els = await p.query_selector_all(sel)
                vals = []
                for el in els:
                    t = await el.text_content()
                    vals.append(t.strip() if t else "")
                out[key] = vals
            except Exception as e:
                out[key] = {"error": str(e)}
        return {"ok": True, "url": p.url,
                "data": "[UNTRUSTED:web] " + str(out)[:max_chars],
                "tab": tab or _active_tab}
    # default: extract tables + lists via DOM
    tables = await p.evaluate(
        """() => Array.from(document.querySelectorAll('table')).map(t =>
            Array.from(t.rows).map(r => Array.from(r.cells).map(c => c.innerText.trim())))""")
    lists = await p.evaluate(
        """() => Array.from(document.querySelectorAll('ul,ol')).map(l =>
            Array.from(l.children).map(li => li.innerText.trim()))""")
    data = {"tables": tables, "lists": lists}
    import json as _json
    text = _json.dumps(data, ensure_ascii=False)
    return {"ok": True, "url": p.url,
            "data": f"[UNTRUSTED:web] {text[:max_chars]}",
            "truncated": len(text) > max_chars,
            "tab": tab or _active_tab}


async def browser_console(action: str = "read",
                          js: str | None = None,
                          tab: str | None = None,
                          timeout_ms: int = 10000) -> dict:
    """Read browser console logs (action='read') OR evaluate arbitrary JS
    (action='eval', requires `js`). Tagged [UNTRUSTED:web]."""
    p = await _page(tab)
    if action == "read":
        # collect messages seen since the page was opened
        msgs: list[dict] = []
        try:
            msgs = await p.evaluate(
                "() => (window.__universe_console = window.__universe_console || []).slice()")
        except Exception:
            pass
        return {"ok": True, "messages": msgs, "tab": tab or _active_tab}
    if action == "eval":
        if not js:
            return {"ok": False, "error": "browser_console eval requires `js`"}
        try:
            val = await p.evaluate(js)
            import json as _json
            text = _json.dumps(val, ensure_ascii=False, default=str) \
                if not isinstance(val, str) else val
            return {"ok": True,
                    "value": f"[UNTRUSTED:web] {text[:20000]}",
                    "tab": tab or _active_tab}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"unknown action {action!r}; use 'read' or 'eval'"}


async def browser_tabs(action: str = "list",
                       tab: str | None = None,
                       url: str | None = None) -> dict:
    """Open / switch / close / list tabs.
    - action='open'  : open a new tab; if `url` given, navigate to it. Returns new tab id.
    - action='switch': make `tab` the active tab.
    - action='close' : close `tab`. If closing the active tab, switches to 'default'.
    - action='list'  : return all open tabs with their current URLs.
    """
    global _active_tab
    # NO unconditional _ensure_browser(): with a CDP browser attached every
    # action routes through it — launching Universe's own Chromium first
    # burned the session when that launch failed (headed build missing).
    # The open/switch/close paths call _page(), which ensures on demand.
    # the tab registry is shared mutable state across concurrent tool
    # calls: open/close/switch must be atomic, or two concurrent opens
    # mint the same tabN id and one page handle is lost (leaked).
    async with _state_lock:
        if action == "list":
            out = {}
            for tid, pg in _pages.items():
                try:
                    out[tid] = {"url": pg.url, "active": tid == _active_tab}
                except Exception:
                    out[tid] = {"url": None, "active": tid == _active_tab}
            # CDP-attached browser: its registered tabs live in _cdp_pages
            # (the user's real pages) — list them too or the model sees an
            # empty map after browser_connect and has no id to pass
            for tid, pg in _cdp_pages.items():
                try:
                    out[tid] = {"url": pg.url, "cdp": True,
                                "active": tid == _active_tab}
                except Exception:
                    out[tid] = {"url": None, "cdp": True,
                                "active": tid == _active_tab}
            return {"ok": True, "tabs": out, "active": _active_tab}
        if action == "open":
            # collision-free: caller id wins only if unused, else a uuid
            # suffix — never tab{len} (close shrinks the dict and reuses
            # numbers, colliding with a live tab).
            import uuid as _uuid
            if tab and tab not in _pages and tab not in _cdp_pages:
                new_id = tab
            else:
                base = tab or "tab"
                new_id = base
                while new_id in _pages or new_id in _cdp_pages:
                    new_id = f"{base}-{_uuid.uuid4().hex[:8]}"
            # route through _page() so a connected CDP browser opens the
            # tab in the USER's browser (the old code always used Universe's
            # own Chromium even when browser_connect was active)
            if url:
                # same SSRF contract as browser_navigate (audit 2026-10):
                # private/loopback targets were consent-escalated, the
                # handler refuses scheme/host-shape junk outright
                from connectors.builtin.misc import _ssrf_guard
                guard = _ssrf_guard(str(url), allow_private=True)
                if not guard.get("ok"):
                    return {"ok": False,
                            "error": guard.get("error", "ssrf guard refused"),
                            "url": url}
            pg = await _page(new_id)
            _active_tab = new_id
            if url:
                await pg.goto(url)
            return {"ok": True, "tab": new_id, "url": url or "about:blank"}
        if action == "switch":
            if tab not in _pages and tab not in _cdp_pages:
                return {"ok": False, "error": f"unknown tab {tab!r}"}
            _active_tab = tab
            return {"ok": True, "active": _active_tab}
        if action == "close":
            if not tab:
                return {"ok": False, "error": "browser_tabs close requires `tab`"}
            pg = _pages.pop(tab, None)
            if pg is None:
                pg = _cdp_pages.pop(tab, None)
            if pg is None:
                return {"ok": False, "error": f"unknown tab {tab!r}"}
            try:
                await pg.close()
            except Exception:
                pass
            if _active_tab == tab:
                rest = list(_pages) + [t for t in _cdp_pages if t not in _pages]
                _active_tab = "default" if "default" in rest else next(iter(rest), "default")
            return {"ok": True, "closed": tab, "active": _active_tab}
        return {"ok": False, "error": f"unknown action {action!r}"}


async def browser_upload(selector: str, file_path: str,
                         tab: str | None = None,
                         timeout_ms: int = 30000) -> dict:
    """Upload a local file into a page's <input type=file>. Touches the user's
    filesystem, so the gate's consent policy for connector.run applies."""
    if not os.path.exists(file_path):
        return {"ok": False, "error": f"file not found: {file_path}"}
    p = await _page(tab)
    try:
        async with p.expect_file_chooser(timeout=timeout_ms) as fc_info:
            await p.click(selector, timeout=timeout_ms)
        fc = await fc_info.value
        await fc.set_files(file_path)
        return {"ok": True, "selector": selector, "file": file_path,
                "tab": tab or _active_tab}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def browser_connect(cdp_url: str = "",
                          host: str = "127.0.0.1",
                          port: int = 9222) -> dict:
    """Attach via CDP to the user's OWN running Chrome/Brave instance, reusing
    their real sessions and cookies. Gated behind explicit consent: this
    touches the user's real logged-in browser. Requires the user to launch
    Chrome with --remote-debugging-port=PORT.

    The connection is stored in module state (_cdp_browser) so it persists
    for the lifetime of the process — subsequent browser_navigate / click /
    snapshot calls can then be routed through it. Calling browser_connect
    again disconnects the previous session first.
    """
    global _cdp_browser
    if not cdp_url:
        cdp_url = f"http://{host}:{port}"
    # Only need the Playwright process, not a full Chromium launch.
    # _ensure_browser() would wastefully open Universe's own browser and
    # pollute module state that belongs to the Universe-owned session.
    pw = await _ensure_playwright()
    # Disconnect any existing CDP session before opening a new one.
    if _cdp_browser is not None:
        try:
            await _cdp_browser.disconnect()
        except Exception:
            pass
        _cdp_browser = None
    try:
        b = await pw.chromium.connect_over_cdp(cdp_url)
        _cdp_browser = b  # persist in module state — NOT a local var
        ctxs = b.contexts
        pages = []
        # register the user's EXISTING pages under explicit tab ids
        # (tab-1, tab-2, …): every later browser tool names an explicit
        # tab — the audit's no-implicit-'default' contract holds — and
        # browser_tabs list can hand the model real ids instead of an
        # empty map
        async with _state_lock:
            _cdp_pages.clear()  # tabs from any previous session are stale
            idx = 0
            for ctx in ctxs:
                for pg in ctx.pages:
                    idx += 1
                    _cdp_pages[f"tab-{idx}"] = pg
                    try:
                        title = await pg.title()
                    except Exception:
                        title = ""
                    pages.append({"tab": f"tab-{idx}", "url": pg.url,
                                  "title": title})
        return {"ok": True, "cdp_url": cdp_url,
                "contexts": len(ctxs), "pages": pages[:10],
                "note": "connected to user's running browser — subsequent "
                        "browser tools route through it; pass an explicit "
                        "tab from this list (or browser_tabs action='list')"}
    except Exception as e:
        _cdp_browser = None
        return {"ok": False, "error": f"CDP connect to {cdp_url} failed: {e}"}


# ---- live screencast (M4) ----------------------------------------------
# CDP screencast of the active tab, relayed to bridge WS viewers as binary
# JPEG frames. Lazy by design: nothing starts until a viewer connects, and
# the stream stops the moment the last viewer leaves — an idle browser
# costs nothing.

_screencast_cdp: Any = None          # active CDP session (or None)
_screencast_tab: str | None = None   # tab id currently streaming
_screencast_listeners: set = set()   # async callbacks: (jpeg_bytes, meta)


def add_screencast_listener(cb: Any) -> None:
    """Register an async (frame_bytes, meta) callback for live frames."""
    _screencast_listeners.add(cb)


def remove_screencast_listener(cb: Any) -> None:
    _screencast_listeners.discard(cb)


def screencast_listener_count() -> int:
    return len(_screencast_listeners)


async def _handle_screencast_frame(params: dict) -> None:
    """Ack the frame (required or the stream stalls after one) and fan it
    out to every listener. Best-effort: a dead listener is dropped."""
    if _screencast_cdp is not None:
        try:
            await _screencast_cdp.send(
                "Page.screencastFrameAck", {"sessionId": params.get("sessionId")})
        except Exception:
            pass
    data = str(params.get("data") or "")
    if not data:
        return
    import base64
    try:
        frame = base64.b64decode(data)
    except Exception:
        return
    for cb in list(_screencast_listeners):
        try:
            await cb(frame, params.get("metadata") or {})
        except Exception:
            _screencast_listeners.discard(cb)


async def ensure_screencast(tab: str | None = None) -> str:
    """Start the CDP screencast on the tab (idempotent — switching tabs
    restarts the session on the new page). Returns the page URL."""
    global _screencast_cdp, _screencast_tab
    target = tab or _active_tab
    page = await _page(target)
    if _screencast_cdp is not None and _screencast_tab == target:
        return page.url or ""
    await stop_screencast()
    cdp = await page.context.new_cdp_session(page)

    def _on_frame(params: dict) -> None:
        # Playwright CDP handlers are sync; the async fan-out runs as a task
        try:
            asyncio.get_running_loop().create_task(_handle_screencast_frame(params))
        except RuntimeError:
            pass

    cdp.on("Page.screencastFrame", _on_frame)
    await cdp.send("Page.startScreencast", {
        "format": "jpeg", "quality": 55,
        "maxWidth": 1024, "maxHeight": 640, "everyNthFrame": 2,
    })
    _screencast_cdp = cdp
    _screencast_tab = target
    return page.url or ""


async def stop_screencast() -> None:
    """Stop the stream and detach the CDP session (viewer count hit zero)."""
    global _screencast_cdp, _screencast_tab
    if _screencast_cdp is None:
        return
    try:
        await _screencast_cdp.send("Page.stopScreencast")
    except Exception:
        pass
    try:
        await _screencast_cdp.detach()
    except Exception:
        pass
    _screencast_cdp = None
    _screencast_tab = None


def current_tab_url() -> str:
    """Best-effort sync URL of the active tab (viewer header line)."""
    t = _active_tab
    for pages in (_cdp_pages, _pages):
        pg = pages.get(t)
        if pg is not None:
            try:
                return pg.url or ""
            except Exception:
                return ""
    return ""


# ---- registration ---------------------------------------------------------

# CDP-attach consent escalation (audit 2026-10): after ONE explicit
# browser_connect, these auto-consent tools route through the USER'S REAL
# browser (their sessions/cookies) — steering clicks/fills or snapshotting
# other logged-in tabs is then a per_turn decision, not silent. Universe's
# own Chromium (no CDP) keeps the original auto consent.
#
# The URL-bearing tools ALSO escalate on local-network targets: reaching
# loopback/private space from the browser (dev server, the bridge, LAN,
# metadata endpoints) is a per_turn decision even without CDP — the m1
# web-tool guard hard-refuses there, but a BROWSER is a legitimate way to
# look at localhost, so it asks instead of refusing.
_CDP_ESCALATED_TOOLS = ("BrowserClick", "BrowserFill", "BrowserSnapshot",
                        "BrowserExtract", "BrowserConsole", "BrowserUpload")


def _browser_url_escalator(args: dict) -> bool:
    """Consent escalator for BrowserNavigate/BrowserTabs: per_turn when the
    user's real browser is attached, or when the target is local-network."""
    if _cdp_browser is not None:
        return True
    url = str(args.get("url") or "")
    if not url:
        return False
    from connectors.builtin.misc import _url_is_private_target
    return _url_is_private_target(url)


def register(reg: ToolRegistry) -> None:
    """Register all 10 browser tools. Consents follow the existing convention:
    navigating/snapshot/extract/console-read are low-risk (auto); upload + CDP
    touch the user's filesystem/real sessions (per_turn / explicit)."""
    from security.capability import register_consent_escalator
    for _name in _CDP_ESCALATED_TOOLS:
        register_consent_escalator(
            _name, lambda _args: _cdp_browser is not None)
    for _name in ("BrowserNavigate", "BrowserTabs"):
        register_consent_escalator(_name, _browser_url_escalator)
    reg.register(Tool(
        name="BrowserNavigate", capability="network.req",
        description="Open a URL in a tab and wait for load",
        handler=browser_navigate, consent="auto",
        # file:// removed (audit 2026-09): navigating Chromium at local files
        # with consent=auto was an unrestricted read path around file.read's
        # project-root scoping
        resources=["url:https://*", "url:http://*"],
    ))
    reg.register(Tool(
        name="BrowserClick", capability="network.req",
        description="Click an element by selector or text description",
        handler=browser_click, consent="auto",
        resources=["url:https://*"],
    ))
    reg.register(Tool(
        name="BrowserFill", capability="network.req",
        description="Fill one or more form fields",
        handler=browser_fill, consent="auto",
        resources=["url:https://*"],
    ))
    reg.register(Tool(
        name="BrowserSnapshot", capability="network.req",
        description="Return the page accessibility tree (tagged [UNTRUSTED:web])",
        handler=browser_snapshot, consent="auto",
        resources=["url:https://*"],
    ))
    reg.register(Tool(
        name="BrowserScreenshot", capability="network.req",
        description="Capture a PNG screenshot (path or base64)",
        handler=browser_screenshot, consent="auto",
        # path declared (audit 2026-09): with only url: resources the old
        # gate never checked `path`, letting a screenshot overwrite any
        # absolute path on disk at consent=auto
        resources=["url:https://*", "path:glob:**/*"],
    ))
    reg.register(Tool(
        name="BrowserExtract", capability="network.req",
        description="Pull tables/lists/structured data from a page as JSON",
        handler=browser_extract, consent="auto",
        resources=["url:https://*"],
    ))
    reg.register(Tool(
        name="BrowserConsole", capability="network.req",
        description="Read browser console logs OR evaluate JS (tagged [UNTRUSTED:web])",
        # eval raised to per_turn (audit 2026-09): after one CDP connect it
        # runs arbitrary JS in the user's logged-in browser with their cookies
        handler=browser_console, consent="per_turn",
        resources=["url:https://*"],
    ))
    reg.register(Tool(
        name="BrowserTabs", capability="network.req",
        description="Open / switch / close / list browser tabs",
        handler=browser_tabs, consent="auto",
        resources=["url:https://*", "url:http://*"],
    ))
    reg.register(Tool(
        name="BrowserUpload", capability="connector.run",
        description="Upload a local file into a page's file input",
        handler=browser_upload, consent="per_turn",
        resources=["path:glob:**/*", "url:https://*"],
    ))
    reg.register(Tool(
        name="BrowserConnect", capability="connector.run",
        description="Attach via CDP to the user's running Chrome/Brave instance",
        handler=browser_connect, consent="explicit",
        resources=["url:http://127.0.0.1:*"],
    ))
