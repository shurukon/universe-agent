"""cli/wizards/__init__.py — all [wizard] command flows (§8).

Each wizard takes (args: list[str], io) where io has print/input/confirm/pause
methods, so wizards can be driven interactively OR programmatically (for tests
and the eval harness). Returns a result string.

Every wizard is a real guided multi-step flow, not a single prompt that skips
the guided steps (§STEP 1).
"""
from __future__ import annotations

import time


# ---- Tasks ----------------------------------------------------------------
async def goal_wizard(args: list[str], io) -> str:
    """ /goal <description>  |  /goal  (interactive)"""
    from core.agent.orchestrator import get_orchestrator
    text = " ".join(args).strip()
    if not text:
        io.print("[goal wizard] Describe your goal in one line:")
        text = io.input("> ").strip()
        if not text:
            return "Goal cancelled."
    io.print(f"\nStarting goal: {text}\n")
    orch = get_orchestrator()

    async def on_step(s):
        phase = s.get("phase")
        if phase == "classified":
            cls = s.get("meta", {}).get("classification", {})
            io.print(f"  Classified: {cls}")
        elif phase == "planned":
            plan = s.get("plan", [])
            io.print(f"  Plan: {' -> '.join(st['agent'] for st in plan)}")
        elif phase == "agent_start":
            io.print(f"  [{s['step']+1}] {s['agent']}: {s.get('instruction','')}")

    summary = await orch.run_turn(text, on_step=on_step)
    _print_summary(io, summary)
    return f"Task {summary['turn_id']} complete (success={summary['success']})."


async def status_wizard(args: list[str], io) -> str:
    from core.memory import get_memory
    mem = get_memory()
    planets = mem.l1.list_planets(status="active")
    if not planets:
        return "No active tasks."
    io.print("Active tasks:")
    for p in planets:
        io.print(f"  {p.id}  {p.goal_text[:60]}")
    return f"{len(planets)} active task(s)."


async def stop_wizard(args: list[str], io) -> str:
    from core.memory import get_memory
    mem = get_memory()
    planets = mem.l1.list_planets(status="active")
    if not planets:
        return "Nothing to stop."
    io.print("Select a task to stop:")
    for i, p in enumerate(planets):
        io.print(f"  [{i+1}] {p.goal_text[:60]}")
    choice = io.input("number> ").strip()
    try:
        p = planets[int(choice) - 1]
        p.status = "cancelled"
        mem.l1.update_planet(p)
        return f"Stopped {p.id}."
    except (ValueError, IndexError):
        return "Cancelled."


async def history_wizard(args: list[str], io) -> str:
    from storage.local import get_storage
    st = get_storage()
    rows = st.query_all("SELECT * FROM goals ORDER BY created_at DESC LIMIT 20;")
    if not rows:
        return "No history yet."
    for r in rows:
        io.print(f"  {r['id'][:12]}  {r['status']:10s}  {r['text'][:50]}")
    return f"{len(rows)} goal(s) in history."


async def resume_wizard(args: list[str], io) -> str:
    from failure.checkpoint import resume_from
    from storage.local import get_storage
    st = get_storage()
    rows = st.query_all("SELECT id, text FROM goals WHERE status IN ('running','failed','cancelled') ORDER BY created_at DESC LIMIT 10;")
    if not rows:
        return "No resumable goals."
    io.print("Resumable goals:")
    for r in rows:
        io.print(f"  {r['id'][:12]}  {r['text'][:50]}")
    gid = io.input("goal id (or enter to cancel)> ").strip()
    if not gid:
        return "Cancelled."
    # match prefix
    match = [r for r in rows if r["id"].startswith(gid)]
    if not match:
        return "No matching goal."
    return await resume_from(match[0]["id"], io)


# ---- Memory ---------------------------------------------------------------
async def memory_overview(args: list[str], io) -> str:
    from core.memory import get_memory
    from skills.loader import skill_counts_by_source
    mem = get_memory()
    planets = mem.l1.list_planets()
    asteroids = mem.l2.list_recent(limit=10000)
    stars = mem.l3.list_stars()
    skills = mem.l4.list()
    mirror = mem.l5.all()
    io.print("Universe Memory Overview")
    io.print(f"  L1 Planets:   {len(planets)} ({sum(1 for p in planets if p.status=='active')} active)")
    io.print(f"  L2 Asteroids: {len(asteroids)}")
    io.print(f"  L3 Stars:     {len(stars)}")
    io.print(f"  L4 Skills:    {len(skills)}  by source: {skill_counts_by_source()}")
    io.print(f"  L5 Dark Matter keys: {len(mirror)}")
    ds = mirror.get("mirror.domain_stats", {})
    if ds:
        io.print("  Domain error rates:")
        for d, s in ds.items():
            io.print(f"    {d}: {s['error_rate']*100:.1f}% ({s['samples']} samples)")
    return "Overview complete."


