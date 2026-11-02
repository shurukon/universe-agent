/**
 * components/desktop/ModelPicker.tsx — the model chip inside the chat
 * composer (2026-09 redesign): model choice lives HERE, not in Settings.
 *
 * One model for everything: the universe agent runs the conversation and the
 * subagents inherit the same routing — so the chip shows the connected
 * provider and its full live model list (searchable, scrollable), and
 * picking one re-routes every agent in a single call. Settings → Providers
 * still manages KEYS (connect/remove); the Settings → Subagents page is a
 * read-only roster.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { useLocation } from "wouter";
import { useChat } from "@/stores/chat";
import { api } from "@/lib/api";

export default function ModelPicker() {
  const hasProvider = useChat(s => s.providerServing !== "none" && s.providerServing !== "loading");
  const provider = useChat(s => s.activeProvider);
  const model = useChat(s => s.activeModel);
  const switchModel = useChat(s => s.switchModel);
  const [, navigate] = useLocation();

  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<string[] | null>(null);
  const [query, setQuery] = useState("");
  const [switching, setSwitching] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // close on outside click / Esc — the popover owns focus while open
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

  // the live model list: cached by api.providerModels (30s TTL + the
  // bridge's own 2-min probe cache) so re-opening is instant
  useEffect(() => {
    if (!open || !provider || models !== null) return;
    api
      .getProviderModels(provider)
      .then(ms => setModels(ms))
      .catch(() => setModels([]));
  }, [open, provider, models]);

  const filtered = useMemo(() => {
    const list = models ?? [];
    const q = query.trim().toLowerCase();
    return q ? list.filter(m => m.toLowerCase().includes(q)) : list;
  }, [models, query]);

  if (!hasProvider) return null;

  const pick = async (m: string) => {
    if (m === model || switching) return;
    setSwitching(true);
    const ok = await switchModel(provider, m);
    setSwitching(false);
    if (ok) {
      setOpen(false);
      setQuery("");
    }
  };

  return (
    <div className="mp-root" ref={rootRef}>
      {/* The trigger shows the CURRENT model once one is picked — the
          user must read it at a glance, not hover for it. Before any
          pick it reads exactly like ZCode's idle slot ("Manage models").
          Long ids truncate; the full id + provider stay in the tooltip. */}
      <button
        className="mp-chip"
        onClick={() => {
          setOpen(o => !o);
          if (!open) {
            setQuery("");
            setModels(null); // force a fresh list each open (cache is in api)
          }
        }}
        title={
          model
            ? `Model: ${model} on ${provider} — click to change (applies to the main agent and every subagent)`
            : "Pick the model"
        }
        aria-label="Change model"
        aria-expanded={open}
      >
        <span className="mp-model">{model || "Manage models"}</span>
        <ChevronDown size={14} strokeWidth={2} className={open ? "mp-chev open" : "mp-chev"} />
      </button>

      {open && (
        <div className="mp-pop" role="listbox" aria-label="Models">
          <div className="mp-search">
            <Search size={12} strokeWidth={2} />
            <input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={`Search models...`}
              aria-label="Search models"
              // Enter picks the first match — the fastest path
              onKeyDown={e => {
                if (e.key === "Enter" && filtered.length > 0) {
                  e.preventDefault();
                  void pick(filtered[0]);
                }
              }}
            />
            {models === null && <span className="mp-loading">loading…</span>}
          </div>
          <div className="mp-list">
            {(models ?? []).length === 0 && models !== null && (
              <p className="mp-empty">
                {provider} did not return a model list — it may forbid
                listing. Type an id and press Enter.
              </p>
            )}
            <div className="mp-rowhead">{provider}</div>
            {filtered.map(m => (
              <button
                key={m}
                className={`mp-row${m === model ? " on" : ""}`}
                role="option"
                aria-selected={m === model}
                onClick={() => void pick(m)}
              >
                <span className="mp-rowname">{m}</span>
                {m === model && <Check size={12} strokeWidth={2.4} />}
              </button>
            ))}
            {models !== null &&
              query.trim() &&
              !filtered.some(m => m.toLowerCase() === query.trim().toLowerCase()) && (
                <button
                  className="mp-row mp-freetext"
                  onClick={() => void pick(query.trim())}
                  title="Use this model id"
                >
                  <span className="mp-rowname">Use “{query.trim()}”</span>
                </button>
              )}
          </div>
          {/* ZCode's model menu ends with a manage-models row — the
              provider/keys page in Universe's settings */}
          <button
            type="button"
            className="mp-manage"
            onClick={() => {
              setOpen(false);
              navigate("/settings");
            }}
            role="menuitem"
          >
            <span>Manage models</span>
          </button>
        </div>
      )}
    </div>
  );
}
