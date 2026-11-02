/**
 * pages/Welcome.tsx — first-run onboarding (dark matte rebuild 2026-09).
 *
 * Same flow and same API as before — 1. Name → 2. Provider → 3. Voice —
 * restyled onto a pure-black matte system: canvas #000, panel #09090b,
 * hairline-only borders, zinc text, no blue, no gradients, no glows,
 * no shadows. One quiet button size (13px/500, 6px radius) — ghost
 * secondary + elevated-dark primary.
 * Styles: components/desktop/welcome-dark.css (classes gx-*).
 *
 * Flow per request: cloud provider cards show "Add API key" first — the key
 * field opens inline — then "Connect" verifies (bridge probes the key) and
 * auto-advances to Voice. Key-optional providers (local servers) connect
 * directly.
 *
 * Logic untouched: catalog load + retry, install/installCustom with
 * auto-advance, finish() PUT + completeSetup + /chat. Everything chosen
 * here stays changeable later in Settings.
 */
import { useEffect, useState } from "react";
import { useLocation } from "wouter";
import { toast } from "sonner";
import { TriangleAlert, Check } from "lucide-react";
import {
  api,
  BridgeError,
  type CatalogProvider,
  type Settings,
} from "@/lib/api";
import { useSetup } from "@/stores/setup";
import { useChat } from "@/stores/chat";
import "../components/desktop/welcome-dark.css";

/** voice options the bridge implements (voice.py). Local STT = open-source
 *  faster-whisper (any machine); cloud STT adds Groq (whisper-large-v3 /
 *  distil) + OpenAI. TTS: Fish Audio S2.1 (incl. free tier) first, then
 *  ElevenLabs, OpenAI, and free local Edge TTS. */
const STT_OPTIONS = [
  {
    value: "local",
    label: "Local — faster-whisper (offline, no key)",
    models: [] as string[],
  },
  {
    value: "groq",
    label: "Groq — whisper-large-v3 (fast, free tier)",
    models: [
      "whisper-large-v3",
      "whisper-large-v3-turbo",
      "distil-whisper-large-v3-en",
    ],
  },
  { value: "openai", label: "OpenAI Whisper API", models: ["whisper-1"] },
];
const TTS_OPTIONS = [
  { value: "edge-tts", label: "Edge TTS (free, local, no key)" },
  { value: "fish-audio", label: "Fish Audio — S2.1 Pro (free tier incl.)" },
  { value: "elevenlabs", label: "ElevenLabs (multilingual v2)" },
  { value: "openai", label: "OpenAI TTS (gpt-4o-mini-tts)" },
];
const STT_KEYED = ["groq", "openai"];
const TTS_KEYED = ["fish-audio", "elevenlabs", "openai"];

interface VoiceDraft {
  stt_provider: string;
  stt_cloud_model: string;
  stt_api_key: string;
  tts_provider: string;
  tts_api_key: string;
}

