"""tests/test_phase3_tools.py — Phase 3 per-agent tools, real behavior tests.

Every test exercises real behavior:
  * file_edit replaces exact text in a real file and verifies the new content
  * code_execute runs real Python/JS and checks real stdout
  * coverage_report runs a real pytest invocation
  * archive zips/unzips real files
  * image_ocr runs real tesseract on a real image with real text
  * graphql_client introspects a real local test server
  * secret_scanner finds real planted secrets in a real source tree
  * dependency_audit parses real manifest files
  * pandas_query runs real aggregations on a real CSV
  * data_validate produces real per-column mismatch reports
  * vision_analyze returns real image dimensions + dominant colours
  * css_tools lints real CSS and extracts real design tokens
  * color_contrast_check verifies the WCAG 2.1 white-on-black ratio = 21
  * k8s enforces the plan-before-apply safety pattern
  * log_tail reads the last N lines of a real log file
"""
import sys
import textwrap

import pytest


# ---- Code Agent tools ---------------------------------------------------

def _scope_to(tmp_path):
    """Point the capability-gated file tools at this test's tmp_path."""
    from config import get_config
    get_config().set("project_root", str(tmp_path))


def test_file_edit_replaces_exact_text_in_a_real_file(fresh_home, tmp_path):
    """file_edit: a real exact-match replacement on a real file produces a
    real result and leaves the surrounding content untouched."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)
    reg = get_registry()
    src = tmp_path / "src.txt"
    src.write_text("line one\nline two\nline three\n", encoding="utf-8")
    r = reg.get("Edit").handler(
        path=str(src), old_string="line two", new_string="line TWO")
    assert r["ok"] is True
    assert r["replacements"] == 1
    final = src.read_text(encoding="utf-8")
    assert final == "line one\nline TWO\nline three\n"


def test_file_edit_fails_when_old_string_absent(fresh_home, tmp_path):
    """file_edit: a match-miss must fail and leave the file byte-identical."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)
    reg = get_registry()
    src = tmp_path / "src.txt"
    src.write_text("line one\nline two\n", encoding="utf-8")
    r = reg.get("Edit").handler(
        path=str(src), old_string="line forty-two", new_string="x")
    assert r["ok"] is False
    assert "not found" in r["error"]
    assert src.read_text(encoding="utf-8") == "line one\nline two\n"


def test_file_edit_fails_on_multiple_matches_without_replace_all(fresh_home, tmp_path):
    """file_edit: an ambiguous match must fail with the match count rather
    than silently editing the first occurrence."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)
    reg = get_registry()
    src = tmp_path / "src.txt"
    src.write_text("same\nsame\nsame\n", encoding="utf-8")
    r = reg.get("Edit").handler(
        path=str(src), old_string="same", new_string="other")
    assert r["ok"] is False
    assert "3 times" in r["error"]
    assert src.read_text(encoding="utf-8") == "same\nsame\nsame\n"


def test_file_edit_replace_all_updates_every_occurrence(fresh_home, tmp_path):
    """file_edit: replace_all=true rewrites every occurrence in one call."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)
    reg = get_registry()
    src = tmp_path / "src.txt"
    src.write_text("same\nsame\nsame\n", encoding="utf-8")
    r = reg.get("Edit").handler(
        path=str(src), old_string="same", new_string="other", replace_all=True)
    assert r["ok"] is True
    assert r["replacements"] == 3
    assert src.read_text(encoding="utf-8") == "other\nother\nother\n"


