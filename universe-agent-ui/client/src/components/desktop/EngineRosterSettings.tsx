/**
 * components/desktop/EngineRosterSettings.tsx — the engine list that both
 * apps now share.
 *
 * The harness owns the ACP/CLI drivers, so this is its live roster, not a
 * second copy kept in Universe. The grouping follows universe bots' own
 * picker (subscriptions / API keys / local); the surface uses the measured
 * settings classes so nothing here changes a size or an icon.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type EngineInfo, type EngineRoster } from "@/lib/api";

const GROUPS = [
  { key: "subscription", title: "Subscriptions", hint: "Local coding CLIs you sign in to." },
  { key: "api", title: "API keys", hint: "Providers you connect with a key." },
  { key: "custom", title: "Local & custom", hint: "Engines on this machine or your own endpoints." },
] as const;

function effortLabel(levels: string[]): string {
  if (!levels.length) return "";
  if (levels.length === 1 && levels[0] === "none") return "";
  const short = levels.filter(level => level !== "none");
  return short.length ? `${short[0]}–${short[short.length - 1]}` : "";
}

function EngineRow({ engine }: { engine: EngineInfo }) {
  const models = engine.models.length;
  const effort = effortLabel(engine.effortLevels);
  return (
    <div className="sx-prov" data-engine={engine.id} data-available={engine.available}>
      <div className="sx-pname">
        {engine.name}
        <span className="sx-pill" data-state={engine.available ? "up" : "down"}>
          {engine.available ? "Ready" : "Needs setup"}
        </span>
      </div>
      <p className="sx-hint">
        {engine.available
          ? `${models} model${models === 1 ? "" : "s"}${effort ? ` · effort ${effort}` : ""}`
          : engine.reason}
      </p>
      <p className="sx-hint">{engine.driver}</p>
    </div>
  );
}

export default function EngineRosterSettings() {
  const [roster, setRoster] = useState<EngineRoster | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [instanceId, setInstanceId] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    setFailed(false);
    try {
      const [next, current] = await Promise.all([
        api.universeBotsInstances(refresh),
        api.universeBotsModel().catch(() => null),
      ]);
      setRoster(next);
      const selected = current?.selection;
      if (selected?.instanceId) {
        setInstanceId(selected.instanceId);
        setModel(selected.model ?? "");
        setEffort(selected.effort ?? "");
      }
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }, []);

  const engines = [
    ...(roster?.groups?.subscription ?? []),
    ...(roster?.groups?.api ?? []),
    ...(roster?.groups?.custom ?? []),
  ];
  const engine = engines.find(item => item.id === instanceId);
  const models = engine?.models ?? [];
  const efforts = engine?.effortLevels.filter(level => level !== "none") ?? [];

  const save = async () => {
    if (!instanceId || !model) return;
    setSaving(true);
    setSaveError("");
    setSaved(false);
    try {
      await api.universeBotsSetModel({
        instanceId, model,
        ...(effort ? { effort } : {}),
      });
      setSaved(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="sx-card">
      <div className="sx-card-head">
        <h2>Engines &amp; models</h2>
        <button className="sx-btn" disabled={busy} onClick={() => void load(true)}>
          {busy ? "Checking…" : "Refresh"}
        </button>
      </div>
      <p className="sx-sub">
        {roster
          ? `${roster.available} of ${roster.count} engines ready. Shared with universe bots — set a model in the composer.`
          : "Reading the engine roster…"}
      </p>
      {failed && (
        <div className="sx-err" role="alert">
          <span>The engine roster couldn&apos;t load.</span>
          <button className="sx-btn" onClick={() => void load()}>Retry</button>
        </div>
      )}
      {roster?.ok && engines.length > 0 && (
        <div className="sx-group">
          <div className="sx-gtitle">Default model</div>
          <p className="sx-ghint">New bots in both apps start with this engine and model.</p>
          <div className="mdl-row">
            <select aria-label="Engine" value={instanceId}
              onChange={event => {
                setInstanceId(event.target.value);
                setModel("");
                setEffort("");
                setSaved(false);
              }}>
              <option value="">Choose an engine…</option>
              {engines.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name}{item.available ? "" : " (needs setup)"}
                </option>
              ))}
            </select>
            <select aria-label="Model" value={model} disabled={!engine}
              onChange={event => { setModel(event.target.value); setSaved(false); }}>
              <option value="">Choose a model…</option>
              {models.map(item => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
            {efforts.length > 0 && (
              <select aria-label="Effort" value={effort}
                onChange={event => { setEffort(event.target.value); setSaved(false); }}>
                <option value="">Auto</option>
                {efforts.map(level => (
                  <option key={level} value={level}>{level}</option>
                ))}
              </select>
            )}
            <button className="sx-btn" disabled={!instanceId || !model || saving}
              onClick={() => void save()}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
          {saved && <p className="sx-hint" role="status">Saved.</p>}
          {saveError && <p className="sx-err" role="alert">{saveError}</p>}
        </div>
      )}
      {GROUPS.map(group => {
        const engines = roster?.groups?.[group.key] ?? [];
        if (!engines.length) return null;
        return (
          <div key={group.key} className="sx-group">
            <div className="sx-gtitle">{group.title}</div>
            <p className="sx-ghint">{group.hint}</p>
            <div className="sx-grid">
              {engines.map(engine => (
                <EngineRow key={engine.id} engine={engine} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