async def open_vault(args: list[str], io) -> str:
    from config import get_config
    import os
    import subprocess
    cfg = get_config()
    editor = os.environ.get("EDITOR", "vi")
    io.print(f"Opening vault at {cfg.vault_dir} with {editor}...")
    # argv list, never os.system: the old f-string interpolated EDITOR and an
    # unquoted Windows path into cmd.exe — shell metacharacters in either
    # executed arbitrarily, and spaces in the path broke the launch
    try:
        proc = subprocess.run([editor, str(cfg.vault_dir)])
        if proc.returncode != 0:
            return f"Editor exited with code {proc.returncode}."
    except FileNotFoundError:
        return f"Editor {editor!r} not found. Set EDITOR to an executable on PATH."
    except Exception as e:
        return f"Could not open editor: {e}"
    return "Vault opened."


async def forget_wizard(args: list[str], io) -> str:
    from core.memory import get_memory
    mem = get_memory()
    stars = mem.l3.list_stars()
    if not stars:
        return "No stars to forget."
    io.print("Stars:")
    for i, s in enumerate(stars):
        io.print(f"  [{i+1}] {s.topic[:60]} ({s.domain})")
    choice = io.input("number to forget> ").strip()
    try:
        s = stars[int(choice) - 1]
        if io.confirm(f"Delete star '{s.topic}'?"):
            mem.l3.delete_star(s.id)
            return f"Forgot {s.id}."
        return "Cancelled."
    except (ValueError, IndexError):
        return "Cancelled."


async def forget_all_wizard(args: list[str], io) -> str:
    from config import get_config
    import shutil
    if not io.confirm("This wipes ALL of ~/.universe. Are you sure?"):
        return "Cancelled."
    if not io.confirm("FINAL CONFIRMATION. This cannot be undone. Continue?"):
        return "Cancelled."
    cfg = get_config()
    if cfg.home.exists():
        shutil.rmtree(cfg.home)
    cfg.ensure_dirs()
    from schema.migrations import ensure_latest
    ensure_latest()
    return "Wiped. Run /setup to reconfigure."


# ---- Agents ---------------------------------------------------------------
async def agents_wizard(args: list[str], io) -> str:
    from core.core_agents import get_all_agents
    agents = get_all_agents()
    io.print("Core Agents:")
    for name, a in agents.items():
        io.print(f"  {name:12s} {a.specialty}")
    from storage.local import get_storage
    st = get_storage()
    moons = st.query_all("SELECT * FROM moons GROUP BY agent_name;")
    io.print(f"\nMoons created: {len(moons)}")
    return f"{len(agents)} core agents."


async def agent_wizard(args: list[str], io) -> str:
    """ /agent create  |  /agent <name>"""
    if args and args[0] == "create":
        return await _agent_create(io)
    if args:
        return await _agent_details(args[0], io)
    return await agents_wizard([], io)


async def _agent_create(io) -> str:
    io.print("[agent create] Create a custom Moon agent.")
    io.print("What should this Moon specialize in?")
    specialty = io.input("> ").strip()
    if not specialty:
        return "Cancelled."
    name = io.input(f"Name (default: moon-{int(time.time())%10000})> ").strip() or f"moon-{int(time.time())%10000}"
    io.print("Which tools should it have? (comma-separated, e.g. file.read,web_search)")
    tools = [t.strip() for t in io.input("> ").split(",") if t.strip()]
    io.print("Which endpoints will it call? (for quarantine scoping; comma-separated URLs, or blank)")
    endpoints = [e.strip() for e in io.input("> ").split(",") if e.strip()]
    # dry-run preview
    io.print("\n--- Proposed Moon ---")
    io.print(f"Name: {name}")
    io.print(f"Specialty: {specialty}")
    io.print(f"Tools: {tools}")
    io.print(f"Declared endpoints: {endpoints}")
    io.print("Probation: shell.exec DENIED for 3 goals; network.req scoped to declared endpoints only.")
    io.print("Estimated cost per goal: depends on the model assigned to this Moon's agent.")
    if not io.confirm("Approve and save this Moon?"):
        return "Moon not saved."
    # save to L4 as a Moon skill
    from core.memory import get_memory
    from core.memory.layers.l4_procedural import Skill
    from core.agent.base_agent import new_id
    import time as _t
    mem = get_memory()
    skill = Skill(
        id=new_id("moon-"), name=name, source="user-moon", version="1.0.0",
        description=specialty, body=f"Specialty: {specialty}\nTools: {tools}\nEndpoints: {endpoints}",
        tags=["moon"], triggers=[], license="user", confidence=0.70,
        status="active", category="general", target_agent=name,
        last_used=0.0, last_verified=_t.time(), use_count=0, needs_review=False,
        created_at=_t.time(),
    )
    mem.l4.upsert(skill)
    return f"Moon '{name}' saved to L4. Probation: 3 goals (shell.exec denied, network scoped to {endpoints or 'none'})."


