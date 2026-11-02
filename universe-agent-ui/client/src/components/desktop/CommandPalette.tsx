/**
 * components/desktop/CommandPalette.tsx — the ZCode command palette
 * (Ctrl+K, also "/" from the empty composer). Built on the installed
 * shadcn command primitive (cmdk): one input, four tabs
 * (All / Actions / Tasks / Files), and the ZCode section set — Recent
 * tasks · Suggested · Panels · Configure · Application.
 *
 * Every entry is a real action against the live stores or a real bridge
 * call; nothing here is a dead placeholder except where it says so
 * (Automations/Skills pages are Phase 4 — the entry says that out loud).
 * cmdk gives the input filtering, arrow keys and Enter for free.
 */
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "wouter";
import { toast } from "sonner";
import {
  FileText,
  FolderOpen,
  Globe,
  ListChecks,
  MessageSquarePlus,
  PanelLeft,
  PanelRight,
  Plug,
  RotateCcw,
  Settings,
  SquareTerminal,
  Sparkles,
  Zap,
  type LucideIcon,
} from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { api } from "@/lib/api";
import { useChat, type Task } from "@/stores/chat";
import { useUi, type PaletteTab } from "@/stores/ui";
import { useRail } from "@/lib/flow/railStore";

interface PaletteItem {
  id: string;
  section: "recent" | "suggested" | "panels" | "configure" | "application";
  label: string;
  hint?: string;
  icon: LucideIcon;
  tab: PaletteTab;
  run: () => void;
}

const TABS: Array<{ id: PaletteTab; label: string }> = [
  { id: "all", label: "All" },
  { id: "actions", label: "Actions" },
  { id: "tasks", label: "Tasks" },
  { id: "files", label: "Files" },
];

