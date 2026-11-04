/**
 * components/desktop/Sidebar.tsx — the Universe workspace sidebar (2026-10-22
 * rebuild, workspaceSidebar vocabulary).
 *
 * Anatomy (Universe 3.14.3): a "Tasks" head with search / filter-and-sort /
 * add tools, a leading "New task" row, the task list (status dot + title +
 * relative age), the "Projects" section with its own add tool, the
 * "Archived · N" disclosure, and a Local footer with settings.
 *
 * Removed as Universe-only chrome: the Automations and Plugin Marketplace
 * nav rows, the inline Group/Project chip pair, the bare pinned/filter
 * icon buttons, and the Ctrl+N/Ctrl+K keycap chips. All the behavior
 * behind them survives: Group / By project / Timeline now live in the
 * filter menu, sort created/updated too, pinned-first and running-only as
 * its toggles, search as an inline input. Sessions reach
 * /automations and the marketplace via the command palette.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "wouter";
import { toast } from "sonner";
import {
  Archive,
  ArchiveRestore,
  Check,
  GitBranch,
  Pencil,
  Pin,
  Trash2,
  X,
} from "lucide-react";
import { api, BridgeError, type Project } from "@/lib/api";
import { resolveTaskStatus, useChat, type TaskStatus } from "@/stores/chat";
import { useUi } from "@/stores/ui";
import ConfirmDialog, { type ConfirmSpec } from "./ConfirmDialog";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
import {
  ArchiveIcon,
  CalendarClock,
  ChevronDownIcon,
  CollapseAllIcon,
  EllipsisIcon,
  FolderIcon,
  GripIcon,
  HashIcon,
  ListFilterIcon,
  MessagePlus,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  SpinnerIcon,
} from "./universe-icons";

interface ChatRow {
  turn_id: string;
  title: string;
  status: TaskStatus;
  created_at: number;
  pinned: number;
  archived: number;
  session_id: string | null;
  project_id?: string | null;
}

function relTime(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

/** inline rename (input + confirm/cancel) — replaces window.prompt so rename
 *  never blocks the socket thread and stays inside the app surface */
function InlineRename({
  initial,
  maxLen,
  onCommit,
  onCancel,
}: {
  initial: string;
  maxLen: number;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);
  const commit = () => {
    const t = value.trim();
    if (t && t !== initial) onCommit(t.slice(0, maxLen));
    else onCancel();
  };
  return (
    <span className="rename-row" onClick={e => e.stopPropagation()}>
      <input
        ref={inputRef}
        value={value}
        maxLength={maxLen}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") onCancel();
        }}
        aria-label="New name"
      />
      <button
        role="menuitem"
        className="rename-ok"
        aria-label="Confirm rename"
        onClick={commit}
      >
        <Check size={12} strokeWidth={2.4} />
      </button>
      <button role="menuitem" aria-label="Cancel rename" onClick={onCancel}>
        <X size={12} strokeWidth={2} />
      </button>
    </span>
  );
}