async def _agent_details(name: str, io) -> str:
    from core.core_agents import get_agent, ALL_AGENTS
    from providers.manager import get_provider_manager
    if name in ALL_AGENTS:
        a = get_agent(name)
        pm = get_provider_manager()
        model = pm.get_agent_model(name)
        io.print(f"Agent: {a.name}")
        io.print(f"  Specialty: {a.specialty}")
        io.print(f"  Default tier: {a.default_model_tier}")
        io.print(f"  Model: {model}")
        io.print(f"  Tools: {a.tool_whitelist_names}")
        return f"Details for {name}."
    return f"Unknown agent {name!r}."


# ---- Rules ----------------------------------------------------------------
async def rules_wizard(args: list[str], io) -> str:
    from core.memory import get_memory
    mem = get_memory()
    rules = mem.active_rules()
    if not rules:
        return "No rules defined."
    for r in rules:
        io.print(f"  [{r['kind']}] {r['rule']}  (scope: {r['scope']})  id={r['id'][:10]}")
    return f"{len(rules)} rule(s)."


async def blackhole_wizard(args: list[str], io) -> str:
    from core.memory import get_memory
    mem = get_memory()
    rule = " ".join(args).strip() or io.input("Black hole rule (blocks any matching action)> ").strip()
    if not rule:
        return "Cancelled."
    mem.add_rule(kind="blackhole", rule=rule)
    return f"Black hole added: {rule}"


async def whitehole_wizard(args: list[str], io) -> str:
    from core.memory import get_memory
    mem = get_memory()
    rule = " ".join(args).strip() or io.input("White hole rule (environmental)> ").strip()
    if not rule:
        return "Cancelled."
    mem.add_rule(kind="whitehole", rule=rule)
    return f"White hole added: {rule}"


async def wormhole_wizard(args: list[str], io) -> str:
    from core.memory import get_memory
    mem = get_memory()
    rule = " ".join(args).strip() or io.input("Worm hole rule (system-specific, agent can update)> ").strip()
    if not rule:
        return "Cancelled."
    mem.add_rule(kind="wormhole", rule=rule, created_by="user")
    return f"Worm hole added: {rule}"


# ---- Connectors -----------------------------------------------------------
async def connectors_wizard(args: list[str], io) -> str:
    from connectors.composio import get_composio
    from connectors.mcp_client import get_mcp_client
    comp = get_composio()
    mcp = get_mcp_client()
    io.print("Composio connected:")
    for n in comp.list_connected():
        io.print(f"  {n}")
    io.print("MCP servers:")
    for s in mcp.list_servers():
        io.print(f"  {s.name}  tools={sorted(s.tools) if s.tools else '[not started]'}  "
                f"read_only={s.read_only}  caps_allowlist={s.declared_capabilities or '[all discovered]'}")
    return "Connectors listed."


async def connect_wizard(args: list[str], io) -> str:
    io.print("[connect] Describe what you want to connect (e.g. 'my Gmail', 'a Slack workspace')")
    desc = " ".join(args).strip() or io.input("> ").strip()
    if not desc:
        return "Cancelled."
    from connectors.composio import AVAILABLE_TOOLS
    io.print("Available Composio tools: " + ", ".join(AVAILABLE_TOOLS[:12]) + "...")
    tool = io.input("Which tool? (or 'mcp' for an MCP server)> ").strip()
    if tool.lower() == "mcp":
        return await mcp_wizard([], io)
    from connectors.composio import get_composio
    token = io.input(f"Paste the {tool} token (or blank to skip)> ").strip()
    result = get_composio().connect(tool, {"token": token} if token else {})
    return f"Connect result: {result}"