function relTime(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

const SECTION_LABEL: Record<PaletteItem["section"], string> = {
  recent: "Recent tasks",
  suggested: "Suggested",
  panels: "Panels",
  configure: "Configure",
  application: "Application",
};

export default function CommandPalette() {
  const open = useUi(s => s.paletteOpen);
  const close = useUi(s => s.closePalette);
  const tab = useUi(s => s.paletteTab);
  const setTab = useUi(s => s.setPaletteTab);
  const toggleSidebar = useUi(s => s.toggleSidebar);
  const [, navigate] = useLocation();

  const tasks = useChat(s => s.tasks);
  const activeTaskId = useChat(s => s.viewedGoalId ?? s.activeTaskId);

  // Files tab data: recent artifacts across finished goals
  const [artifacts, setArtifacts] = useState<
    Array<{ path: string; turn_id: string }>
  >([]);
  useEffect(() => {
    if (!open || tab !== "files") return;
    let cancelled = false;
    void api
      .goals(10)
      .then(r => {
        if (cancelled) return;
        const out: Array<{ path: string; turn_id: string }> = [];
        for (const g of r.goals) {
          for (const a of g.artifacts ?? []) {
            const p = String(
              (a as { path?: string; label?: string }).path ??
                (a as { label?: string }).label ??
                ""
            );
            if (p) out.push({ path: p, turn_id: g.turn_id });
          }
        }
        setArtifacts(out.slice(0, 30));
      })
      .catch(() => {
        if (!cancelled) setArtifacts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, tab]);

  const openGoal = (turnId: string) => {
    close();
    navigate(`/chat?goal=${encodeURIComponent(turnId)}`);
  };

  const openWorkFolder = () => {
    close();
    void api
      .getSettings()
      .then(r => {
        const g = (
          window as unknown as {
            universe?: { openPath?: (p: string) => void };
          }
        ).universe;
        if (r.project_root && g?.openPath) g.openPath(r.project_root);
        else toast("The desktop shell opens the work folder.");
      })
      .catch(() => toast.error("Bridge unreachable"));
  };

  const items = useMemo<PaletteItem[]>(() => {
    const recentTasks: PaletteItem[] = tasks
      .filter(t => t.turn_id !== activeTaskId)
      .slice(0, 3)
      .map(t => ({
        id: `recent-${t.turn_id}`,
        section: "recent" as const,
        label: t.title || t.text.slice(0, 50) || "New task",
        hint: relTime(t.created_at),
        icon: MessageSquarePlus,
        tab: "tasks" as const,
        run: () => openGoal(t.turn_id),
      }));

    const suggested: PaletteItem[] = [
      {
        id: "new-task",
        section: "suggested",
        label: "New task",
        hint: "Ctrl+N",
        icon: Zap,
        tab: "actions",
        run: () => {
          close();
          useChat.getState().newConversation();
          navigate("/chat");
          document
            .querySelector<HTMLTextAreaElement>(".composer textarea")
            ?.focus();
        },
      },
      {
        id: "open-workspace",
        section: "suggested",
        label: "Open work folder",
        icon: FolderOpen,
        tab: "actions",
        run: openWorkFolder,
      },
      {
        id: "settings",
        section: "suggested",
        label: "Settings",
        icon: Settings,
        tab: "actions",
        run: () => {
          close();
          navigate("/settings");
        },
      },
    ];

    const panels: PaletteItem[] = [
      {
        id: "panel-preview",
        section: "panels",
        label: "Toggle side pane (Preview · Browser · Files)",
        icon: PanelRight,
        tab: "actions",
        run: () => {
          close();
          const rail = useRail.getState();
          if (rail.open) rail.closeRail();
          else rail.openRail();
        },
      },
      {
        id: "panel-browser",
        section: "panels",
        label: "Add browser tab",
        icon: Globe,
        tab: "actions",
        run: () => {
          close();
          useRail.getState().openRail("browser");
        },
      },
      {
        id: "panel-progress",
        section: "panels",
        label: "Show Progress (live todos)",
        icon: ListChecks,
        tab: "actions",
        run: () => {
          close();
          useRail.getState().openRail("progress");
        },
      },
      {
        id: "panel-terminal",
        section: "panels",
        label: "Toggle terminal",
        hint: "Ctrl+J",
        icon: SquareTerminal,
        tab: "actions",
        run: () => {
          close();
          const rail = useRail.getState();
          if (rail.open && rail.tab === "terminal") rail.closeRail();
          else rail.openRail("terminal");
        },
      },
      {
        id: "panel-sidebar",
        section: "panels",
        label: "Toggle sidebar",
        hint: "Ctrl+B",
        icon: PanelLeft,
        tab: "actions",
        run: () => {
          close();
          toggleSidebar();
        },
      },
    ];

    const configure: PaletteItem[] = [
      {
        id: "cfg-mcp",
        section: "configure",
        label: "MCP Servers",
        icon: Plug,
        tab: "actions",
        run: () => {
          close();
          navigate("/settings?section=mcp");
        },
      },
      {
        id: "cfg-skills",
        section: "configure",
        label: "Skills",
        icon: Sparkles,
        tab: "actions",
        run: () => {
          close();
          navigate("/settings?section=skills");
        },
      },
      {
        id: "cfg-automations",
        section: "configure",
        label: "Automations",
        icon: RotateCcw,
        tab: "actions",
        run: () => {
          close();
          navigate("/automations");
        },
      },
      {
        id: "cfg-settings",
        section: "configure",
        label: "Settings",
        icon: Settings,
        tab: "actions",
        run: () => {
          close();
          navigate("/settings");
        },
      },
    ];

    const application: PaletteItem[] = [
      {
        id: "app-restart-bridge",
        section: "application",
        label: "Restart the backend bridge",
        icon: RotateCcw,
        tab: "actions",
        run: () => {
          close();
          const g = (
            window as unknown as {
              universe?: { bridgeRestart?: () => Promise<boolean> };
            }
          ).universe;
          if (g?.bridgeRestart)
            void g
              .bridgeRestart()
              .then(ok =>
                ok
                  ? toast.success("Backend bridge restarting…")
                  : toast("A restart is already in flight")
              );
          else toast("Needs the desktop shell");
        },
      },
      {
        id: "app-work-folder",
        section: "application",
        label: "Open work folder",
        icon: FolderOpen,
        tab: "actions",
        run: openWorkFolder,
      },
    ];

    return [
      ...recentTasks,
      ...suggested,
      ...panels,
      ...configure,
      ...application,
    ];
    // tasks identity changes on every store task mutation — the memo is
    // per-open freshness, exactly what the palette wants
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, activeTaskId]);

  const visible =
    tab === "all"
      ? items
      : tab === "actions"
        ? items.filter(i => i.section !== "recent")
        : tab === "tasks"
          ? tasks.map((t: Task) => ({
              id: `task-${t.turn_id}`,
              section: "recent" as const,
              label: t.title || t.text.slice(0, 60) || "New task",
              hint: relTime(t.created_at),
              icon: MessageSquarePlus,
              tab: "tasks" as const,
              run: () => openGoal(t.turn_id),
            }))
          : artifacts.map(a => ({
              id: `file-${a.path}`,
              section: "recent" as const,
              label: a.path.split(/[\\/]/).pop() ?? a.path,
              hint: "artifact",
              icon: FileText,
              tab: "files" as const,
              run: () => {
                close();
                useRail.getState().openPreview(a.path);
              },
            }));

  // Esc closes even when cmdk's own input has focus
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, close]);

  if (!open) return null;

  return (
    <div
      className="cmdk-overlay"
      onClick={close}
      role="dialog"
      aria-label="Command palette"
      aria-modal="true"
    >
      <div className="cmdk-card" onClick={e => e.stopPropagation()}>
        <div className="cmdk-tabs" role="tablist" aria-label="Palette sections">
          {TABS.map(t => (
            <button
              key={t.id}
              type="button"
              role="tab"
              className="cmdk-tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <Command loop>
          <CommandInput
            autoFocus
            placeholder={
              tab === "files"
                ? "Search files…"
                : tab === "tasks"
                  ? "Search tasks…"
                  : "Search actions, tasks, or files…"
            }
          />
          <CommandList>
            <CommandEmpty className="cmdk-empty">Nothing matches.</CommandEmpty>
            {(
              [
                "recent",
                "suggested",
                "panels",
                "configure",
                "application",
              ] as const
            ).map(sec => {
              const rows = visible.filter(i => i.section === sec);
              if (!rows.length) return null;
              return (
                <CommandGroup key={sec} heading={SECTION_LABEL[sec]}>
                  {rows.map(i => (
                    <CommandItem
                      key={i.id}
                      value={`${i.label} ${i.hint ?? ""}`}
                      onSelect={() => i.run()}
                    >
                      <i.icon aria-hidden="true" />
                      <span>{i.label}</span>
                      {i.hint && <CommandShortcut>{i.hint}</CommandShortcut>}
                    </CommandItem>
                  ))}
                </CommandGroup>
              );
            })}
          </CommandList>
        </Command>
      </div>
    </div>
  );
}
