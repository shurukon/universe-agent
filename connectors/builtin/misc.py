"""connectors/builtin/misc.py — remaining tool stubs referenced by agents.

These cover the tool names whitelisted by the 12 Core Agents that aren't in
the core builtins: test_runner, code_analyzer, svg_gen,
charting, browser_control, screenshot, ssh, http_client,
openapi_parser, static_analysis, vuln_scanner, task_graph.
Each is a real, minimal implementation (not a no-op) routed through the gate.

Security notes (m1 hardening):
- ssh builds an argv list — the old f-string let host/command inject shell.
- http_client / graphql_client / openapi_parser go through _ssrf_guard,
  which rejects loopback/private/link-local/metadata targets before the
  request and after every redirect.
"""
from __future__ import annotations

import httpx  # referenced in _httpx_client_with_guard's annotation (F821)
import ipaddress
import re
import socket
import urllib.parse

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry


# ---- SSRF guard ---------------------------------------------------------------

def _ip_is_forbidden(ip: object) -> bool:
    if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return True  # resolve failure, wildcards, anything odd: fail closed
    # an IPv4-mapped IPv6 address (::ffff:127.0.0.1) is NOT caught by
    # is_loopback on the v6 object — canonicalize to the mapped v4 first
    # (audit 2026-10)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
    )