/** the ⋯ menu — chat actions, closed on outside click / Esc */
function ChatMenu({
  chat,
  project,
  onDone,
  onChanged,
  onConfirm,
}: {
  chat: ChatRow;
  project: string;
  onDone: () => void;
  /** refresh the visible chat list after a mutation (pin/archive keep the
   *  same row count, so a length-watcher alone would never refire) */
  onChanged: () => void;
  /** open the in-app confirm card instead of window.confirm */
  onConfirm: (spec: ConfirmSpec) => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const refresh = useChat(s => s.refreshTasks);
  const [renaming, setRenaming] = useState(false);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) onDone();
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onDone();
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [onDone]);

  const act = (fn: () => Promise<unknown>, opts?: { dropLocalId?: string }) => {
    void fn()
      .then(() => refresh())
      .then(onChanged)
      .catch(e => {
        // local-only rows (local-* offline rejects) can never DELETE on the
        // backend — drop them locally instead of showing a dead 404 toast
        const code = (e as { code?: string })?.code;
        if (
          opts?.dropLocalId &&
          (code === "http_404" || code === "turn_not_found")
        ) {
          useChat.getState().dropLocalTask(opts.dropLocalId);
          onChanged();
          return;
        }
        toast.error(e instanceof BridgeError ? e.message : "action failed");
      })
      .finally(onDone);
  };

  /** server mutation + live-state reconciliation: without dropTask the
   *  locally-"running" row re-merged into the visible list as a ghost
   *  after delete, and a deleted ACTIVE task left the composer viewing
   *  (and continuing) a dead thread */
  const actAndDrop = (fn: () => Promise<unknown>) => {
    void fn()
      .then(() => {
        useChat.getState().dropTask(chat.turn_id);
        return refresh();
      })
      .then(onChanged)
      .catch(e => {
        toast.error(e instanceof BridgeError ? e.message : "action failed");
      })
      .finally(onDone);
  };
  const hardDelete = () => {
    onConfirm({
      title: "Delete this chat?",
      body: `"${chat.title}" — this cannot be undone. Use Archive if you want to keep it recoverable.`,
      confirmLabel: "Delete",
      danger: true,
      onConfirm: () => actAndDrop(() => api.deleteGoal(chat.turn_id, true)),
    });
  };

  if (renaming) {
    return (
      <div
        className="chat-menu"
        ref={menuRef}
        role="menu"
        aria-label={`Rename ${chat.title}`}
        onClick={e => e.stopPropagation()}
      >
        <InlineRename
          initial={chat.title}
          maxLen={200}
          onCommit={t => act(() => api.patchGoal(chat.turn_id, { title: t }))}
          onCancel={onDone}
        />
      </div>
    );
  }

  return (
    <div
      className="chat-menu"
      ref={menuRef}
      role="menu"
      aria-label={`Actions for ${chat.title}`}
      onClick={e => e.stopPropagation()}
    >
      <button role="menuitem" onClick={() => setRenaming(true)}>
        <Pencil size={12} strokeWidth={1.8} /> Rename
      </button>
      <button
        role="menuitem"
        onClick={() => act(() => api.branchGoal(chat.turn_id))}
      >
        <GitBranch size={12} strokeWidth={1.8} /> Branch from here
      </button>
      <button
        role="menuitem"
        onClick={() =>
          act(() =>
            api.patchGoal(chat.turn_id, { pinned: chat.pinned ? false : true })
          )
        }
      >
        <Pin size={12} strokeWidth={1.8} /> {chat.pinned ? "Unpin" : "Pin"}
      </button>
      {chat.archived ? (
        <button
          role="menuitem"
          onClick={() =>
            act(() => api.patchGoal(chat.turn_id, { archived: false }))
          }
        >
          <ArchiveRestore size={12} strokeWidth={1.8} /> Unarchive
        </button>
      ) : (
        <button
          role="menuitem"
          onClick={() =>
            actAndDrop(() => api.patchGoal(chat.turn_id, { archived: true }))
          }
        >
          <Archive size={12} strokeWidth={1.8} /> Archive
        </button>
      )}
      {project !== "" && (
        <button
          role="menuitem"
          onClick={() =>
            act(() => api.patchGoal(chat.turn_id, { project_id: "" }))
          }
        >
          <X size={12} strokeWidth={1.8} /> Remove from project
        </button>
      )}
      <button role="menuitem" onClick={hardDelete} className="danger">
        <Trash2 size={12} strokeWidth={1.8} /> Delete…
      </button>
      <span className="cm-proj mono">in {project}</span>
    </div>
  );
}

/** a chat row — Universe's exact shape (measured live 3.14.3): a 16px status
 *  slot (spinner while running, a 6px destructive dot when failed, empty at
 *  rest), the title at 14px foreground, the age at 12px subtle, and hover
 *  swaps the age for the ⋯ actions button. Menu state is lifted so exactly
 *  one row can hold the popover at a time. */
