"""Icon inventory — map every lucide icon chunk in ZCode's renderer bundle
to the chunk that imports it, so each Universe surface can use the SAME icon.

Read-only: parses docs/re-artifacts/asar-inventory.json (written by the
asar header parse) and reads member files directly from app.asar.
"""
import io
import json
import re
from collections import defaultdict

INV = r"D:\Universecomputer\Universe-compter\docs\re-artifacts\asar-inventory.json"
ASAR = r"C:\Program Files\ZCode\resources\app.asar"
OUT = r"D:\Universecomputer\Universe-compter\docs\re-artifacts\icon-inventory.json"

inv = json.load(io.open(INV, encoding="utf-8"))
base = inv["base"]
files = inv["files"]
asar = io.open(ASAR, "rb")


def read(path):
    ent = files[path]
    asar.seek(base + int(ent["offset"]))
    return asar.read(int(ent["size"])).decode("utf-8", errors="replace")


assets = sorted(p for p in files if p.startswith("/out/renderer/assets/"))
print("renderer assets:", len(assets))

icon_chunks = {}
for p in assets:
    if not p.endswith(".js"):
        continue
    if files[p]["size"] > 4096:
        continue
    try:
        src = read(p)
    except Exception:
        continue
    if "createLucideIcon" in src:
        m = re.search(r"\(\s*[`\"]([a-z0-9-]+)[`\"]", src)
        if m:
            icon_chunks[p] = m.group(1)
print("icon chunks found:", len(icon_chunks))
print("sample:", list(icon_chunks.items())[:5])

by_import = defaultdict(set)
import_pat = re.compile(r'from\s*"\.\/([A-Za-z0-9_.-]+\.js)"')
for p in assets:
    if not p.endswith(".js") or files[p]["size"] > 8_000_000:
        continue
    try:
        src = read(p)
    except Exception:
        continue
    for m in import_pat.finditer(src):
        target = "/out/renderer/assets/" + m.group(1)
        if target in icon_chunks:
            by_import[icon_chunks[target]].add(p.rsplit("/", 1)[-1])

out = {
    "icon_count": len(icon_chunks),
    "icons": sorted(icon_chunks.values()),
    "importers": {k: sorted(v) for k, v in sorted(by_import.items())},
}
with io.open(OUT, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print("wrote icon-inventory.json —", out["icon_count"], "icons,",
      len(out["importers"]), "with importers")