export default function WelcomePage() {
  const [, navigate] = useLocation();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [catalog, setCatalog] = useState<CatalogProvider[]>([]);
  const [localReachable, setLocalReachable] = useState<Record<string, boolean>>(
    {}
  );
  const [hasProvider, setHasProvider] = useState(false);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [autoAdvance] = useState(true);

  // step 1 state (name)
  const [name, setName] = useState("Universe");

  // step 2 state (provider)
  const [openName, setOpenName] = useState<string | null>(null);
  const [apiKeyBy, setApiKeyBy] = useState<Record<string, string>>({});
  // templated presets (Cloudflare account_id / Azure resource): user-filled
  // values keyed by provider name, substituted into the base URL at install
  const [urlTmpl, setUrlTmpl] = useState<Record<string, string>>({});
  const [busyName, setBusyName] = useState<string | null>(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customBase, setCustomBase] = useState("");
  const [customKey, setCustomKey] = useState("");
  const [customBusy, setCustomBusy] = useState(false);

  // step 3 state (voice)
  const [voice, setVoice] = useState<VoiceDraft>({
    stt_provider: "local",
    stt_cloud_model: "whisper-large-v3",
    stt_api_key: "",
    tts_provider: "edge-tts",
    tts_api_key: "",
  });
  const [finishing, setFinishing] = useState(false);

  /** One honest loader: catalog + installed-provider state. Re-runnable —
   *  the cold start races the bridge (Electron shows the page before the
   *  Python process answers), so mount + every WS reconnect re-run it. */
  const loadCatalog = () => {
    let alive = true;
    setCatalogFailed(false);
    api
      .getProviderCatalog()
      .then(r => {
        if (!alive) return;
        setCatalog(r.catalog);
        setLocalReachable(r.local_reachable);
      })
      .catch(() => {
        if (alive) setCatalogFailed(true);
      });
    api
      .getProviders()
      .then(r => {
        if (alive) setHasProvider(r.has_provider);
      })
      .catch(() => {
        /* leave false */
      });
    return () => {
      alive = false;
    };
  };

  useEffect(() => loadCatalog(), []);

  // bridge came up after the page loaded — make sure the list arrived
  const connection = useChat(s => s.connection);
  useEffect(() => {
    if (connection === "live" && catalog.length === 0 && !catalogFailed) {
      const unload = loadCatalog();
      return unload;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection]);

  // refresh provider state when returning to step 2 (e.g. after "Back")
  useEffect(() => {
    if (step !== 2) return;
    let alive = true;
    api
      .getProviders()
      .then(r => {
        if (alive) setHasProvider(r.has_provider);
      })
      .catch(() => {
        if (alive) setHasProvider(false);
      });
    return () => {
      alive = false;
    };
  }, [step]);

  const install = async (c: CatalogProvider) => {
    const key = (apiKeyBy[c.name] ?? "").trim();
    if (!key && !c.key_optional) {
      toast.error("Paste an API key first", { description: c.name });
      return;
    }
    setBusyName(c.name);
    try {
      let url = c.base_url;
      if (c.url_fields?.length) {
        const raw = (urlTmpl[c.name] ?? "").trim();
        c.url_fields.forEach((f, i) => {
          url = url.replace(`{${f}}`, raw.split(/[\s,]+/)[i] ?? "");
        });
        if (!raw || url.includes("{")) {
          // early exit — busyName is reset by the finally block below;
          // nothing installed, nothing changed
          toast.error(`Fill the URL value(s): ${c.url_fields.join(", ")}`, {
            description: c.name,
          });
          return;
        }
      }
      // no model from the wizard — the bridge probes the key and routes
      // agents to the first live model it discovers
      const res = await api.installProvider({
        name: c.name,
        base_url: url,
        api_key: key,
      });
      setHasProvider(true);
      useSetup.getState().markProvider();
      setApiKeyBy(p => ({ ...p, [c.name]: "" }));
      setOpenName(null);
      toast.success(`${res.provider} verified`, {
        description: `${res.model} — change models anytime from the chip in the chat composer`,
      });
      // verified & accepted → straight to Voice; no Continue press needed
      if (autoAdvance) setTimeout(() => setStep(3), 600);
    } catch (err) {
      toast.error(
        err instanceof BridgeError ? err.message : "failed to reach provider",
        {
          description: "The key was NOT installed — nothing changed",
        }
      );
    } finally {
      setBusyName(null);
    }
  };

  const installCustom = async () => {
    if (!customName.trim() || !customBase.trim() || !customKey.trim()) {
      toast.error("Custom provider needs a name, base URL and API key");
      return;
    }
    setCustomBusy(true);
    try {
      const res = await api.installProvider({
        name: customName.trim(),
        base_url: customBase.trim(),
        api_key: customKey.trim(),
      });
      setHasProvider(true);
      useSetup.getState().markProvider();
      setCustomOpen(false);
      toast.success(`${res.provider} verified`, {
        description: `${res.agents_routed.length} agents on ${res.model}`,
      });
      if (autoAdvance) setTimeout(() => setStep(3), 600);
    } catch (err) {
      toast.error(
        err instanceof BridgeError ? err.message : "failed to reach provider"
      );
    } finally {
      setCustomBusy(false);
    }
  };

  const finish = async () => {
    setFinishing(true);
    try {
      // name first (step 1) → wake word; voice (step 3) settings — one PUT
      const wake = name.trim() || "Universe";
      const patch: Partial<Settings> = {
        wake_word: wake.toLowerCase().startsWith("wake")
          ? wake
          : `wake up ${wake}`,
        stt_provider: voice.stt_provider,
        tts_provider: voice.tts_provider,
      };
      if (STT_KEYED.includes(voice.stt_provider) && voice.stt_api_key.trim()) {
        patch.stt_api_key = voice.stt_api_key.trim();
        if (voice.stt_cloud_model)
          patch.stt_cloud_model = voice.stt_cloud_model;
      }
      if (TTS_KEYED.includes(voice.tts_provider) && voice.tts_api_key.trim()) {
        patch.tts_api_key = voice.tts_api_key.trim();
      }
      await api.putSettings(patch);
      await useSetup.getState().complete(wake);
      navigate("/chat");
    } catch (err) {
      toast.error(
        err instanceof BridgeError ? err.message : "failed to finish setup"
      );
      // setup incomplete: stay on the wizard — the gate stays shut
    } finally {
      setFinishing(false);
    }
  };

  const groups: Array<{
    key: CatalogProvider["category"];
    label: string;
    hint: string;
  }> = [
    {
      key: "free",
      label: "Free-tier cloud",
      hint: "A real key from a genuinely free tier — no card needed for most.",
    },
    {
      key: "paid",
      label: "Paid cloud",
      hint: "Pay-per-token; you control spend on the provider side.",
    },
    {
      key: "local",
      label: "Local servers",
      hint: "Runs on this machine — no key, no internet. Lights up when reachable.",
    },
  ];

  const steps = [
    { n: 1, label: "Name" },
    { n: 2, label: "Provider" },
    { n: 3, label: "Voice" },
  ];

  // the chat shell paints a cyan wash on body::before — keep it off while
  // onboarding so the wizard sits on the pure Linear canvas
  useEffect(() => {
    document.body.classList.add("gx-welcome-active");
    return () => document.body.classList.remove("gx-welcome-active");
  }, []);

  return (
    <div className="gx-welcome">
      <div className="gx-col">
        <div className="gx-eyebrow">SET UP YOUR UNIVERSE</div>
        <h1>
          {step === 1 && "What should we call it?"}
          {step === 2 && "Connect a model"}
          {step === 3 && "Give it a voice?"}
        </h1>
        <p className="gx-sub">
          {step === 1 && "The name you'll say in voice mode."}
          {step === 2 &&
            "Universe has no built-in model. One verified key is enough."}
          {step === 3 &&
            "Local by default. Optional — changeable later in Settings."}
        </p>

        {/* steps rail */}
        <ol className="gx-steps" aria-label="Setup steps">
          {steps.map((s, i) => {
            const active = step === s.n;
            const done = s.n < step || (s.n === 2 && hasProvider);
            return (
              <li
                key={s.n}
                className={`gx-step${active ? " active" : ""}${done ? " done" : ""}`}
                aria-current={active ? "step" : undefined}
              >
                <span className="gx-num" aria-hidden="true">
                  {done ? <Check size={11} strokeWidth={3} /> : s.n}
                </span>
                <span className="gx-slabel">{s.label}</span>
                {i < steps.length - 1 && (
                  <span className="gx-sep" aria-hidden="true" />
                )}
              </li>
            );
          })}
        </ol>

        <div className="gx-panel" key={step}>
          {step === 1 && (
            <section aria-label="Name your universe">
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Nova"
                aria-label="Universe name"
                className="gx-name"
                autoFocus
                maxLength={40}
              />
              <div className="gx-wake" aria-live="polite">
                Wake phrase: “
                {name.trim()
                  ? name.trim().toLowerCase().startsWith("wake")
                    ? name.trim()
                    : `wake up ${name.trim()}`
                  : "wake up Universe"}
                ”
              </div>
            </section>
          )}

          {step === 2 && (
            <section aria-label="Choose a model provider">
              {hasProvider && (
                <div className="gx-ok" role="status">
                  <Check size={13} strokeWidth={2.6} aria-hidden="true" />
                  <span>
                    A provider is connected. Add more below, or continue.
                  </span>
                </div>
              )}

              {catalog.length === 0 && !catalogFailed && (
                <p className="gx-muted" role="status">
                  Loading the provider catalog…
                </p>
              )}
              {catalog.length === 0 && catalogFailed && (
                <div className="gx-err" role="alert">
                  <TriangleAlert size={14} aria-hidden="true" />
                  <span>
                    The provider list couldn't load — the backend may still be
                    starting.
                  </span>
                  <button
                    className="gx-btn"
                    onClick={() => {
                      void loadCatalog();
                    }}
                    aria-label="Retry loading providers"
                  >
                    Retry
                  </button>
                </div>
              )}

              {groups.map(g => {
                const items = catalog.filter(c => c.category === g.key);
                if (!items.length) return null;
                return (
                  <div key={g.key} className="gx-group">
                    <div className="gx-gtitle">{g.label}</div>
                    <p className="gx-ghint">{g.hint}</p>
                    <div className="gx-grid">
                      {items.map(c => {
                        const isOpen = openName === c.name;
                        const reachable = localReachable[c.name] === true;
                        const busy = busyName === c.name;
                        return (
                          <div
                            key={c.name}
                            className={`gx-card${isOpen ? " open" : ""}`}
                          >
                            <div className="gx-cname">
                              {c.name}
                              {c.category === "free" && (
                                <span className="gx-pill">free</span>
                              )}
                              {c.category === "paid" && (
                                <span className="gx-pill">paid</span>
                              )}
                              {c.category === "local" && (
                                <span
                                  className={`gx-pill${reachable ? " live" : ""}`}
                                >
                                  {reachable && (
                                    <span
                                      className="gx-dot"
                                      aria-hidden="true"
                                    />
                                  )}
                                  {reachable ? "online" : "offline"}
                                </span>
                              )}
                            </div>
                            {c.note && <p className="gx-cnote">{c.note}</p>}
                            {!isOpen ? (
                              <div className="gx-actions">
                                {c.key_optional ? (
                                  <button
                                    className="gx-btn primary"
                                    onClick={() => void install(c)}
                                    disabled={busy}
                                  >
                                    {busy ? "Verifying…" : "Connect"}
                                  </button>
                                ) : (
                                  <button
                                    className="gx-btn"
                                    onClick={() => setOpenName(c.name)}
                                  >
                                    Add API key
                                  </button>
                                )}
                                {c.key_url && (
                                  <button
                                    className="gx-link"
                                    onClick={() => {
                                      void window.open(
                                        c.key_url,
                                        "_blank",
                                        "noopener"
                                      );
                                    }}
                                  >
                                    {c.category === "free"
                                      ? "Get a free key"
                                      : "Get a key"}
                                  </button>
                                )}
                              </div>
                            ) : (
                              <div>
                                <label className="gx-field">
                                  <span className="gx-flabel">
                                    {c.name} API key
                                  </span>
                                  <input
                                    type="password"
                                    value={apiKeyBy[c.name] ?? ""}
                                    onChange={e =>
                                      setApiKeyBy(p => ({
                                        ...p,
                                        [c.name]: e.target.value,
                                      }))
                                    }
                                    placeholder="paste the key…"
                                    className="gx-input mono"
                                    aria-label={`${c.name} API key`}
                                    autoFocus
                                  />
                                </label>
                                {c.url_fields?.length ? (
                                  <label className="gx-field">
                                    <span className="gx-flabel">
                                      URL value — {c.url_fields.join(", ")}
                                    </span>
                                    <input
                                      value={urlTmpl[c.name] ?? ""}
                                      onChange={e =>
                                        setUrlTmpl(p => ({
                                          ...p,
                                          [c.name]: e.target.value,
                                        }))
                                      }
                                      placeholder={c.url_fields.join(" ")}
                                      className="gx-input mono"
                                      aria-label={`URL values for ${c.name}`}
                                    />
                                  </label>
                                ) : null}
                                <div className="gx-actions">
                                  <button
                                    className="gx-btn primary"
                                    onClick={() => void install(c)}
                                    disabled={
                                      busy || !(apiKeyBy[c.name] ?? "").trim()
                                    }
                                  >
                                    {busy ? "Verifying…" : "Connect"}
                                  </button>
                                  <button
                                    className="gx-link"
                                    onClick={() => setOpenName(null)}
                                    disabled={busy}
                                  >
                                    Cancel
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}

              {/* custom OpenAI-compatible provider */}
              <div className="gx-group">
                <div className="gx-gtitle">Custom provider</div>
                <p className="gx-ghint">Any OpenAI-compatible endpoint.</p>
                {!customOpen ? (
                  <button
                    className="gx-btn"
                    onClick={() => setCustomOpen(true)}
                  >
                    Add custom provider
                  </button>
                ) : (
                  <div>
                    <label className="gx-field">
                      <span className="gx-flabel">Name</span>
                      <input
                        value={customName}
                        onChange={e => setCustomName(e.target.value)}
                        placeholder="My Provider"
                        aria-label="Custom provider name"
                        className="gx-input"
                      />
                    </label>
                    <label className="gx-field">
                      <span className="gx-flabel">Base URL</span>
                      <input
                        value={customBase}
                        onChange={e => setCustomBase(e.target.value)}
                        placeholder="https://host.example/v1"
                        className="gx-input mono"
                        aria-label="Custom provider base URL"
                      />
                    </label>
                    <label className="gx-field">
                      <span className="gx-flabel">API key</span>
                      <input
                        type="password"
                        value={customKey}
                        onChange={e => setCustomKey(e.target.value)}
                        placeholder="paste the key…"
                        className="gx-input mono"
                        aria-label="Custom provider API key"
                      />
                    </label>
                    <div className="gx-actions">
                      <button
                        className="gx-btn primary"
                        onClick={() => void installCustom()}
                        disabled={customBusy}
                      >
                        {customBusy ? "Verifying…" : "Connect"}
                      </button>
                      <button
                        className="gx-link"
                        onClick={() => setCustomOpen(false)}
                        disabled={customBusy}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </section>
          )}

          {step === 3 && (
            <section aria-label="Voice mode setup">
              <div className="gx-group">
                <div className="gx-gtitle">Hearing</div>
                <div className="gx-opts">
                  {STT_OPTIONS.map(o => {
                    const sel = voice.stt_provider === o.value;
                    return (
                      <label
                        key={o.value}
                        className={`gx-opt${sel ? " on" : ""}`}
                      >
                        <input
                          type="radio"
                          name="stt"
                          value={o.value}
                          checked={sel}
                          onChange={() =>
                            setVoice(v => ({ ...v, stt_provider: o.value }))
                          }
                          aria-label={o.label}
                        />
                        <span className="gx-radio" aria-hidden="true" />
                        <span>{o.label}</span>
                      </label>
                    );
                  })}
                </div>
                <div>
                  {voice.stt_provider !== "local" &&
                  STT_OPTIONS.find(o => o.value === voice.stt_provider)?.models
                    .length ? (
                    <label className="gx-field">
                      <span className="gx-flabel">Model</span>
                      <select
                        value={voice.stt_cloud_model}
                        onChange={e =>
                          setVoice(v => ({
                            ...v,
                            stt_cloud_model: e.target.value,
                          }))
                        }
                        aria-label="Cloud speech-to-text model"
                        className="gx-input"
                      >
                        {STT_OPTIONS.find(
                          o => o.value === voice.stt_provider
                        )!.models.map(m => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  {STT_KEYED.includes(voice.stt_provider) && (
                    <label className="gx-field">
                      <span className="gx-flabel">
                        {voice.stt_provider === "groq" ? "Groq" : "OpenAI"} key
                      </span>
                      <input
                        type="password"
                        value={voice.stt_api_key}
                        onChange={e =>
                          setVoice(v => ({ ...v, stt_api_key: e.target.value }))
                        }
                        placeholder="paste the key…"
                        className="gx-input mono"
                        aria-label="Speech to text API key"
                      />
                    </label>
                  )}
                </div>
              </div>
              <div className="gx-group">
                <div className="gx-gtitle">Speech</div>
                <div className="gx-opts">
                  {TTS_OPTIONS.map(o => {
                    const sel = voice.tts_provider === o.value;
                    return (
                      <label
                        key={o.value}
                        className={`gx-opt${sel ? " on" : ""}`}
                      >
                        <input
                          type="radio"
                          name="tts"
                          value={o.value}
                          checked={sel}
                          onChange={() =>
                            setVoice(v => ({ ...v, tts_provider: o.value }))
                          }
                          aria-label={o.label}
                        />
                        <span className="gx-radio" aria-hidden="true" />
                        <span>{o.label}</span>
                      </label>
                    );
                  })}
                </div>
                <div>
                  {TTS_KEYED.includes(voice.tts_provider) && (
                    <label className="gx-field">
                      <span className="gx-flabel">
                        {voice.tts_provider === "fish-audio"
                          ? "Fish Audio"
                          : voice.tts_provider === "elevenlabs"
                            ? "ElevenLabs"
                            : "OpenAI"}{" "}
                        key
                      </span>
                      <input
                        type="password"
                        value={voice.tts_api_key}
                        onChange={e =>
                          setVoice(v => ({ ...v, tts_api_key: e.target.value }))
                        }
                        placeholder="paste the key…"
                        className="gx-input mono"
                        aria-label="Text to speech API key"
                      />
                    </label>
                  )}
                </div>
              </div>
            </section>
          )}
        </div>
        {/* /gx-panel */}

        <footer className="gx-foot">
          {step > 1 ? (
            <button
              className="gx-btn"
              onClick={() => setStep(s => (s - 1) as 1 | 2)}
            >
              Back
            </button>
          ) : (
            <span />
          )}
          <span className="flex-sp" />
          {step < 3 ? (
            step === 1 ? (
              <button className="gx-btn primary" onClick={() => setStep(2)}>
                Continue
              </button>
            ) : hasProvider ? (
              /* step 2 with a verified provider auto-advances on install;
                 Continue only appears for adding a second provider */
              <button className="gx-btn primary" onClick={() => setStep(3)}>
                Continue
              </button>
            ) : (
              /* step 2, nothing verified yet — no Continue button: verify a
                 key and the wizard advances itself */
              <span className="gx-muted">
                Verify a provider key to continue
              </span>
            )
          ) : (
            <div className="gx-foot-actions">
              <button
                className="gx-link"
                onClick={() => void finish()}
                disabled={finishing}
              >
                Skip voice
              </button>
              <button
                className="gx-btn primary"
                onClick={() => void finish()}
                disabled={finishing}
              >
                {finishing ? "Launching…" : "Launch Universe"}
              </button>
            </div>
          )}
        </footer>
      </div>
    </div>
  );
}
