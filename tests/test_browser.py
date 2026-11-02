"""tests/test_browser.py — Phase 1 real-Playwright browser tests.

CI is the one legitimate headless exception (per Phase 1 spec): we force
UNIVERSE_BROWSER_HEADLESS=1 here because this sandbox has no display server.
The default everywhere else (including `python3 walkthrough.py` and any
interactive `/goal` invocation) is headed, falling back to headless only
when no display is detected — see connectors/builtin/browser.py.

These tests launch a real Chromium and exercise real page state: a real
navigation, a real click that mutates the DOM, a real form fill, a real
extraction, a real screenshot file. No mocks of Playwright.

Serving contract (audit 2026-09): the gate now refuses file:// navigation
(a browser pointed at local files was an unrestricted read path around
file.read's project-root scoping), screenshots/uploads are path-scoped to
the project root, and browser_console eval is per_turn consent. The local
fixture is therefore served over http from an ephemeral loopback server,
screenshot/upload paths live inside the project root, and eval tests grant
the console tool first — exactly the contract a real user gets.
"""
import os
import threading
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

# Force headless BEFORE the registry is constructed (env var read on first call).
os.environ["UNIVERSE_BROWSER_HEADLESS"] = "1"


@lru_cache(maxsize=1)
def _chromium_available() -> bool:
    """Honest environment probe: is a real Chromium actually installed for
    the Playwright driver? (CI sandboxes ship without the browser download;
    the tests below need the REAL thing and skip without it.)"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            return bool(exe) and os.path.exists(exe)
    except Exception:
        return False


def _skip_without_chromium() -> None:
    if not _chromium_available():
        pytest.skip("playwright Chromium is not installed in this environment "
                    "(`playwright install chromium` would enable these)")

_FIXTURE_HTML = """<!doctype html>
<html><head><title>Universe Test Page</title></head>
<body>
  <h1 id="h1">Hello Universe</h1>
  <button id="btn" onclick="document.getElementById('out').textContent='CLICKED'">Click me</button>
  <div id="out">not clicked</div>
  <form id="f">
    <input id="name" type="text" placeholder="name">
    <input id="email" type="email" placeholder="email">
  </form>
  <table id="t">
    <tr><th>k</th><th>v</th></tr>
    <tr><td>a</td><td>1</td></tr>
    <tr><td>b</td><td>2</td></tr>
  </table>
  <ul id="l"><li>alpha</li><li>beta</li><li>gamma</li></ul>
</body></html>"""

_UPLOAD_HTML = """<!doctype html>
<html><head><title>Upload Test</title></head>
<body>
  <input id="file-input" type="file">
  <div id="result">no file</div>
  <script>
    document.getElementById('file-input').addEventListener('change', function() {
      document.getElementById('result').textContent = this.files[0].name;
    });
  </script>
</body></html>"""


@pytest.fixture(scope="session")
def http_root(tmp_path_factory):
    """A directory holding the fixture pages, served over loopback http —
    the gate no longer accepts file:// URLs, so tests must use real http."""
    root = tmp_path_factory.mktemp("gax-http")
    (root / "fixture.html").write_text(_FIXTURE_HTML, encoding="utf-8")
    (root / "upload.html").write_text(_UPLOAD_HTML, encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def local_http_server(http_root):
    """Ephemeral loopback http server for the fixture pages. The handler
    subclass carries the directory — SimpleHTTPRequestHandler reads
    self.directory, and a bare `server.directory = ...` assignment is
    silently ignored (the fixture 404'd and every post-navigate tool
    timed out on the error page)."""
    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(http_root), **kwargs)

        def log_message(self, *args):  # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def local_html_url(local_http_server):
    return f"{local_http_server}/fixture.html"


@pytest.fixture
def upload_html_url(local_http_server):
    return f"{local_http_server}/upload.html"


@pytest.fixture(autouse=True)
async def _reset_browser_between_tests():
    """Each pytest-asyncio test gets a fresh event loop, but the browser module
    holds a process-global Playwright instance bound to whatever loop created
    it. Reset between tests so every test starts a fresh browser on its own
    loop — otherwise the second test hangs waiting on a dead browser."""
    from connectors.builtin.browser import reset_browser_for_tests
    try:
        await reset_browser_for_tests()
    except Exception:
        pass
    yield
    try:
        await reset_browser_for_tests()
    except Exception:
        pass


def _project_root() -> str:
    from config import get_config
    return str(get_config().get("project_root", "") or "")