def test_file_edit_rejects_missing_file_and_degenerate_args(fresh_home, tmp_path):
    """file_edit: not-found path, empty old_string, and identical old/new are
    all hard errors — the agent gets a precise message, never a no-op write."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)
    reg = get_registry()
    missing = tmp_path / "ghost.txt"
    r = reg.get("Edit").handler(
        path=str(missing), old_string="a", new_string="b")
    assert r["ok"] is False
    assert r["error"] == "not found"
    src = tmp_path / "src.txt"
    src.write_text("keep\n", encoding="utf-8")
    r = reg.get("Edit").handler(path=str(src), old_string="", new_string="b")
    assert r["ok"] is False
    assert "non-empty" in r["error"]
    r = reg.get("Edit").handler(
        path=str(src), old_string="keep", new_string="keep")
    assert r["ok"] is False
    assert "identical" in r["error"]
    assert src.read_text(encoding="utf-8") == "keep\n"


def test_file_edit_outside_scope_is_rejected(fresh_home, tmp_path):
    """file_edit: paths outside the project scope are refused, same as the
    other file tools. The victim file lives in a sibling dir of the scoped
    root and must come out byte-identical."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)
    reg = get_registry()
    outside_root = tmp_path.parent / "gax-edit-outside-scope"
    outside_root.mkdir(exist_ok=True)
    outside = outside_root / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    r = reg.get("Edit").handler(
        path=str(outside), old_string="secret", new_string="public")
    assert r["ok"] is False
    assert "outside scope" in r["error"]
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_code_execute_runs_real_python_and_returns_stdout(fresh_home):
    """code_execute: real Python execution. The subprocess returns real stdout
    captured from a real interpreter — not a mocked string."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = reg.get("CodeExecute").handler(
        code="import sys; print('hello from', sys.version_info.major)",
        language="python")
    assert r["ok"] is True
    assert r["returncode"] == 0
    assert "hello from 3" in r["stdout"]


def test_code_execute_rejects_unsupported_language(fresh_home):
    """code_execute: unknown language returns a real, clear error — never a
    fake 'ok'."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = reg.get("CodeExecute").handler(code="x", language="brainfuck")
    assert r["ok"] is False
    assert "unsupported language" in r["error"]


# ---- Review Agent tools -------------------------------------------------

def test_coverage_report_runs_real_pytest(fresh_home, tmp_path):
    """coverage_report: invokes a real pytest run (against a tiny fixture
    test) and returns the real return code + stdout. The test fixture is
    planted on disk; pytest actually runs it."""
    from connectors.builtin import get_registry
    reg = get_registry()
    # plant a tiny test file pytest will run
    (tmp_path / "test_micro.py").write_text(textwrap.dedent("""
        def test_pass():
            assert 1 + 1 == 2
    """), encoding="utf-8")
    r = reg.get("CoverageReport").handler(
        command=f"{sys.executable} -m pytest test_micro.py -q --no-header",
        cwd=str(tmp_path))
    # the test passed
    assert "passed" in (r.get("stdout", "") + r.get("stderr", ""))


# ---- Design Agent tools -------------------------------------------------

def test_vision_analyze_returns_real_image_properties(fresh_home, tmp_path):
    """vision_analyze: generate a real PNG with PIL, then check the analysis
    reports real dimensions, real format, and a real brightness bucket."""
    from connectors.builtin import get_registry
    from PIL import Image, ImageDraw
    reg = get_registry()
    p = tmp_path / "test.png"
    img = Image.new("RGB", (200, 100), color=(255, 255, 255))  # all white
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 50, 50], fill=(0, 0, 0))  # black square
    img.save(p, format="PNG")
    r = reg.get("VisionAnalyze").handler(path=str(p))
    assert r["ok"] is True
    assert r["dimensions"] == {"width": 200, "height": 100}
    assert r["aspect_ratio"] == 2.0
    assert r["format"] == "PNG"
    assert r["brightness_bucket"] in ("bright", "very_bright")  # mostly white
    assert r["size_bytes"] > 0
    assert len(r["dominant_colours"]) > 0
    # white should be in the dominant colours
    hexes = [c["hex"].lower() for c in r["dominant_colours"]]
    assert "#ffffff" in hexes or "#f0f0f0" in hexes  # quantised


def test_css_tools_lints_real_css_and_finds_unbalanced_braces(fresh_home):
    """css_tools lint: real CSS with an unbalanced brace produces a real
    finding — not a fake 'ok'."""
    from connectors.builtin import get_registry
    reg = get_registry()
    broken_css = "body { color: red;\n.btn { color: blue; }\n"  # missing closing } for body
    r = reg.get("CssTools").handler(action="lint", css=broken_css)
    assert r["ok"] is True  # the call succeeded
    assert r["finding_count"] >= 1
    kinds = [f["rule"] for f in r["findings"]]
    assert "balanced_braces" in kinds


