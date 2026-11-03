/**
 * components/desktop/ProjectBar.tsx — the workspace scope control.
 *
 * Two surfaces (2026-10-24 user pass — the composer must be an EXACT
 * Universe chat card):
 *   `inCard`  → the head row INSIDE the composer card: [folder] Project ⌄
 *               [branch], the crumbs Universe shows on top of its input.
 *               No machine slot, no inline add — "Add project" lives in
 *               the dropdown.
 *   default   → the old standalone strip (kept for callers outside the
 *               chat card).
 *
 * shell parity: with no project selected the crumb reads "Select project";
 * the dropdown lists existing projects (name · git branch · check on the
 * active one) plus an "Add project" action that opens the OS folder picker.
 * Picking a project scopes the conversation to it. Unselected sends create
 * UNASSIGNED tasks ('' project_id) — never a fake "general" project.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Folder, GitBranch, Plus } from "lucide-react";
import { useChat } from "@/stores/chat";
import { api, type Project } from "@/lib/api";

export default function ProjectBar({ inCard = false }: { inCard?: boolean }) {
  const activeProject = useChat(s => s.activeProject);
  const setActiveProject = useChat(s => s.setActiveProject);
  const [projects, setProjects] = useState<Project[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = () =>
    api
      .listProjects()
      .then(r => setProjects(r.projects.filter(p => !p.archived)))
      .catch(() => setProjects([]));

  useEffect(() => {
    void load();
  }, [activeProject]);

  // close on outside click / Esc, like every other chip popover here
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current = useMemo(
    () => projects.find(p => p.id === activeProject),
    [projects, activeProject]
  );
  const chosen = !!current;
  const label = current?.name ?? "Select project";
  const branch = current?.branch ?? null;

  const addProject = () => {
    const g = (
      window as unknown as {
        universe?: { pickFolder?: () => Promise<string | null> };
      }
    ).universe;
    if (!g?.pickFolder || busy) return;
    setBusy(true);
    void g
      .pickFolder()
      .then(async folderRoot => {
        if (!folderRoot) return;
        const folderName =
          folderRoot.split(/[\\/]/).filter(Boolean).pop() || "Project";
        try {
          const r = await api.createProject(folderName, folderRoot);
          await load();
          setActiveProject(r.project.id);
        } catch {
          /* bridge down — the list stays as it was */
        }
      })
      .finally(() => setBusy(false));
  };

  const menu = open ? (
    <div className="pb-menu zc-c-menu" role="listbox" aria-label="Projects">
      <div className="pb-head">Projects</div>
      {projects.length === 0 && (
        <p className="pb-empty">No projects yet — add a work folder.</p>
      )}
      {projects.map(p => (
        <button
          key={p.id}
          type="button"
          role="option"
          aria-selected={p.id === activeProject}
          className={`pb-row${p.id === activeProject ? " on" : ""}`}
          onClick={() => {
            setOpen(false);
            if (p.id !== activeProject) setActiveProject(p.id);
          }}
          title={p.root || p.name}
        >
          <span className="pb-rowname">{p.name}</span>
          {p.branch && (
            <span className="pb-branch mono">
              <GitBranch size={10} strokeWidth={2} aria-hidden="true" />
              {p.branch}
            </span>
          )}
          {p.id === activeProject && <Check size={12} strokeWidth={2.4} />}
        </button>
      ))}
      <div className="pb-sep" />
      <button
        type="button"
        className="pb-row pb-new"
        onClick={addProject}
        disabled={busy}
      >
        <Plus size={12} strokeWidth={2} aria-hidden="true" />
        <span className="pb-rowname">Add project</span>
      </button>
    </div>
  ) : null;

  // inCard: the Universe composer head row — [folder] Project ⌄ [branch]
  if (inCard) {
    return (
      <div className="zc-c-head" ref={rootRef}>
        <button
          type="button"
          className={`zc-crumb${chosen ? " set" : ""}`}
          onClick={() => setOpen(o => !o)}
          aria-expanded={open}
          aria-haspopup="listbox"
          title={current?.root || label}
        >
          <Folder
            size={16}
            strokeWidth={2}
            aria-hidden="true"
            className="pb-ic"
          />
          <span className="zc-crumb-name">{label}</span>
          <ChevronDown
            size={14}
            strokeWidth={2}
            aria-hidden="true"
            className={`zc-crumb-chev${open ? " open" : ""}`}
          />
        </button>
        {/* the branch is real data from the bridge — shown when the
            workspace reports one; read-only (no fake switcher menu) */}
        {branch && (
          <span
            className="zc-crumb static"
            title={`git branch: ${branch}`}
          >
            <GitBranch size={16} strokeWidth={2} aria-hidden="true" />
            <span className="zc-crumb-name">{branch}</span>
          </span>
        )}
        {menu}
      </div>
    );
  }

  return (
    <div
      className="project-bar"
      ref={rootRef}
      role="navigation"
      aria-label="Project"
    >
      <button
        type="button"
        className={`pb-trigger${chosen ? " set" : ""}`}
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={current?.root || label}
      >
        <Folder
          size={13}
          strokeWidth={1.8}
          aria-hidden="true"
          className="pb-ic"
        />
        <span className="pb-name">{label}</span>
        {branch && (
          <span className="pb-branch mono" title={`git branch: ${branch}`}>
            <GitBranch size={10} strokeWidth={2} aria-hidden="true" />
            {branch}
          </span>
        )}
        <ChevronDown
          size={12}
          strokeWidth={2}
          aria-hidden="true"
          className={`pb-chev${open ? " open" : ""}`}
        />
      </button>
      {/* Universe's machine slot: Local with the connected dot (always local
          today; SSH targets land here when the bridge gains remote roots) */}
      <span className="pb-machine" title="Local machine">
        <span className="pb-machine-dot" aria-hidden="true" />
        Local
      </span>
      <span className="sp" />
      <button
        type="button"
        className="pb-add"
        onClick={() => {
          setOpen(false);
          addProject();
        }}
        aria-label="Add project"
        title="Add project — choose a work folder"
      >
        <Plus size={13} strokeWidth={2} />
      </button>

      {menu}
    </div>
  );
}