@pytest.mark.asyncio
async def test_browser_navigate_loads_real_page(fresh_home, local_html_url):
    """Real navigation: title and URL come back from a real Chromium."""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    reg = get_registry()
    r = await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                       args={"url": local_html_url})
    assert r["ok"] is True, f"gate blocked: {r}"
    assert r["result"]["title"] == "Universe Test Page"
    assert r["result"]["url"].startswith("http://127.0.0.1")
    assert r["result"]["status"] == 200


@pytest.mark.asyncio
async def test_browser_file_navigation_is_blocked(fresh_home, tmp_path):
    """audit 2026-09: navigating Chromium at file:// was an unrestricted
    local-file read path around file.read's scoping — the gate must deny it
    even for the whitelisted general agent."""
    from connectors.builtin import get_registry
    p = tmp_path / "secret.html"
    p.write_text("<html><body>secrets</body></html>", encoding="utf-8")
    reg = get_registry()
    r = await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                       args={"url": f"file://{p}"})
    assert r["ok"] is False
    assert r.get("blocked_by") == "resource"


@pytest.mark.asyncio
async def test_browser_click_mutates_real_dom(fresh_home, local_html_url):
    """Real click: the click handler fires and #out's text changes from
    'not clicked' to 'CLICKED'. This is the real resulting state, not a mock."""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    reg = get_registry()
    from security.capability import get_gate
    get_gate().grant("g1", "BrowserConsole")  # eval is per_turn consent now
    await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                   args={"url": local_html_url})
    r = await reg.call("BrowserClick", agent="universe", turn_id="g1",
                       args={"selector": "#btn"})
    assert r["ok"] is True
    # verify the resulting DOM state by reading the changed element via JS
    r2 = await reg.call("BrowserConsole", agent="universe", turn_id="g1",
                        args={"action": "eval",
                              "js": "() => document.getElementById('out').textContent"})
    assert "CLICKED" in r2["result"]["value"]


@pytest.mark.asyncio
async def test_browser_fill_writes_real_input_values(fresh_home, local_html_url):
    """Real fill: the form fields now contain the values we typed."""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    reg = get_registry()
    from security.capability import get_gate
    get_gate().grant("g1", "BrowserConsole")
    await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                   args={"url": local_html_url})
    r = await reg.call("BrowserFill", agent="universe", turn_id="g1",
                       args={"fields": {"#name": "Ada", "#email": "ada@example.com"}})
    assert r["ok"] is True
    assert "#name" in r["result"]["filled"]
    r2 = await reg.call("BrowserConsole", agent="universe", turn_id="g1",
                        args={"action": "eval",
                              "js": "() => ({name: document.getElementById('name').value, "
                                   "  email: document.getElementById('email').value})"})
    assert 'Ada' in r2["result"]["value"]
    assert 'ada@example.com' in r2["result"]["value"]


@pytest.mark.asyncio
async def test_browser_extract_pulls_real_table_and_list(fresh_home, local_html_url):
    """Real extract: the table comes back as a 2D array, the list as a list of
    strings — both match the fixture exactly."""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    import json as _json
    reg = get_registry()
    await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                   args={"url": local_html_url})
    r = await reg.call("BrowserExtract", agent="universe", turn_id="g1", args={})
    assert r["ok"] is True
    # data is tagged [UNTRUSTED:web] + JSON
    raw = r["result"]["data"].replace("[UNTRUSTED:web] ", "", 1)
    data = _json.loads(raw)
    assert data["tables"][0][0] == ["k", "v"]
    assert data["tables"][0][1] == ["a", "1"]
    assert data["tables"][0][2] == ["b", "2"]
    assert data["lists"][0] == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_browser_screenshot_writes_real_png_file(fresh_home, local_html_url):
    """Real screenshot: a non-empty PNG lands INSIDE the project root (the
    gate path-scopes screenshots since the audit — any absolute path used
    to be writable at consent=auto)."""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    reg = get_registry()
    await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                   args={"url": local_html_url})
    from pathlib import Path
    out = Path(_project_root()) / "shot.png"
    r = await reg.call("BrowserScreenshot", agent="universe", turn_id="g1",
                       args={"path": str(out), "full_page": True})
    assert r["ok"] is True, f"gate blocked: {r}"
    assert out.exists()
    # real PNG signature: 89 50 4E 47
    with open(out, "rb") as fh:
        sig = fh.read(4)
    assert sig == b"\x89PNG"
    assert out.stat().st_size > 1000  # not a stub


