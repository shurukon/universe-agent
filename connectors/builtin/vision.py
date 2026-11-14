"""connectors/builtin/vision.py — image understanding for the Universe agent.

One tool:

  * vision_analyze        — analyze any image and return a structured
                            description: real dimensions, dominant colours,
                            aspect ratio, brightness, file format. If a vision-
                            capable LLM provider is configured, attach its
                            description under llm_description. NEVER a stub.

The CSS/SVG/contrast tools that used to live here belonged to the retired
Design specialist; they were deleted with the rest of the unreachable tool
set (audit 2026-11-10).
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry


# ---- vision_analyze ------------------------------------------------------



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




def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="VisionAnalyze", capability="Read",
        description="Analyze any image (dimensions, dominant colours, brightness; optional LLM description)",
        handler=vision_analyze, consent="auto",
        resources=["path:glob:**/*"],
    ))