async def mcp_wizard(args: list[str], io) -> str:
    from connectors.mcp_client import KNOWN_SERVERS
    io.print("[mcp add] Known servers: " + ", ".join(
        f"{k} ({v['description']})" for k, v in KNOWN_SERVERS.items()))
    io.print("Pick one of the names above, or type 'custom' for any other MCP server.")
    choice = io.input("Server> ").strip()
    name = io.input("What should Universe call this connection?> ").strip()
    if not name:
        return "Cancelled."
    from connectors.builtin.mcp_tools import mcp_add_server
    if choice in KNOWN_SERVERS:
        kwargs = {"name": name, "server_key": choice}
        needs = KNOWN_SERVERS[choice].get("requires", [])
        if "path" in needs:
            kwargs["path"] = io.input("Path> ").strip()
        if "connection_string" in needs:
            kwargs["connection_string"] = io.input("Connection string> ").strip()
    else:
        command = io.input("Launch command (e.g. 'npx -y @scope/some-server')> ").strip()
        hosts = io.input("Declared upstream hosts this server may reach "
                         "(comma-sep, blank = localhost only)> ").strip()
        kwargs = {"name": name, "command": command, "declared_hosts": hosts}
    result = await mcp_add_server(**kwargs)
    if not result["ok"]:
        return f"Failed to connect '{name}': {result['error']}"
    return (f"MCP server '{name}' connected — {len(result['tools_discovered'])} tools "
            f"discovered: {', '.join(result['tools_discovered'][:8])}"
            f"{'...' if len(result['tools_discovered']) > 8 else ''}. "
            f"read_only={result['read_only']} (write-like tools blocked while True); "
            f"egress limited to {result['egress_limited_to']} "
            f"(enforced via a local proxy — see connectors/mcp_client.py).")


async def skills_wizard(args: list[str], io) -> str:
    from skills.loader import skill_counts_by_source, verify_all_signatures
    if args and args[0] == "--verify":
        return f"Signature verification: {verify_all_signatures()}"
    if args and args[0] == "--reload":
        from skills.loader import load_all_skills
        return f"Reloaded: {load_all_skills()}"
    io.print("Skills by source:")
    for src, c in skill_counts_by_source().items():
        io.print(f"  {src}: {c}")
    return "Use /skills --verify or /skills --reload."


async def quarantine_wizard(args: list[str], io) -> str:
    from skill.quarantine import list_quarantine, approve, reject
    items = list_quarantine()
    pending = [q for q in items if not q["approved"]]
    if not pending:
        return "No skills in quarantine."
    for q in pending:
        io.print(f"  {q['id'][:10]}  {q['name']}  (from {q.get('source_url','?')})")
    choice = io.input("id to approve/reject (or 'approve all'/'cancel')> ").strip()
    if choice == "approve all":
        for q in pending:
            approve(q["id"])
        return f"Approved {len(pending)} skill(s)."
    for q in pending:
        if q["id"].startswith(choice):
            action = io.input("approve or reject? (a/r)> ").strip().lower()
            if action == "a":
                approve(q["id"])
                return f"Approved {q['name']}."
            else:
                reject(q["id"])
                return f"Rejected {q['name']}."
    return "Cancelled."


async def skill_wizard(args: list[str], io) -> str:
    if args and len(args) >= 2 and args[1] == "--pin":
        name, _, version = args[0], args[1], args[2] if len(args) > 2 else ""
        from core.memory import get_memory
        # find the skill by name and pin version
        s = get_memory().l4.find_by_name(name)
        if s and version:
            from storage.local import get_storage
            with get_storage().transaction() as conn:
                conn.execute("UPDATE skills SET version=? WHERE name=?;", (version, name))
            return f"Pinned {name} to {version}."
        return f"Skill {name} not found."
    io.print("[skill create] Author a custom skill.")
    name = io.input("Name> ").strip()
    if not name:
        return "Cancelled."
    desc = io.input("Description> ").strip()
    body = io.input("Body (one line; multi-line editor for real use)> ").strip()
    from core.memory import get_memory
    from core.memory.layers.l4_procedural import Skill
    from core.agent.base_agent import new_id
    from skill.signing import sign_skill
    s = Skill(id=new_id("skill-"), name=name, source="user", version="1.0.0",
              description=desc, body=body, tags=[], triggers=[],
              license="user", confidence=0.70, status="active",
              signature=sign_skill(name, "user", "1.0.0", body),
              category="general", target_agent="", last_verified=time.time(),
              created_at=time.time())
    get_memory().l4.upsert(s)
    return f"Skill '{name}' saved to L4 (confidence 0.70)."