@pytest.mark.asyncio
async def test_browser_screenshot_outside_root_is_denied(fresh_home, local_html_url,
                                                        tmp_path):
    """audit 2026-09: browser_screenshot with only url: resources never had
    its path arg checked — an out-of-root absolute path must be denied."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = await reg.call("BrowserScreenshot", agent="universe", turn_id="g1",
                       args={"path": str(tmp_path / "evil.png")})
    assert r["ok"] is False
    assert r.get("blocked_by") == "resource"


@pytest.mark.asyncio
async def test_browser_tabs_open_switch_close(fresh_home, local_html_url):
    """Real tab lifecycle: open a second tab, switch to it, then close it.
    The 'list' action reflects real state at every step."""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    reg = get_registry()
    # default tab exists after navigation
    await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                   args={"url": local_html_url, "tab": "default"})
    # open a second tab
    r = await reg.call("BrowserTabs", agent="universe", turn_id="g1",
                       args={"action": "open", "tab": "extra", "url": local_html_url})
    assert r["ok"] is True
    assert r["result"]["tab"] == "extra"
    # list shows both
    r = await reg.call("BrowserTabs", agent="universe", turn_id="g1",
                       args={"action": "list"})
    assert "default" in r["result"]["tabs"]
    assert "extra" in r["result"]["tabs"]
    # close extra
    r = await reg.call("BrowserTabs", agent="universe", turn_id="g1",
                       args={"action": "close", "tab": "extra"})
    assert r["ok"] is True
    r = await reg.call("BrowserTabs", agent="universe", turn_id="g1",
                       args={"action": "list"})
    assert "extra" not in r["result"]["tabs"]


@pytest.mark.asyncio
async def test_browser_snapshot_returns_untrusted_tag(fresh_home, local_html_url):
    """Real snapshot: the accessibility tree is tagged [UNTRUSTED:web] so an
    agent can never treat it as an instruction."""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    reg = get_registry()
    await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                   args={"url": local_html_url})
    r = await reg.call("BrowserSnapshot", agent="universe", turn_id="g1", args={})
    assert r["ok"] is True
    assert r["result"]["snapshot"].startswith("[UNTRUSTED:web]")
    # the page heading appears in the a11y tree somewhere
    assert "Hello Universe" in r["result"]["snapshot"] or "Universe Test Page" in r["result"]["snapshot"]


@pytest.mark.asyncio
async def test_browser_navigate_blocked_for_non_browser_agent(fresh_home, local_html_url):
    """Phase 0.1 fail-closed enforcement, current reality: the specialist
    roster collapsed into ONE universe agent, so the retired 'code' name is
    an UNREGISTERED agent — denied outright (unknown_agent strictly
    subsumes the old whitelist denial), browser or not."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = await reg.call("BrowserNavigate", agent="code", turn_id="g1",
                       args={"url": local_html_url})
    assert r["ok"] is False
    assert r["blocked_by"] == "unknown_agent"


@pytest.mark.asyncio
async def test_browser_upload_sets_file_input(fresh_home, upload_html_url):
    """Real upload: browser_upload() sets an IN-ROOT local file into an
    <input type=file> and the change handler fires — the result div shows
    the filename. (Out-of-root uploads are denied by the gate since the
    audit — the old test passed through the vacuous url-resource branch.)"""
    _skip_without_chromium()
    from connectors.builtin import get_registry
    from pathlib import Path
    reg = get_registry()
    from security.capability import get_gate
    target = Path(_project_root()) / "payload.txt"
    target.write_text("universe upload test payload", encoding="utf-8")
    await reg.call("BrowserNavigate", agent="universe", turn_id="g1",
                   args={"url": upload_html_url})
    get_gate().grant("g1", "BrowserConsole")
    r = await reg.call("BrowserUpload", agent="universe", turn_id="g1",
                       args={"selector": "#file-input", "file_path": str(target)})
    assert r["ok"] is True, f"gate blocked: {r}"
    assert r["result"]["ok"] is True, f"handler failed: {r['result']}"
    r2 = await reg.call("BrowserConsole", agent="universe", turn_id="g1",
                        args={"action": "eval",
                              "js": "() => document.getElementById('result').textContent"})
    assert "payload.txt" in r2["result"]["value"]


