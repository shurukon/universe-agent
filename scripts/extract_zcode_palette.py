# -*- coding: utf-8 -*-
"""Extract the exact ZCode visual palette from the reference screenshots.

Read-only: samples pixel colors from the 8 PNGs captured from ZCode
(C:\\Users\\MKT\\.zcode\\workspace\\default\\zcode-harness-docs\\screenshots)
and prints labeled hex values — backgrounds (5x5 patch median), text ink
(brightest pixel in a box), and saturated accents (most saturated pixel
in a box). The output feeds docs/zcode-tokens.md.
"""

import os
import sys

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

DIR = r"C:\Users\MKT\.zcode\workspace\default\zcode-harness-docs\screenshots"


def hexc(rgb):
    return "#{:02x}{:02x}{:02x}".format(rgb[0], rgb[1], rgb[2])


def patch_med(img, fx, fy, r=3):
    """Median-color 5x5 patch at fractional coordinates."""
    w, h = img.size
    x, y = int(fx * w), int(fy * h)
    px = []
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            xx = min(max(x + dx, 0), w - 1)
            yy = min(max(y + dy, 0), h - 1)
            px.append(img.getpixel((xx, yy)))
    px.sort(key=lambda c: c[0] + c[1] + c[2])
    return px[len(px) // 2]


def brightest(img, box, step=2):
    """Brightest pixel in a fractional box — approximates text ink."""
    x0, y0, x1, y1 = box
    w, h = img.size
    best = (0, 0, 0)
    for x in range(int(x0 * w), int(x1 * w), step):
        for y in range(int(y0 * h), int(y1 * h), step):
            c = img.getpixel((x, y))
            if c[0] + c[1] + c[2] > sum(best):
                best = c
    return best


def most_saturated(img, box, step=1):
    """Most saturated pixel in a fractional box — finds accent colors."""
    x0, y0, x1, y1 = box
    w, h = img.size
    best = (0, 0, 0)
    bestsat = -1
    for x in range(int(x0 * w), int(x1 * w), step):
        for y in range(int(y0 * h), int(y1 * h), step):
            c = img.getpixel((x, y))
            sat = max(c) - min(c)
            if sat > bestsat:
                bestsat = sat
                best = c
    return best


def main():
    imgs = {}
    for name in sorted(os.listdir(DIR)):
        if name.lower().endswith(".png"):
            imgs[name] = Image.open(os.path.join(DIR, name)).convert("RGB")
    for name, im in imgs.items():
        print(f"== {name} {im.size} ==")

    m1 = imgs.get("01-main-window.png")
    m2 = imgs.get("02-chat-with-tools.png")
    m3 = imgs.get("03-search-panel.png")
    m4 = imgs.get("04-live-progress-update.png")
    m5 = imgs.get("05-settings-open.png")
    m6 = imgs.get("06-settings-general.png")
    m7 = imgs.get("07-settings-general-middle.png")
    m8 = imgs.get("08-settings-general-bottom.png")

    def p(label, im, fx, fy, r=3):
        if im:
            print(f"  {label:32s} {hexc(patch_med(im, fx, fy, r))}")

    def b(label, im, box):
        if im:
            print(f"  {label:32s} {hexc(brightest(im, box))}  (brightest)")

    def s(label, im, box):
        if im:
            print(f"  {label:32s} {hexc(most_saturated(im, box))}  (saturated)")

    print("\n-- 01-main-window surfaces --")
    p("topbar_bg", m1, 0.44, 0.022)
    p("topbar_left_bg", m1, 0.03, 0.022)
    p("update_btn_bg", m1, 0.085, 0.026)
    p("sidebar_bg", m1, 0.075, 0.55)
    p("sidebar_active_pill", m1, 0.07, 0.39)
    p("chat_bg", m1, 0.44, 0.72)
    p("progress_card_bg", m1, 0.89, 0.19)
    p("progress_waiting_bg", m1, 0.85, 0.30)
    p("input_card_bg", m1, 0.475, 0.855)
    p("input_footer_bg", m1, 0.475, 0.93)
    p("send_btn_bg", m1, 0.727, 0.908)
    b("text_new_task", m1, (0.025, 0.062, 0.10, 0.078))
    b("text_title_topbar", m1, (0.28, 0.012, 0.45, 0.035))
    b("text_task_age", m1, (0.14, 0.385, 0.165, 0.40))
    s("shield_full_access", m1, (0.205, 0.895, 0.225, 0.92))
    s("computer_use_dot", m1, (0.358, 0.90, 0.372, 0.915))
    s("update_btn_green", m1, (0.07, 0.015, 0.10, 0.035))

    print("\n-- 02-chat-with-tools --")
    p("chat_bg", m2, 0.44, 0.5)
    b("text_tool_name", m2, (0.21, 0.093, 0.26, 0.11))
    b("text_events_counter", m2, (0.28, 0.093, 0.36, 0.11))

    print("\n-- 03-search-panel --")
    p("overlay_backdrop", m3, 0.08, 0.5)
    p("palette_card_bg", m3, 0.5, 0.55)
    p("palette_search_bg", m3, 0.5, 0.107)
    p("tab_all_pill", m3, 0.368, 0.141)
    p("selected_row_bg", m3, 0.5, 0.369)
    p("kbd_chip_bg", m3, 0.621, 0.411)
    b("text_section_header", m3, (0.355, 0.19, 0.42, 0.21))
    b("text_item", m3, (0.36, 0.36, 0.45, 0.38))
    b("text_kbd", m3, (0.60, 0.402, 0.645, 0.42))

    print("\n-- 04-live-progress-update --")
    p("progress_card_bg", m4, 0.89, 0.19)

    print("\n-- 05-settings-open --")
    p("settings_bg", m5, 0.55, 0.62)
    p("left_active_pill", m5, 0.078, 0.227)
    p("provider_selected_bg", m5, 0.39, 0.217)
    p("provider_row_bg", m5, 0.39, 0.29)
    p("blue_card_bg", m5, 0.65, 0.40)
    p("subscribe_btn_bg", m5, 0.785, 0.27)
    p("disabled_chip_bg", m5, 0.529, 0.198)
    b("text_big_title", m5, (0.32, 0.055, 0.44, 0.10))
    b("text_nav_item", m5, (0.03, 0.16, 0.10, 0.18))
    b("text_section_label", m5, (0.02, 0.125, 0.09, 0.14))
    s("green_enabled_dot", m5, (0.437, 0.318, 0.45, 0.335))

    for label, im in (("06", m6), ("07", m7), ("08", m8)):
        print(f"\n-- {label} settings-general --")
        p("settings_bg", im, 0.55, 0.5)
        p("card_bg", im, 0.55, 0.35)
        p("left_nav_bg", im, 0.078, 0.6)


if __name__ == "__main__":
    main()
