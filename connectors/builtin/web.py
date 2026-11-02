"""connectors/builtin/web.py — web_search + web_fetch + web_research tools.

Phase 2 rewrite: the previous implementation imported a single-platform SDK
that only works inside one specific environment and broke Universe's local-
first promise everywhere else. This rewrite is fully local-first:

  * web_search  scrapes https://html.duckduckgo.com/html/?q=... directly (the
                 well-precedented static HTML endpoint, no API key — see
                 nickclyde/duckduckgo-mcp-server, MIT). Parses with
                 beautifulsoup4. Tagged [UNTRUSTED:web].
  * web_fetch   primary path: httpx. Fallback: curl_cffi (impersonates a real
                 Chrome TLS fingerprint) for sites that reject a plain Python
                 User-Agent (Cloudflare-protected). Tagged [UNTRUSTED:web].
  * web_research a compound tool: runs the search, fetches the top few results
                 in parallel, returns them ranked by relevance to the question.

No API key is required anywhere. All retrieved strings are tagged
[UNTRUSTED:web] so they can never be followed as instructions (§10).
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus, urlparse

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry
from connectors.builtin.misc import _RedirectBlocked, _ssrf_guard


# A real, modern browser User-Agent. Some sites (Cloudflare-protected) reject
# any UA that looks like Python's default; this one is what a real Chrome
# browser sends, and pairs with curl_cffi's TLS fingerprint for the fallback.
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _parse_ddg_html(html: str, max_results: int = 5) -> list[dict]:
    """Parse DuckDuckGo's static HTML results page into a list of
    {title, url, snippet} dicts. Robust to small markup variations: tries the
    structured result class first, falls back to scanning <a class=result__a>."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    # Primary selector: every result is a <div class="result ..."> or
    # <div class="web-result">. Inside each: an <a class="result__a"> for the
    # link, and an <a class="result__snippet"> for the snippet.
    for a in soup.select("a.result__a"):
        if len(out) >= max_results:
            break
        title = a.get_text(strip=True)
        href = a.get("href", "")
        # DDG wraps URLs in /l/?uddg=<encoded>; unwrap.
        url = _unwrap_ddg_url(href)
        # Find the snippet associated with this result. DDG puts it in a sibling
        # <a class="result__snippet"> inside the same result block.
        snippet = ""
        parent = a.find_parent(["div", "li"]) or a.parent
        if parent is not None:
            snip_el = parent.select_one("a.result__snippet") or parent.select_one(".result__snippet")
            if snip_el is not None:
                snippet = snip_el.get_text(" ", strip=True)
        if title and url:
            out.append({
                "title": title,
                "url": url,
                "snippet": f"[UNTRUSTED:web] {snippet[:300]}",
            })
    return out