# ---- Channels -------------------------------------------------------------
async def channels_wizard(args: list[str], io) -> str:
    from channels.telegram import get_telegram_channel
    tg = get_telegram_channel()
    io.print("Channels:")
    io.print("  CLI: always available")
    io.print(f"  Telegram: {'configured' if tg.token else 'not configured'}")
    return "Channels listed."


async def channel_wizard(args: list[str], io) -> str:
    if not args or args[0] == "add":
        io.print("[channel add] Connect Telegram.")
        io.print("1. Open https://t.me/BotFather")
        io.print("2. /newbot, follow prompts, copy the bot token")
        token = io.input("Paste bot token> ").strip()
        if not token:
            return "Cancelled."
        io.print("3. Access control: (1) only me (2) specific user IDs (3) open")
        ac = io.input("choice> ").strip()
        allowed = None
        if ac not in ("1", "2", "3"):
            # fail-closed (audit 2026-10): a blank/unknown answer used to fall
            # through with allowed=None — i.e. silently OPEN access.
            return ("Cancelled: no access-control choice made. Re-run "
                    "/channel add and pick 'only me' to keep the bot closed.")
        if ac == "1":
            uid = io.input("Your Telegram user ID> ").strip()
            # fail-closed (audit 2026-09): a blank ID used to produce
            # allowed=[] -> configure(..., None) -> OPEN TO EVERYONE. A
            # blank answer now cancels the access-control step entirely.
            if not uid:
                return ("Cancelled: an empty ID would configure OPEN access. "
                        "Find your ID via @userinfobot and re-run /channel add.")
            allowed = [int(uid)]
        elif ac == "2":
            ids = io.input("Comma-separated user IDs> ").strip()
            allowed = [int(x) for x in ids.split(",") if x.strip()]
            if not allowed:
                return ("Cancelled: no IDs given would configure OPEN access.")
        elif ac == "3":
            # open access is an explicit, warned choice (audit 2026-10):
            # allowed=None means ANY Telegram account that finds the bot can
            # drive the agent — run tools, spend provider quota, touch files.
            io.print("")
            io.print("WARNING: open access lets ANYONE who finds this bot send")
            io.print("goals that drive Universe — run tools, spend provider quota,")
            io.print("and touch the files your account can reach. 'Only me' with")
            io.print("your user ID (via @userinfobot) is the recommended mode.")
            confirm = io.input("Type 'open' to confirm open access "
                               "(anything else cancels)> ").strip().lower()
            if confirm not in ("open", "y", "yes"):
                return ("Cancelled: open access was not confirmed. Re-run "
                        "/channel add and choose 'only me'.")
        from channels.telegram import get_telegram_channel
        result = get_telegram_channel().configure(token, allowed)
        return f"Telegram: {result}"
    return "Usage: /channel add"


# ---- Providers & Models ---------------------------------------------------
async def providers_wizard(args: list[str], io) -> str:
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    for p in pm.list_providers():
        io.print(f"  {p.name}  ({p.base_url})")
        for k in p.keys:
            io.print(f"    key #{k.id} {k.alias} status={k.status} tier={k.tier}")
    return f"{len(pm.list_providers())} provider(s)."


