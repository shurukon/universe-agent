"""Phase 2 codemod — rename Universe's tool engine to ZCode's exact tool names.

Applies the docs/zcode-re-map.md §6 mapping with word-boundary regexes
across .py/.ts/.tsx/.md/.css. Evidence artifacts (docs/re-artifacts/) and
the RE extraction scripts are excluded — they record the OLD names as
evidence.

Usage:
  python scripts/re/apply_rename.py --dry-run     # report only
  python scripts/re/apply_rename.py --apply       # rewrite + report
"""
import io
import os
import re
import sys

ROOT = r"D:\Universecomputer\Universe-compter"
EXCLUDE_DIRS = {"node_modules", "dist", "dist-electron", "__pycache__", ".git",
                "re-artifacts", "skills_data", "vendor", ".zcode", ".kilo",
                "zcode-live-screens", "phase2-screens", "phase3-screens"}
EXCLUDE_FILES = {"apply_rename.py", "extract_zcode.py", "extract_icons.py",
                 "extract_renderer.py", "zcode-re-map.md"}
# only these roots carry tool names as code — everything else (docs corpora,
# plans, vendored code, sqlite wrappers) is out of scope
INCLUDE_ROOTS = (
    "connectors", "core", "security", "hooks", "universe_desktop_bridge", "cli",
    "observability", "providers", "tests", "docs", "scripts", "electron",
    "README.md", "SOURCES.md", "universe_cua_helper.py", "config.py",
    "universe-agent-ui",
)

MAP: dict[str, str] = {
    # ---- files
    "file.read": "Read", "file.write": "Write", "file.edit": "Edit",
    "file.delete": "Delete", "file.list": "LS",
    # ---- shell
    "shell.exec": "Bash", "shell_exec_unsandboxed": "BashUnsandboxed",
    # ---- web
    "web_search": "WebSearch", "web_fetch": "WebFetch",
    "web_research": "WebResearch",
    # ---- git / docker / memory
    "git.status": "GitStatus", "git.diff": "GitDiff", "git.log": "GitLog",
    "git.commit": "GitCommit", "git.push": "GitPush",
    "docker.build": "DockerBuild", "docker.run": "DockerRun",
    "docker.ps": "DockerPs",
    "memory_query": "MemoryQuery",
    # ---- misc / data / vision / devops
    "test_runner": "TestRunner", "code_analyzer": "CodeAnalyzer",
    "static_analysis": "StaticAnalysis", "vuln_scanner": "VulnScanner",
    "svg_gen": "SvgGen", "figma_mcp": "FigmaMcp", "sql": "Sql",
    "charting": "Charting", "ssh": "Ssh", "ci_cd": "Cicd",
    "pdf": "Pdf", "docx": "Docx", "xlsx": "Xlsx", "pptx": "Pptx",
    "http_client": "HttpClient", "openapi_parser": "OpenapiParser",
    "code_execute": "CodeExecute", "coverage_report": "CoverageReport",
    "archive": "Archive", "image_ocr": "ImageOcr",
    "graphql_client": "GraphqlClient", "secret_scanner": "SecretScanner",
    "dependency_audit": "DependencyAudit",
    "pandas_query": "PandasQuery", "data_validate": "DataValidate",
    "vision_analyze": "VisionAnalyze", "css_tools": "CssTools",
    "color_contrast_check": "ColorContrastCheck",
    "k8s": "K8s", "log_tail": "LogTail",
    # ---- browser
    "browser_navigate": "BrowserNavigate", "browser_click": "BrowserClick",
    "browser_fill": "BrowserFill", "browser_snapshot": "BrowserSnapshot",
    "browser_screenshot": "BrowserScreenshot",
    "browser_extract": "BrowserExtract", "browser_tabs": "BrowserTabs",
    "browser_console": "BrowserConsole", "browser_upload": "BrowserUpload",
    "browser_connect": "BrowserConnect",
    # ---- capabilities / skills / swarm / todos / plan / ask / cron
    "capability_catalog": "CapabilityCatalog",
    "skill.search": "Skill", "skill.read": "Skill", "skill.activate": "Skill",
    "spawn": "Agent",
    "todo.write": "TodoWrite", "todo.read": "TodoRead",
    "plan.submit": "ExitPlanMode", "question.ask": "AskUserQuestion",
    "cron.create": "CronCreate", "cron.list": "CronList",
    "cron.update": "CronUpdate", "cron.delete": "CronDelete",
    # ---- mcp registry tools
    "mcp_add_server": "McpAddServer", "mcp_list_servers": "McpListServers",
    "mcp_remove_server": "McpRemoveServer", "mcp_call_tool": "McpCallTool",
    # ---- computer use (ZCode CUA names)
    "computer.request_access": "RequestAccess",
    "computer.list_apps": "ListApps",
    "computer.list_windows": "ListWindows",
    "computer.get_app_state": "GetAppState",
    "computer.screenshot": "Screenshot",
    "computer.zoom": "Zoom",
    "computer.swipe": "Swipe", "computer.scroll": "Scroll",
    "computer.set_value": "SetValue",
    "computer.open_application": "OpenApplication",
    "computer.switch_display": "SwitchDisplay",
}

ORDERED = sorted(MAP, key=len, reverse=True)

# Quoted-occurrence patterns only — prose that merely says "SSH" or "sql"
# stays untouched; the rename hits string literals (Python names, registry
# calls, whitelists, UI comparison literals, doc tables/code fences).
QUOTED = [(re.compile(r"([\"'`])" + re.escape(k) + r"\1"), "\\1" + v + "\\1")
          for k, v in ((k, MAP[k]) for k in ORDERED)]
# TS object-literal keys are often unquoted in the UI icon maps
TS_KEYS = [(re.compile(r"(?m)^(\s*)" + re.escape(k) + r"\s*:"),
            "\\1" + MAP[k] + ":") for k in ORDERED]

EXTS = (".py", ".ts", ".tsx", ".md", ".css")


def transform(text: str, is_ts: bool) -> tuple[str, int]:
    hits = 0
    for pat, repl in QUOTED:
        text, n = pat.subn(repl, text)
        hits += n
    if is_ts:
        for pat, repl in TS_KEYS:
            text, n = pat.subn(repl, text)
            hits += n
    return text, hits


def walk():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        top = rel.split(os.sep)[0]
        if top in EXCLUDE_DIRS:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        # scope: keep only the code roots (+ repo-root single files)
        keep_dir = (rel == "." or top in INCLUDE_ROOTS or top.endswith(".md"))
        for fn in filenames:
            if fn in EXCLUDE_FILES:
                continue
            if fn.endswith(EXTS) and (keep_dir or fn in INCLUDE_ROOTS):
                yield os.path.join(dirpath, fn)


def main() -> None:
    apply = "--apply" in sys.argv
    total_files = 0
    total_hits = 0
    for path in walk():
        try:
            text = io.open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        new, hits = transform(text, path.endswith((".ts", ".tsx")))
        if hits:
            rel = os.path.relpath(path, ROOT)
            print(f"{hits:4d}  {rel}")
            total_files += 1
            total_hits += hits
            if apply:
                io.open(path, "w", encoding="utf-8", newline="").write(new)
    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: "
          f"{total_hits} replacements in {total_files} files")


if __name__ == "__main__":
    main()