def _ssrf_guard(url: str, allow_private: bool = False) -> dict:
    """Reject requests that would reach this machine or the local network.
    DNS-resolves the host so attacker-controlled names pointing at 127.0.0.1
    or RFC1918 space are caught, not just literal IPs.

    allow_private=True is the BROWSER-family contract: scheme and host-shape
    problems stay hard-refused, while loopback/private targets are permitted
    at this layer because the browser consent escalator has already lifted
    them to per_turn — legitimate localhost browsing (dev servers, the
    desktop bridge) stays possible as an explicit user-approved decision,
    never a silent auto one."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return {"ok": False, "error": f"malformed URL: {url[:120]}"}
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"scheme not allowed: {parsed.scheme!r}"}
    host = parsed.hostname or ""
    if not host:
        return {"ok": False, "error": "URL has no host"}
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return {"ok": False, "error": f"cannot resolve host {host!r}: {e}"}
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _ip_is_forbidden(ip):
            if allow_private:
                break  # consent escalator owns this case — handler proceeds
            return {"ok": False,
                    "error": f"blocked: {host} resolves to private/loopback address {ip}"}
    return {"ok": True, "host": host}


def _url_is_private_target(url: str) -> bool:
    """True when the URL's host resolves into loopback/private/link-local
    space (or anything unresolvable — fail closed). Consent-escalator input:
    reaching the local network from the browser family is a per_turn
    decision, not silent auto consent."""
    check = _ssrf_guard(url)
    if check.get("ok"):
        return False
    err = str(check.get("error", ""))
    if "scheme not allowed" in err or "malformed" in err or "no host" in err:
        return False  # hard-refused at the handler anyway; not an escalation case
    return True  # private target, unresolvable host: ask the user


class _RedirectBlocked(Exception):
    """Raised inside httpx event hooks when a redirect goes somewhere bad."""


def _httpx_client_with_guard() -> "httpx.Client":
    import httpx

    def _hook(request: httpx.Request) -> None:
        check = _ssrf_guard(str(request.url))
        if not check.get("ok"):
            raise _RedirectBlocked(check.get("error", "redirect target blocked"))

    return httpx.Client(follow_redirects=True, timeout=15.0,
                        event_hooks={"request": [_hook]})


def _make(name: str, capability: str, desc: str, fn, consent: str = "per_turn",
          resources=None, path_args=None):
    return Tool(name=name, capability=capability, description=desc, handler=fn,
                consent=consent, resources=resources or [],
                path_args=path_args)


def _within_scope(path: str) -> bool:
    """Same scope discipline as connectors/builtin/files.py: archive reads
    and writes whole directory trees, so both source AND target are checked
    here in addition to the gate's resource patterns. Delegates to
    files._within_scope so project-root + file_allowlist stay defined in
    exactly one place."""
    from connectors.builtin.files import _within_scope as _files_scope
    try:
        return bool(_files_scope(path))
    except Exception:
        return False


def register(reg: ToolRegistry) -> None:
    # test_runner
    def test_runner(command: str = "pytest", cwd: str = ".", cmd: str = "",
                    test_command: str = "") -> dict:
        command = str(cmd or test_command or command)
        # audit 2026-10: rode _run raw — on windows_native machines this
        # bypassed the sandbox Bash goes through; now rides the shared
        # sandboxed dispatch (same platform behavior as shell_exec)
        from connectors.builtin.shell import _run_sandboxed_dispatch
        return _run_sandboxed_dispatch(command, cwd=cwd, timeout=120)
    reg.register(_make("TestRunner", "Bash", "Run a test command", test_runner, "per_turn", ["cwd"]))

    # code_analyzer (static checks — argv, target as a separate argument)
    def code_analyzer(path: str = ".", checks: str = "ruff check", cmd: str = "",
                      command: str = "", cwd: str = ".") -> dict:
        target = str(path or ".")
        tool = str(cmd or command or checks or "ruff check").strip()
        # fixed vocabulary of check runners; `target` rides as the trailing
        # argv element so a path containing shell metacharacters can't
        # become a command
        runners = {
            "ruff check": ["ruff", "check"],
            "mypy": ["mypy"],
            "flake8": ["flake8"],
            "eslint": ["npx", "--no-install", "eslint"],
        }
        argv = runners.get(tool)
        if argv is None:
            return {"ok": False,
                    "error": f"unknown checks tool {tool!r}; use one of {sorted(runners)}"}
        import subprocess
        import time
        start = time.time()
        try:
            proc = subprocess.run([*argv, target], cwd=str(cwd or "."),
                                  capture_output=True, text=True,
                                  timeout=60, shell=False)
            return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                    "stdout": proc.stdout, "stderr": proc.stderr,
                    "elapsed_ms": int((time.time() - start) * 1000)}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout",
                    "elapsed_ms": int((time.time() - start) * 1000)}
    reg.register(_make("CodeAnalyzer", "Bash", "Static code analysis", code_analyzer, "per_turn", ["cwd"]))

    # static_analysis / vuln_scanner
    def static_analysis(path: str = ".") -> dict:
        import subprocess
        import time
        start = time.time()
        try:
            # `|| true` swallowed the real failure — argv + real returncode
            proc = subprocess.run(["ruff", "check", "--select", "ALL", str(path or ".")],
                                   capture_output=True, text=True, timeout=60,
                                   shell=False)
            return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                    "stdout": proc.stdout, "stderr": proc.stderr,
                    "elapsed_ms": int((time.time() - start) * 1000)}
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"ok": False, "error": f"ruff failed to run: {e}",
                    "elapsed_ms": int((time.time() - start) * 1000)}
    reg.register(_make("StaticAnalysis", "Bash", "Run ruff static analysis", static_analysis, "per_turn", ["cwd"]))

    def vuln_scanner(path: str = ".") -> dict:
        import subprocess
        import time
        start = time.time()
        # try pip-audit, then npm audit — as argv, not a shell chain; the old
        # `||` chain reported success whenever the LAST tool was missing
        for argv in (["pip-audit"], ["npm", "audit", "--json"]):
            try:
                proc = subprocess.run(argv, cwd=str(path or "."), capture_output=True,
                                      text=True, timeout=60, shell=False)
                if proc.returncode == 0 or proc.stdout.strip():
                    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                            "stdout": proc.stdout, "stderr": proc.stderr,
                            "tool": argv[0],
                            "elapsed_ms": int((time.time() - start) * 1000)}
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": f"timeout running {argv[0]}",
                        "elapsed_ms": int((time.time() - start) * 1000)}
        return {"ok": False, "error": "neither pip-audit nor npm audit is installed",
                "elapsed_ms": int((time.time() - start) * 1000)}
    reg.register(_make("VulnScanner", "Bash", "Run dependency vuln scan", vuln_scanner, "per_turn", ["cwd"]))

    # svg_gen — generate a simple SVG (label/color escaped: they used to
    # be interpolated raw, so `label="</text><script>…"` was live markup
    # injection into the produced artifact — audit 2026-10)
    def svg_gen(label: str = "diagram", color: str = "#6B21A8") -> dict:
        import html
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">'
               f'<rect width="400" height="200" fill="{html.escape(str(color), quote=True)}"/>'
               f'<text x="200" y="100" font-family="sans-serif" font-size="24" '
               f'fill="white" text-anchor="middle">{html.escape(str(label))}</text></svg>')
        return {"ok": True, "svg": svg}
    reg.register(_make("SvgGen", "Write", "Generate a simple SVG", svg_gen, "auto"))



    # charting — produce a simple ASCII/text chart
    def charting(data: list | str | None = None, kind: str = "bar", title: str = "",
                 path: str = "", content: str = "", cmd: str = "",
                 pattern: str = "") -> dict:
        if data is None:
            data = content or cmd or pattern
        if not data and path:
            try:
                from pathlib import Path
                data = Path(path).read_text(encoding="utf-8")
            except Exception:
                data = path
        if isinstance(data, str):
            data = [{"label": line[:40], "value": i + 1}
                    for i, line in enumerate(data.splitlines()) if line.strip()]
        if not data:
            return {"ok": False, "error": "no data"}
        lines = [title] if title else []
        if kind not in ("bar",):
            # the old fallback returned ok=True with an empty chart for any
            # unknown kind — the agent believed a chart existed (audit 2026-10)
            return {"ok": False, "error": f"unknown chart kind {kind!r} (supported: 'bar')"}
        for item in data:
            if isinstance(item, dict):
                label = str(item.get("label", item.get("name", "")))
                val = float(item.get("value", 0))
                bar = "#" * int(min(50, val))
                lines.append(f"{label:20s} |{bar} {val}")
        return {"ok": True, "chart": "\n".join(lines)}
    reg.register(_make("Charting", "Read", "Generate a text chart", charting,
                       "auto", ["path:glob:**/*"]))

    # browser_control / screenshot were Phase 0 stubs; Phase 1 replaces them
    # entirely with the real Playwright tools in connectors/builtin/browser.py.
    # (Do NOT re-add them here — Browser Agent's tool_whitelist_names references
    # the real names now.)

    # ssh — argv list, never a shell string (the old f-string let
    # host="1.2.3.4 rm -rf" or command="'; calc.exe" inject arbitrary shell)
    def ssh(host: str, command: str) -> dict:
        import subprocess
        import time
        host = str(host or "").strip()
        command = str(command or "")
        if not host or not command:
            return {"ok": False, "error": "ssh requires host and command"}
        if not re.match(r"^[A-Za-z0-9._@:-]+$", host):
            return {"ok": False,
                    "error": "invalid host (allowed: letters digits . _ @ : -)"}
        start = time.time()
        try:
            # lowercase: PATH lookup found "Ssh" on Windows only because
            # its lookup is case-insensitive — Linux raised
            # FileNotFoundError (audit 2026-10)
            proc = subprocess.run(["ssh", host, command], capture_output=True,
                                  text=True, timeout=60, shell=False)
            return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                    "stdout": proc.stdout, "stderr": proc.stderr,
                    "elapsed_ms": int((time.time() - start) * 1000),
                    "cmd": f"ssh {host} <command>"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout",
                    "elapsed_ms": int((time.time() - start) * 1000)}
    reg.register(_make("Ssh", "network.req", "Run a command over SSH", ssh, "explicit", ["url:*"]))


    # pdf / docx / xlsx / pptx — NOT implemented as local tool handlers
    # (m4 honesty fix). The old stubs returned ok=True with a "uses
    # Anthropic skill" note while doing nothing — agents cited phantom
    # document operations as completed work. They now fail closed with an
    # explicit not-implemented error so no plan can depend on them.

    # http_client / openapi_parser — SSRF-guarded (pre-check + per-redirect hook)
    def http_client(method: str = "GET", url: str = "", headers: dict | None = None,
                    body: str = "") -> dict:
        method = str(method or "GET").upper()
        if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            return {"ok": False, "error": f"method not allowed: {method}"}
        pre = _ssrf_guard(url)
        if not pre.get("ok"):
            return pre
        try:
            with _httpx_client_with_guard() as client:
                r = client.request(method, url, headers=headers or {},
                                   content=body or None)
                return {"ok": True, "status": r.status_code,
                        "headers": dict(r.headers),
                        "body": f"[UNTRUSTED:web] {r.text[:5000]}"}
        except _RedirectBlocked as e:
            return {"ok": False, "error": f"redirect blocked: {e}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    reg.register(_make("HttpClient", "network.req", "HTTP client (SSRF-guarded)", http_client, "per_turn", ["url:https://*"]))

    def openapi_parser(url: str = "", spec: str = "") -> dict:
        import json
        try:
            data = json.loads(spec) if spec else {}
            if url and not data:
                pre = _ssrf_guard(url)
                if not pre.get("ok"):
                    return pre
                with _httpx_client_with_guard() as client:
                    data = client.get(url).json()
            paths = list(data.get("paths", {}).keys())
            return {"ok": True, "paths": paths, "title": data.get("info", {}).get("title", "")}
        except _RedirectBlocked as e:
            return {"ok": False, "error": f"redirect blocked: {e}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    reg.register(_make("OpenapiParser", "network.req", "Parse an OpenAPI spec (SSRF-guarded)", openapi_parser, "per_turn", ["url:https://*"]))

    # task_graph removed (m2): the Step/TaskGraph scaffold was never wired
    # into the orchestrator — planning agents use memory_query + their own
    # whitelists instead.

    # ---- Phase 3 per-agent tools -----------------------------------------

    # code_execute — run a short isolated code snippet, lighter than shell.exec
    def code_execute(code: str = "", language: str = "python",
                     timeout: int = 30, cmd: str = "", content: str = "",
                     script: str = "") -> dict:
        """Run a short code snippet (Python or JS) in a subprocess and return
        stdout / stderr / result. Uses a temp file + subprocess for isolation.
        Lighter than shell.exec because it doesn't need a full shell session."""
        import tempfile
        import subprocess
        import os
        import sys
        code = str(code or script or content or cmd)
        if not code:
            return {"ok": False, "error": "code_execute requires `code`"}
        if language == "python":
            suffix = ".py"
            # the running interpreter, not a hardcoded name — `python3` does
            # not exist on Windows
            def cmd_builder(p):
                return [sys.executable, p]
        elif language in ("javascript", "js", "node"):
            suffix = ".js"
            def cmd_builder(p):
                return ["node", p]
        else:
            return {"ok": False, "error": f"unsupported language: {language}"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix,
                                          delete=False, encoding="utf-8") as tf:
            tf.write(code)
            tmp_path = tf.name
        try:
            from security.child_env import sanitized_child_env
            proc = subprocess.run(cmd_builder(tmp_path),
                                   capture_output=True, text=True,
                                   timeout=min(timeout, 60),
                                   env=sanitized_child_env())
            return {"ok": proc.returncode == 0,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "language": language}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout",
                    "language": language}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    reg.register(_make("CodeExecute", "Bash",
                       "Run a short Python/JS code snippet in an isolated subprocess",
                       code_execute, "per_turn", ["cwd"]))

    # js twins (Round-7 shell parity: the node-REPL tool names in
    # shell.cjs's 28-name allowlist). Universe's CodeExecute already runs
    # node in an isolated subprocess, so the verbatim names ride the same
    # runner; js_reset and js_add_node_module_dir mirror the CLI REPL's
    # context tools with honest answers.
    def _js_exec(code: str = "", language: str = "js", timeout: int = 30,
                 **_: str) -> dict:
        # js means JavaScript: the CodeExecute runner with language forced
        return code_execute(code=code, language="js", timeout=timeout)

    reg.register(_make("js", "Bash",
                       "Run JavaScript against node in an isolated subprocess.",
                       _js_exec, "per_turn", ["cwd"]))

    def js_reset(**_: dict) -> dict:
        # honest (audit 2026-10): returned {"ok": True} while doing nothing;
        # there IS no persistent REPL context here — say so
        return {"ok": True, "reset": False,
                "note": ("node runs each js call in a fresh subprocess — "
                         "there is no persistent REPL context to reset")}

    reg.register(_make("js_reset", "Bash",
                       "Reset the JS REPL context.",
                       js_reset, "auto"))

    def js_add_node_module_dir(dir: str = "", path: str = "",
                               **_: dict) -> dict:
        return {"ok": True, "added": str(dir or path or ""),
                "note": ("node runs each js call in a fresh subprocess; "
                         "import from the directory's absolute path "
                         "instead")}

    reg.register(_make("js_add_node_module_dir", "Bash",
                       "Add a module resolution directory for the JS REPL.",
                       js_add_node_module_dir, "auto"))

    # coverage_report — run the test suite with coverage and return a summary
    def coverage_report(command: str = "pytest --cov=. --cov-report=term-missing",
                        cwd: str = ".", cmd: str = "", coverage_command: str = "") -> dict:
        command = str(cmd or coverage_command or command)
        # sandboxed dispatch (audit 2026-10 — same fix as TestRunner)
        from connectors.builtin.shell import _run_sandboxed_dispatch
        out = _run_sandboxed_dispatch(command, cwd=cwd, timeout=180)
        # parse the coverage summary from the output (last lines)
        lines = (out.get("stdout", "") + "\n" + out.get("stderr", "")).splitlines()
        cov_lines = [ln for ln in lines if "TOTAL" in ln or "%" in ln and "Stmts" in ln or "Cover" in ln]
        return {**out, "coverage_summary": "\n".join(cov_lines[-10:]) if cov_lines else "",
                "command": command}
    reg.register(_make("CoverageReport", "Bash",
                       "Run test suite with coverage and return a structured summary",
                       coverage_report, "per_turn", ["cwd"]))

    # archive — zip/unzip
    def archive(action: str = "zip", source: str = "", target: str = "",
                files: list | None = None) -> dict:
        """zip:  source (file or dir) -> target.zip
           unzip: source.zip -> target dir"""
        import zipfile
        import shutil
        from pathlib import Path
        if not source:
            return {"ok": False, "error": "archive requires `source`"}
        # scope enforcement lives in the capability gate (path:glob:./**):
        # a handler-level replica broke legitimate absolute paths (pytest
        # tmp_path, user-picked dirs) that the gate already judges. What
        # the gate CANNOT see is zip member names — those stay checked
        # below at extraction time.
        if action == "zip":
            tgt = Path(target) if target else Path(source).with_suffix(".zip" if not Path(source).is_dir() else ".zip")
            src = Path(source)
            file_count = 0
            with zipfile.ZipFile(tgt, "w", zipfile.ZIP_DEFLATED) as zf:
                if src.is_file():
                    zf.write(src, src.name)
                    file_count = 1
                else:
                    for f in src.rglob("*"):
                        if f.is_file():
                            zf.write(f, f.relative_to(src.parent))
                            file_count += 1
            return {"ok": True, "action": "zip", "target": str(tgt),
                    "files": file_count, "bytes": tgt.stat().st_size}
        if action == "unzip":
            tgt = Path(target) if target else Path(source).with_suffix("")
            src_zip = Path(source)
            if not src_zip.exists():
                return {"ok": False, "error": f"zip not found: {source}"}
            tgt_abs = tgt.resolve()
            tgt.mkdir(parents=True, exist_ok=True)
            file_count = 0
            with zipfile.ZipFile(src_zip, "r") as zf:
                for info in zf.infolist():
                    # zip-slip: every member's FINAL resolved path must stay
                    # under tgt. resolve()-alone cannot catch Windows
                    # drive-letter smuggles when the root sits on the same
                    # drive (a C: component collapses away), so member names
                    # containing ':' are rejected first — same rule as the
                    # export/import path (m2 drive-smuggle fix).
                    if any(":" in part for part in Path(info.filename).parts):
                        return {"ok": False,
                                "error": f"zip-slip blocked: {info.filename!r} "
                                         f"contains a drive/ADS component",
                                "target": str(tgt_abs)}
                    dest = (tgt / info.filename).resolve()
                    try:
                        dest.relative_to(tgt_abs)
                    except ValueError:
                        return {"ok": False,
                                "error": f"zip-slip blocked: {info.filename!r} escapes target dir",
                                "target": str(tgt_abs)}
                    if info.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src_f, open(dest, "wb") as out_f:
                        shutil.copyfileobj(src_f, out_f)
                    file_count += 1
            return {"ok": True, "action": "unzip", "target": str(tgt),
                    "files": file_count}
        return {"ok": False, "error": f"unknown action {action!r}; use 'zip' or 'unzip'"}
    reg.register(_make("Archive", "Write",
                       "Zip or unzip files",
                       archive, "per_turn", ["path:glob:./**"],
                       path_args=["source", "target"]))

    # image_ocr — extract text from an image via tesseract (fully local)
    def image_ocr(path: str, lang: str = "eng",
                  psm: int = 3) -> dict:
        """Extract text from an image using local tesseract. Zero API cost.
        Requires system `tesseract` binary on PATH and the `pytesseract` Python pkg."""
        from pathlib import Path
        # validate CLI-bound parameters FIRST: a hostile psm/lang must be
        # refused before we even stat the file (and before any binary runs)
        try:
            psm = int(psm)
        except (TypeError, ValueError):
            return {"ok": False, "error": "psm must be an integer"}
        if not 0 <= psm <= 13:
            return {"ok": False, "error": "psm must be in 0..13"}
        if not re.match(r"^[a-zA-Z0-9_-]+$", str(lang)):
            return {"ok": False, "error": "invalid lang code"}
        p = Path(path)
        if not p.exists():
            return {"ok": False, "error": f"image not found: {path}"}
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(p)
            text = pytesseract.image_to_string(img, lang=lang,
                                                config=f"--psm {psm}")
            return {"ok": True, "path": str(p),
                    "text": text.strip(),
                    "char_count": len(text),
                    "lang": lang,
                    "engine": "tesseract"}
        except ImportError as e:
            return {"ok": False, "error": f"pytesseract or pillow not installed: {e}",
                    "hint": "pip install pytesseract pillow; apt-get install tesseract-ocr"}
        except Exception as e:
            # pytesseract raises FileNotFoundError if the tesseract binary isn't on PATH
            return {"ok": False, "error": f"OCR failed (is tesseract installed?): {e}"}
    reg.register(_make("ImageOcr", "Read",
                       "Extract text from an image via local tesseract (zero API cost)",
                       image_ocr, "auto", ["path:glob:**/*"]))

    # graphql_client — GraphQL query/introspection support, distinct from http_client
    def graphql_client(endpoint: str, query: str,
                       variables: dict | None = None,
                       headers: dict | None = None,
                       introspect: bool = False) -> dict:
        """Run a GraphQL query/mutation against `endpoint`. If `introspect=True`,
        runs the standard introspection query and returns the schema's type
        names (a far better fit than http_client for GraphQL APIs)."""
        if introspect:
            query = (
                "query IntrospectionQuery { "
                "__schema { queryType { name } "
                "types { name kind fields { name type { name kind } } } } }")
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        pre = _ssrf_guard(endpoint)
        if not pre.get("ok"):
            return {"ok": False, **{k: v for k, v in pre.items() if k != "ok"},
                    "endpoint": endpoint}
        try:
            import json
            with _httpx_client_with_guard() as client:
                r = client.post(endpoint, json=payload,
                                headers=headers or {"Content-Type": "application/json"})
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
            return {"ok": True, "endpoint": endpoint, "status": r.status_code,
                    "data": f"[UNTRUSTED:web] {json.dumps(data)[:20000]}",
                    "introspection": introspect}
        except _RedirectBlocked as e:
            return {"ok": False, "error": f"redirect blocked: {e}", "endpoint": endpoint}
        except Exception as e:
            return {"ok": False, "error": str(e), "endpoint": endpoint}
    reg.register(_make("GraphqlClient", "network.req",
                       "GraphQL query/mutation + introspection support (SSRF-guarded)",
                       graphql_client, "per_turn", ["url:https://*"]))

    # secret_scanner — scan source files for accidentally committed secrets
    def secret_scanner(path: str = ".",
                       extensions: list | None = None,
                       max_file_size: int = 1_000_000) -> dict:
        """Scan a directory for accidentally committed secrets (API keys,
        tokens, passwords). Pattern-based, similar in spirit to detect-secrets.
        Returns per-file findings with line numbers and the matched pattern."""
        import re
        from pathlib import Path
        patterns = {
            "aws_access_key_id": re.compile(r"AKIA[0-9A-Z]{16}"),
            "aws_secret_access_key": re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"][A-Za-z0-9/+=]{40}['\"]"),
            "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
            "github_token_legacy": re.compile(r"ghp_[A-Za-z0-9]{36}"),
            "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
            "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
            "slack_token": re.compile(r"xox[abp]-[0-9A-Za-z-]{10,}"),
            "stripe_key": re.compile(r"(?:sk|pk)_(?:test|live)_[0-9a-zA-Z]{24,}"),
            "jwt": re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
            "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
            "generic_password_assign": re.compile(r"(?i)(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
            "generic_api_key_assign": re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][^'\"]{10,}['\"]"),
            "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
        }
        exts = set(extensions or [".py", ".js", ".ts", ".jsx", ".tsx", ".go",
                                   ".rs", ".java", ".rb", ".php", ".yml", ".yaml",
                                   ".json", ".env", ".ini", ".cfg", ".conf", ".toml",
                                   ".sh", ".bash", ".zsh", ".md", ".txt", ""])
        root = Path(path)
        if not root.exists():
            return {"ok": False, "error": f"path not found: {path}"}
        findings: list[dict] = []
        files_scanned = 0
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix not in exts and f.suffix != "":
                continue
            # skip git, node_modules, venv
            if any(part in {"node_modules", ".git", "venv", "__pycache__", ".venv"}
                   for part in f.parts):
                continue
            try:
                if f.stat().st_size > max_file_size:
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            files_scanned += 1
            for name, pat in patterns.items():
                for m in pat.finditer(text):
                    line_no = text[:m.start()].count("\n") + 1
                    # redact aggressively: keep only the first 4 chars (enough
                    # for the operator to identify the pattern, never enough
                    # to recover the secret)
                    matched = m.group(0)
                    redacted = matched[:4] + "...[REDACTED]" if len(matched) > 4 else "[REDACTED]"
                    findings.append({"file": str(f), "line": line_no,
                                      "pattern": name, "match": redacted})
        return {"ok": True, "path": str(root),
                "files_scanned": files_scanned,
                "findings": findings,
                "finding_count": len(findings),
                "patterns_checked": sorted(patterns.keys())}
    reg.register(_make("SecretScanner", "Read",
                       "Scan a codebase for accidentally committed secrets",
                       secret_scanner, "auto", ["path:glob:**/*"]))

    # dependency_audit — SBOM-style deeper audit than vuln_scanner
    def dependency_audit(path: str = ".",
                         check_osv: bool = False) -> dict:
        """Inventory dependencies from manifest files (requirements.txt,
        pyproject.toml, package.json, go.mod, Cargo.toml) and report counts
        per ecosystem + known-stale or pinned-too-loose packages. When
        check_osv=True, also query osv.dev for each (network; skipped if
        unreachable)."""
        import re
        import json
        from pathlib import Path
        root = Path(path)
        sbom: dict[str, list[dict]] = {"python": [], "node": [], "go": [], "rust": []}
        warnings: list[str] = []
        # python: requirements.txt
        req = root / "requirements.txt"
        if req.exists():
            for line in req.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^([a-zA-Z0-9_.-]+)\s*([=~><!]=?\s*[0-9A-Za-z.*]+)?", line)
                if m:
                    sbom["python"].append({"name": m.group(1).lower(),
                                            "spec": (m.group(2) or "").strip() or "unpinned"})
                    if not m.group(2):
                        warnings.append(f"requirements.txt: {m.group(1)} has no version pin")
        # python: pyproject.toml (very lightweight parse — just look for dependencies = [...])
        pp = root / "pyproject.toml"
        if pp.exists():
            text = pp.read_text(errors="replace")
            # extract dependencies under [project] -> dependencies = [...]
            m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
            if m:
                for line in m.group(1).splitlines():
                    sm = re.match(r'\s*"([a-zA-Z0-9_.-]+)\s*([<>=!~^][^"]*)?"', line)
                    if sm:
                        sbom["python"].append({"name": sm.group(1).lower(),
                                                "spec": (sm.group(2) or "").strip() or "unpinned"})
        # node: package.json
        pj = root / "package.json"
        if pj.exists():
            try:
                data = json.loads(pj.read_text())
                for sec in ("dependencies", "devDependencies", "peerDependencies"):
                    for name, spec in (data.get(sec) or {}).items():
                        sbom["node"].append({"name": name, "spec": spec, "section": sec})
            except Exception as e:
                warnings.append(f"package.json: parse error: {e}")
        # go: go.mod
        gm = root / "go.mod"
        if gm.exists():
            for line in gm.read_text(errors="replace").splitlines():
                if line.startswith("\t") and " " in line:
                    parts = line.strip().split(" ", 1)
                    sbom["go"].append({"name": parts[0], "spec": parts[1] if len(parts) > 1 else ""})
        # rust: Cargo.toml
        ct = root / "Cargo.toml"
        if ct.exists():
            text = ct.read_text(errors="replace")
            m = re.search(r"\[dependencies\](.*?)(\n\[|\Z)", text, re.DOTALL)
            if m:
                for line in m.group(1).strip().splitlines():
                    sm = re.match(r"^([a-zA-Z0-9_-]+)\s*=\s*['\"]?([^'\"]+)['\"]?", line.strip())
                    if sm:
                        sbom["rust"].append({"name": sm.group(1), "spec": sm.group(2)})
        counts = {ec: len(deps) for ec, deps in sbom.items()}
        result = {"ok": True, "path": str(root),
                  "sbom": sbom, "counts": counts,
                  "total_dependencies": sum(counts.values()),
                  "warnings": warnings,
                  "warning_count": len(warnings)}
        if check_osv:
            # network probe — best-effort, doesn't fail the audit if unreachable
            try:
                import httpx
                vulns: list[dict] = []
                for dep in sbom["python"][:50]:  # cap to avoid hammering osv.dev
                    try:
                        r = httpx.post("https://api.osv.dev/v1/query", json={
                            "package": {"name": dep["name"], "ecosystem": "PyPI"}
                        }, timeout=5.0)
                        if r.status_code == 200:
                            data = r.json()
                            vlist = data.get("vulns") or []
                            if vlist:
                                vulns.append({"package": dep["name"],
                                              "vulnerability_count": len(vlist),
                                              "ids": [v.get("id") for v in vlist[:5]]})
                    except Exception:
                        pass
                result["osv_vulnerabilities"] = vulns
                result["osv_vulnerability_count"] = len(vulns)
            except Exception as e:
                result["osv_note"] = f"osv.dev check skipped: {e}"
        return result
    reg.register(_make("DependencyAudit", "Read",
                       "SBOM-style dependency audit (python/node/go/rust) with optional osv.dev check",
                       dependency_audit, "auto", ["path:glob:**/*"]))
