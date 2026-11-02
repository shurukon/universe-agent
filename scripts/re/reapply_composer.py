"""Re-apply the lost Composer.tsx edits after the ENOSPC truncation.

The restored file comes from commit 8afd461 (pre-Phase-4); this script
brings it back to the committed intent: ZCode four modes, the bridge-owned
slash registry, and the resolve-on-send path.
"""
import io

P = r"universe-agent-ui\client\src\components\desktop\Composer.tsx"
t = io.open(P, encoding="utf-8").read()

# 1) imports: PermissionMode + api
t = t.replace('import { useChat } from "@/stores/chat";',
              'import { useChat, type PermissionMode } from "@/stores/chat";')
t = t.replace('import ModelPicker from "@/components/desktop/ModelPicker";',
              'import ModelPicker from "@/components/desktop/ModelPicker";\n'
              'import { api } from "@/lib/api";')

# 2) MODES -> the ZCode four
old_modes = '''const MODES: Array<{
  id: "full" | "plan" | "ask";
  label: string;
  note: string;
}> = [
  { id: "full", label: "Full access", note: "current Universe behavior" },
  { id: "plan", label: "Plan", note: "read-only until you approve the plan" },
  { id: "ask", label: "Ask", note: "every tool call needs your approval" },
];'''
assert old_modes in t, "MODES block not found"
new_modes = '''const MODES: Array<{
  id: PermissionMode;
  label: string;
  note: string;
}> = [
  { id: "plan", label: "Plan", note: "read-only until you approve the plan" },
  { id: "build", label: "Build", note: "writes and commands ask once per goal" },
  { id: "edit", label: "Edit", note: "writes run without the gate; commands ask" },
  { id: "yolo", label: "Yolo", note: "everything auto — no prompts" },
];'''
t = t.replace(old_modes, new_modes)

# 3) the slash block -> the live registry
anchor_start = "  // ---- slash commands (Phase C) ---"
anchor_end = "  const ctx = useContextUsage(runningGoal?.turn_id ?? null);"
i0 = t.index(anchor_start)
i1 = t.index(anchor_end)
new_block = '''  // ---- slash commands (ZCode's live registry) -----------------------------
  // The bridge owns the menu: ZCode's 20 built-ins + discovered .md command
  // files. Built-ins with an action run it; the honest placeholders say so
  // instead of pretending; custom commands insert "/name " so args follow.
  const [slashCommands, setSlashCommands] = useState<
    Array<{
      name: string; summary: string; usage: string; action: string;
      supported: boolean; kind?: string;
    }>
  >([]);
  useEffect(() => {
    let alive = true;
    api
      .listCommands()
      .then(r => {
        if (alive) setSlashCommands(r.commands);
      })
      .catch(() => {
        /* bridge down: the popup stays closed, not a fake list */
      });
    return () => {
      alive = false;
    };
  }, []);

  const runAction = (action: string) => {
    if (action === "new") {
      useChat.getState().newConversation();
      navigate("/chat");
    } else if (action === "settings") navigate("/settings");
    else if (action === "settings:skills")
      navigate("/settings?section=skills");
    else if (action === "settings:hooks")
      navigate("/settings?section=hooks");
    else if (action === "settings:mcp") navigate("/settings?section=mcp");
    else if (action === "automations") navigate("/automations");
    else if (action === "terminal") {
      const rail = useRail.getState();
      if (rail.open && rail.tab === "terminal") rail.closeRail();
      else rail.openRail("terminal");
    } else if (action === "mode") {
      // /mode with no arg: guide to the mode chip (Plan/Build/Edit/Yolo)
      taRef.current?.focus();
      toast("Switch mode from the chip row — Plan/Build/Edit/Yolo");
    } else if (action === "model" || action === "effort" || action === "goal") {
      taRef.current?.focus();
    }
  };

  const SLASH_TOKEN_RE = /^\\/([a-zA-Z0-9:_-]*)$/;
  const slashToken = SLASH_TOKEN_RE.exec(text);
  const [slashIdx, setSlashIdx] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  useEffect(() => {
    setSlashDismissed(false);
    setSlashIdx(0);
  }, [text]);
  const slashQuery = slashToken ? slashToken[1] : null;
  const slashMatches = useMemo(() => {
    if (slashQuery === null) return [];
    return slashQuery
      ? slashCommands.filter(c => c.name.startsWith(slashQuery))
      : slashCommands;
  }, [slashQuery, slashCommands]);
  const slashVisible =
    !!slashToken && !slashDismissed && slashMatches.length > 0;

  const runSlash = (cmd: (typeof slashCommands)[number]) => {
    if (cmd.action && cmd.kind !== "custom") {
      setText("");
      runAction(cmd.action);
      return;
    }
    if (cmd.kind === "custom") {
      // keep the slash form in the input — args can follow, the prompt
      // expands on send (the bridge resolves it) so the user always sees
      // what will be sent
      setText(`/${cmd.name} `);
      taRef.current?.focus();
      return;
    }
    // ZCode has this command, Universe has no counterpart yet — say so plainly
    setText("");
    toast(`${cmd.usage.split(" ")[0]} is a ZCode command — Universe lands it in a later phase`, {
      description: cmd.summary,
    });
  };

'''
t = t[:i0] + new_block + t[i1:]

