/**
 * components/desktop/DesignWorkspace.tsx — the DESIGN tool's real
 * workspace, rendered on the Universe design system. Every number, name
 * and pixel here comes from a real source:
 *
 *   · session files  — the agent's actual deliverables on disk
 *                      (orbit-coffee.html …) served by the bridge
 *   · projects       — the open-design daemon's real project store (:7456)
 *   · skills         — the daemon's real skill corpus (169 entries), the
 *                      same corpus the agent drives through StudioDesign
 *   · recolor        — a REAL palette swap that rewrites the file
 *   · comments       — real persisted design comments (JSONL)
 *
 * The preview renders the real bytes; a stopped daemon is shown honestly.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Code2,
  Eye,
  Loader2,
  MessageSquare,
  Palette,
  Plus,
  RefreshCw,
  Search,
  MousePointer2,
} from "lucide-react";
import { toast } from "sonner";
import {
  addDesignComment,
  createDesignProject,
  designRecolor,
  getDesignFileSource,
  getDesignOverview,
  listDesignComments,
  type DesignComment,
  type DesignOverview,
} from "@/lib/studioApi";

/* Real, opinionated palette swaps — each is a full accent→accent mapping
   applied to the file's actual colors. */
const PALETTES: ReadonlyArray<{ label: string; from: string; to: string }> = [
  { label: "Violet → Amber", from: "bb86fc", to: "ff9f1c" },
  { label: "Violet → Emerald", from: "bb86fc", to: "2ec4b6" },
  { label: "Violet → Crimson", from: "bb86fc", to: "e63946" },
  { label: "Amber → Violet", from: "ff9f1c", to: "bb86fc" },
  { label: "White ink → Mint", from: "ffffff", to: "b8f2e6" },
];

type Tool = "select" | "comments" | "recolor";

