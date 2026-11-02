# ZCode Visual Tokens — the measured palette

Source of truth for the Universe Agent UI restyle ("exactly like ZCode").
Every value below was **measured by pixel sampling** (PIL) from the 8
reference screenshots in `C:\Users\MKT\.zcode\workspace\default\zcode-harness-docs\screenshots\`
and cross-verified against the **live ZCode window** (2026-10-08,
`PIL.ImageGrab` at identical fractional coordinates — all core surfaces
returned identical hex values, palette confirmed stable). Extractor:
`scripts/extract_zcode_palette.py`; live evidence:
`docs/zcode-live-screens/live-zcode-window.png`.

## Core surfaces

| Role | Hex | Evidence |
|---|---|---|
| Canvas / chat background | `#161616` | ref 01/02 + live (0.44, 0.72) |
| Panels: sidebar, cards, composer, rail, palette, settings | `#2b2b2b` | ref 01/03/05–08 + live |
| Top bar (over chat) | `#262626` | ref 01 + live (0.44, 0.035) |
| Top bar left segment (over sidebar) | `#2b2b2b` | ref 01 + live (0.03, 0.035) |
| Active pill (sidebar task, palette tab, settings nav) | `#414141` | ref 01/03/05 |
| Selected / hover row (palette list) | `#363636` | ref 03 |
| kbd chip | bg `#404040`, ink `#d4d4d4` | ref 03 |
| Overlay / backdrop | ≈`rgba(0,0,0,0.45)` (renders `#0d0d0d` over chat) | ref 03 edge scan |
| Card borders | hairline `#414141`, strong `#4b4b4b` (composer card) | edge scans ref 03/01 |
| Electron window background | `#161616` | matches canvas (no white flash) |

Sidebar↔chat and topbar↔chat separations are **pure color steps** —
ZCode draws no hairline there.

## Ink scale

| Role | Hex |
|---|---|
| Primary text (titles, items, tool names, user name) | `#d4d4d4` |
| Secondary text (task ages, counters, "now") | `#999999` |
| Muted (palette section headers, inactive tabs) | `#909090` |
| Faint (sidebar section headers, composer placeholder) | `#5e5e5e` |
| Top bar window controls / right icon cluster | `#f5f5f5` |

## Accents

| Role | Hex | Notes |
|---|---|---|
| Green — the accent (Update pill, enabled dots, positive) | `#46bf72` | dark text on green: `#2b2b2b`; live-confirmed |
| Orange — permission chip (shield + "Full access") | `#ff8a30` | live-confirmed |
| Primary CTA (Subscribe-style) | `#ffffff` | text `#161616` |
| Composer square button (send/stop) | `#d4d4d4` | dark icon |
| Computer Use dot | `#46bf72` @ ~50% over panel ≈ `#39754f` | derived |
| Transparency bar ("…is controlling your computer") | `#f5c518` (estimate) | yellow, dark text — refine with a live capture in Phase 6 |
| Blue tint card (subscription content) | `#2e3b4a` | Z.ai-specific content, not a core token |

ZCode is **near-monochrome**: green marks positive/status only; there is
no colored interactive accent. Selection is a gray pill (`#414141`),
primary actions are white, the permission chip is orange.

## Type

- UI font: system stack — `"Segoe UI", -apple-system, system-ui, sans-serif`
  (matches ZCode's Segoe rendering on Windows; no bundled UI face).
- UI base size: 13px. Mono: keep JetBrains Mono (no ZCode mono reference).
- Radii: pills `9999px`, cards ≈ 8–14px. Sidebar width ≈ 264px (unchanged).

## Mapping to Universe tokens (design-system.css / shell.css)

| Universe token | ZCode value |
|---|---|
| `--canvas` | `#161616` |
| `--ds-surface-1` (panels) | `#2b2b2b` |
| `--ds-surface-2` (hover/selected/raised) | `#363636` |
| `--ds-surface-3` (active pills) | `#414141` |
| `--ds-line` | `#414141` |
| `--ds-line-strong` | `#4b4b4b` |
| `--ds-ink` | `#d4d4d4` |
| `--ds-ink-2` (titles — ZCode renders secondary as bright as primary) | `#d4d4d4` |
| `--ds-ink-3` (muted) | `#999999` |
| `--ds-ink-4` (faint) | `#5e5e5e` |
| `--ds-teal` → `--ds-accent` | `#46bf72` (dim `rgba(70,191,114,0.14)`) |
| `--amber` (fixes dangling refs in flow.css) / `--warning` | `#ff8a30` |
| `--success` | `#46bf72` |
| primary CTA / send-ready button | `#ffffff` / `#d4d4d4` on `#161616` text |
| `--tb-bg` | `#262626` |
| `--tb-ink` (topbar buttons) | `#f5f5f5` |
| `--chip-bg` / `--chip-line` | transparent (ZCode chips are text-level) |
| Electron `backgroundColor` (main.ts) | `#161616` |

## Live stability check (2026-10-08)

Identical fractional coordinates on the live window returned identical
hex for every core surface (`#161616` / `#2b2b2b` / `#262626`) and both
accents (`#ff8a30`, `#46bf72`) → the palette is stable across the
reference capture and the current version.

## Universe live pass (2026-10-09 — Phase B verified)

After the restyle, `scripts/verify_universe_live.py` activated the running
**Universe** window (GetForegroundWindow-confirmed, so the capture is
Universe's own pixels — not ZCode wearing the same palette) and measured:

| Surface | Got | Want |
|---|---|---|
| Top bar left segment (over sidebar) | `#2b2b2b` | `#2b2b2b` |
| Top bar over chat | `#262626` | `#262626` |
| Sidebar mid / lower | `#2b2b2b` | `#2b2b2b` |
| Canvas | `#161616` | `#161616` |

Histogram: kbd chips `#404040` 2.30%, active pills `#414141` 2.28%,
selected rows `#363636` 1.44%, composer border `#4b4b4b` present, ink
`#d4d4d4` present, **teal `rgb(20,184,166)`: 0 px** — the legacy accent
is fully gone from the chrome. Capture:
`docs/zcode-live-screens/live-universe-window.png`.