def _unwrap_ddg_url(href: str) -> str:
    """DDG wraps redirect URLs as /l/?uddg=<encoded>&rut=... . Unwrap to the
    real URL. Falls back to the raw href if not wrapped."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            from urllib.parse import unquote
            return unquote(uddg)
    return href


def web_search(query: str = "", max_results: int = 5, q: str = "",
               content: str = "", cmd: str = "") -> dict:
    """Search the web via DuckDuckGo's static HTML endpoint. No API key.
    Returns results tagged [UNTRUSTED:web].

    DuckDuckGo sometimes serves an anti-bot challenge page (HTTP 202 with the
    string 'anomaly' in the body) to plain Python User-Agents. When that
    happens, transparently retry via curl_cffi (Chrome TLS fingerprint)."""
    query = str(query or q or content or cmd)
    if not query.strip():
        return {"ok": False, "error": "web_search requires a non-empty query",
                "query": query, "results": []}
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    primary_err: str | None = None
    try:
        import httpx
        r = httpx.get(url, timeout=15.0, follow_redirects=True,
                      headers={"User-Agent": _UA,
                               "Accept": "text/html,application/xhtml+xml",
                               "Accept-Language": "en-US,en;q=0.9"})
        body = r.text or ""
        # 202 + 'anomaly' = DDG anti-bot challenge — fall back to curl_cffi
        if r.status_code == 202 and "anomaly" in body.lower():
            return _ddg_search_via_curl_cffi(query, max_results)
        if r.status_code != 200:
            primary_err = f"DDG returned HTTP {r.status_code}"
        else:
            results = _parse_ddg_html(body, max_results=max_results)
            # Retry any empty 200 response through a real Chrome TLS fingerprint;
            # DDG may return a consent/challenge variant without the word anomaly.
            if not results:
                try:
                    fallback = _ddg_search_via_curl_cffi(query, max_results)
                    if fallback.get("results"):
                        return fallback
                except Exception:
                    pass
            return {"ok": True, "query": query, "results": results,
                    "tag": "[UNTRUSTED:web]",
                    "source": "duckduckgo/html"}
    except Exception as e:
        # last resort: try curl_cffi directly — but report BOTH failures.
        # The old code reported only the httpx error when the fallback
        # also failed, hiding the actual last-mile cause.
        primary_err = f"httpx path failed: {e}"
    try:
        return _ddg_search_via_curl_cffi(query, max_results)
    except Exception as fallback_err:
        return {"ok": False,
                "error": f"web_search failed ({primary_err}; "
                         f"curl_cffi fallback also failed: {fallback_err})",
                "query": query, "results": []}


def _ddg_search_via_curl_cffi(query: str, max_results: int) -> dict:
    """Fallback search path that impersonates Chrome's TLS fingerprint, used
    when httpx gets DDG's anti-bot challenge page."""
    from urllib.parse import quote_plus
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    from curl_cffi import requests as cffi_requests
    r = cffi_requests.get(url, impersonate="chrome120", timeout=20.0)
    if r.status_code != 200:
        return {"ok": False, "error": f"DDG (curl_cffi) returned HTTP {r.status_code}",
                "query": query, "results": []}
    results = _parse_ddg_html(r.text, max_results=max_results)
    return {"ok": True, "query": query, "results": results,
            "tag": "[UNTRUSTED:web]",
            "source": "duckduckgo/html (curl_cffi)"}


