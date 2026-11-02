"""Renderer-side extraction — UI strings + per-surface icon maps from ZCode's
renderer chunks (settings, palette, terminal, entry, catalog tree).

Read-only: reads docs/re-artifacts/asar-inventory.json + app.asar members.
"""
import io
import json
import re

INV = r"D:\Universecomputer\Universe-compter\docs\re-artifacts\asar-inventory.json"
ASAR = r"C:\Program Files\ZCode\resources\app.asar"
OUT = r"D:\Universecomputer\Universe-compter\docs\re-artifacts\renderer-facts.json"

inv = json.load(io.open(INV, encoding="utf-8"))
base = inv["base"]
files = inv["files"]
asar = io.open(ASAR, "rb")


def read(path):
    ent = files[path]
    asar.seek(base + int(ent["offset"]))
    return asar.read(int(ent["size"])).decode("utf-8", errors="replace")


name_by_chunk = {}
assets = [p for p in files if p.startswith("/out/renderer/assets/") and p.endswith(".js")]
for p in assets:
    if int(files[p]["size"]) > 4096:
        continue
    try:
        src = read(p)
    except Exception:
        continue
    if "createLucideIcon" in src:
        m = re.search(r"\(\s*[`\"]([a-z0-9-]+)[`\"]", src)
        if m:
            name_by_chunk[p.rsplit("/", 1)[-1]] = m.group(1)

interesting = [p for p in assets
               if re.search(r"/(index|styles|catalogTree|palette|settings|terminal|welcome|"
                            r"chat|composer|IntlProvider|codingPlan)[A-Za-z0-9_-]*\.js$", p)]
print("interesting chunks:", len(interesting))

import_pat = re.compile(r'from\s*"\.\/([A-Za-z0-9_.-]+\.js)"')
facts = {"surface_icons": {}, "strings": {}}

for p in interesting:
    if int(files[p]["size"]) > 6_000_000:
        continue
    try:
        src = read(p)
    except Exception:
        continue
    label = p.rsplit("/", 1)[-1]
    used = []
    for m in import_pat.finditer(src):
        ic = name_by_chunk.get(m.group(1))
        if ic:
            used.append(ic)
    if used:
        facts["surface_icons"][label] = sorted(set(used))
    lits = re.findall(r'"((?:[A-Z][A-Za-z0-9 .&/:%+_\'#!?()\[\]—-]{6,60}))"', src)
    keep = [s for s in lits if not re.search(r"[{}<>\\`$]|function|return|const|=>", s)]
    if keep:
        facts["strings"][label] = sorted(set(keep))[:400]

with io.open(OUT, "w", encoding="utf-8") as fh:
    json.dump(facts, fh, ensure_ascii=False, indent=1)
print("wrote renderer-facts.json — surfaces:", len(facts["surface_icons"]),
      "chunks with strings:", len(facts["strings"]))
for k, v in facts["surface_icons"].items():
    print(" ", k, "->", len(v), "icons:", v[:14])