export default function DesignWorkspace() {
  const [overview, setOverview] = useState<DesignOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<string | null>(null);
  const [previewHtml, setPreviewHtml] = useState<string>("");
  const [previewErr, setPreviewErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [codeView, setCodeView] = useState(false);
  const [source, setSource] = useState("");
  const [tool, setTool] = useState<Tool>("select");
  const [comments, setComments] = useState<DesignComment[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [skillQuery, setSkillQuery] = useState("");
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [projName, setProjName] = useState("");
  const previewNonce = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const ov = await getDesignOverview();
      setOverview(ov);
      setFile(prev => prev ?? ov.sessionFiles[0]?.name ?? null);
    } catch {
      setOverview(prev => prev);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // real preview bytes: fetched with the bridge auth, rendered via srcdoc
  const loadPreview = useCallback(async (name: string) => {
    const nonce = ++previewNonce.current;
    setPreviewErr("");
    setBusy(true);
    try {
      const { bridgeConfig } = await import("@/lib/api");
      const { url, token } = await bridgeConfig();
      const res = await fetch(
        `${url}/api/studio/design/files/${encodeURIComponent(name)}/preview`,
        { headers: token ? { Authorization: `Bearer ${token}` } : undefined }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const html = await res.text();
      if (nonce === previewNonce.current) setPreviewHtml(html);
    } catch (e) {
      if (nonce === previewNonce.current) {
        setPreviewHtml("");
        setPreviewErr(e instanceof Error ? e.message : "preview failed");
      }
    } finally {
      if (nonce === previewNonce.current) setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (file) void loadPreview(file);
  }, [file, loadPreview]);

  useEffect(() => {
    if (!file) return;
    void listDesignComments(file).then(setComments).catch(() => setComments([]));
  }, [file]);

  const openCode = useCallback(async (name: string) => {
    try {
      setSource(await getDesignFileSource(name));
    } catch {
      setSource("// source unavailable");
    }
  }, []);

  const applyPalette = useCallback(
    async (p: { label: string; from: string; to: string }) => {
      if (!file) return;
      setBusy(true);
      try {
        const { replaced } = await designRecolor(file, { [p.from]: p.to });
        await loadPreview(file);
        toast.success(`${p.label}: ${replaced} color(s) swapped in ${file}`);
      } catch (e) {
        toast.error("Recolor failed", {
          description: e instanceof Error ? e.message : undefined,
        });
      } finally {
        setBusy(false);
      }
    },
    [file, loadPreview]
  );

  const addComment = useCallback(async () => {
    if (!file || !commentDraft.trim()) return;
    try {
      const c = await addDesignComment(file, commentDraft.trim(), "canvas");
      if (c) setComments(prev => [...prev, c]);
      setCommentDraft("");
    } catch (e) {
      toast.error("Comment failed", {
        description: e instanceof Error ? e.message : undefined,
      });
    }
  }, [file, commentDraft]);

  const newProject = useCallback(async () => {
    const name = projName.trim();
    if (!name) return;
    try {
      const p = await createDesignProject(name);
      if (p) {
        setProjName("");
        toast.success(`Project "${p.name}" created`);
        void refresh();
      }
    } catch (e) {
      toast.error("Could not create the project", {
        description: e instanceof Error ? e.message : "daemon unreachable",
      });
    }
  }, [projName, refresh]);

  const skills = useMemo(() => {
    const all = overview?.skills ?? [];
    const q = skillQuery.trim().toLowerCase();
    return (q ? all.filter(s => s.name.toLowerCase().includes(q)) : all)
      .slice(0, 60);
  }, [overview, skillQuery]);

  /* ---- render ------------------------------------------------------------- */

  if (loading) {
    return (
      <div className="dw dw-loading">
        <Loader2 className="size-4 animate-spin" /> Reading the design
        surfaces…
      </div>
    );
  }

  return (
    <div className="dw">
      {/* left mini-rail — NEVER hides, same Universe tokens */}
      <aside className="dw-side">
        <section className="dw-side-sec">
          <h4>Designs</h4>
          {(overview?.sessionFiles.length ?? 0) === 0 && (
            <p className="dw-empty">
              No deliverables yet — ask Universe to design something.
            </p>
          )}
          <ul className="dw-list">
            {overview?.sessionFiles.map(f => (
              <li key={f.name}>
                <button
                  type="button"
                  className={`dw-item${file === f.name ? " on" : ""}`}
                  onClick={() => {
                    setFile(f.name);
                    setCodeView(false);
                  }}
                >
                  <span className="dw-item-name">{f.name}</span>
                  <span className="dw-item-meta">
                    {(f.size / 1024).toFixed(1)} kB
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="dw-side-sec">
          <h4>Projects</h4>
          <div className="dw-proj-add">
            <input
              value={projName}
              onChange={e => setProjName(e.target.value)}
              onKeyDown={e => e.key === "Enter" && void newProject()}
              placeholder="New project name…"
              aria-label="New project name"
            />
            <button
              type="button"
              onClick={() => void newProject()}
              disabled={!projName.trim()}
              title="Create a real project in open-design"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
          <ul className="dw-list">
            {overview?.projects.map(p => (
              <li key={p.id}>
                <div className="dw-item static" title={p.id}>
                  <span className="dw-item-name">{p.name}</span>
                  <span className="dw-item-meta">daemon</span>
                </div>
              </li>
            ))}
          </ul>
          {overview && !overview.daemon && (
            <p className="dw-empty">
              open-design daemon is not running — start it from the dock head.
            </p>
          )}
        </section>

        <section className="dw-side-sec dw-side-skills">
          <button
            type="button"
            className="dw-skills-head"
            onClick={() => setSkillsOpen(o => !o)}
            aria-expanded={skillsOpen}
          >
            <h4>Skills</h4>
            <span className="dw-count">{overview?.skills.length ?? 0}</span>
          </button>
          {skillsOpen && (
            <>
              <div className="dw-search">
                <Search className="size-3" />
                <input
                  value={skillQuery}
                  onChange={e => setSkillQuery(e.target.value)}
                  placeholder="Search skills…"
                  aria-label="Search open-design skills"
                />
              </div>
              <ul className="dw-list dw-skills">
                {skills.map(s => (
                  <li key={s.id} title={s.description ?? s.name}>
                    <span className="dw-item-name">{s.name}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </aside>

      {/* right: toolbar + real preview */}
      <div className="dw-main">
        <div className="dw-toolbar">
          <button
            type="button"
            className={`dw-tool${tool === "select" ? " on" : ""}`}
            onClick={() => setTool("select")}
            title="Select & inspect"
          >
            <MousePointer2 className="size-3.5" /> Select
          </button>
          <button
            type="button"
            className={`dw-tool${tool === "comments" ? " on" : ""}`}
            onClick={() => setTool("comments")}
            title="Comment on this design"
          >
            <MessageSquare className="size-3.5" /> Comments
            {comments.length > 0 && (
              <span className="dw-badge">{comments.length}</span>
            )}
          </button>
          <div className={`dw-tool dw-palette${tool === "recolor" ? " on" : ""}`}>
            <button
              type="button"
              className="dw-tool-btn"
              onClick={() => setTool("recolor")}
              title="Swap the palette — rewrites the real file"
            >
              <Palette className="size-3.5" /> Recolor
            </button>
            {tool === "recolor" && (
              <div className="dw-palette-menu">
                {PALETTES.map(p => (
                  <button
                    key={p.label}
                    type="button"
                    disabled={busy}
                    onClick={() => void applyPalette(p)}
                  >
                    <i style={{ background: `#${p.to}` }} />
                    {p.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <span className="dw-sp" />
          <button
            type="button"
            className={`dw-tool${codeView ? " on" : ""}`}
            disabled={!file}
            onClick={() => {
              if (!codeView && file) void openCode(file);
              setCodeView(v => !v);
            }}
            title="Inspect the real source"
          >
            {codeView ? (
              <Eye className="size-3.5" />
            ) : (
              <Code2 className="size-3.5" />
            )}
            {codeView ? "Preview" : "Code"}
          </button>
          <button
            type="button"
            className="dw-tool"
            disabled={busy || !file}
            onClick={() => file && void loadPreview(file)}
            title="Reload the real file"
          >
            <RefreshCw className={`size-3.5${busy ? " spin" : ""}`} /> Refresh
          </button>
        </div>

        {tool === "comments" && (
          <div className="dw-comments">
            {comments.length === 0 && (
              <p className="dw-empty">No comments on this design yet.</p>
            )}
            {comments.map(c => (
              <div key={c.id} className="dw-comment">
                <MessageSquare className="size-3" />
                <span>{c.body}</span>
                <time>
                  {new Date(c.ts * 1000).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </time>
              </div>
            ))}
            <div className="dw-comment-add">
              <input
                value={commentDraft}
                onChange={e => setCommentDraft(e.target.value)}
                onKeyDown={e => e.key === "Enter" && void addComment()}
                placeholder="Add a comment…"
                aria-label="Comment text"
              />
              <button
                type="button"
                onClick={() => void addComment()}
                disabled={!commentDraft.trim()}
              >
                Post
              </button>
            </div>
          </div>
        )}

        <div className="dw-stage">
          {!file ? (
            <div className="dw-loading">
              <p>No design selected.</p>
              <p className="dw-empty">
                Pick a session file, or ask Universe in the chat — “Design a
                landing page for …” — and it lands here the moment it's
                written.
              </p>
            </div>
          ) : codeView ? (
            <pre className="dw-code">{source || "// loading…"}</pre>
          ) : previewErr ? (
            <div className="dw-loading">
              <p>Preview failed — {previewErr}</p>
            </div>
          ) : (
            <iframe
              title={`Design preview — ${file}`}
              srcDoc={previewHtml}
              sandbox="allow-same-origin"
              className="dw-frame"
            />
          )}
        </div>
      </div>
    </div>
  );
}