def web_fetch(url: str = "", max_chars: int = 20000, timeout: float = 15.0,
              pattern: str = "", content: str = "") -> dict:
    """Fetch a URL's content. Tagged [UNTRUSTED:web]. Primary path: httpx.
    Fallback for Cloudflare-protected sites: curl_cffi impersonating Chrome.
    SSRF-guarded: loopback/private/link-local targets are refused before the
    request AND after every redirect hop."""
    url = str(url or pattern or content)
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "error": f"invalid url: {url!r}"}
    guard = _ssrf_guard(url)
    if not guard.get("ok"):
        return guard
    # primary path: httpx with a per-request guard hook — every redirect
    # hop re-resolves the new host before the connection is made
    try:
        import httpx

        def _hook(request: httpx.Request) -> None:
            check = _ssrf_guard(str(request.url))
            if not check.get("ok"):
                raise _RedirectBlocked(check.get("error", "redirect target blocked"))

        with httpx.Client(follow_redirects=True, timeout=min(float(timeout), 30.0),
                          event_hooks={"request": [_hook]},
                          headers={"User-Agent": _UA,
                                   "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                   "Accept-Language": "en-US,en;q=0.9"}) as client:
            r = client.get(url)
        if r.status_code == 200:
            return {"ok": True, "url": str(r.url), "title": _extract_title(r.text),
                    "content": f"[UNTRUSTED:web] {r.text[:max_chars]}",
                    "truncated": len(r.text) > max_chars,
                    "status_code": r.status_code,
                    "fetched_via": "httpx"}
        # 403 / 503 typically means Cloudflare blocking — try curl_cffi fallback
        if r.status_code in (403, 503):
            return _fetch_via_curl_cffi(url, max_chars)
        return {"ok": False, "error": f"HTTP {r.status_code}",
                "url": url, "status_code": r.status_code}
    except _RedirectBlocked as e:
        return {"ok": False, "error": f"redirect blocked: {e}", "url": url}
    except Exception:
        # any failure on httpx -> try curl_cffi (guard re-checked inside)
        return _fetch_via_curl_cffi(url, max_chars)


def _fetch_via_curl_cffi(url: str, max_chars: int) -> dict:
    """Fallback fetcher that impersonates a real Chrome TLS fingerprint, for
    sites that reject a plain Python User-Agent (Cloudflare-protected).
    curl_cffi follows redirects INTERNALLY, and the old code only checked
    the final destination AFTER the fetch — content from a redirect that
    already landed on a private target was retrieved and handed to the
    model (audit 2026-10). Now: redirects are followed MANUALLY, each hop
    SSRF-guarded BEFORE it is fetched, capped at 5 hops."""
    guard = _ssrf_guard(url)
    if not guard.get("ok"):
        return guard
    try:
        from curl_cffi import requests as cffi_requests
        current = url
        r = None
        for _hop in range(5):
            r = cffi_requests.get(current, impersonate="chrome120",
                                  timeout=20.0, allow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location", "")
                if not loc:
                    return {"ok": False, "error": "redirect without a location",
                            "url": url}
                from urllib.parse import urljoin
                current = urljoin(current, loc)
                hop_guard = _ssrf_guard(current)
                if not hop_guard.get("ok"):
                    return {"ok": False,
                            "error": f"redirect blocked: {hop_guard.get('error')}",
                            "url": url}
                continue
            break
        else:
            return {"ok": False, "error": "too many redirects (>5)", "url": url}
        return {"ok": True, "url": str(r.url), "title": _extract_title(r.text),
                "content": f"[UNTRUSTED:web] {r.text[:max_chars]}",
                "truncated": len(r.text) > max_chars,
                "status_code": r.status_code,
                "fetched_via": "curl_cffi"}
    except ImportError:
        return {"ok": False, "error": ("web_fetch failed: httpx was blocked and "
                                       "curl_cffi is not installed (pip install curl_cffi)"),
                "url": url}
    except Exception as e:
        return {"ok": False, "error": f"web_fetch failed (httpx + curl_cffi): {e}",
                "url": url}


def _extract_title(html: str) -> str:
    """Cheap title extractor — avoids needing bs4 for the common case."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _relevance(query: str, text: str) -> float:
    """Tiny relevance scorer: count query terms appearing in the text, weighted
    by inverse length so longer pages don't trivially win."""
    if not text:
        return 0.0
    q_terms = [t.lower() for t in re.split(r"\W+", query) if len(t) > 2]
    if not q_terms:
        return 0.0
    body = text.lower()
    hits = sum(1 for t in q_terms if t in body)
    return hits / (1.0 + len(text) / 5000.0)


async def web_research(question: str = "", max_results: int = 5,
                       fetch_top_n: int = 3, query: str = "", url: str = "",
                       content: str = "", pattern: str = "") -> dict:
    """Compound tool: runs web_search, fetches the top `fetch_top_n` results in
    parallel, and returns them ranked by relevance to `question`. Saves an
    agent the round-trips of search → fetch → fetch → fetch."""
    question = str(question or query or content or pattern or url)
    search = web_search(question, max_results=max_results)
    if not search.get("ok"):
        return search
    results = search.get("results", [])
    if not results:
        return {"ok": True, "query": question, "results": [],
                "tag": "[UNTRUSTED:web]", "source": "duckduckgo/html"}

    # fetch the top N in parallel
    top = results[:fetch_top_n]

    async def _fetch_one(r: dict) -> dict:
        # run sync web_fetch in a thread so we don't block the loop
        fetched = await asyncio.to_thread(web_fetch, r["url"], 8000)
        return {**r, "page_text": fetched.get("content", ""),
                "page_title": fetched.get("title", ""),
                "fetch_ok": fetched.get("ok", False)}

    fetched = await asyncio.gather(*(_fetch_one(r) for r in top))
    # rank by relevance to the question
    ranked = sorted(fetched,
                    key=lambda x: _relevance(question, x.get("page_text", "")),
                    reverse=True)
    return {"ok": True, "query": question, "results": ranked,
            "tag": "[UNTRUSTED:web]",
            "source": "duckduckgo/html + parallel fetch"}


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="WebSearch", capability="network.req",
        description="Search the web via DuckDuckGo HTML (no API key; tagged [UNTRUSTED:web])",
        handler=web_search, consent="auto",
        resources=["url:https://*"],
    ))
    reg.register(Tool(
        name="WebFetch", capability="network.req",
        description="Fetch a URL's content (httpx primary, curl_cffi fallback for Cloudflare; tagged [UNTRUSTED:web])",
        handler=web_fetch, consent="auto",
        resources=["url:https://*"],
    ))
    reg.register(Tool(
        name="WebResearch", capability="network.req",
        description="Search + fetch top results in parallel + rank by relevance (tagged [UNTRUSTED:web])",
        handler=web_research, consent="auto",
        resources=["url:https://*"],
    ))