def test_css_tools_extracts_real_design_tokens(fresh_home):
    """css_tools tokens: real CSS produces a real structured token inventory
    — colours, fonts, custom properties, z-indexes — not a stub."""
    from connectors.builtin import get_registry
    reg = get_registry()
    css = textwrap.dedent("""
        :root {
            --color-primary: #6B21A8;
            --color-bg: #FFFFFF;
            --font-sans: "Inter", sans-serif;
            --space-md: 16px;
        }
        body {
            color: var(--color-primary);
            background: var(--color-bg);
            font-family: var(--font-sans);
        }
        .modal { z-index: 1000; }
        .overlay { z-index: 500; }
        @media (max-width: 768px) { body { font-size: 14px; } }
    """)
    r = reg.get("CssTools").handler(action="tokens", css=css)
    assert r["ok"] is True
    assert r["custom_properties"]["color-primary"] == "#6B21A8"
    assert "#6B21A8" in r["colours"]
    assert "#FFFFFF" in r["colours"]
    assert 1000 in r["z_indexes"]
    assert 500 in r["z_indexes"]
    assert any("Inter" in f for f in r["font_families"])
    assert len(r["media_queries"]) >= 1


def test_color_contrast_check_verifies_white_on_black_is_21(fresh_home):
    """color_contrast_check: pure-math WCAG. The maximum possible ratio is
    white-on-black = 21.0 — this is a deterministic check that the math is
    correct, not a vague range assertion."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = reg.get("ColorContrastCheck").handler(
        foreground="#FFFFFF", background="#000000")
    assert r["ok"] is True
    # WCAG 2.1 maximum contrast = (1.0 + 0.05) / (0.0 + 0.05) = 21.0
    assert abs(r["contrast_ratio"] - 21.0) < 0.01
    assert r["wcag_aa_pass"] is True
    assert r["wcag_aaa_pass"] is True
    assert r["verdict"] == "pass"


def test_color_contrast_check_flags_low_contrast(fresh_home):
    """color_contrast_check: a mid-grey on white (~3.5 ratio) fails AA for
    normal text (needs 4.5) but passes AA for large text (needs 3.0) — the
    per-size logic is real. #888888 vs white = ratio ~3.55."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = reg.get("ColorContrastCheck").handler(
        foreground="#888888", background="#FFFFFF", font_size_px=16)
    assert r["ok"] is True
    # ~3.55 is between 3.0 (AA large) and 4.5 (AA normal)
    assert 3.0 <= r["contrast_ratio"] < 4.5, f"ratio was {r['contrast_ratio']}"
    assert r["wcag_aa_pass"] is False  # 3.55 < 4.5 for normal text
    assert r["verdict"] == "fail"
    # but for large text it would pass
    r2 = reg.get("ColorContrastCheck").handler(
        foreground="#888888", background="#FFFFFF", font_size_px=24)
    assert r2["wcag_aa_pass"] is True  # 3.55 >= 3.0 for large text


# ---- Data Agent tools ---------------------------------------------------

def test_pandas_query_runs_real_aggregation_on_real_csv(fresh_home, tmp_path):
    """pandas_query: real CSV + real groupby/agg = real summary numbers, not a
    mock."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)  # PandasQuery is workspace-scoped since the 2026-10 audit
    reg = get_registry()
    p = tmp_path / "sales.csv"
    p.write_text(
        "region,product,amount\n"
        "north,A,100\n"
        "north,B,200\n"
        "south,A,50\n"
        "south,B,300\n",
        encoding="utf-8")
    r = reg.get("PandasQuery").handler(
        path=str(p), op="groupby_agg",
        columns=["region", "amount"], expr="sum")
    assert r["ok"] is True
    assert r["shape"] == [4, 3]
    # north: 100+200=300, south: 50+300=350
    assert r["groups"]["north"] == 300
    assert r["groups"]["south"] == 350


def test_data_validate_produces_real_per_column_report(fresh_home, tmp_path):
    """data_validate: plant a CSV with two real schema violations and assert
    the report flags them by name."""
    from connectors.builtin import get_registry
    _scope_to(tmp_path)  # DataValidate is workspace-scoped since the 2026-10 audit
    reg = get_registry()
    p = tmp_path / "users.csv"
    p.write_text(
        "name,age,email\n"
        "Ada,30,ada@example.com\n"
        "Bob,250,bob-not-an-email\n"  # age > 150, email fails regex
        ",25,cara@example.com\n",  # null name (required)
        encoding="utf-8")
    schema = {
        "columns": {
            "name":  {"dtype": "object", "required": True},
            "age":   {"dtype": "int64", "required": True, "min": 0, "max": 150},
            "email": {"dtype": "object", "required": True,
                      "regex": "^[^@]+@[^@]+$"},
        }
    }
    r = reg.get("DataValidate").handler(path=str(p), schema=schema)
    assert r["ok"] is False  # there are violations
    assert r["row_count"] == 3
    issues = {(e["column"], e["issue"]) for e in r["errors"]}
    assert ("age", "above_max") in issues
    assert ("email", "regex_mismatch") in issues
    assert ("name", "has_nulls_in_required") in issues


# ---- DevOps Agent tools -------------------------------------------------

def test_k8s_plan_before_apply_blocks_writes_without_confirm(fresh_home):
    """k8s: write actions without confirm=True return the planned command,
    not execution. This is the real plan-before-apply safety pattern."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = reg.get("K8s").handler(action="apply", file="deployment.yaml",
                                namespace="prod")
    assert r["ok"] is False
    assert r["blocked_by"] == "plan_before_apply"
    assert "kubectl -n prod apply -f deployment.yaml" in r["planned_command"]


