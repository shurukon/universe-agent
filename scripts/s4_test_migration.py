"""scripts/s4_test_migration.py — one-shot S4 test-suite migration.

Retired agent names (planning/write/file/api/data/devops/browser) become
"general" in every test context; ladder-shaped tests get rewritten for the
single-general-run model. EOL-aware (CRLF files stay CRLF). Guarded with
asserts; a failed anchor aborts BEFORE any write.
"""
import io

RETIRED = ["planning", "write", "file", "api", "data", "devops", "browser"]


def load(p: str) -> tuple:
    raw = io.open(p, encoding="utf-8", newline="").read()
    crlf = "\r\n" in raw
    return (raw.replace("\r\n", "\n") if crlf else raw), crlf


def save(p: str, s: str, crlf: bool) -> None:
    if crlf:
        s = s.replace("\n", "\r\n")
    io.open(p, "w", encoding="utf-8", newline="").write(s)


# ---- mechanical agent-name renames -----------------------------------------
MECH_CALLS = [f'agent="{r}"' for r in RETIRED] + [f'get_agent("{r}")' for r in RETIRED]
MECH_CALL_FILES = [
    "tests/test_tool_whitelist.py",
    "tests/test_thirdparty.py",
    "tests/test_mcp_client.py",
    "tests/test_m1_security.py",
    "tests/test_security.py",
]
MECH_QUOTE_FILES = [  # agent-list literals (ladders/rubrics)
    "tests/test_work_intent_routing.py",
    "tests/test_integration.py",
    "tests/test_live_universe_validation.py",
    "eval/suite.py",
]

for p in MECH_CALL_FILES:
    s, crlf = load(p)
    before = s
    for pat in MECH_CALLS:
        s = s.replace(pat, 'agent="general"' if pat.startswith("agent=") else 'get_agent("general")')
    assert s != before, f"no mechanical renames applied in {p}"
    save(p, s, crlf)
    print(f"mech-calls: {p}")

for p in MECH_QUOTE_FILES:
    s, crlf = load(p)
    before = s
    for r in RETIRED:
        s = s.replace(f'"{r}"', '"general"')
    # the orchestrator identity literal inside routing/route_all assertions
    s = s.replace('"orchestrator" in routed', '"general" in routed')
    s = s.replace('"orchestrator" in names', '"general" in names')
    assert s != before, f"no quote renames applied in {p}"
    save(p, s, crlf)
    print(f"mech-quotes: {p}")

# ---- test_provider_routing_e2e.py — counts + labels ------------------------
p = "tests/test_provider_routing_e2e.py"
s, crlf = load(p)
pairs = [
    ("assert len(names) == 13", "assert len(names) == 6"),
    ('assert "orchestrator" in routed and len(routed) == 13',
     'assert "general" in routed and len(routed) == 6'),
    ('ag.name = "planning"', 'ag.name = "general"'),
    ('agent="planning", turn_id="g-break"', 'agent="general", turn_id="g-break"'),
]
for a, b in pairs:
    assert a in s, f"provider_routing anchor missing: {a[:40]}"
    s = s.replace(a, b)
save(p, s, crlf)
print("patched: tests/test_provider_routing_e2e.py")

# ---- test_integration.py — single-run assertions ---------------------------
p = "tests/test_integration.py"
s, crlf = load(p)
OLD = ('    agents = [s["agent"] for s in summary["steps"]]\n'
       '    assert "general" in agents\n'
       '    assert "code" in agents\n'
       '    assert "review" in agents\n'
       '    assert len(agents) >= 3  # real handoff chain')
NEW = ('    agents = [s["agent"] for s in summary["steps"]]\n'
       '    assert "general" in agents  # S1d: the single general agent run\n'
       '    assert len(agents) >= 1')
assert OLD in s, "integration ladder assertions not found post-rename"
s = s.replace(OLD, NEW)
save(p, s, crlf)
print("patched: tests/test_integration.py")

# ---- test_adaptive_repairs.py — ladder tests → media-contract test ----------
p = "tests/test_adaptive_repairs.py"
s, crlf = load(p)
START = "def test_media_plan_is_closed_and_ordered():"
END = "def test_effective_prompt_contains_capability_and_acceptance_protocol():"
i0, i1 = s.find(START), s.find(END)
assert 0 < i0 < i1, "adaptive_repairs ladder block not found"
NEW_BLOCK = '''def test_media_contract_is_closed_and_ordered():
    plan = media_phase_plan()
    assert [step["phase_id"] for step in plan] == [
        "capability_check", "method_research", "input_probe", "scene_timestamps",
        "ffmpeg_edit", "subtitle_decision", "ffprobe", "final_review",
    ]
    assert "api" not in [step["agent"] for step in plan]
    assert all(int(step["budget"]) > 0 for step in plan)


# S1d: _default_plan and _recovery_steps are gone with the ladder — the
# general agent owns planning and recovery itself.


'''
s = s[:i0] + NEW_BLOCK + s[i1:]
save(p, s, crlf)
print("patched: tests/test_adaptive_repairs.py")

# ---- walkthrough.py — route the 6, drop the dead label ----------------------
p = "walkthrough.py"
s, crlf = load(p)
assert ' + ["orchestrator"]' in s, "walkthrough routing anchor missing"
s = s.replace(' + ["orchestrator"]', "")
save(p, s, crlf)
print("patched: walkthrough.py")

print("S4 test migration applied OK")