function TaskRow({
  chat,
  onOpen,
  viewing,
  menuOpen,
  onMenuToggle,
  project,
  onChanged,
  askConfirm,
}: {
  chat: ChatRow;
  onOpen: (id: string) => void;
  viewing?: boolean;
  menuOpen?: boolean;
  onMenuToggle?: (id: string | null) => void;
  project?: string;
  onChanged?: () => void;
  askConfirm?: (spec: ConfirmSpec) => void;
}) {
  const running = chat.status === "running";
  const failed = chat.status === "failed";
  return (
    <li
      className={`zsb-task${viewing ? " on" : ""}`}
      data-testid={`task-item-${chat.turn_id}`}
      role="listitem"
      aria-label={`Chat ${chat.status}: ${chat.title}`}
      aria-current={viewing ? "true" : undefined}
      tabIndex={0}
      onClick={() => onOpen(chat.turn_id)}
      onKeyDown={e => e.key === "Enter" && onOpen(chat.turn_id)}
    >
      <span className="zsb-slot" aria-hidden="true">
        {running ? (
          <SpinnerIcon size={16} className="zsb-spin" />
        ) : failed ? (
          <span className="zsb-dot" data-status="failed" />
        ) : null}
      </span>
      <div className="zsb-tw">
        <p className="zsb-ttl" title={chat.title}>
          {chat.title}
        </p>
      </div>
      <span className="zsb-meta">
        <span>{relTime(chat.created_at)}</span>
      </span>
      {onMenuToggle && (
        <span className="zsb-rowmenu" onClick={e => e.stopPropagation()}>
          <button
            type="button"
            className="zsb-rowbtn24"
            aria-label={`Actions for ${chat.title}`}
            onClick={() => onMenuToggle(menuOpen ? null : chat.turn_id)}
          >
            <EllipsisIcon size={14} />
          </button>
          {menuOpen && project && onChanged && askConfirm && (
            <ChatMenu
              chat={chat}
              project={project}
              onDone={() => onMenuToggle(null)}
              onChanged={onChanged}
              onConfirm={askConfirm}
            />
          )}
        </span>
      )}
    </li>
  );
}

