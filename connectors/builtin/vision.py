"""connectors/builtin/vision.py — Design Agent's vision + CSS tools (Phase 3).

This is the file that closes the "Design Agent is blind" gap (Phase 3 design
tools). Three tools:

  * vision_analyze        — analyze any image and return a structured
                            description: real dimensions, dominant colours,
                            aspect ratio, brightness, file format. If a vision-
                            capable LLM provider is configured, attach its
                            description under llm_description. NEVER a stub.
  * css_tools             — validate/lint CSS for basic syntax errors
                            (unclosed braces, missing semicolons), and extract
                            a design system's token values (colours, fonts,
                            spacing) from a stylesheet.
  * color_contrast_check  — pure-math WCAG 2.1 contrast-ratio checker. Zero
                            dependencies. Returns the ratio + AA/AAA pass/fail
                            for normal and large text. Pairs with the 99 UX
                            guidelines already loaded in L4 (color-contrast,
                            contrast-readability).
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry


# ---- vision_analyze ------------------------------------------------------

def _hex_to_rgb(s: str) -> tuple[int, int, int] | None:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def _dominant_colours(img: Any, k: int = 5) -> list[dict[str, Any]]:
    """Real k-most-common colour extraction by quantising to 4 bits per
    channel and counting. Returns hex + count + fraction."""
    from collections import Counter
    small = img.resize((80, 80))  # downsample for speed
    pixels = list(small.getdata())
    # quantise to 4 bits per channel (16 levels each)
    quantised = [((r >> 4) << 4, (g >> 4) << 4, (b >> 4) << 4)
                 for (r, g, b) in (p[:3] for p in pixels)]
    counter = Counter(quantised)
    total = sum(counter.values()) or 1
    return [
        {"hex": f"#{r:02x}{g:02x}{b:02x}",
         "count": cnt, "fraction": round(cnt / total, 4)}
        for (r, g, b), cnt in counter.most_common(k)
    ]


def vision_analyze(path: str, top_colors: int = 5,
                   describe_with_llm: bool = False) -> dict:
    """Analyze any image file and return a structured description: dimensions,
    aspect ratio, format, file size, average brightness, dominant colours.
    Optionally call a vision-capable LLM for a human-language description.

    This is the tool that lets Design Agent see what it produces — and the
    Review Agent verify a UI screenshot. The structural analysis is ALWAYS
    real (never a stub); the LLM description is added when a vision provider
    is configured (otherwise omitted, with a clear note)."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"image not found: {path}"}
    try:
        from PIL import Image
        img = Image.open(p)
        # capture format/mode BEFORE convert() — convert() creates a new Image
        # without preserving the original format metadata
        original_format = img.format
        original_mode = img.mode
        img = img.convert("RGB")
    except Exception as e:
        return {"ok": False, "error": f"could not open image: {e}"}
    width, height = img.size
    # downsample BEFORE touching pixels: `list(img.getdata())` over a
    # 50-megapixel photo spikes memory by ~150MB (audit 2026-10); the
    # brightness sample takes ≤5000 points and dominant colours are
    # approximate anyway — a 256px thumbnail is statistically equivalent
    sample = img
    if max(width, height) > 256:
        sample = img.copy()
        sample.thumbnail((256, 256))
    pixels = list(sample.getdata())
    # real average brightness (perceptual)
    brightness = sum(0.299 * r + 0.587 * g + 0.114 * b
                     for (r, g, b) in pixels[::max(1, len(pixels) // 5000)]) / \
                 max(1, len(pixels[::max(1, len(pixels) // 5000)]))
    colours = _dominant_colours(img, k=top_colors)
    out: dict[str, Any] = {
        "ok": True,
        "path": str(p),
        "format": original_format,
        "mode": original_mode,
        "size_bytes": p.stat().st_size,
        "dimensions": {"width": width, "height": height},
        "aspect_ratio": round(width / height, 4) if height else None,
        "megapixels": round((width * height) / 1_000_000, 3),
        "average_brightness": round(brightness, 2),  # 0..255
        "brightness_bucket": "dark" if brightness < 64 else
                              ("dim" if brightness < 128 else
                               ("bright" if brightness < 200 else "very_bright")),
        "dominant_colours": colours,
    }
    if describe_with_llm:
        # Try the configured LLM with a vision request. If no vision provider
        # is configured, return a clear note instead of a fake description.
        try:
            from providers.client import get_llm_client
            client = get_llm_client()
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            # use the model's complete() with a multimodal user message — most
            # provider SDKs accept image_url content blocks; fall back if not.
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in 2-3 sentences. Focus on the visual layout and any UI elements."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }]
            resp = client.complete(agent="design", messages=messages, max_tokens=200)
            out["llm_description"] = resp.text
        except Exception as e:
            out["llm_description"] = None
            out["llm_description_note"] = f"vision LLM unavailable: {e}"
    return out


# ---- css_tools -----------------------------------------------------------

def css_tools(action: str = "lint", css: str = "",
              path: str = "") -> dict:
    """CSS toolkit. Two actions:
    - lint:    basic syntax checks (unbalanced braces, missing semicolons,
               missing units on numeric values). Returns real lint findings.
    - tokens:  extract a design system's tokens from a stylesheet — every
               custom property (--foo), every colour, every font-family, every
               z-index, every media query. Returns them as structured JSON.
    Pass `css` directly or `path` to a CSS file (path wins)."""
    if path:
        try:
            css = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"ok": False, "error": f"could not read css file: {e}"}
    if not css:
        return {"ok": False, "error": "css_tools requires `css` or `path`"}
    if action == "lint":
        findings: list[dict[str, Any]] = []
        # unbalanced braces
        open_b = css.count("{")
        close_b = css.count("}")
        if open_b != close_b:
            findings.append({"rule": "balanced_braces",
                             "severity": "error",
                             "msg": f"unbalanced braces: {open_b} '{{' vs {close_b} '}}'"})
        # missing semicolons: a property line ending in a value (not ; or { or }) before newline
        for m in re.finditer(r"([a-zA-Z-]+\s*:\s*[^;{}\n]+)\s*\n", css):
            findings.append({"rule": "missing_semicolon",
                             "severity": "warning",
                             "line": css[:m.start()].count("\n") + 1,
                             "msg": f"property without trailing ';': {m.group(1).strip()[:60]}"})
        # missing units on non-zero numeric values (e.g. "padding: 10;")
        for m in re.finditer(r":\s*(\d+(?:\.\d+)?)\s*;", css):
            val = m.group(1)
            if val != "0" and val != "0.0":
                findings.append({"rule": "missing_unit",
                                 "severity": "warning",
                                 "line": css[:m.start()].count("\n") + 1,
                                 "msg": f"numeric value without unit: {val}"})
        return {"ok": True, "action": "lint",
                "findings": findings, "finding_count": len(findings)}
    if action == "tokens":
        # extract custom properties
        custom_props = {m.group(1): m.group(2).strip()
                        for m in re.finditer(r"--([a-zA-Z0-9_-]+)\s*:\s*([^;]+);", css)}
        # extract colours
        colours = set()
        for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]+\)|hsla?\([^)]+\)", css):
            colours.add(m.group(0))
        # extract font-families from font-family: ... declarations
        fonts = set()
        for m in re.finditer(r"font-family\s*:\s*([^;]+);", css):
            fonts.add(m.group(1).strip())
        # ALSO extract font names from custom property values that look like
        # fonts (e.g. --font-sans: "Inter", sans-serif) — design systems often
        # define fonts as custom properties
        for prop_name, prop_val in custom_props.items():
            if "font" in prop_name.lower() or "type" in prop_name.lower():
                # strip var() wrappers if present, and the value should look like a font spec
                if any(font_kw in prop_val.lower() for font_kw in ("serif", "sans", "mono", '"', "'")):
                    fonts.add(prop_val)
        # extract z-indexes
        zindexes = set()
        for m in re.finditer(r"z-index\s*:\s*(-?\d+)", css):
            zindexes.add(int(m.group(1)))
        # extract media queries
        media = re.findall(r"@media\s+[^{]+", css)
        return {"ok": True, "action": "tokens",
                "custom_properties": custom_props,
                "colours": sorted(colours),
                "font_families": sorted(fonts),
                "z_indexes": sorted(zindexes),
                "media_queries": [m.strip() for m in media]}
    return {"ok": False, "error": f"unknown action {action!r}; use 'lint' or 'tokens'"}


# ---- color_contrast_check ------------------------------------------------

def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance. Pure math, zero deps."""
    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(fg: tuple[int, int, int],
                    bg: tuple[int, int, int]) -> float:
    """WCAG 2.1 contrast ratio. Returns e.g. 4.5 for the AA threshold."""
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_colour(s: str) -> tuple[int, int, int] | None:
    s = s.strip()
    if s.startswith("#"):
        return _hex_to_rgb(s)
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def color_contrast_check(foreground: str, background: str,
                         font_size_px: int = 16,
                         font_weight: str = "normal") -> dict:
    """Pure-math WCAG 2.1 contrast ratio checker. Zero dependencies.
    Inputs accept #hex or rgb()/rgba() strings.
    Returns the ratio + AA/AAA pass/fail for normal and large text.

    Large text = >=18pt (24px) normal OR >=14pt (18.66px) bold per WCAG 2.1."""
    fg = _parse_colour(foreground)
    bg = _parse_colour(background)
    if fg is None:
        return {"ok": False, "error": f"unparseable foreground: {foreground!r}"}
    if bg is None:
        return {"ok": False, "error": f"unparseable background: {background!r}"}
    ratio = _contrast_ratio(fg, bg)
    is_bold = font_weight.lower() in ("bold", "700", "800", "900")
    is_large = font_size_px >= 24 or (font_size_px >= 18.66 and is_bold)
    # WCAG 2.1 thresholds
    aa_normal = ratio >= 4.5
    aa_large = ratio >= 3.0
    aaa_normal = ratio >= 7.0
    aaa_large = ratio >= 4.5
    return {
        "ok": True,
        "foreground": foreground, "background": background,
        "foreground_rgb": fg, "background_rgb": bg,
        "contrast_ratio": round(ratio, 4),
        "is_large_text": is_large,
        "wcag_aa_pass": aa_normal if not is_large else aa_large,
        "wcag_aaa_pass": aaa_normal if not is_large else aaa_large,
        "wcag_aa_threshold": 4.5 if not is_large else 3.0,
        "wcag_aaa_threshold": 7.0 if not is_large else 4.5,
        "verdict": ("pass" if (aa_normal if not is_large else aa_large) else "fail"),
    }


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="VisionAnalyze", capability="Read",
        description="Analyze any image (dimensions, dominant colours, brightness; optional LLM description)",
        handler=vision_analyze, consent="auto",
        resources=["path:glob:**/*"],
    ))
    reg.register(Tool(
        name="CssTools", capability="Read",
        description="Lint CSS or extract design-system tokens (colours, fonts, custom props, z-indexes)",
        handler=css_tools, consent="auto",
        resources=["path:glob:**/*"],
    ))
    reg.register(Tool(
        name="ColorContrastCheck", capability="Read",
        description="Pure-math WCAG 2.1 contrast ratio checker (zero deps)",
        handler=color_contrast_check, consent="auto",
        resources=[],
    ))