async def provider_wizard(args: list[str], io) -> str:
    if args and args[0] == "add":
        io.print("[provider add] Add a provider.")
        from providers.manager import KNOWN_PROVIDERS, get_provider_manager
        io.print("Known providers: " + ", ".join(list(KNOWN_PROVIDERS.keys())[:8]) + "...")
        name = io.input("Provider name (or 'custom')> ").strip()
        base_url = KNOWN_PROVIDERS.get(name, "")
        if not base_url:
            base_url = io.input("Base URL> ").strip()
        if base_url:
            # same private-IP refusal the desktop bridge's provider probe
            # enforces — the CLI used to accept any URL, so a pasted (or
            # model-suggested) internal host would receive system prompts
            # and tool results from LAN space (audit 2026-09). Explicit
            # localhost stays allowed: local inference servers (Ollama,
            # LM Studio, vLLM) are a first-class use case.
            from urllib.parse import urlparse
            _parsed = urlparse(base_url)
            _host = _parsed.hostname or ""
            if _parsed.scheme not in ("http", "https"):
                return f"Refused: provider URL scheme {_parsed.scheme!r} not allowed."
            if _host not in ("localhost", "127.0.0.1", "::1"):
                from connectors.builtin.misc import _ssrf_guard
                check = _ssrf_guard(base_url)
                if not check.get("ok"):
                    return f"Refused: {check.get('error')}"
        pm = get_provider_manager()
        pm.add_provider(name, base_url)
        alias = io.input("Key alias (e.g. 'personal')> ").strip() or "default"
        key = io.input("API key (paste)> ").strip()
        tier = io.input("Tier? (paid/free) [paid]> ").strip() or "paid"
        from security.secrets_fallback import encrypt_secret
        enc = encrypt_secret(key) if key else ""
        pm.add_key(name, alias, enc, tier=tier, status="high")
        return f"Provider {name} added with key #{1} ({alias})."
    if args and args[0] == "keys":
        name = args[1] if len(args) > 1 else ""
        return await _provider_keys(name, io)
    return "Usage: /provider add | /provider keys <name>"


async def _provider_keys(name: str, io) -> str:
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    p = pm.get_provider(name)
    if not p:
        return f"Unknown provider {name!r}."
    io.print(f"Keys for {name}:")
    for k in p.keys:
        io.print(f"  #{k.id} {k.alias} status={k.status} tier={k.tier}")
    return f"{len(p.keys)} key(s)."


async def model_wizard(args: list[str], io) -> str:
    from providers.manager import get_provider_manager
    from core.core_agents import ALL_AGENTS
    pm = get_provider_manager()
    agent = args[0] if args else io.input(f"Agent ({'/'.join(ALL_AGENTS)} or orchestrator)> ").strip()
    if agent not in ALL_AGENTS and agent != "orchestrator":
        return f"Unknown agent {agent!r}."
    io.print("Providers: " + ", ".join(p.name for p in pm.list_providers()))
    prov = io.input("Provider> ").strip()
    model = io.input("Model name> ").strip()
    pm.set_agent_model(agent, prov, model)
    return f"{agent} -> {prov}/{model}"


async def fallback_wizard(args: list[str], io) -> str:
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    agent = args[0] if args else io.input("Agent> ").strip()
    io.print("Enter fallback chain as 'provider/model' pairs, blank to finish:")
    chain = []
    while True:
        line = io.input(f"  [{len(chain)+1}]> ").strip()
        if not line:
            break
        if "/" in line:
            p, m = line.split("/", 1)
            chain.append((p.strip(), m.strip()))
    pm.set_fallback_chain(agent, chain)
    return f"Fallback chain for {agent}: {chain}"


# ---- Observability --------------------------------------------------------
async def debug_cmd(args: list[str], io) -> str:
    from core.memory import get_memory
    from providers.manager import get_provider_manager
    mem = get_memory()
    pm = get_provider_manager()
    io.print("=== Live Universe State ===")
    io.print(f"Active planets: {len(mem.l1.list_planets(status='active'))}")
    io.print(f"Stars: {len(mem.l3.list_stars())}  Skills: {len(mem.l4.list())}")
    io.print(f"Providers: {len(pm.list_providers())}")
    if "--llm" in args:
        from providers.client import get_llm_client
        for r in get_llm_client().call_log()[-10:]:
            io.print(f"  {r['agent']:10s} {r['provider']:12s} in={r['input_tokens']} out={r['output_tokens']} {r['latency_ms']}ms")
    return "Debug snapshot shown."


async def log_cmd(args: list[str], io) -> str:
    from config import get_config
    cfg = get_config()
    log = cfg.home / "universe.log"
    if not log.exists():
        return "No log file yet."
    lines = log.read_text(encoding="utf-8").splitlines()[-50:]
    for line in lines:
        io.print(line)
    return f"Last {len(lines)} log lines."


async def trace_cmd(args: list[str], io) -> str:
    from observability.timeline import replay_goal
    if not args:
        return "Usage: /trace <turn_id>"
    return replay_goal(args[0], io)


async def metrics_cmd(args: list[str], io) -> str:
    from observability.metrics import render_metrics
    return render_metrics(io)


