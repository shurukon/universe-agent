# -*- coding: utf-8 -*-
"""One-shot restyle of the legacy CSS sheets to the measured ZCode palette.

The token layer (design-system.css/shell.css/index.css) is already
ZCode-literal, but the older interaction sheets still hard-code the retired
teal accents — rgba(32,128,141,…) borders/glows/tints — plus a handful of
legacy hex values that bypass the variables. This rewrites them in place:

  * teal rgba tints  -> neutral white lifts (alpha-aware: strong usages
    become rgba(255,255,255,.16), soft tints rgba(255,255,255,.05))
  * amber #d8a25f / rgba(216,162,95,…) -> ZCode orange #ff8a30 family
  * settings sheet's self-contained Linear hexes -> ZCode surfaces/inks
  * #0b0d11 -> #161616 (ZCode canvas)

Idempotent: running it twice changes nothing the second time.
"""

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SRC = Path(
    r"D:\Universecomputer\Universe-compter\universe-agent-ui\client\src\components\desktop"
)
FILES = [
    "flow.css",
    "desktop.css",
    "redesign.css",
    "settings-linear.css",
    "welcome-dark.css",
]

TEAL = re.compile(r"rgba\(\s*32\s*,\s*128\s*,\s*141\s*,\s*([0-9.]+)\s*\)")
AMBER = re.compile(r"rgba\(\s*216\s*,\s*162\s*,\s*95\s*,\s*([0-9.]+)\s*\)")


def teal_to_neutral(m: re.Match) -> str:
    alpha = float(m.group(1) or 0)
    return "rgba(255,255,255,.16)" if alpha >= 0.3 else "rgba(255,255,255,.05)"


def amber_to_orange(m: re.Match) -> str:
    return f"rgba(255,138,48,{m.group(1)})"


SETTINGS_HEX = [
    # (pattern, replacement) — settings-linear.css self-contained scale
    ("#010102", "#161616"),
    ("#0f1011", "#2b2b2b"),
    ("#141516", "#363636"),
    ("#18191a", "#363636"),
    ("#23252a", "#414141"),
    ("#34343a", "#4b4b4b"),
    ("#8a8f98", "#999999"),
    ("#d0d6e0", "#d4d4d4"),
    ("#62666d", "#5e5e5e"),
    ("#27a644", "#46bf72"),
    ("#20808d", "#46bf72"),
    ("#0b0d11", "#161616"),
    ("color: #000", "color: #161616"),
]


def restyle_settings(text: str) -> str:
    for old, new in SETTINGS_HEX:
        text = text.replace(old, new)
    # #f7f8f8 is double-duty: white buttons stay white, ink drops to #d4d4d4
    text = re.sub(r"(background(?:-color)?\s*:\s*)#f7f8f8", r"\g<1>#ffffff", text)
    text = re.sub(r"(border-color\s*:\s*)#f7f8f8", r"\g<1>#ffffff", text)
    text = re.sub(r"(accent-color\s*:\s*)#f7f8f8", r"\g<1>#d4d4d4", text)
    text = text.replace("#f7f8f8", "#d4d4d4")  # remaining ink usages
    return text


def main() -> None:
    for name in FILES:
        path = SRC / name
        if not path.exists():
            print(f"skip (missing): {name}")
            continue
        text = path.read_text(encoding="utf-8")
        before = text
        text = TEAL.sub(teal_to_neutral, text)
        text = AMBER.sub(amber_to_orange, text)
        text = text.replace("#d8a25f", "#ff8a30")
        text = text.replace("#0b0d11", "#161616")
        if name == "settings-linear.css":
            text = restyle_settings(text)
        if text != before:
            path.write_text(text, encoding="utf-8", newline="")
            n_teal = len(TEAL.findall(before))
            n_amber = before.count("#d8a25f") + len(AMBER.findall(before))
            print(f"rewrote {name}: {n_teal} teal rgba, {n_amber} amber")
        else:
            print(f"clean (no change): {name}")


if __name__ == "__main__":
    main()
