/**
 * components/desktop/TaskHeader.tsx — the chat column's task title row.
 *
 * Measured from the live ZCode 3.12.1 window (2026-10-23): the title row
 * sits at the top of the CHAT COLUMN, not in the window chrome —
 *   [folder icon] Task title [⋯]   ......   [utility icons]
 * The folder icon reveals the task's workspace in Explorer; "⋯" carries
 * the session actions (reload / paths / ids). The TURN chip is retired
 * (2026-10-28): one turn engine runs every task, nothing to badge.
 * Everything here is real — no decorative buttons.
 */
import { History, Settings } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { useChat } from "@/stores/chat";
import { useUi } from "@/stores/ui";
import { api } from "@/lib/api";
import { toast } from "sonner";

export default function TaskHeader() {
  const [, navigate] = useLocation();
  const tabId = useChat(s => s.viewedGoalId ?? s.activeTaskId);
  // primitive-only selections: refreshTasks rebuilds every task row per
  // poll, so selecting the whole object re-rendered the header on every
  // poll tick — these return primitives and re-render only when the
  // rendered fields themselves change
  const tabTitle = useChat(s => {
    const t = tabId ? s.tasks.find(x => x.turn_id === tabId) : undefined;
    return t?.title || "New task";
  });
  const activeProject = useChat(s => s.activeProject);

  const [headMenu, setHeadMenu] = useState(false);
  const headRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!headMenu) return;
    const close = (e: MouseEvent) => {
      if (!headRef.current?.contains(e.target as Node)) setHeadMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setHeadMenu(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [headMenu]);

  const loadRoot = async (): Promise<string | null> => {
    try {
      const r = await api.listProjects();
      return (
        r.projects.find(p => p.id === activeProject)?.root ??
        r.projects[0]?.root ??
        null
      );
    } catch {
      return null;
    }
  };

  const openInExplorer = () => {
    void loadRoot().then(root => {
      if (!root) {
        toast("No workspace root for this task");
        return;
      }
      const g = (
        window as unknown as {
          universe?: { openPath?: (t: string) => unknown };
        }
      ).universe;
      if (!g?.openPath) {
        toast("File manager is only available in the desktop app");
        return;
      }
      void g.openPath(root);
    });
  };

  return (
    <div className="zc-task-head">
      <button
        type="button"
        className="tth-folder"
        onClick={openInExplorer}
        title="Reveal the task workspace in File Explorer"
        aria-label="Reveal workspace in File Explorer"
      >
        <svg width={14} height={14} viewBox="0 0 14 14" aria-hidden="true">
          <path
            d="M1.5 3.5h4l1.4 1.6h5.6v6.4c0 .6-.4 1-1 1h-9c-.6 0-1-.4-1-1V3.5Z"
            stroke="currentColor"
            strokeWidth="1.2"
            fill="none"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <span className="tth-title">{tabTitle}</span>

      {tabId && (
        <span className="cx-pop header-pop" ref={headRef}>
          <button
            type="button"
            className="tb-btn"
            aria-label="Session actions"
            title="Session actions"
            aria-haspopup="menu"
            aria-expanded={headMenu}
            onClick={() => setHeadMenu(v => !v)}
          >
            <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
              <circle cx="2" cy="6.5" r="1.15" fill="currentColor" />
              <circle cx="6.5" cy="6.5" r="1.15" fill="currentColor" />
              <circle cx="11" cy="6.5" r="1.15" fill="currentColor" />
            </svg>
          </button>
          {headMenu && (
            <span className="cx-menu side-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setHeadMenu(false);
                  void useChat.getState().loadGoalHistory(tabId);
                }}
              >
                <span>Reload session</span>
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setHeadMenu(false);
                  openInExplorer();
                }}
              >
                <span>Open in File Explorer</span>
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setHeadMenu(false);
                  void loadRoot().then(root => {
                    if (root) void navigator.clipboard.writeText(root);
                  });
                }}
              >
                <span>Copy task path</span>
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setHeadMenu(false);
                  void navigator.clipboard.writeText(tabId);
                }}
              >
                <span>Copy session ID</span>
              </button>
            </span>
          )}
        </span>
      )}

      <span className="tth-flex" aria-hidden="true" />

      <button
        type="button"
        className="tb-btn"
        onClick={() => useUi.getState().openPalette()}
        title="Search sessions and commands (Ctrl+K)"
        aria-label="Open command palette"
      >
        <History size={14} strokeWidth={1.8} />
      </button>
      <button
        type="button"
        className="tb-btn"
        onClick={() => navigate("/settings")}
        title="Settings"
        aria-label="Settings"
      >
        <Settings size={14} strokeWidth={1.8} />
      </button>
    </div>
  );
}