# 4) submit(): bridge-resolved slash handling
old_submit = '''    // Phase C slash resolution: a custom command expands to its prompt
    // (trailing text rides along as extra args); a bare built-in runs its
    // shell action; anything else is sent literally — never a silent guess
    const slash = /^\\/([a-z0-9-]+)(?:\\s+([\\s\\S]*))?$/.exec(body);
    if (slash) {
      const name = slash[1];
      const rest = slash[2] ?? "";
      const custom = customSlash.find(c => c.name === name);
      if (custom?.prompt) {
        body = rest ? `${custom.prompt}\\n\\n${rest}` : custom.prompt;
      } else if (!rest) {
        const builtin = builtinSlash.find(c => c.name === name);
        if (builtin?.run) {
          setText("");
          builtin.run();
          return;
        }
      }
    }'''
assert old_submit in t, "submit block not found"
new_submit = '''    // ZCode slash resolution: the bridge owns the registry. A .md command
    // expands to its prompt ($ARGUMENTS rides along); a bare built-in runs
    // its action; anything else is sent literally — never a silent guess
    if (body.startsWith("/")) {
      try {
        const r = await api.resolveCommand(body);
        const res = r.command;
        if (res) {
          if (res.kind === "custom" && res.prompt) {
            body = res.args ? `${res.prompt}\\n\\n${res.args}` : res.prompt;
          } else if (res.kind === "builtin") {
            const cmd = res.command as { action?: string } | undefined;
            if (cmd?.action) {
              setText("");
              runAction(cmd.action);
              return;
            }
            // unsupported ZCode command: honest placeholder, nothing sent
            setText("");
            toast(`${res.name} is a ZCode command — Universe lands it in a later phase`, {
              description: (res.command as { summary?: string })?.summary,
            });
            return;
          }
        }
      } catch {
        /* bridge down: send literally — never a silent guess */
      }
    }'''
t = t.replace(old_submit, new_submit)

# 5) docstring header refresh
t = t.replace(''' * built-in shell actions (new, settings, automations, terminal, …) run
 * immediately; the user's custom commands (Settings → Commands) expand
 * to their prompt on send. An unknown "/word" is sent literally — the
 * model sees what the user typed, never a silent guess.''',
''' * over the bridge's live registry — ZCode's 20 built-ins plus discovered
 * .md command files (zcode-guide contract). A built-in with an action runs
 * it; a custom command expands to its prompt on send; an unknown "/word"
 * is sent literally — the model sees what the user typed, never a silent
 * guess.''')

io.open(P, "w", encoding="utf-8", newline="").write(t)
print("Composer re-applied:", len(t.splitlines()), "lines")