export default function Sidebar({
  open,
  surface = "universe",
  botsBody = null,
}: {
  open: boolean;
  /** universe → rows navigate /chat; studio → the SAME rows navigate
   *  /studio (Universe Studio is the same workspace, minus Automations);
   *  bots → the switcher stays, the body swaps for the bots roster
   *  (same width, same tokens — the ONLY surface that changes the column) */
  surface?: "universe" | "studio" | "bots";
  /** the roster node rendered when surface === "bots" */
  botsBody?: ReactNode;
}) {
  const tasks = useChat(s => s.tasks);
  const activeProject = useChat(s => s.activeProject);
  const setActiveProject = useChat(s => s.setActiveProject);
  const [, navigate] = useLocation();
  /** Universe's organize views: Group (every project with its chats nested),
   *  By project (the active project scopes everything), Timeline (all
   *  chats, chronological, flat) */
  const [view, setView] = useState<"project" | "group" | "timeline">(
    "project"
  );
  /** Universe's task-list tree: projects open/close individually, the View
   *  menu expands/collapses all — no extra toggles beyond what Universe has */
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(
    () => new Set()
  );
  const projectsInitRef = useRef(false);
  const toggleProject = (id: string) =>
    setExpandedProjects(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  /** the Tasks list pages like Universe's: eight rows, then a disclosure */
  const [showAllTasks, setShowAllTasks] = useState(false);
  /** the open ⋯ row menu (exactly one at a time) */
  const [menuFor, setMenuFor] = useState<string | null>(null);
  /** the head tools: the filter-and-sort popover (Search opens the palette) */
  const [toolsMenu, setToolsMenu] = useState(false);
  const toolsRef = useRef<HTMLSpanElement>(null);
  /** Universe's section headers and per-project disclosure state */
  const [projectsCollapsed, setProjectsCollapsed] = useState(false);
  const [tasksCollapsed, setTasksCollapsed] = useState(false);
  const [projectMenu, setProjectMenu] = useState<string | null>(null);
  const [showAllProjects, setShowAllProjects] = useState<Set<string>>(
    () => new Set()
  );
  /** ALL tasks, grouped by project_id — the tree render reads from this */
  const [chatsByProject, setChatsByProject] = useState<
    Map<string, ChatRow[]>
  >(new Map());
  /** tasks not assigned to any project (empty/null project_id) — the
   *  composer's "Select project" slot is where they regroup */
  const [unassignedChats, setUnassignedChats] = useState<ChatRow[]>([]);
  const [projects, setProjects] = useState<
    Array<Project & { goal_count?: number }>
  >([]);
  const [archivedChats, setArchivedChats] = useState<ChatRow[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [archiveMenu, setArchiveMenu] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);
  const askConfirm = (spec: ConfirmSpec) => setConfirm(spec);

  const loadProjects = () =>
    api
      .listProjects()
      .then(r => {
        const live = r.projects.filter(p => !p.archived);
        setProjects(live);
        if (!live.some(p => p.id === activeProject)) {
          // no project selected (and none active): the empty selection is
          // "Select project" — tasks created now are unassigned ('')
          setActiveProject("");
        }
        // first load: Universe opens every group by default
        if (!projectsInitRef.current) {
          projectsInitRef.current = true;
          setExpandedProjects(new Set(live.map(p => p.id)));
        }
      })
      .catch(() => {
        /* bridge down: keep what we have */
      });

  const loadChats = () => {
    api
      .goals(200)
      .then(r => {
        const map = (g: (typeof r.goals)[number]): ChatRow => ({
          turn_id: g.turn_id,
          title: (g.title || "New task").slice(0, 80),
          status: resolveTaskStatus(g),
          created_at: g.created_at ?? 0,
          pinned: g.pinned ? 1 : 0,
          archived: g.archived ? 1 : 0,
          session_id: g.session_id ?? null,
          project_id: g.project_id ?? null,
        });
        const rows = r.goals.map(map);
        // group by project_id for the tree
        const byProject = new Map<string, ChatRow[]>();
        const unassigned: ChatRow[] = [];
        for (const r2 of rows) {
          const pid = r2.project_id || "";
          if (!pid || r2.archived) {
            if (!r2.archived) unassigned.push(r2);
          } else {
            if (!byProject.has(pid)) byProject.set(pid, []);
            byProject.get(pid)!.push(r2);
          }
        }
        setChatsByProject(byProject);
        setUnassignedChats(unassigned);
        setArchivedChats(rows.filter(g => g.archived));
      })
      .catch(() => {
        /* keep */
      });
  };
  // stable re-run handle for chat-menu actions (pin/archive/branch/rename
  // keep the same row count, so the length-watcher below would never refire)
  const refreshList = useCallback(() => {
    loadChats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject, tasks]);

  useEffect(() => {
    void loadProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject]);
  useEffect(() => {
    void loadChats();
  }, [activeProject]);
  // live tasks arriving (sendGoal) also refresh the chat list
  useEffect(() => {
    void loadChats();
  }, [tasks.length]);

  /** create a project from a chosen folder path (name = folder basename) */
  const createProjectFromFolder = async (root: string) => {
    // Windows roots arrive with backslashes; splitting on "/" alone leaves
    // the whole path as the name (it used to render as "D:\Universe…" in the
    // tree). Trim both separators and any trailing slash.
    const name =
      root
        .split(/[\\/]+/)
        .filter(Boolean)
        .pop() || "Project";
    try {
      const r = await api.createProject(name, root);
      toast.success(`Project "${name}" created`);
      await loadProjects();
      setActiveProject(r.project.id);
      setExpandedProjects(prev => new Set(prev).add(r.project.id));
    } catch (e) {
      toast.error("Could not create the project", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    }
  };

  /** new task — empty conversation; the row appears when the first message
   *  is sent, so pressing it twice never mints sidebar ghosts */
  const newTaskIn = (projectId: string) => {
    useChat.getState().newConversation();
    setActiveProject(projectId);
    setExpandedProjects(prev => new Set(prev).add(projectId));
    const home = surface === "studio" ? "/studio" : "/chat";
    if (window.location.pathname !== home) navigate(home);
    else if (window.location.search) navigate(home);
    const input = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea"
    );
    input?.focus();
  };

  // the welcome screen's "Start from a work folder" card asks for the same
  // add-project flow (native folder picker in the desktop app)
  useEffect(() => {
    const onAddProject = () => {
      const g = (
        window as unknown as {
          universe?: { pickFolder?: () => Promise<string | null> };
        }
      ).universe;
      if (g?.pickFolder) {
        void g.pickFolder().then(root => {
          if (root) void createProjectFromFolder(root);
        });
      } else {
        toast.info("Folder picking needs the desktop app", {
          description: "Add a work folder from the Universe desktop build.",
        });
      }
    };
    window.addEventListener("universe:add-project", onAddProject);
    return () =>
      window.removeEventListener("universe:add-project", onAddProject);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // outside close for the filter popover
  useEffect(() => {
    if (!toolsMenu) return;
    const close = (e: MouseEvent) => {
      if (!toolsRef.current?.contains(e.target as Node)) setToolsMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setToolsMenu(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [toolsMenu]);
  // the per-project ⋯ popover — fixed: pointerdown closed the menu before the click event reached the button (lost "New task here" click)
  useEffect(() => {
    if (!projectMenu) return;
    const close = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest(".cx-menu") || t?.closest(".zsb-pmenu") || t?.closest(".zsb-rowbtn24")) return;
      setProjectMenu(null);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setProjectMenu(null);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [projectMenu]);

  /** folder basename for display — rows created before the backslash fix were
   *  stored with the whole root path as their name */
  const displayName = (p: Project) =>
    /[\\/]/.test(p.name)
      ? p.name.split(/[\\/]+/).filter(Boolean).pop() || p.name
      : p.name;

  const activeRow = projects.find(p => p.id === activeProject);
  const activeName = activeRow ? displayName(activeRow) : "General";

  /** the row the user is looking at right now — carries the selected fill */
  const viewingGoalId = useChat(s => s.viewedGoalId ?? s.activeTaskId);

  /** Universe's row order: newest (created) on top */
  const orderRows = (rows: ChatRow[]) =>
    rows.slice().sort((a, b) => b.created_at - a.created_at);

  /** which projects the tree renders per view — the live Universe sidebar lists
   *  EVERY project in both Group and Project views (measured 2026-10-23);
   *  ALL real projects render (there is no synthetic "general" anymore) */
  const treeProjects = projects;

  const TASK_PAGE = 8;
  /** Universe's project lists page five rows before the disclosure */
  const TASK_PAGE_PROJECT = 5;
  /** timeline view flattens every project back into one list */
  const timelineAll = orderRows(
    view === "timeline"
      ? [
          ...unassignedChats,
          ...Array.from(chatsByProject.values()).flat(),
        ]
      : unassignedChats
  );
  const filteredTasks = timelineAll;
  const visibleTasks = showAllTasks
    ? filteredTasks
    : filteredTasks.slice(0, TASK_PAGE);

  // previous-conversation view: the chat column reads ?goal= on change and
  // loads that task's thread (history + filter). Same URL twice = no-op.
  const openGoal = (turnId: string) => {
    const url = `${surface === "studio" ? "/studio" : "/chat"}?goal=${turnId}`;
    if (window.location.search !== `?goal=${turnId}`) navigate(url);
    else void useChat.getState().loadGoalHistory(turnId);
  };

  return (
    <aside
      className={`sidebar zsb${open ? "" : " closed"}`}
      aria-label="Workspace"
    >
      {/* --- workspace switcher: Universe | Studio | Bots. The
          sidebar is the stable shell; only Bots swaps this column's body
          for the bots rail (same width, same tokens). --- */}
      <div className="zsb-ws-row">
        <WorkspaceSwitcher />
      </div>
      {surface === "bots" ? (
        <div className="zsb-body">
          <div className="zsb-scroll">{botsBody}</div>
        </div>
      ) : (
      <>
      {/* --- the menu rows: New task / Search / Automations. Universe
          Studio drops the Automations row (it has no automations). --- */}
      <div className="zsb-nav">
        <div
          className="zsb-newtask"
          role="button"
          tabIndex={0}
          data-testid="task-new-button"
          onClick={() => newTaskIn("")}
          onKeyDown={e => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              newTaskIn("");
            }
          }}
        >
          <div className="zsb-newtask-in">
            <MessagePlus size={16} />
            <span className="zsb-label">New task</span>
            <span className="zsb-kbd">Ctrl+N</span>
          </div>
        </div>
        <button
          type="button"
          className="zsb-navrow"
          onClick={() => useUi.getState().openPalette()}
        >
          <SearchIcon size={16} />
          <span className="zsb-label zsb-grow">Search</span>
          <span className="zsb-kbd">Ctrl+K</span>
        </button>
        {surface !== "studio" && (
          <button
            type="button"
            className="zsb-navrow"
            onClick={() => navigate("/automations")}
          >
            <CalendarClock size={16} />
            <span className="zsb-label zsb-grow">Automations</span>
          </button>
        )}
      </div>

      {/* --- scroll body: organize row + sections (Universe geometry) --- */}
      <div className="zsb-body">
        <div className="zsb-scroll">
          <div className="zsb-org">
            <div className="zsb-org-in">
              <div className="zsb-org-left">
                <div className="zsb-tabs" role="tablist" aria-label="Grouping">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === "group"}
                    className={`zsb-tab${view === "group" ? " on" : ""}`}
                    onClick={() => setView("group")}
                    title="Group"
                  >
                    <HashIcon size={12} />
                    <span>Group</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === "project"}
                    className={`zsb-tab${view === "project" ? " on" : ""}`}
                    onClick={() => setView("project")}
                    title="Project"
                  >
                    <FolderIcon size={12} />
                    <span>Project</span>
                  </button>
                </div>
                <button
                  type="button"
                  className="zsb-icon24"
                  title="Collapse all"
                  aria-label="Collapse or expand every project"
                  onClick={() => {
                    const allOpen = treeProjects.every(p =>
                      expandedProjects.has(p.id)
                    );
                    setExpandedProjects(
                      allOpen ? new Set() : new Set(treeProjects.map(p => p.id))
                    );
                  }}
                >
                  <CollapseAllIcon size={14} />
                </button>
              </div>
              <div className="zsb-org-right">
                <span className="cx-pop" ref={toolsRef}>
                  <button
                    type="button"
                    className={`zsb-icon24${toolsMenu ? " on" : ""}`}
                    title="Filter and sort"
                    aria-label="Filter and sort"
                    aria-expanded={toolsMenu}
                    onClick={() => setToolsMenu(v => !v)}
                  >
                    <ListFilterIcon size={14} />
                  </button>
                  {toolsMenu && (
                    <span
                      className="cx-menu side-menu"
                      role="menu"
                      aria-label="Filter and sort"
                    >
                      <div className="cx-menu-h">View</div>
                      {(
                        [
                          ["group", "Group"],
                          ["project", "By project"],
                          ["timeline", "Timeline"],
                        ] as const
                      ).map(([v, label]) => (
                        <button
                          key={v}
                          type="button"
                          role="menuitemradio"
                          aria-checked={view === v}
                          className={view === v ? "on" : ""}
                          onClick={() => {
                            setView(v);
                            setToolsMenu(false);
                          }}
                        >
                          <span>{label}</span>
                        </button>
                      ))}
                      <div className="cx-menu-h">Groups</div>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setExpandedProjects(new Set(treeProjects.map(p => p.id)));
                          setToolsMenu(false);
                        }}
                      >
                        <span>Expand all</span>
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setExpandedProjects(new Set());
                          setToolsMenu(false);
                        }}
                      >
                        <span>Collapse all</span>
                      </button>
                    </span>
                  )}
                </span>
                <button
                  type="button"
                  className={`zsb-icon24${showArchived ? " on" : ""}`}
                  title="Archive"
                  aria-label="Archive"
                  aria-expanded={showArchived}
                  onClick={() => setShowArchived(v => !v)}
                >
                  <ArchiveIcon size={14} />
                </button>
              </div>
            </div>
          </div>

          <div className="zsb-sections">
            <div className="zsb-sections-in">
              {/* ---------- Projects ---------- */}
              {view !== "timeline" && (
                <section className="zsb-section">
                  <div className="zsb-sechead">
                    <button
                      type="button"
                      className="zsb-seclabel"
                      aria-expanded={!projectsCollapsed}
                      onClick={() => setProjectsCollapsed(v => !v)}
                    >
                      <span>Projects</span>
                      <ChevronDownIcon size={14} className="zsb-secchev" />
                    </button>
                    <div className="zsb-secacts">
                      <span className="zsb-grip" aria-hidden="true">
                        <GripIcon size={14} />
                      </span>
                      <button
                        type="button"
                        className="zsb-icon24"
                        aria-label="Add project"
                        title="Add project — choose a work folder"
                        onClick={() => {
                          const g = (
                            window as unknown as {
                              universe?: { pickFolder?: () => Promise<string | null> };
                            }
                          ).universe;
                          if (g?.pickFolder) {
                            void g.pickFolder().then(root => {
                              if (root) void createProjectFromFolder(root);
                            });
                          }
                        }}
                      >
                        <PlusIcon size={14} />
                      </button>
                    </div>
                  </div>
                  {!projectsCollapsed && (
                    <ul className="zsb-plist" role="list">
                      {treeProjects.length === 0 && (
                        <li className="zsb-empty">
                          No open projects — add a work folder
                        </li>
                      )}
                      {treeProjects.map(p => {
                        const rows = orderRows(chatsByProject.get(p.id) ?? []);
                        const openNode = expandedProjects.has(p.id);
                        const all = showAllProjects.has(p.id);
                        const shown = all ? rows : rows.slice(0, TASK_PAGE_PROJECT);
                        return (
                          <li key={p.id} className="zsb-pitem">
                            <div className="zsb-pnode">
                              <div className="zsb-prowwrap">
                                <div
                                  role="button"
                                  tabIndex={0}
                                  data-testid="workspace-item"
                                  className="zsb-prow"
                                  aria-expanded={openNode}
                                  title={p.root || displayName(p)}
                                  onClick={() => {
                                    if (activeProject !== p.id)
                                      setActiveProject(p.id);
                                    toggleProject(p.id);
                                  }}
                                  onKeyDown={e => {
                                    if (e.key === "Enter" || e.key === " ") {
                                      e.preventDefault();
                                      toggleProject(p.id);
                                    }
                                  }}
                                >
                                  <div className="zsb-prowin">
                                    <span className="zsb-picon">
                                      <FolderIcon size={16} />
                                    </span>
                                    <p className="zsb-plabel">{displayName(p)}</p>
                                  </div>
                                </div>
                                <span className="cx-pop zsb-pmenu">
                                  <button
                                    type="button"
                                    className="zsb-rowbtn24"
                                    aria-label={`Actions for ${displayName(p)}`}
                                    title="Project actions"
                                    onClick={e => {
                                      e.stopPropagation();
                                      setProjectMenu(
                                        projectMenu === p.id ? null : p.id
                                      );
                                    }}
                                  >
                                    <EllipsisIcon size={14} />
                                  </button>
                                  {projectMenu === p.id && (
                                    <span className="cx-menu side-menu" role="menu">
                                      <button
                                        type="button"
                                        role="menuitem"
                                        onClick={() => {
                                          setProjectMenu(null);
                                          newTaskIn(p.id);
                                        }}
                                      >
                                        <span>New task here</span>
                                      </button>
                                      <button
                                        type="button"
                                        role="menuitem"
                                        onClick={() => {
                                          setProjectMenu(null);
                                          setActiveProject(p.id);
                                        }}
                                      >
                                        <span>Set as active project</span>
                                      </button>
                                    </span>
                                  )}
                                </span>
                              </div>
                              {openNode && (
                                <div className="zsb-tasks">
                                  {shown.length > 0 ? (
                                    <ul className="zsb-taskul" role="list">
                                      {shown.map(c => (
                                        <TaskRow
                                          key={c.turn_id}
                                          chat={c}
                                          onOpen={openGoal}
                                          viewing={c.turn_id === viewingGoalId}
                                          menuOpen={menuFor === c.turn_id}
                                          onMenuToggle={setMenuFor}
                                          project={displayName(p)}
                                          onChanged={refreshList}
                                          askConfirm={askConfirm}
                                        />
                                      ))}
                                    </ul>
                                  ) : (
                                    <div className="zsb-empty-tight">
                                      No tasks yet
                                    </div>
                                  )}
                                  {rows.length > TASK_PAGE_PROJECT && (
                                    <div
                                      className="zsb-more"
                                      role="button"
                                      tabIndex={0}
                                      onClick={() =>
                                        setShowAllProjects(prev => {
                                          const next = new Set(prev);
                                          if (next.has(p.id)) next.delete(p.id);
                                          else next.add(p.id);
                                          return next;
                                        })
                                      }
                                      onKeyDown={e => {
                                        if (e.key === "Enter" || e.key === " ") {
                                          e.preventDefault();
                                          setShowAllProjects(prev => {
                                            const next = new Set(prev);
                                            if (next.has(p.id)) next.delete(p.id);
                                            else next.add(p.id);
                                            return next;
                                          });
                                        }
                                      }}
                                    >
                                      <span>
                                        {all ? "Show less" : "Show more"}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              )}

              {/* ---------- Tasks (unassigned) ---------- */}
              <section className="zsb-section">
                <div className="zsb-sechead">
                  <button
                    type="button"
                    className="zsb-seclabel"
                    aria-expanded={!tasksCollapsed}
                    onClick={() => setTasksCollapsed(v => !v)}
                  >
                    <span>Tasks</span>
                    <ChevronDownIcon size={14} className="zsb-secchev" />
                  </button>
                  <div className="zsb-secacts">
                    <span className="zsb-grip" aria-hidden="true">
                      <GripIcon size={14} />
                    </span>
                    <button
                      type="button"
                      className="zsb-icon24"
                      aria-label="New task"
                      title="New task"
                      onClick={() => newTaskIn("")}
                    >
                      <MessagePlus size={14} />
                    </button>
                  </div>
                </div>
                {!tasksCollapsed && (
                  <div className="zsb-tasks">
                    {visibleTasks.length > 0 ? (
                      <ul className="zsb-taskul" role="list">
                        {visibleTasks.map(c => (
                          <TaskRow
                            key={c.turn_id}
                            chat={c}
                            onOpen={openGoal}
                            viewing={c.turn_id === viewingGoalId}
                            menuOpen={menuFor === c.turn_id}
                            onMenuToggle={setMenuFor}
                            project={activeName}
                            onChanged={refreshList}
                            askConfirm={askConfirm}
                          />
                        ))}
                      </ul>
                    ) : (
                      <div className="zsb-empty-tight">No tasks yet</div>
                    )}
                    {filteredTasks.length > TASK_PAGE && (
                      <div
                        className="zsb-more"
                        role="button"
                        tabIndex={0}
                        aria-expanded={showAllTasks}
                        onClick={() => setShowAllTasks(v => !v)}
                        onKeyDown={e => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setShowAllTasks(v => !v);
                          }
                        }}
                      >
                        <span>{showAllTasks ? "Show less" : "Show more"}</span>
                      </div>
                    )}
                  </div>
                )}
              </section>

              {/* ---------- Archived (Universe's own state, Universe's rows) ---------- */}
              {archivedChats.length > 0 && (
                <section className="zsb-section">
                  <div className="zsb-sechead">
                    <button
                      type="button"
                      className="zsb-seclabel"
                      aria-expanded={showArchived}
                      onClick={() => setShowArchived(v => !v)}
                    >
                      <span>Archived · {archivedChats.length}</span>
                      <ChevronDownIcon size={14} className="zsb-secchev" />
                    </button>
                    <div className="zsb-secacts">
                      <span className="cx-pop">
                        <button
                          type="button"
                          className="zsb-icon24"
                          aria-label="Archive actions"
                          title="Archive actions"
                          onClick={() => setArchiveMenu(v => !v)}
                        >
                          <EllipsisIcon size={14} />
                        </button>
                        {archiveMenu && (
                          <span className="cx-menu side-menu" role="menu">
                            <div className="cx-menu-h">Archive actions</div>
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setArchiveMenu(false);
                                const n = archivedChats.length;
                                askConfirm({
                                  title:
                                    n === 1
                                      ? "Delete this archived task?"
                                      : `Delete ${n} archived tasks?`,
                                  body:
                                    "Tasks will be removed from the task list and Archive.",
                                  confirmLabel: "Delete all",
                                  danger: true,
                                  onConfirm: () => {
                                    let del = 0;
                                    let failed = 0;
                                    void (async () => {
                                      for (const c of archivedChats) {
                                        try {
                                          await api.deleteGoal(c.turn_id, true);
                                          del += 1;
                                        } catch {
                                          failed += 1;
                                        }
                                      }
                                      toast.success(
                                        `Deleted ${del}, skipped ${n - del - failed}, failed ${failed}.`
                                      );
                                      refreshList();
                                    })();
                                  },
                                });
                              }}
                            >
                              <span>Delete all archived tasks…</span>
                            </button>
                          </span>
                        )}
                      </span>
                    </div>
                  </div>
                  {showArchived && (
                    <ul className="zsb-taskul" role="list">
                      {archivedChats.map(c => (
                        <TaskRow
                          key={c.turn_id}
                          chat={c}
                          onOpen={openGoal}
                          viewing={c.turn_id === viewingGoalId}
                          menuOpen={menuFor === c.turn_id}
                          onMenuToggle={setMenuFor}
                          project={activeName}
                          onChanged={refreshList}
                          askConfirm={askConfirm}
                        />
                      ))}
                    </ul>
                  )}
                </section>
              )}
            </div>
          </div>
        </div>
      </div>
      </>
      )}

      {/* --- footer: 32px avatar, 14px/600 name, gear (Universe's slot) --- */}
      <footer className="zsb-foot">
        <div className="zsb-user">
          <button
            type="button"
            className="zsb-usermain"
            onClick={() => navigate("/settings")}
            title="Local workspace"
          >
            <span className="zsb-avatar" aria-hidden="true">
              L
            </span>
            <span className="zsb-uname">Local</span>
          </button>
          <div className="zsb-uacts">
            <button
              type="button"
              className="zsb-icon32"
              title="Settings"
              aria-label="Settings"
              onClick={() => navigate("/settings")}
            >
              <SettingsIcon size={16} />
            </button>
          </div>
        </div>
      </footer>

      {confirm && (
        <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} />
      )}
    </aside>
  );
}