# ---- Evaluation -----------------------------------------------------------
async def eval_cmd(args: list[str], io) -> str:
    from eval.runner import run_eval
    compare = "--compare" in args
    return await run_eval(io, compare=compare)


# ---- Data -----------------------------------------------------------------
async def export_cmd(args: list[str], io) -> str:
    from data.export_import import export_all
    path = export_all()
    return f"Exported to {path}"


async def import_cmd(args: list[str], io) -> str:
    from data.export_import import import_all
    if not args:
        return "Usage: /import <archive>"
    return import_all(args[0])


# ---- Profile --------------------------------------------------------------
async def profile_wizard(args: list[str], io) -> str:
    from core.memory.orbits import get_orbits
    o = get_orbits()
    g = o.get_galactic()
    io.print(f"Name: {g.name}")
    io.print(f"Profession: {g.profession}")
    io.print(f"Language: {g.preferred_language}")
    io.print(f"Communication style: {g.communication_style}")
    io.print(f"Control preference: {g.control_preference}")
    if io.confirm("Edit?"):
        g.name = io.input(f"Name [{g.name}]> ") or g.name
        g.profession = io.input(f"Profession [{g.profession}]> ") or g.profession
        g.preferred_language = io.input(f"Language [{g.preferred_language}]> ") or g.preferred_language
        o.save_galactic(g)
        return "Profile updated."
    return "Profile shown."


async def orbit_wizard(args: list[str], io) -> str:
    from core.memory.orbits import get_orbits
    o = get_orbits()
    locals_ = o.list_local()
    if not locals_:
        return "No local orbits yet."
    for loc in locals_:
        io.print(f"  {loc.solar_system_id}: skill={loc.skill_level} style={loc.preferred_style}")
    return f"{len(locals_)} local orbit(s)."


# ---- System ---------------------------------------------------------------
async def setup_wizard(args: list[str], io) -> str:
    from cli.setup import run_setup
    return await run_setup(io)


async def version_cmd(args: list[str], io) -> str:
    from config import UNIVERSE_VERSION, SCHEMA_VERSION
    from storage.local import get_storage
    import sys
    st = get_storage()
    io.print(f"Universe Agent v{UNIVERSE_VERSION}")
    io.print(f"Schema version: {st.user_version()} (target {SCHEMA_VERSION})")
    io.print(f"Python: {sys.version.split()[0]}")
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    io.print(f"Providers: {', '.join(p.name for p in pm.list_providers())}")
    return f"v{UNIVERSE_VERSION}"


async def migrate_cmd(args: list[str], io) -> str:
    from schema.migrations import run, pending
    if "--dry-run" in args:
        pend = pending()
        return f"{len(pend)} pending migration(s): " + ", ".join(f"v{v:04d}" for v, _, _ in pend)
    lines = run()
    for ln in lines:
        io.print(ln)
    return "Migrations complete."


async def help_cmd(args: list[str], io) -> str:
    from cli.slash_commands import commands_by_category
    if args:
        from cli.slash_commands import get_command
        c = get_command(args[0])
        if c:
            io.print(f"/{c.name} — {c.description}" + (" [wizard]" if c.wizard else ""))
            return c.description
        return f"Unknown command {args[0]!r}."
    for cat, cmds in commands_by_category().items():
        io.print(f"\n── {cat} {'─' * max(1, 60 - len(cat))}")
        for c in cmds:
            tag = " [wizard]" if c.wizard else ""
            io.print(f"  /{c.name:18s} {c.description}{tag}")
    return "Help shown."


# ---- sessions (audit M5: the README-promised /new /fork /compact) ---------
def _session_target(args: list[str]) -> str:
    """Session id from args, else the current REPL session."""
    if args:
        return args[0].strip()
    from cli.session_state import current_session_id
    return current_session_id(create=False) or ""


async def new_session_cmd(args: list[str], io) -> str:
    from core.session.store import get_session_store
    from cli.session_state import set_current_session
    meta = get_session_store().create()
    set_current_session(meta["id"])
    io.print(f"New session: {meta['id']} — context is fresh.")
    return meta["id"]