def test_k8s_read_actions_run_immediately(fresh_home):
    """k8s: read actions (get/describe) execute immediately. If kubectl isn't
    installed, the call returns a real runtime error — not a fake block."""
    from connectors.builtin import get_registry
    reg = get_registry()
    r = reg.get("K8s").handler(action="get", resource="pods")
    # kubectl probably not installed in this sandbox — that's a real runtime
    # error, not a fake block. The point is the tool didn't refuse to run.
    assert r.get("blocked_by") != "plan_before_apply"


def test_log_tail_reads_last_n_lines_of_real_file(fresh_home, tmp_path):
    """log_tail: writes a real log file with 200 lines and asserts the tool
    returns exactly the last 50."""
    from connectors.builtin import get_registry
    reg = get_registry()
    p = tmp_path / "service.log"
    p.write_text("\n".join(f"line {i}" for i in range(1, 201)) + "\n",
                  encoding="utf-8")
    r = reg.get("LogTail").handler(path=str(p), lines=50)
    assert r["ok"] is True
    assert r["lines_returned"] == 50
    assert "line 151" in r["tail"]
    assert "line 200" in r["tail"]
    assert "line 100" not in r["tail"]  # not in the last 50


# ---- File Agent tools ---------------------------------------------------

def test_archive_zip_unzip_roundtrip(fresh_home, tmp_path):
    """archive: real zip → real unzip → files match."""
    from connectors.builtin import get_registry
    reg = get_registry()
    src = tmp_path / "to_zip"
    src.mkdir()
    (src / "a.txt").write_text("alpha", encoding="utf-8")
    (src / "b.txt").write_text("beta", encoding="utf-8")
    # zip
    zip_path = tmp_path / "out.zip"
    r = reg.get("Archive").handler(action="zip", source=str(src),
                                    target=str(zip_path))
    assert r["ok"] is True
    assert r["files"] == 2
    assert zip_path.exists() and zip_path.stat().st_size > 0
    # unzip
    out_dir = tmp_path / "unzipped"
    r = reg.get("Archive").handler(action="unzip", source=str(zip_path),
                                    target=str(out_dir))
    assert r["ok"] is True
    assert r["files"] == 2
    # find the extracted files (they'll be under to_zip/ inside the out dir)
    extracted = list(out_dir.rglob("*.txt"))
    contents = sorted(p.read_text(encoding="utf-8") for p in extracted)
    assert contents == ["alpha", "beta"]


