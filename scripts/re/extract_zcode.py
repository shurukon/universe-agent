"""ZE RE extraction — pull ZCode's real internals from the installed app.

Sources (all read-only):
  C:\\Program Files\\ZCode\\resources\\glm\\zcode.cjs        — bundled agent runtime
  C:\\Program Files\\ZCode\\resources\\app.asar               — renderer bundle
Outputs JSON evidence under docs/re-artifacts/ for docs/zcode-re-map.md.
"""
import io
import json
import re

BASE = r"C:\Program Files\ZCode\resources"
OUT = r"D:\Universecomputer\Universe-compter\docs\re-artifacts"

SRC = io.open(BASE + r"\glm\zcode.cjs", encoding="utf-8", errors="replace").read()
print("zcode.cjs chars:", len(SRC))

facts = {}

# ---------------------------------------------------------------- slash ---
# registry entries look like  name:"help",summary:"..."  (bundled/minified)
pat = re.compile(
    r'name:\s*"([a-z][a-z0-9-]{1,24})"\s*,\s*summary:\s*"([^"\\]{3,220})"'
)
pairs = pat.findall(SRC)
uniq: dict[str, str] = {}
for name, summary in pairs:
    if name not in uniq or len(summary) > len(uniq[name]):
        uniq[name] = summary
print("slash name/summary pairs:", len(uniq))
facts["slash_summaries"] = uniq

usage_pat = re.compile(
    r'name:\s*"([a-z][a-z0-9-]{1,24})"\s*,\s*summary:\s*"[^"\\]{3,220}"\s*,'
    r'\s*usage:\s*"([^"\\]{0,120})"'
)
usages = {n: u for n, u in usage_pat.findall(SRC)}
print("usages found:", len(usages))
facts["slash_usages"] = usages

alias_pat = re.compile(r'alias(?:es)?:\s*\[([^\]]{1,120})\]')
facts["slash_alias_lists"] = alias_pat.findall(SRC)[:40]
facts["has_compress"] = "compress" in SRC

# ---------------------------------------------------------------- tools ---
TOOL_NAMES = [
    "Bash", "BashOutput", "Read", "Write", "Edit", "NotebookEdit", "Grep",
    "Glob", "TodoWrite", "TodoRead", "Agent", "Task", "Skill", "EnterPlanMode",
    "ExitPlanMode", "AskUserQuestion", "CronCreate", "CronList", "CronUpdate",
    "CronDelete", "SendMessage", "ReadSessionContext", "TaskOutput", "TaskStop",
    "WebFetch", "WebSearch", "LSP", "Notebook",
]
facts["tool_name_counts"] = {
    t: len(re.findall(r"(?<![A-Za-z])" + t + r"(?![A-Za-z])", SRC))
    for t in TOOL_NAMES
}
print("tool name counts:", facts["tool_name_counts"])

desc_pat = re.compile(
    r'name:\s*"(Bash|Read|Write|Edit|Grep|Glob|TodoWrite|Agent|Skill|'
    r'CronCreate|CronList|CronUpdate|CronDelete|SendMessage|ExitPlanMode|'
    r'EnterPlanMode|AskUserQuestion|WebFetch|WebSearch)"\s*,\s*'
    r'description:\s*"((?:[^"\\]|\\.){10,400})"'
)
descs: dict[str, list[str]] = {}
for name, desc in desc_pat.findall(SRC):
    descs.setdefault(name, []).append(desc)
facts["tool_descriptions"] = {k: v[:3] for k, v in descs.items()}
print("tool descriptions:", {k: len(v) for k, v in descs.items()})

# ---------------------------------------------------------------- modes ---
facts["yolo_contexts"] = re.findall(r"[^ ]{0,40}(?:yolo|YOLO)[^ ]{0,60}", SRC)[:20]
facts["permission_mode_contexts"] = sorted(
    set(re.findall(r"(?:permission_?mode|permissionMode)[^,}]{0,80}", SRC))
)[:40]

# ------------------------------------------------------------- i18n/ui ---
ui_keys = re.findall(r'"(?:titleBar|tray|dock)[A-Za-z0-9_.]{2,60}"', SRC)
facts["ui_i18n_keys"] = sorted(set(ui_keys))[:200]

# ---------------------------------------------------------------- write ---
with io.open(OUT + r"\zcode-facts.json", "w", encoding="utf-8") as fh:
    json.dump(facts, fh, ensure_ascii=False, indent=1)
print("wrote zcode-facts.json")

m = re.search(r'"help"', SRC)
if m:
    s = max(0, m.start() - 400)
    e = min(len(SRC), m.start() + 6000)
    with io.open(OUT + r"\slash-region.txt", "w", encoding="utf-8") as fh:
        fh.write(SRC[s:e])
    print("wrote slash-region.txt", e - s, "chars")