@pytest.mark.asyncio
async def test_browser_upload_outside_root_is_denied(fresh_home, upload_html_url,
                                                    tmp_path):
    """audit 2026-09: an out-of-root file_path used to sail through the
    url-resource branch — the path scope must now deny it."""
    from connectors.builtin import get_registry
    reg = get_registry()
    target = tmp_path / "id_rsa"
    target.write_text("pretend key", encoding="utf-8")
    r = await reg.call("BrowserUpload", agent="universe", turn_id="g1",
                       args={"selector": "#file-input", "file_path": str(target)})
    assert r["ok"] is False
    assert r.get("blocked_by") == "resource"


@pytest.mark.asyncio
async def test_browser_upload_missing_file_returns_error(fresh_home):
    """browser_upload with a non-existent IN-ROOT file must return ok=False
    from the handler before touching the browser at all — the file check is
    the first thing the handler does, so no Chromium is needed here."""
    from connectors.builtin import get_registry
    from pathlib import Path
    reg = get_registry()
    r = await reg.call("BrowserUpload", agent="universe", turn_id="g1",
                       args={"selector": "#file-input",
                             "file_path": str(Path(_project_root()) / "does_not_exist_universe.xyz")})
    # gate passes (path is in-root); handler catches file-not-found
    # m1 truth-envelope: the handler's ok IS the envelope's ok — a missing
    # file is an ok=False call carrying the handler's error
    inner = r.get("result", r)
    assert inner["ok"] is False, r
    assert "not found" in inner["error"]


@pytest.mark.asyncio
async def test_browser_console_eval_requires_consent(fresh_home, local_html_url):
    """audit 2026-09: eval raised from auto to per_turn — after one CDP
    connect it runs arbitrary JS in the user's logged-in browser, so an
    ungranted call must be consent-blocked."""
    from connectors.builtin import get_registry
    reg = get_registry()
    from security.capability import get_gate
    gate = get_gate()
    gate.set_auto_grant(False)
    r = await reg.call("BrowserConsole", agent="universe", turn_id="g-c",
                       args={"action": "eval", "js": "() => 1"})
    assert r["ok"] is False
    assert r.get("needs_consent") is True


@pytest.mark.asyncio
async def test_browser_connect_requires_explicit_consent(fresh_home):
    """browser_connect is declared consent=explicit. Without a grant the gate
    must block it — verifying capability.py's per-tool consent fix applies."""
    from security.capability import get_gate
    from connectors.builtin import get_registry
    get_gate().set_auto_grant(False)
    reg = get_registry()
    r = await reg.call("BrowserConnect", agent="universe", turn_id="g-cdp",
                       args={"host": "127.0.0.1", "port": 9222})
    assert r["ok"] is False
    assert r.get("needs_consent") is True


@pytest.mark.asyncio
async def test_browser_connect_does_not_launch_own_browser_on_failure(fresh_home):
    """Old browser_connect called _ensure_browser() before the CDP connect,
    launching Universe's own Chromium even when the CDP target didn't exist.
    Fixed: uses _ensure_playwright() instead. After a failed connect,
    _browser must remain None — no wasteful Universe-browser side-effect."""
    from connectors.builtin import get_registry
    import connectors.builtin.browser as bmod
    reg = get_registry()
    # auto_grant=True (fresh_home default) so gate passes; handler fails at TCP
    r = await reg.call("BrowserConnect", agent="universe", turn_id="g-cdp",
                       args={"host": "127.0.0.1", "port": 9999})
    # m1 truth-envelope: CDP connect failure is an ok=False call
    assert r.get("result", r).get("ok") is False, r
    assert bmod._browser is None, (
        "browser_connect launched Universe's own Chromium as a side-effect — "
        "_ensure_browser() bug was not removed."
    )


@pytest.mark.asyncio
async def test_browser_connect_cleans_up_cdp_browser_on_failure(fresh_home):
    """Old browser_connect stored the CDP handle in a local var `b` that was
    GC'd immediately, dropping the connection. Fixed: stored in _cdp_browser.
    On failure, _cdp_browser must be cleaned up to None (not a dangling object)."""
    from connectors.builtin import get_registry
    import connectors.builtin.browser as bmod
    reg = get_registry()
    r = await reg.call("BrowserConnect", agent="universe", turn_id="g-cdp2",
                       args={"host": "127.0.0.1", "port": 9998})
    # m1 truth-envelope: CDP connect failure is an ok=False call
    assert r.get("result", r).get("ok") is False, r
    assert bmod._cdp_browser is None, (
        "Failed CDP connect left a dangling _cdp_browser — "
        "cleanup on failure is broken."
    )