async def sessions_cmd(args: list[str], io) -> str:
    from core.session.store import get_session_store
    from cli.session_state import set_current_session, current_session_id
    store = get_session_store()
    if args and args[0] == "use" and len(args) > 1:
        sid = args[1].strip()
        if store.get(sid) is None:
            return f"Unknown session: {sid}"
        set_current_session(sid)
        io.print(f"Switched to session {sid}")
        return sid
    if args and args[0] == "rename" and len(args) > 2:
        sid, title = args[1].strip(), " ".join(args[2:])
        store.set_title(sid, title)
        io.print(f"Renamed {sid} → {title!r}")
        return title
    rows = store.list_sessions(limit=15)
    cur = current_session_id(create=False)
    if not rows:
        io.print("No sessions yet — just start typing, or /new for a fresh one.")
        return "no sessions"
    import datetime as _dt
    for r in rows:
        mark = "*" if r.get("id") == cur else " "
        try:
            ts = _dt.datetime.fromtimestamp(r.get("updated_at") or 0).strftime("%m-%d %H:%M")
        except Exception:
            ts = "?"
        io.print(f"{mark} {r.get('id', '')}  {ts}  {str(r.get('title', ''))[:48]}")
    io.print("[dim](*) current — /sessions use <id> to switch, /sessions rename <id> <title>[/dim]")
    return f"{len(rows)} session(s)"


async def fork_session_cmd(args: list[str], io) -> str:
    from core.session.store import get_session_store
    from cli.session_state import set_current_session
    src = _session_target(args)
    if not src:
        return "No session to fork — pass an id, or start one first (/new)."
    meta = get_session_store().fork(src)
    if meta is None:
        return f"Unknown session: {src}"
    set_current_session(meta["id"])
    io.print(f"Forked {src} → {meta['id']} (parent transcript carried over)")
    return meta["id"]


async def compact_session_cmd(args: list[str], io) -> str:
    from core.session.store import get_session_store
    from cli.session_state import current_session_id
    sid = _session_target(args) or current_session_id(create=False)
    if not sid:
        return "No session to compact — pass an id or start one first."
    store = get_session_store()
    entries = store.transcript(sid)
    if not entries:
        return f"Session {sid} is empty — nothing to compact."
    approx_chars = sum(len(str(e.get("content", ""))) for e in entries)
    approx_tokens = max(1, approx_chars // 4)
    summary_text = ""
    try:
        from core.agent.orchestrator import get_orchestrator
        orch = get_orchestrator()
        flat = "\n".join(
            f"{str(e.get('role', 'user'))}: {str(e.get('content', ''))[:1200]}"
            for e in entries if e.get("content"))
        resp = await orch.llm.complete(
            agent="universe",
            messages=[
                {"role": "system",
                 "content": ("Summarize this session into a compact handoff: "
                             "decisions made, facts learned, artifacts created, "
                             "open threads. Maximum 300 words.")},
                {"role": "user", "content": flat[:30000]}],
            max_tokens=700)
        summary_text = str(resp.text or "").strip()
    except Exception as exc:
        io.print(f"[dim]model summary unavailable ({exc}); recording a mechanical marker[/dim]")
    post_tokens = max(1, len(summary_text) // 4) if summary_text else approx_tokens
    store.compact_separator(sid, approx_tokens, post_tokens, summary_text)
    io.print(f"Compacted {sid}: ~{approx_tokens} → ~{post_tokens} tokens "
             f"(summary {'recorded' if summary_text else 'marker'} in the transcript)")
    return f"compacted {sid}"


# ---- helpers --------------------------------------------------------------
def _print_summary(io, summary: dict) -> None:
    io.print("\n" + "=" * 60)
    io.print(f"Universe Mission Complete — {summary.get('elapsed_ms', 0)/1000:.1f}s elapsed")
    io.print(f"  success: {summary.get('success', False)}")
    io.print(f"  classification: {summary.get('classification', '-')}")
    # gravity keys exist only on the goal-engine path (audit M1: the failure
    # envelope never carried them and direct indexing crashed on KeyError)
    if "gravity_score" in summary:
        io.print(f"  gravity: {summary.get('gravity_score')} ({summary.get('gravity_bucket', '-')})")
        io.print(f"  promoted to L3: {summary.get('promoted_to_l3', False)}")
    io.print("  steps:")
    for s in summary.get("steps", []):
        mark = "+" if s.get("success") else "x"
        io.print(f"    [{mark}] {s.get('agent', '')}: {s.get('what', '')}")
    io.print(f"  tokens: {summary.get('input_tokens', 0)} in / {summary.get('output_tokens', 0)} out")
    io.print(f"  llm_calls: {summary.get('llm_calls', 0)}")
    io.print("=" * 60)