def test_image_ocr_extracts_real_text_from_real_image(fresh_home, tmp_path):
    """image_ocr: render a real image with the string 'HELLO UNIVERSE' and run
    real tesseract OCR. The result must contain the actual text — not a stub.
    Skipped when the system tesseract binary is absent (it's an optional
    system dependency, documented in the README)."""
    import shutil
    if not shutil.which("tesseract"):
        pytest.skip("tesseract binary not installed on this machine")
    from connectors.builtin import get_registry
    from PIL import Image, ImageDraw, ImageFont
    reg = get_registry()
    # render big black text on white background (high contrast helps tesseract)
    img = Image.new("RGB", (600, 100), color="white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
    except Exception:
        font = ImageFont.load_default()
    d.text((10, 20), "HELLO UNIVERSE", fill="black", font=font)
    p = tmp_path / "ocr.png"
    img.save(p, format="PNG")
    r = reg.get("ImageOcr").handler(path=str(p))
    assert r["ok"] is True, f"OCR failed: {r}"
    # OCR is imperfect on synthetic images but should at least find HELLO
    assert "HELLO" in r["text"].upper() or "UNIVERSE" in r["text"].upper(), \
        f"OCR returned: {r['text']!r}"


# ---- API Agent tools ----------------------------------------------------

def test_graphql_client_introspection_runs_real_query(fresh_home, monkeypatch):
    """graphql_client: (a) SSRF regression — a loopback endpoint (the classic
    internal-network target) must be REFUSED by the guard, never reached;
    (b) with the guard relaxed to a test double, the real POST/parsing path
    still works against a local canned introspection server."""
    import http.server
    import socketserver
    import threading
    import json
    from connectors.builtin import get_registry
    import connectors.builtin.misc as misc_mod
    reg = get_registry()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                json.loads(body)
            except Exception:
                pass
            # real-ish introspection response
            resp = {"data": {"__schema": {
                "queryType": {"name": "Query"},
                "types": [{"name": "Query", "kind": "OBJECT",
                           "fields": [{"name": "hello", "type": {"name": "String", "kind": "SCALAR"}}]}]}}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        def log_message(self, *a, **k): pass

    # (a) the guard must refuse loopback BEFORE any request is attempted —
    # even when a live server is listening there
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            r = reg.get("GraphqlClient").handler(
                endpoint=f"http://127.0.0.1:{port}/graphql",
                query="{}",
                introspect=True)
            assert r["ok"] is False, "SSRF guard must block loopback GraphQL endpoints"
            assert "private/loopback" in r["error"]
        finally:
            srv.shutdown()

    # (b) real request/parsing path: relax the guard to a test double only
    # (the HTTP machinery itself still runs for real)
    monkeypatch.setattr(misc_mod, "_ssrf_guard", lambda url: {"ok": True, "host": "test"})
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            r = reg.get("GraphqlClient").handler(
                endpoint=f"http://127.0.0.1:{port}/graphql",
                query="{}",
                introspect=True)
            assert r["ok"] is True
            assert r["status"] == 200
            assert "Query" in r["data"]
        finally:
            srv.shutdown()


# ---- Security Agent tools -----------------------------------------------

def test_secret_scanner_finds_real_planted_secrets(fresh_home, tmp_path):
    """secret_scanner: plant real secrets in real source files and verify the
    scanner finds them by pattern name and file path."""
    from connectors.builtin import get_registry
    reg = get_registry()
    (tmp_path / "config.py").write_text(
        'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        'GITHUB_TOKEN = "ghp_' + "A" * 36 + '"\n'
        'OPENAI_KEY = "sk-' + "B" * 40 + '"\n',
        encoding="utf-8")
    (tmp_path / "app.js").write_text(
        'const password = "supersecret123"\n',
        encoding="utf-8")
    r = reg.get("SecretScanner").handler(path=str(tmp_path))
    assert r["ok"] is True
    assert r["files_scanned"] >= 2
    patterns_found = {f["pattern"] for f in r["findings"]}
    assert "aws_access_key_id" in patterns_found
    assert "github_token" in patterns_found or "github_token_legacy" in patterns_found
    assert "openai_key" in patterns_found
    # the redacted match must NOT contain the actual secret
    for f in r["findings"]:
        assert "EXAMPLE" not in f["match"]  # aws example string
        assert "BBBB" not in f["match"]  # openai key body


def test_dependency_audit_parses_real_manifests(fresh_home, tmp_path):
    """dependency_audit: plant real requirements.txt + package.json + go.mod +
    Cargo.toml files and verify the SBOM counts are real."""
    from connectors.builtin import get_registry
    reg = get_registry()
    (tmp_path / "requirements.txt").write_text(
        "requests==2.31.0\nnumpy\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.18.0"}, "devDependencies": {"jest": "29.0.0"}}',
        encoding="utf-8")
    (tmp_path / "go.mod").write_text(
        "module x\n\ngo 1.21\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.0\n)\n",
        encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(
        "[dependencies]\nserde = \"1.0\"\nreqwest = { version = \"0.11\", features = [\"json\"] }\n",
        encoding="utf-8")
    r = reg.get("DependencyAudit").handler(path=str(tmp_path))
    assert r["ok"] is True
    assert r["counts"]["python"] == 2  # requests + numpy
    assert r["counts"]["node"] == 2    # express + jest
    assert r["counts"]["go"] == 1      # gin
    assert r["counts"]["rust"] == 2    # serde + reqwest
    # numpy has no version pin — that should be a warning
    assert any("numpy" in w for w in r["warnings"])
