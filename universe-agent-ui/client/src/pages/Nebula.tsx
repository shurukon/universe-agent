/**
 * pages/Nebula.tsx — the memory vault (Obsidian-style notes for Universe).
 *
 * Three columns on the Universe dark palette:
 *   LEFT   w-72  — header, Distill/New-note actions, search, note list
 *   CENTER flex — markdown note view with [[wikilink]] chips, edit & delete
 *   RIGHT  w-80 — "Constellation": circular SVG of the link graph
 *
 * Data lives in lib/nebulaApi.ts (bridge /api/nebula/*). Every failure is
 * toasted via sonner; notes and the graph are re-synced after any mutation.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  Link2,
  Loader2,
  Orbit,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sparkle,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { BridgeError } from "@/lib/api";
import {
  createNote,
  deleteNote,
  distill,
  getGraph,
  getNote,
  listNotes,
  updateNote,
} from "@/lib/nebulaApi";
import type { NebulaGraph, NebulaNote } from "@/lib/nebulaApi";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useUi } from "@/stores/ui";
import Sidebar from "@/components/desktop/Sidebar";
import "../components/desktop/design-system.css";
import "../components/desktop/desktop.css";

// ---- palette ----------------------------------------------------------------

const PANEL =
  "rounded-xl border border-[var(--ds-line)] bg-white/[0.03] backdrop-blur-md";
const ACCENT_BTN =
  "gap-1.5 bg-[#d4d4d4] text-[#161616] hover:bg-white hover:text-[#161616]";
const GHOST_BTN =
  "gap-1.5 border-[var(--ds-line)] bg-white/[0.03] text-xs text-white/70 hover:bg-white/[0.07] hover:text-white/90";

// ---- small helpers -----------------------------------------------------------

function errMsg(e: unknown): string {
  return e instanceof BridgeError
    ? e.message
    : "Universe bridge is not responding";
}

function relTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso || "—";
  const s = (Date.now() - t) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return new Date(t).toLocaleDateString();
}

function clip(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

// ---- markdown (headings / bullets / fences / bold / [[wikilinks]]) -----------

type MdBlock =
  | { kind: "h1" | "h2" | "h3" | "para"; text: string }
  | { kind: "code"; text: string; lang: string }
  | { kind: "ul"; items: string[] };

function parseMarkdown(src: string): MdBlock[] {
  const blocks: MdBlock[] = [];
  let para: string[] = [];
  let bullets: string[] = [];
  let code: string[] | null = null;
  let lang = "";

  const flushPara = () => {
    if (para.length) {
      blocks.push({ kind: "para", text: para.join(" ") });
      para = [];
    }
  };
  const flushBullets = () => {
    if (bullets.length) {
      blocks.push({ kind: "ul", items: bullets });
      bullets = [];
    }
  };

  for (const raw of src.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (code !== null) {
      if (/^\s*```/.test(line)) {
        blocks.push({ kind: "code", text: code.join("\n"), lang });
        code = null;
        lang = "";
      } else {
        code.push(raw);
      }
      continue;
    }
    if (/^\s*```/.test(line)) {
      flushPara();
      flushBullets();
      code = [];
      lang = line.replace(/^\s*```/, "").trim();
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      flushBullets();
      blocks.push({
        kind: `h${h[1].length}` as "h1" | "h2" | "h3",
        text: h[2],
      });
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      flushPara();
      bullets.push(line.replace(/^[-*]\s+/, ""));
      continue;
    }
    if (line.trim() === "") {
      flushPara();
      flushBullets();
      continue;
    }
    flushBullets();
    para.push(line.trim());
  }
  if (code !== null) blocks.push({ kind: "code", text: code.join("\n"), lang });
  flushPara();
  flushBullets();
  return blocks;
}

const INLINE_SPLIT = /(\[\[[^\]]+\]\]|\*\*[^*]+\*\*)/g;

function renderInline(
  text: string,
  onWiki: (title: string) => void,
  keyBase: string
): ReactNode[] {
  return text
    .split(INLINE_SPLIT)
    .filter(p => p !== "")
    .map((part, i) => {
      const key = `${keyBase}:${i}`;
      if (part.startsWith("[[") && part.endsWith("]]")) {
        const title = part.slice(2, -2).trim();
        return (
          <button
            key={key}
            type="button"
            onClick={() => onWiki(title)}
            className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-[rgba(70,191,114,0.3)] bg-[rgba(70,191,114,0.12)] px-1.5 py-px align-baseline text-[0.92em] font-medium text-[var(--ds-accent)] transition-colors hover:bg-[rgba(70,191,114,0.2)] hover:text-[var(--ds-accent)]"
          >
            <Link2 className="size-3 shrink-0" aria-hidden />
            {title}
          </button>
        );
      }
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        return (
          <strong key={key} className="font-semibold text-white/90">
            {part.slice(2, -2)}
          </strong>
        );
      }
      return <span key={key}>{part}</span>;
    });
}

function MarkdownView({
  content,
  onWiki,
}: {
  content: string;
  onWiki: (title: string) => void;
}) {
  const blocks = useMemo(() => parseMarkdown(content), [content]);
  return (
    <div className="px-6 py-5 text-[13.5px] leading-relaxed text-white/75">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "h1":
            return (
              <h3
                key={i}
                className="mt-6 mb-2 text-lg font-semibold text-white/90 first:mt-0"
              >
                {renderInline(b.text, onWiki, `h1-${i}`)}
              </h3>
            );
          case "h2":
            return (
              <h4
                key={i}
                className="mt-6 mb-2 text-base font-semibold text-white/85 first:mt-0"
              >
                {renderInline(b.text, onWiki, `h2-${i}`)}
              </h4>
            );
          case "h3":
            return (
              <h5
                key={i}
                className="mt-5 mb-1.5 text-[13px] font-semibold tracking-wide text-white/70 first:mt-0"
              >
                {renderInline(b.text, onWiki, `h3-${i}`)}
              </h5>
            );
          case "ul":
            return (
              <ul key={i} className="my-2.5 list-disc space-y-1 pl-5">
                {b.items.map((item, j) => (
                  <li key={j}>{renderInline(item, onWiki, `ul${i}-${j}`)}</li>
                ))}
              </ul>
            );
          case "code":
            return (
              <div key={i} className="my-3">
                {b.lang && (
                  <div className="mb-1 text-[10px] tracking-wider text-white/30 uppercase">
                    {b.lang}
                  </div>
                )}
                <pre className="overflow-x-auto rounded-lg border border-[var(--ds-line)] bg-black/40 p-3 font-mono text-xs leading-relaxed text-[var(--ds-ink-3)]">
                  <code>{b.text}</code>
                </pre>
              </div>
            );
          default:
            return (
              <p key={i} className="my-2 first:mt-0">
                {renderInline(b.text, onWiki, `p-${i}`)}
              </p>
            );
        }
      })}
    </div>
  );
}

// ---- constellation (circular SVG of the link graph) ---------------------------

const CX = 130;
const CY = 124;
const R = 94;

function Constellation({
  graph,
  activeId,
  onSelect,
}: {
  graph: NebulaGraph;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const layout = useMemo(() => {
    const sorted = [...graph.nodes].sort(
      (a, b) => b.degree - a.degree || a.title.localeCompare(b.title)
    );
    const maxDeg = Math.max(1, ...sorted.map(n => n.degree));
    const placed = sorted.map((node, i) => {
      const angle = (i / sorted.length) * Math.PI * 2 - Math.PI / 2;
      const t = Math.min(1, node.degree / maxDeg);
      return {
        node,
        x: CX + R * Math.cos(angle),
        y: CY + R * Math.sin(angle),
        r: 4.5 + 7 * t,
        o: 0.4 + 0.6 * t,
      };
    });
    const byId = new Map(placed.map(p => [p.node.id, p]));
    return { placed, byId };
  }, [graph]);

  return (
    <svg
      viewBox="0 0 260 248"
      className="h-full w-full"
      role="img"
      aria-label="Note link graph"
    >
      {graph.edges.map((e, i) => {
        const a = layout.byId.get(e.src);
        const b = layout.byId.get(e.dst);
        if (!a || !b) return null;
        return (
          <line
            key={`${e.src}-${e.dst}-${i}`}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke="rgba(212,212,212,0.25)"
            strokeWidth={1}
          />
        );
      })}
      {layout.placed.map(p => {
        const active = p.node.id === activeId;
        return (
          <g
            key={p.node.id}
            className="cursor-pointer"
            onClick={() => onSelect(p.node.id)}
          >
            <title>
              {p.node.title} — degree {p.node.degree}
            </title>
            <circle cx={p.x} cy={p.y} r={p.r + 7} fill="transparent" />
            <circle
              cx={p.x}
              cy={p.y}
              r={p.r}
              fill="#46bf72"
              fillOpacity={p.o}
              stroke={active ? "#f2f2f2" : "rgba(70,191,114,0.55)"}
              strokeWidth={active ? 1.6 : 1}
            />
            <text
              x={p.x}
              y={p.y + p.r + 9}
              textAnchor="middle"
              fontSize={8}
              fill={active ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.45)"}
            >
              {clip(p.node.title, 16)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---- the page -----------------------------------------------------------------

/** The shared shell: stable left sidebar + the Nebula surface. The sidebar
 *  is the same column on every workspace — only Bots swaps its body. */
