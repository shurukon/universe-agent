/**
 * components/desktop/ModelPicker.tsx — engine + model selectors in the chat
 * composer.
 *
 * The fix: show ONLY engines that are actually connected, and when one is
 * chosen show ONLY its models. Model names are display names (the part after
 * the last slash, or the catalog label), never a full `provider/model/id`.
 * Search filters both selectors by what the user can actually read.
 *
 * Form: the measured mp-* chip/popover of the Universe shell. Content: the
 * one engine roster the harness owns, shared with universe bots.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { useLocation } from "wouter";
import { useChat } from "@/stores/chat";
import { api, type EngineInfo, type EngineRoster } from "@/lib/api";

/** What a person should see: only the part after the last slash. Everything
 *  before it is the provider/route and carries no meaning for the choice. */
function displayName(model: { id: string; label?: string }): string {
  const parts = model.id.split("/");
  return parts[parts.length - 1] || model.id;
}

type Pane = "engine" | "model" | null;

export default function ModelPicker() {
  const hasProvider = useChat(s => s.providerServing !== "none" && s.providerServing !== "loading");
  const [, navigate] = useLocation();

  const [pane, setPane] = useState<Pane>(null);
  const [roster, setRoster] = useState<EngineRoster | null>(null);
  const [current, setCurrent] = useState<{
    instanceId: string; model: string; effort?: string;
  }>({ instanceId: "", model: "" });
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!pane) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setPane(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPane(null);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [pane]);

  useEffect(() => {
    let active = true;
    void Promise.all([
      api.universeBotsInstances().catch(() => null),
      api.universeBotsModel().catch(() => null),
    ]).then(([engines, model]) => {
      if (!active) return;
      setRoster(engines);
      if (model?.selection?.instanceId) setCurrent(model.selection);
    });
    return () => { active = false; };
  }, [pane]);

  const allEngines = useMemo(
    () => Object.values(roster?.groups ?? {}).flat(),
    [roster]);

  // Only engines the harness reports as AVAIBLE are offered. This is the
  // whole fix for "every provider shows, even ones I never connected".
  const engines = useMemo(() => {
    const connected = allEngines.filter(engine => engine.available);
    const q = query.trim().toLowerCase();
    return q
      ? connected.filter(engine =>
          engine.name.toLowerCase().includes(q) ||
          engine.id.toLowerCase().includes(q))
      : connected;
  }, [allEngines, query]);

  const engine = allEngines.find(item => item.id === current.instanceId) ?? null;
  const models = useMemo(() => {
    if (!engine) return [];
    const q = query.trim().toLowerCase();
    return q
      ? engine.models.filter(model =>
          displayName(model).toLowerCase().includes(q) ||
          model.id.toLowerCase().includes(q))
      : engine.models;
  }, [engine, query]);

  if (!hasProvider) return null;

  const save = async (instanceId: string, model: string, effort = "") => {
    if (busy) return;
    setBusy(true);
    try {
      await api.universeBotsSetModel({
        instanceId, model, ...(effort ? { effort } : {}),
      });
      setCurrent({ instanceId, model, effort });
      setPane(null);
      setQuery("");
    } finally {
      setBusy(false);
    }
  };

  const open = (next: Pane) => {
    setPane(pane === next ? null : next);
    setQuery("");
  };

  const currentModel = engine?.models.find(model => model.id === current.model);

  return (
    <div className="mp-root" ref={rootRef}>
      <div className="mp-selectors">
        {/* provider selector — connected engines only */}
        <button
          className={`mp-chip mp-engine-chip${pane === "engine" ? " open" : ""}`}
          onClick={() => open("engine")}
          title="Choose a connected provider or CLI"
          aria-label="Change provider"
          aria-expanded={pane === "engine"}
        >
          <span className="mp-model">{engine?.name ?? "Provider"}</span>
          <ChevronDown size={13} className={pane === "engine" ? "mp-chev open" : "mp-chev"} />
        </button>

        {/* model selector — only this provider's models, short display names */}
        <button
          className={`mp-chip${pane === "model" ? " open" : ""}`}
          onClick={() => open("model")}
          title={current.model
            ? `Model: ${engine?.name ?? ""} / ${current.model} — click to change`
            : "Pick a model"}
          aria-label="Change model"
          aria-expanded={pane === "model"}
        >
          <span className="mp-model">
            {currentModel ? displayName(currentModel) : (current.model || "Model")}
          </span>
          <ChevronDown size={14} className={pane === "model" ? "mp-chev open" : "mp-chev"} />
        </button>
      </div>

      {pane && (
        <div className="mp-pop" role="listbox" aria-label={pane === "engine" ? "Providers" : "Models"}>
          <div className="mp-search">
            <Search size={12} strokeWidth={2} />
            <input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={pane === "engine" ? "Search connected providers…" : `Search ${engine?.name ?? ""} models…`}
              aria-label="Search"
              onKeyDown={e => {
                if (e.key !== "Enter") return;
                e.preventDefault();
                if (pane === "engine" && engines[0]) {
                  void save(engines[0].id, engines[0].default || engines[0].models[0]?.id || "");
                } else if (pane === "model" && models[0] && engine) {
                  void save(engine.id, models[0].id);
                }
              }}
            />
            {roster === null && <span className="mp-loading">loading…</span>}
          </div>
          <div className="mp-list">
            {pane === "engine" && engines.map(item => (
              <button
                key={item.id}
                className={`mp-row${item.id === current.instanceId ? " on" : ""}`}
                role="option"
                aria-selected={item.id === current.instanceId}
                onClick={() => void save(item.id, item.default || item.models[0]?.id || "")}
              >
                <span className="mp-rowname">{item.name}</span>
                <span className="mp-meta">{item.models.length} models</span>
                {item.id === current.instanceId && <Check size={12} strokeWidth={2.4} />}
              </button>
            ))}
            {pane === "model" && models.map(model => (
              <button
                key={model.id}
                className={`mp-row${model.id === current.model ? " on" : ""}`}
                role="option"
                aria-selected={model.id === current.model}
                onClick={() => engine && void save(engine.id, model.id)}
              >
                <span className="mp-rowname">{displayName(model)}</span>
                {model.id === current.model && <Check size={12} strokeWidth={2.4} />}
              </button>
            ))}
            {((pane === "engine" && engines.length === 0) ||
              (pane === "model" && models.length === 0)) && (
              <p className="mp-empty">Nothing matches “{query}”.</p>
            )}
          </div>
          <button type="button" className="mp-manage"
            onClick={() => { setPane(null); navigate("/settings"); }}
            role="menuitem">
            <span>Manage models</span>
          </button>
        </div>
      )}
    </div>
  );
}
