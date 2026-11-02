"""scripts/css_dead_sweep.py — one-off dead-CSS sweep (Phase E).

Removes CSS rule blocks whose EVERY class token is verified dead: the
class appears in no .ts/.tsx source (checked as literal AND as a whole
token, with dynamic-prefix families like sx-/cmdk-/fl- excluded from the
dead set entirely). @media wrappers survive when they still hold live
content and die when their body empties.

Safety rails:
  * a selector is dropped ONLY if it has at least one class and every
    class token is in the verified-dead set (element/id-only selectors
    are never touched)
  * comma lists keep their live halves; a fully-dead list dies
  * the set is hardcoded from the 2026-10-09 verified scan — idempotent
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "universe-agent-ui", "client", "src")

DEAD = {
    "a-body", "a-copy", "a-files", "a-thinking", "ac-meta", "ac-model",
    "ac-top", "activity-feed", "ag-close", "ag-count", "ag-foot",
    "ag-glyph", "ag-goal", "ag-handoff", "ag-handoffs", "ag-head",
    "ag-list", "ag-main", "ag-name", "ag-right", "ag-row", "ag-sub",
    "ag-time", "ag-title", "ag-what", "agent-card", "agent-grid",
    "agents-panel", "app-root-scroll", "attach-row", "ds-pill", "link-btn",
    "m-body", "m-head", "m-name", "m-side", "m-size", "mcp-addr",
    "mcp-list", "mcp-row", "mem-band", "mem-system", "mem-systems",
    "mo-head", "mo-skill", "mo-skills", "mos-meta", "mos-name", "ms-count",
    "ms-head", "ms-orbit", "ms-orbits", "name-input", "premium",
    "prov-installed", "prov-model-input", "prov-status", "rdx-animated",
    "s-grid", "s-note", "s-row", "s-saved", "settings-card",
    "settings-head", "settings-two", "settings-wrap", "sn-txt", "st-body",
    "st-card", "st-chip", "st-code", "st-cols", "st-field", "st-grid",
    "st-head", "st-hint", "st-ic", "st-label", "st-list", "st-meta",
    "st-name", "st-nav", "st-nav-btn", "st-page", "st-pick", "st-row",
    "st-save", "st-section", "st-sm", "st-stack", "st-toggle", "t-act",
    "t-del", "t-main", "t-when", "tg-track", "thinking-spark",
    "tool-chip", "transport-tabs", "ts-bars", "ts-canvas", "ts-dot",
    "ts-label", "tt-btn", "u-file", "u-files", "u-text", "w-mark", "w-sub",
    "wc-actions", "wc-badge", "wc-connect", "wc-field-label", "wc-form",
    "wc-key", "wc-key-field", "wc-note", "wc-pulse", "wc-top",
    "welcome-page", "wiz", "wiz-back", "wiz-body", "wiz-brand", "wiz-card",
    "wiz-cards", "wiz-count", "wiz-cta", "wiz-custom-btn", "wiz-eyebrow",
    "wiz-foot", "wiz-group", "wiz-group-head", "wiz-head", "wiz-launch",
    "wiz-lead", "wiz-mark", "wiz-must", "wiz-name-field", "wiz-name-glow",
    "wiz-name-input", "wiz-next", "wiz-node", "wiz-ok", "wiz-ok-dot",
    "wiz-rail", "wiz-skip", "wiz-step", "wiz-step-tag", "wiz-steps",
    "wiz-sub", "wiz-title", "wiz-verify-hint", "wiz-verify-pulse",
    "wiz-voice", "wiz-voices", "wiz-wake", "wn-dot", "wn-hint", "wn-label",
    "wn-link", "wn-text", "wp-aurora", "wp-core", "wp-orbit", "wp-stage",
    "ws-dot", "wv-dot", "wv-label", "o1", "o2",
}


def _parse(css: str):
    """All blocks with exact indices. Each entry:
    (header_start, brace_start, close_idx, header, body). The header spans
    from the previous sibling's end (so its comments ride along) to '{'.
    A stack handles the single nesting level this CSS uses (@media)."""
    blocks = []
    stack = []          # open brace indices
    hstack = []         # header-start per open brace
    seg = 0             # where the next header begins
    for i, ch in enumerate(css):
        if ch == "{":
            stack.append(i)
            hstack.append(seg)
            seg = i + 1
        elif ch == "}":
            if not stack:
                continue
            brace = stack.pop()
            hstart = hstack.pop()
            blocks.append((hstart, brace, i,
                           css[hstart:brace], css[brace + 1:i]))
            seg = i + 1
    return blocks


def _selector_dead(sel: str) -> bool:
    sel = sel.strip()
    if not sel or sel.startswith("@"):
        return False
    classes = re.findall(r"\.([a-zA-Z0-9_-]+)", sel)
    if not classes:
        return False
    return all(c in DEAD for c in classes)


def _header_is_wrapper(header: str) -> bool:
    return bool(re.search(r"@(media|supports|keyframes)", header))


def sweep(path: str) -> int:
    css = open(path, encoding="utf-8").read()
    orig = len(css)
    while True:
        drop = []
        for hstart, brace, close, header, body in _parse(css):
            if _header_is_wrapper(header):
                # die only when every child rule was already removed
                if not body.strip():
                    drop.append((hstart, close + 1))
                continue
            if "{" in body:      # never expected here; safety
                continue
            selectors = header.split(",")
            if selectors and all(_selector_dead(s) for s in selectors):
                drop.append((hstart, close + 1))
        if not drop:
            break
        for s, e in sorted(set(drop), reverse=True):
            css = css[:s] + css[e:]
    removed = orig - len(css)
    if removed:
        open(path, "w", encoding="utf-8", newline="").write(css)
    return removed


def main() -> int:
    total = 0
    files = [os.path.join(ROOT, "components", "desktop", n)
             for n in os.listdir(os.path.join(ROOT, "components", "desktop"))
             if n.endswith(".css")]
    files.append(os.path.join(ROOT, "index.css"))
    for p in files:
        r = sweep(p)
        if r:
            print(f"{os.path.basename(p)}: -{r} bytes")
        total += r
    print(f"total removed: {total} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