function Shell({ children }: { children: React.ReactNode }) {
  const sidebarOpen = useUi(s => s.sidebarOpen);
  return (
    <div className="app-root">
      <div className={`app${sidebarOpen ? "" : " sb-closed"}`}>
        <Sidebar open={sidebarOpen} />
        <main className="min-w-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}

export default function NebulaPage() {
  const [notes, setNotes] = useState<
    Awaited<ReturnType<typeof listNotes>> | null
  >(null);
  const [graph, setGraph] = useState<NebulaGraph | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [note, setNote] = useState<NebulaNote | null>(null);
  const [noteLoading, setNoteLoading] = useState(false);
  const loadGen = useRef(0);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  const [distilling, setDistilling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createTitle, setCreateTitle] = useState("");
  const [createContent, setCreateContent] = useState("");
  const [creating, setCreating] = useState(false);

  // -- data sync --------------------------------------------------------------

  const refreshMeta = useCallback(async () => {
    try {
      const [ns, g] = await Promise.all([listNotes(), getGraph()]);
      setNotes(ns);
      setGraph(g);
      setLoadErr(null);
    } catch (e) {
      setLoadErr(errMsg(e));
      toast.error("Nebula sync failed", { description: errMsg(e) });
    }
  }, []);

  useEffect(() => {
    void refreshMeta();
  }, [refreshMeta]);

  const select = useCallback(async (id: string) => {
    const gen = ++loadGen.current;
    setSelectedId(id);
    setEditing(false);
    setNoteLoading(true);
    try {
      const n = await getNote(id);
      if (loadGen.current === gen) setNote(n);
    } catch (e) {
      if (loadGen.current === gen) {
        setNote(null);
        toast.error("Could not load note", { description: errMsg(e) });
      }
    } finally {
      if (loadGen.current === gen) setNoteLoading(false);
    }
  }, []);

  // first load lands on the most recently touched note
  useEffect(() => {
    if (notes && notes.length > 0 && !selectedId) void select(notes[0].id);
  }, [notes, selectedId, select]);

  const visible = useMemo(() => {
    if (!notes) return [];
    const q = query.trim().toLowerCase();
    if (!q) return notes;
    return notes.filter(
      n =>
        n.title.toLowerCase().includes(q) ||
        n.tags.some(t => t.toLowerCase().includes(q))
    );
  }, [notes, query]);

  // -- actions ------------------------------------------------------------------

  const openWiki = (title: string) => {
    const match =
      notes?.find(n => n.title === title) ??
      notes?.find(
        n => n.title.toLowerCase() === title.trim().toLowerCase()
      );
    if (match) {
      void select(match.id);
      return;
    }
    toast.info(`No note titled “${title}”`, {
      description: "Create it to grow this constellation.",
      action: {
        label: "Create",
        onClick: () => {
          setCreateTitle(title);
          setCreateContent("");
          setCreateOpen(true);
        },
      },
    });
  };

  const openCreate = () => {
    setCreateTitle("");
    setCreateContent("");
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    const title = createTitle.trim();
    if (!title || creating) return;
    setCreating(true);
    try {
      const meta = await createNote(title, createContent);
      toast.success("Note created", { description: title });
      setCreateOpen(false);
      setCreateTitle("");
      setCreateContent("");
      await refreshMeta();
      void select(meta.id);
    } catch (e) {
      toast.error("Could not create note", { description: errMsg(e) });
    } finally {
      setCreating(false);
    }
  };

  const startEdit = () => {
    if (!note) return;
    setDraft(note.content);
    setEditing(true);
  };

  const saveEdit = async () => {
    if (!note || saving) return;
    setSaving(true);
    try {
      await updateNote(note.id, draft);
      toast.success("Note saved", { description: note.title });
      setEditing(false);
      setNote(await getNote(note.id));
      void refreshMeta();
    } catch (e) {
      toast.error("Could not save note", { description: errMsg(e) });
    } finally {
      setSaving(false);
    }
  };

  const removeNote = async () => {
    if (!note || deleting) return;
    setDeleting(true);
    try {
      await deleteNote(note.id);
      toast.success("Note deleted", { description: note.title });
      setConfirmDelete(false);
      loadGen.current++;
      setNote(null);
      setSelectedId(null);
      void refreshMeta();
    } catch (e) {
      toast.error("Could not delete note", { description: errMsg(e) });
    } finally {
      setDeleting(false);
    }
  };

  const runDistill = async () => {
    if (distilling) return;
    setDistilling(true);
    try {
      const r = await distill();
      if (r.created.length > 0) {
        toast.success(
          `Distilled ${r.created.length} ${r.created.length === 1 ? "note" : "notes"}`,
          { description: r.created.join(" · ") }
        );
      } else {
        toast.info("Nothing to distill", {
          description: "No new experiences condensed into notes.",
        });
      }
      void refreshMeta();
    } catch (e) {
      toast.error("Distill failed", { description: errMsg(e) });
    } finally {
      setDistilling(false);
    }
  };

  const refreshGraph = async () => {
    try {
      setGraph(await getGraph());
    } catch (e) {
      toast.error("Could not refresh constellation", {
        description: errMsg(e),
      });
    }
  };

  // -- render --------------------------------------------------------------------

  const isEmpty = notes !== null && notes.length === 0;
  const initialLoading = notes === null && loadErr === null;

  const distillButton = (extra?: { className?: string }) => (
    <Button
      size="sm"
      variant="outline"
      disabled={distilling}
      onClick={() => void runDistill()}
      className={cn(GHOST_BTN, extra?.className)}
    >
      <Sparkle className="size-3.5 text-[var(--ds-accent)]" aria-hidden />
      {distilling ? "Distilling…" : "Distill session"}
    </Button>
  );

  const newNoteButton = () => (
    <Button size="sm" onClick={openCreate} className={cn(ACCENT_BTN, "text-xs")}>
      <Plus className="size-3.5" aria-hidden />
      New note
    </Button>
  );

  if (initialLoading) {
    return (
      <Shell>
        <div className="flex h-full gap-3 p-3">
          <div className={cn(PANEL, "w-72 animate-pulse rounded-xl border bg-white/[0.03]")} />
          <div className={cn(PANEL, "flex-1 animate-pulse rounded-xl border bg-white/[0.03]")} />
          <div className={cn(PANEL, "w-80 animate-pulse rounded-xl border bg-white/[0.03]")} />
        </div>
      </Shell>
    );
  }

  if (notes === null) {
    return (
      <Shell>
        <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
          <AlertTriangle className="size-6 text-[#ffb454]" aria-hidden />
          <p className="max-w-md text-sm text-white/60">{loadErr}</p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void refreshMeta()}
            className={GHOST_BTN}
          >
            <RefreshCw className="size-3.5" aria-hidden />
            Retry
          </Button>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
    <div className="flex h-full gap-3 overflow-hidden p-3 text-[var(--ds-ink)]">
      {isEmpty ? (
        /* ---- empty-state hero ------------------------------------------------ */
        <div className="flex flex-1 flex-col items-center justify-center gap-4 text-center">
          <div
            className={cn(
              "flex size-14 items-center justify-center rounded-2xl border border-[var(--ds-line)] bg-white/[0.03]"
            )}
          >
            <Sparkles className="size-6 text-[var(--ds-accent)]" aria-hidden />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-white/90">
              Your nebula is empty
            </h1>
            <p className="mt-1.5 max-w-md text-sm text-white/45">
              Distill a session into notes (Dust → Stars) or write your first
              note.
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            {distillButton({ className: "text-sm" })}
            {newNoteButton()}
          </div>
        </div>
      ) : (
        <>
          {/* ---- LEFT: header + search + note list --------------------------- */}
          <aside className={cn(PANEL, "flex w-72 shrink-0 flex-col")}>
            <div className="px-3.5 pt-3.5">
              <div className="flex items-center gap-2">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-[rgba(70,191,114,0.12)]">
                  <Sparkles className="size-4 text-[var(--ds-accent)]" aria-hidden />
                </span>
                <h1 className="text-sm font-semibold text-white/90">Nebula</h1>
              </div>
              <p className="mt-1.5 text-[11px] leading-snug text-white/40">
                Memory — where experiences condense into stars
              </p>
              <div className="mt-3 flex gap-2">
                {distillButton()}
                {newNoteButton()}
              </div>
            </div>
            <Separator className="mt-3.5 bg-[var(--ds-line)]" />
            <div className="px-3 py-2.5">
              <div className="relative">
                <Search
                  className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-white/30"
                  aria-hidden
                />
                <Input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Search title or tag"
                  className="h-8 border-[var(--ds-line)] bg-black/20 pl-8 text-xs text-white/80 placeholder:text-white/25 focus-visible:ring-[rgba(70,191,114,0.4)]"
                />
              </div>
            </div>
            <ScrollArea className="min-h-0 flex-1 pb-2">
              <div className="space-y-1 px-2">
                {visible.map(n => (
                  <button
                    key={n.id}
                    type="button"
                    onClick={() => void select(n.id)}
                    className={cn(
                      "w-full rounded-lg border px-2.5 py-2 text-left transition-colors",
                      n.id === selectedId
                        ? "border-[rgba(70,191,114,0.4)] bg-[rgba(70,191,114,0.12)]"
                        : "border-transparent hover:bg-white/[0.04]"
                    )}
                  >
                    <div className="truncate text-[13px] font-medium text-white/85">
                      {n.title}
                    </div>
                    {n.tags.length > 0 && (
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        {n.tags.slice(0, 3).map(t => (
                          <Badge
                            key={t}
                            variant="outline"
                            className="border-[rgba(70,191,114,0.3)] bg-[rgba(70,191,114,0.12)] px-1.5 py-0 text-[10px] text-[var(--ds-accent)]"
                          >
                            {t}
                          </Badge>
                        ))}
                      </div>
                    )}
                    <div className="mt-1 flex items-center justify-between text-[10px] text-white/35">
                      <span className="font-mono">
                        →{n.links} ⇐{n.backlinks}
                      </span>
                      <span>{relTime(n.updated_at)}</span>
                    </div>
                  </button>
                ))}
                {visible.length === 0 && (
                  <p className="px-2 py-6 text-center text-xs text-white/30">
                    No notes match “{query.trim()}”
                  </p>
                )}
              </div>
            </ScrollArea>
          </aside>

          {/* ---- CENTER: note view / editor ---------------------------------- */}
          <main className={cn(PANEL, "flex min-w-0 flex-1 flex-col")}>
            {noteLoading ? (
              <div className="flex-1 animate-pulse p-6">
                <div className="h-6 w-1/3 rounded bg-white/[0.06]" />
                <div className="mt-4 space-y-2.5">
                  {[92, 100, 78, 96, 60].map((w, i) => (
                    <div
                      key={i}
                      className="h-3.5 rounded bg-white/[0.04]"
                      style={{ width: `${w}%` }}
                    />
                  ))}
                </div>
              </div>
            ) : note === null ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
                <Sparkles className="size-5 text-white/15" aria-hidden />
                <p className="text-sm text-white/35">Select a note to read it</p>
              </div>
            ) : editing ? (
              <>
                <div className="flex items-center justify-between gap-3 border-b border-[var(--ds-line)] px-5 py-3">
                  <div className="min-w-0">
                    <p className="text-[10px] tracking-widest text-[var(--ds-accent)]/80 uppercase">
                      Editing markdown
                    </p>
                    <h2 className="truncate text-sm font-medium text-white/80">
                      {note.title}
                    </h2>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditing(false)}
                      disabled={saving}
                      className="text-xs text-white/60 hover:text-white/90"
                    >
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => void saveEdit()}
                      disabled={saving}
                      className={cn(ACCENT_BTN, "text-xs")}
                    >
                      <Save className="size-3.5" aria-hidden />
                      {saving ? "Saving…" : "Save"}
                    </Button>
                  </div>
                </div>
                <div className="min-h-0 flex-1 p-3">
                  <Textarea
                    value={draft}
                    onChange={e => setDraft(e.target.value)}
                    spellCheck={false}
                    autoFocus
                    className="h-full w-full resize-none border-[var(--ds-line)] bg-black/25 font-mono text-[13px] leading-relaxed text-white/85 focus-visible:ring-[rgba(70,191,114,0.3)]"
                  />
                </div>
              </>
            ) : (
              <>
                <header className="flex items-start justify-between gap-3 border-b border-[var(--ds-line)] px-5 py-3.5">
                  <div className="min-w-0">
                    <h2 className="truncate text-lg font-semibold text-white/90">
                      {note.title}
                    </h2>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-white/40">
                      {note.tags.length > 0 && (
                        <span className="flex flex-wrap items-center gap-1">
                          {note.tags.map(t => (
                            <Badge
                              key={t}
                              variant="outline"
                              className="border-[rgba(70,191,114,0.3)] bg-[rgba(70,191,114,0.12)] px-1.5 py-0 text-[10px] text-[var(--ds-accent)]"
                            >
                              {t}
                            </Badge>
                          ))}
                        </span>
                      )}
                      <span className="font-mono">
                        → {note.links} {note.links === 1 ? "link" : "links"}
                      </span>
                      <span className="font-mono">
                        ⇐ {note.backlinks}{" "}
                        {note.backlinks === 1 ? "backlink" : "backlinks"}
                      </span>
                      <span>updated {relTime(note.updated_at)}</span>
                    </div>
                  </div>
                  <TooltipProvider delayDuration={150}>
                    <div className="flex shrink-0 items-center gap-1">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={startEdit}
                            aria-label="Edit note"
                          >
                            <Pencil className="size-3.5 text-white/50" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Edit</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => setConfirmDelete(true)}
                            aria-label="Delete note"
                            className="hover:bg-[#ff6b81]/10"
                          >
                            <Trash2 className="size-3.5 text-white/50 hover:text-[#ff6b81]" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Delete</TooltipContent>
                      </Tooltip>
                    </div>
                  </TooltipProvider>
                </header>
                <ScrollArea className="min-h-0 flex-1">
                  <MarkdownView
                    content={note.content}
                    onWiki={openWiki}
                  />
                </ScrollArea>
              </>
            )}
          </main>

          {/* ---- RIGHT: constellation graph ----------------------------------- */}
          <aside className={cn(PANEL, "flex w-80 shrink-0 flex-col")}>
            <div className="flex items-center justify-between border-b border-[var(--ds-line)] px-4 py-3">
              <div className="flex items-center gap-1.5">
                <Orbit className="size-3.5 text-[var(--ds-accent)]" aria-hidden />
                <h2 className="text-xs font-semibold tracking-widest text-white/60 uppercase">
                  Constellation
                </h2>
              </div>
              <TooltipProvider delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => void refreshGraph()}
                      aria-label="Refresh graph"
                    >
                      <RefreshCw className="size-3.5 text-white/40" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Refresh</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
            <div className="min-h-0 flex-1 p-2">
              {graph === null ? (
                <div className="flex h-full items-center justify-center">
                  <Loader2
                    className="size-4 animate-spin text-white/25"
                    aria-hidden
                  />
                </div>
              ) : graph.nodes.length < 2 ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center">
                  <Sparkles className="size-4 text-white/15" aria-hidden />
                  <p className="text-xs leading-relaxed text-white/35">
                    Notes with{" "}
                    <span className="font-mono text-[var(--ds-accent)]">[[links]]</span>{" "}
                    will connect here
                  </p>
                </div>
              ) : (
                <Constellation
                  graph={graph}
                  activeId={selectedId}
                  onSelect={id => void select(id)}
                />
              )}
            </div>
            <Separator className="bg-[var(--ds-line)]" />
            <div className="px-4 py-2.5 text-[10px] text-white/30">
              {graph?.nodes.length ?? 0} stars · {graph?.edges.length ?? 0}{" "}
              links
            </div>
          </aside>
        </>
      )}

      {/* ---- new-note dialog ------------------------------------------------ */}
      <Dialog
        open={createOpen}
        onOpenChange={open => {
          if (!open) setCreateOpen(false);
        }}
      >
        <DialogContent className="border-[var(--ds-line)] bg-[#2b2b2b] sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-white/90">New note</DialogTitle>
            <DialogDescription>
              Write in markdown — use <span className="font-mono">[[Title]]</span>{" "}
              to link other notes.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={createTitle}
            onChange={e => setCreateTitle(e.target.value)}
            placeholder="Note title"
            autoFocus
            className="border-[var(--ds-line)] bg-black/25 text-sm text-white/85 placeholder:text-white/25 focus-visible:ring-[rgba(70,191,114,0.4)]"
          />
          <Textarea
            value={createContent}
            onChange={e => setCreateContent(e.target.value)}
            placeholder={"# Heading\n\nIdeas, observations, decisions…"}
            rows={9}
            className="border-[var(--ds-line)] bg-black/25 font-mono text-[13px] leading-relaxed text-white/85 placeholder:text-white/25 focus-visible:ring-[rgba(70,191,114,0.4)]"
          />
          <DialogFooter>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setCreateOpen(false)}
              disabled={creating}
              className="text-xs text-white/60 hover:text-white/90"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => void submitCreate()}
              disabled={creating || createTitle.trim() === ""}
              className={cn(ACCENT_BTN, "text-xs")}
            >
              <Plus className="size-3.5" aria-hidden />
              {creating ? "Creating…" : "Create note"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- delete confirm dialog ------------------------------------------- */}
      <Dialog
        open={confirmDelete}
        onOpenChange={open => {
          if (!open) setConfirmDelete(false);
        }}
      >
        <DialogContent className="border-[var(--ds-line)] bg-[#2b2b2b] sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-white/90">Delete note</DialogTitle>
            <DialogDescription>
              Delete “{note?.title ?? ""}”? Notes linking here will dangle —
              this cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmDelete(false)}
              disabled={deleting}
              className="text-xs text-white/60 hover:text-white/90"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => void removeNote()}
              disabled={deleting}
              className="text-xs"
            >
              <Trash2 className="size-3.5" aria-hidden />
              {deleting ? "Deleting…" : "Delete note"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
    </Shell>
  );
}
