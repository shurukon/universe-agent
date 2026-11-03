/**
 * pages/Studio.tsx — Universe Studio, the one workspace to design, edit
 * and model — driven by Universe.
 *
 * Structure (Manus-2.0-inspired, Universe-styled):
 *   • mode === null  → centered hero with the three tool cards
 *   • a mode chosen  → workspace panel (h-[52vh]) docked above the fixed
 *     bottom composer. Design/Video embed the tool service once it is
 *     running (iframe ?embed=1) and offer a launch card otherwise; 3D is
 *     a Blender MCP connection card (addon instructions + bridge mode).
 *   • Design additionally carries the compact Muse Image card, wired to
 *     the bridge's /api/meta/image endpoint.
 *
 * The service list is polled every 8s (every 3s while something is
 * "starting") so the status chips stay fresh. All state is local — this
 * page is a control surface, not a store.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Box,
  Cable,
  ChevronRight,
  Clapperboard,
  ExternalLink,
  Image as ImageIcon,
  LayoutGrid,
  Loader2,
  Orbit,
  Palette,
  Play,
  RefreshCw,
  Square,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import {
  generateImage,
  listServices,
  startService,
  stopService,
  type ImageResult,
  type StudioService,
  type StudioServiceId,
  type StudioServiceStatus,
} from "@/lib/studioApi";

/* ---- static tool + status metadata -------------------------------------- */

type Mode = "design" | "video" | "3d";
type BlenderBridgeMode = "preview" | "background";

interface ToolMeta {
  id: Mode;
  serviceId: StudioServiceId;
  name: string;
  tagline: string;
  hint: string;
  icon: LucideIcon;
}

const TOOLS: readonly ToolMeta[] = [
  {
    id: "design",
    serviceId: "opendesign",
    name: "Design",
    tagline: "Websites & presentations",
    hint: "powered by open-design",
    icon: Palette,
  },
  {
    id: "video",
    serviceId: "ave",
    name: "Video",
    tagline: "Edit, montage & effects",
    hint: "powered by AVE",
    icon: Clapperboard,
  },
  {
    id: "3d",
    serviceId: "blender",
    name: "3D",
    tagline: "Models & renders",
    hint: "powered by Blender MCP",
    icon: Box,
  },
];

const BRIDGE_MODES: readonly {
  id: BlenderBridgeMode;
  name: string;
  description: string;
}[] = [
  {
    id: "preview",
    name: "Preview",
    description: "watch Blender live while Universe works",
  },
  {
    id: "background",
    name: "Background",
    description: "headless, results delivered to Studio",
  },
];

const STATUS_META: Record<
  StudioServiceStatus,
  { label: string; dot: string; chip: string }
> = {
  running: {
    label: "Running",
    dot: "bg-emerald-400",
    chip: "border-emerald-400/25 bg-emerald-400/10 text-emerald-300",
  },
  starting: {
    label: "Starting",
    dot: "animate-pulse bg-amber-400",
    chip: "border-amber-400/25 bg-amber-400/10 text-amber-300",
  },
  stopped: {
    label: "Stopped",
    dot: "bg-slate-500",
    chip: "border-white/10 bg-white/[0.03] text-slate-400",
  },
  unreachable: {
    label: "Unreachable",
    dot: "bg-red-500",
    chip: "border-red-400/25 bg-red-400/10 text-red-300",
  },
  unconfigured: {
    label: "Not configured",
    dot: "bg-slate-500",
    chip: "border-dashed border-white/15 bg-transparent text-slate-400",
  },
};

/** Palette tokens for the Universe dark theme (kept here so every surface
 *  on the page shares one source of truth). */
const PANEL =
  "border-[#7da0ff]/[0.14] bg-white/[0.03] backdrop-blur-xl";
const ACCENT_BTN =
  "bg-[#5b9dff] text-white shadow-[0_0_24px_rgba(91,157,255,0.25)] hover:bg-[#7db3ff]";

/* ---- helpers -------------------------------------------------------------- */

function errMessage(e: unknown): string {
  return e instanceof Error && e.message
    ? e.message
    : "Universe bridge is not responding";
}

/** service.url with the embed flag appended (? or & as needed). */
function embedUrl(base: string): string {
  return `${base}${base.includes("?") ? "&" : "?"}embed=1`;
}

function imageSrc(image: ImageResult): string | null {
  if (image.b64) return `data:image/webp;base64,${image.b64}`;
  return image.url ?? null;
}

/* ---- small shared pieces -------------------------------------------------- */

function StatusChip({ status }: { status: StudioServiceStatus }) {
  const meta = STATUS_META[status];
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        meta.chip
      )}
    >
      <span className={cn("size-1.5 rounded-full", meta.dot)} />
      {meta.label}
    </Badge>
  );
}

/* ---- hero (mode === null) ------------------------------------------------- */

function Hero({ onPick }: { onPick: (mode: Mode) => void }) {
  return (
    <section className="flex w-full flex-col items-center text-center">
      <div
        className={cn(
          "mb-7 flex size-16 items-center justify-center rounded-2xl text-[#5b9dff] shadow-[0_0_24px_rgba(91,157,255,0.25)]",
          PANEL
        )}
      >
        <Orbit className="size-8" />
      </div>
      <h1 className="text-4xl font-semibold tracking-tight text-slate-100">
        Universe Studio
      </h1>
      <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-400">
        One workspace to design, edit and model — driven by Universe.
      </p>

      <div className="mt-10 grid w-full gap-4 sm:grid-cols-3">
        {TOOLS.map(tool => {
          const Icon = tool.icon;
          return (
            <button
              key={tool.id}
              type="button"
              onClick={() => onPick(tool.id)}
              className={cn(
                "group relative flex h-full flex-col rounded-xl p-5 text-left outline-none transition-all hover:-translate-y-0.5 hover:border-[#5b9dff]/40 hover:bg-white/[0.05] hover:shadow-[0_0_24px_rgba(91,157,255,0.25)] focus-visible:ring-2 focus-visible:ring-[#5b9dff]/50",
                PANEL
              )}
            >
              <span className="mb-4 flex size-10 items-center justify-center rounded-lg bg-[#5b9dff]/10 text-[#5b9dff]">
                <Icon className="size-5" />
              </span>
              <span className="text-base font-semibold text-slate-100">
                {tool.name}
              </span>
              <span className="mt-1 text-sm text-slate-400">
                {tool.tagline}
              </span>
              <span className="mt-auto flex items-center justify-between pt-6">
                <span className="text-[11px] uppercase tracking-widest text-slate-600">
                  {tool.hint}
                </span>
                <ChevronRight className="size-4 -translate-x-1 text-[#5b9dff] opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

/* ---- workspace body cards ------------------------------------------------- */

function PendingBody() {
  return (
    <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
      <Loader2 className="size-4 animate-spin" />
      Reaching the bridge…
    </div>
  );
}

function BridgeDownBody({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex h-full items-center justify-center">
      <Card
        className={cn("w-full max-w-md text-center shadow-none", PANEL)}
      >
        <CardHeader>
          <CardTitle className="text-base text-slate-100">
            Universe bridge is not responding
          </CardTitle>
          <CardDescription>
            Studio services are managed by the local bridge. Make sure the
            Universe backend is running, then retry.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            type="button"
            variant="outline"
            onClick={onRetry}
            className="gap-2 border-white/10 bg-white/[0.03] text-slate-300 hover:bg-white/[0.06] hover:text-slate-100"
          >
            <RefreshCw className="size-4" />
            Retry
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function LaunchBody({
  service,
  icon,
  busy,
  onStart,
}: {
  service: StudioService;
  icon: LucideIcon;
  busy: boolean;
  onStart: () => void;
}) {
  const Icon = icon;
  return (
    <div className="flex h-full items-center justify-center">
      <Card className={cn("w-full max-w-md text-center shadow-none", PANEL)}>
        <CardHeader>
          <div className="mx-auto mb-1 flex size-11 items-center justify-center rounded-lg bg-[#5b9dff]/10 text-[#5b9dff]">
            <Icon className="size-5" />
          </div>
          <CardTitle className="text-base text-slate-100">
            {service.name}
          </CardTitle>
          <CardDescription>{service.description}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-3">
          {service.detail ? (
            <p className="text-xs leading-relaxed text-slate-500">
              {service.detail}
            </p>
          ) : null}
          {service.status === "starting" ? (
            <span className="inline-flex items-center gap-2 text-sm text-amber-300">
              <Loader2 className="size-4 animate-spin" />
              Starting {service.name}…
            </span>
          ) : (
            <Button
              type="button"
              size="lg"
              disabled={busy}
              onClick={onStart}
              className={cn("gap-2", ACCENT_BTN)}
            >
              <Play className="size-4" />
              Start {service.name}
            </Button>
          )}
          <p className="text-[11px] uppercase tracking-widest text-slate-600">
            {STATUS_META[service.status].label}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function BlenderBody({
  service,
  bridgeMode,
  onBridgeMode,
}: {
  service: StudioService;
  bridgeMode: BlenderBridgeMode;
  onBridgeMode: (mode: BlenderBridgeMode) => void;
}) {
  return (
    <ScrollArea className="h-full">
      <Card
        className={cn("mx-auto max-w-lg shadow-none", PANEL)}
      >
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[#5b9dff]/10 text-[#5b9dff]">
              <Cable className="size-5" />
            </div>
            <div className="min-w-0 flex-1">
              <CardTitle className="text-base text-slate-100">
                Blender MCP
              </CardTitle>
              <CardDescription className="truncate">
                {service.description}
              </CardDescription>
            </div>
            <StatusChip status={service.status} />
          </div>
          {service.detail ? (
            <p className="text-xs leading-relaxed text-slate-500">
              {service.detail}
            </p>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <ol className="flex flex-col gap-2.5">
            {[
              "Open Blender on this machine.",
              "Enable the Universe Bridge addon (Edit → Preferences → Add-ons).",
              "Choose how Universe should connect:",
            ].map((step, i) => (
              <li key={step} className="flex items-start gap-3 text-sm">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-[#7da0ff]/[0.14] bg-white/[0.03] text-[11px] font-medium text-[#5b9dff]">
                  {i + 1}
                </span>
                <span className="text-slate-300">{step}</span>
              </li>
            ))}
          </ol>

          <div
            role="radiogroup"
            aria-label="Universe Bridge mode"
            className="grid gap-2 sm:grid-cols-2"
          >
            {BRIDGE_MODES.map(option => {
              const selected = bridgeMode === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => onBridgeMode(option.id)}
                  className={cn(
                    "flex items-start gap-3 rounded-xl border p-3 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-[#5b9dff]/50",
                    selected
                      ? "border-[#5b9dff]/60 bg-[#5b9dff]/[0.08]"
                      : "border-[#7da0ff]/[0.14] bg-white/[0.02] hover:border-[#5b9dff]/40 hover:bg-white/[0.04]"
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                      selected ? "border-[#5b9dff]" : "border-slate-600"
                    )}
                  >
                    {selected ? (
                      <span className="size-2 rounded-full bg-[#5b9dff]" />
                    ) : null}
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-slate-200">
                      {option.name}
                    </span>
                    <span className="mt-0.5 block text-xs leading-snug text-slate-500">
                      {option.description}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          <p className="text-xs leading-relaxed text-slate-500">
            Modeling requests are sent from the chat once Blender is
            connected.
          </p>
        </CardContent>
      </Card>
    </ScrollArea>
  );
}

/* ---- Muse Image card (Design mode) ---------------------------------------- */

function MuseCard({
  prompt,
  onPrompt,
  busy,
  image,
  onGenerate,
}: {
  prompt: string;
  onPrompt: (value: string) => void;
  busy: boolean;
  image: ImageResult | null;
  onGenerate: () => void;
}) {
  const src = image ? imageSrc(image) : null;
  return (
    <div className="shrink-0 px-3 pb-3">
      <div className="flex items-center gap-3 rounded-xl border border-[#7da0ff]/[0.14] bg-white/[0.02] p-2.5">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[#5b9dff]/10 text-[#5b9dff]">
          <Wand2 className="size-4" />
        </span>
        <div className="w-28 shrink-0">
          <p className="text-xs font-medium text-slate-200">Muse Image</p>
          <p className="text-[11px] leading-snug text-slate-500">
            Render an image for your design
          </p>
        </div>
        <Input
          value={prompt}
          onChange={e => onPrompt(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && prompt.trim() && !busy) onGenerate();
          }}
          disabled={busy}
          placeholder="A lone lighthouse under a nebula sky…"
          className="h-9 min-w-0 flex-1 border-white/10 bg-black/20 text-sm text-slate-200 placeholder:text-slate-600"
        />
        <Button
          type="button"
          size="sm"
          disabled={busy || !prompt.trim()}
          onClick={onGenerate}
          className={cn("h-9 shrink-0 gap-1.5", ACCENT_BTN)}
        >
          {busy ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Wand2 className="size-3.5" />
          )}
          Generate
        </Button>
        {src ? (
          <img
            src={src}
            alt="Muse image result"
            className="size-14 shrink-0 rounded-lg border border-[#7da0ff]/[0.14] object-cover"
          />
        ) : (
          <span className="flex size-14 shrink-0 items-center justify-center rounded-lg border border-dashed border-white/10 text-slate-700">
            <ImageIcon className="size-4" />
          </span>
        )}
      </div>
    </div>
  );
}

/* ---- composer (fixed bottom) ---------------------------------------------- */

function Composer({
  mode,
  onMode,
  prompt,
  onPrompt,
  onSend,
}: {
  mode: Mode | null;
  onMode: (mode: Mode) => void;
  prompt: string;
  onPrompt: (value: string) => void;
  onSend: () => void;
}) {
  const canSend = mode !== null && prompt.trim().length > 0;
  const tooltip =
    mode === null
      ? "Choose a tool first — Design, Video or 3D"
      : "Describe what to build first";

  return (
    <div
      className={cn(
        "rounded-xl p-3 shadow-[0_0_40px_rgba(3,5,10,0.55)]",
        PANEL
      )}
    >
      <Textarea
        value={prompt}
        onChange={e => onPrompt(e.target.value)}
        onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && canSend) {
            e.preventDefault();
            onSend();
          }
        }}
        placeholder="Describe what to build…"
        className="max-h-40 min-h-10 resize-none border-0 bg-transparent p-1 shadow-none focus-visible:ring-0 md:text-sm dark:bg-transparent"
      />
      <div className="mt-2 flex items-center gap-1.5">
        {TOOLS.map(tool => {
          const Icon = tool.icon;
          const active = mode === tool.id;
          return (
            <Tooltip key={tool.id}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-pressed={active}
                  aria-label={tool.name}
                  onClick={() => onMode(tool.id)}
                  className={cn(
                    "size-9 rounded-lg",
                    active
                      ? "bg-[#5b9dff]/15 text-[#5b9dff] shadow-[0_0_24px_rgba(91,157,255,0.25)]"
                      : "text-slate-500 hover:bg-white/[0.05] hover:text-slate-300"
                  )}
                >
                  <Icon className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">
                {tool.name} — {tool.tagline}
              </TooltipContent>
            </Tooltip>
          );
        })}
        <div className="flex-1" />
        {canSend ? (
          <Button
            type="button"
            size="icon"
            aria-label="Send"
            onClick={onSend}
            className={cn("size-9 rounded-full", ACCENT_BTN)}
          >
            <ArrowUp className="size-4" />
          </Button>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              {/* span wrapper: disabled buttons swallow pointer events, so
                  the tooltip must live on the still-alive parent */}
              <span className="inline-flex">
                <Button
                  type="button"
                  size="icon"
                  aria-label="Send"
                  disabled
                  className={cn("size-9 rounded-full", ACCENT_BTN)}
                >
                  <ArrowUp className="size-4" />
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent side="top">{tooltip}</TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  );
}

/* ---- the page -------------------------------------------------------------- */

export default function Studio() {
  const [services, setServices] = useState<StudioService[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [mode, setMode] = useState<Mode | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busyId, setBusyId] = useState<StudioServiceId | null>(null);
  const [bridgeMode, setBridgeMode] = useState<BlenderBridgeMode>("preview");
  // Muse state lives here so the composer can hand its prompt straight in
  const [musePrompt, setMusePrompt] = useState("");
  const [museImage, setMuseImage] = useState<ImageResult | null>(null);
  const [museBusy, setMuseBusy] = useState(false);
  const fetchingRef = useRef(false);

  /* polling: 8s heartbeat, tightened to 3s while anything is "starting" */
  const refresh = useCallback(async () => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const list = await listServices();
      setServices(list);
      setLoadFailed(false);
    } catch {
      // keep the last known statuses on the chips; only the very first
      // load failure gets its own card
      setLoadFailed(true);
    } finally {
      fetchingRef.current = false;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const anyStarting = services?.some(s => s.status === "starting") ?? false;
  useEffect(() => {
    const id = window.setInterval(
      () => void refresh(),
      anyStarting ? 3000 : 8000
    );
    return () => window.clearInterval(id);
  }, [anyStarting, refresh]);

  const activeTool = mode === null ? null : TOOLS.find(t => t.id === mode) ?? null;
  const service = activeTool
    ? (services?.find(s => s.id === activeTool.serviceId) ?? null)
    : null;
  const bridgeDown = loadFailed && services === null;

  /* ---- actions ---- */

  const toggleService = useCallback(
    async (svc: StudioService, action: "start" | "stop") => {
      if (busyId) return; // one service operation at a time
      setBusyId(svc.id);
      try {
        const updated =
          action === "start"
            ? await startService(svc.id)
            : await stopService(svc.id);
        setServices(prev =>
          prev ? prev.map(s => (s.id === updated.id ? updated : s)) : prev
        );
        toast.success(
          action === "start"
            ? `${svc.name} ${updated.status === "running" ? "is running" : "is starting"}`
            : `${svc.name} stopped`
        );
      } catch (e) {
        toast.error(`Could not ${action} ${svc.name}`, {
          description: errMessage(e),
        });
      } finally {
        setBusyId(null);
      }
    },
    [busyId]
  );

  const runMuse = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      if (museBusy) {
        toast.info("Muse is already rendering", {
          description: "One image at a time — your prompt is waiting in the card.",
        });
        return;
      }
      setMuseBusy(true);
      try {
        const image = await generateImage(text);
        setMuseImage(image);
        toast.success("Muse delivered a new image");
      } catch (e) {
        // missing provider key etc. — the server's own message reads through
        toast.error("Muse could not generate the image", {
          description: errMessage(e),
        });
      } finally {
        setMuseBusy(false);
      }
    },
    [museBusy]
  );

  const send = () => {
    if (mode === null) return;
    const text = prompt.trim();

    // Design: the brief becomes the Muse prompt and renders immediately —
    // /api/meta/image rides the bridge, not the tool service.
    if (mode === "design") {
      if (!text) return;
      setMusePrompt(text);
      setPrompt("");
      void runMuse(text);
      return;
    }

    if (!service) {
      toast.info("Still reaching the bridge", {
        description: "Give the service list a second, then try again.",
      });
      return;
    }
    if (service.status === "starting") {
      toast.info(`${service.name} is starting`, {
        description: "The workspace opens as soon as it is running.",
      });
      return;
    }
    if (service.status === "running") {
      toast.info(
        mode === "video" ? "AVE is live in the workspace" : "Blender is connected",
        {
          description:
            mode === "video"
              ? "Edit inside the embedded studio — ask Universe in the chat to drive it."
              : "Describe the model in the chat and Universe sends it to Blender.",
        }
      );
      return;
    }
    // stopped / unreachable / unconfigured — bring the service up first
    void toggleService(service, "start");
  };

  /* ---- render ---- */

  const body = (() => {
    if (bridgeDown) return <BridgeDownBody onRetry={() => void refresh()} />;
    if (!service) return <PendingBody />;
    if (mode === "3d") {
      return (
        <BlenderBody
          service={service}
          bridgeMode={bridgeMode}
          onBridgeMode={setBridgeMode}
        />
      );
    }
    if (service.status === "running") {
      return (
        <iframe
          key={service.url}
          src={embedUrl(service.url)}
          title={service.name}
          className="h-full w-full rounded-lg border border-[#7da0ff]/[0.14] bg-[#05070d]"
        />
      );
    }
    return (
      <LaunchBody
        service={service}
        icon={activeTool?.icon ?? Palette}
        busy={busyId === service.id}
        onStart={() => void toggleService(service, "start")}
      />
    );
  })();

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#05070d] text-slate-200">
      {/* ambient glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_0%,rgba(91,157,255,0.10),transparent_70%)]"
      />

      <main
        className={cn(
          "relative mx-auto w-full max-w-3xl px-4",
          mode === null
            ? "flex min-h-screen items-center justify-center pb-56 pt-16"
            : "pb-[calc(52vh+10.5rem)] pt-6"
        )}
      >
        {mode === null ? <Hero onPick={setMode} /> : null}
      </main>

      {/* fixed dock: workspace panel above the composer */}
      <div className="fixed inset-x-0 bottom-0 z-40 px-4 pb-4">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
          {mode !== null && activeTool ? (
            <section
              className={cn(
                "flex h-[52vh] flex-col overflow-hidden rounded-xl shadow-[0_0_40px_rgba(3,5,10,0.55)]",
                PANEL
              )}
            >
              <header className="flex shrink-0 items-center gap-3 border-b border-[#7da0ff]/[0.14] py-2 pl-4 pr-2.5">
                <h2 className="text-sm font-semibold text-slate-100">
                  {activeTool.name}
                </h2>
                {service ? (
                  <StatusChip status={service.status} />
                ) : (
                  <Badge
                    variant="outline"
                    className="gap-1.5 rounded-full border-white/10 bg-white/[0.03] px-2 py-0.5 text-[11px] text-slate-400"
                  >
                    <Loader2 className="size-3 animate-spin" />
                    Checking
                  </Badge>
                )}
                <div className="ml-auto flex items-center gap-1.5">
                  {service ? (
                    service.status === "running" ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={busyId === service.id}
                        onClick={() => void toggleService(service, "stop")}
                        className="h-8 gap-1.5 border-white/10 bg-white/[0.03] text-slate-300 hover:bg-white/[0.06] hover:text-slate-100"
                      >
                        <Square className="size-3.5" />
                        Stop
                      </Button>
                    ) : service.status === "starting" ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled
                        className="h-8 gap-1.5 border-white/10 bg-white/[0.03] text-slate-400"
                      >
                        <Loader2 className="size-3.5 animate-spin" />
                        Starting…
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={busyId === service.id}
                        onClick={() => void toggleService(service, "start")}
                        className="h-8 gap-1.5 border-[#5b9dff]/40 bg-[#5b9dff]/10 text-[#5b9dff] hover:bg-[#5b9dff]/20 hover:text-[#7db3ff]"
                      >
                        <Play className="size-3.5" />
                        Start
                      </Button>
                    )
                  ) : null}
                  {service?.status === "running" ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Open in new window"
                          onClick={() =>
                            window.open(service.url, "_blank", "noopener")
                          }
                          className="text-slate-400 hover:bg-white/[0.05] hover:text-slate-100"
                        >
                          <ExternalLink className="size-4" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        Open in new window
                      </TooltipContent>
                    </Tooltip>
                  ) : null}
                  <span className="mx-1 h-5 w-px bg-[#7da0ff]/[0.14]" />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setMode(null)}
                    className="h-8 gap-1.5 text-slate-400 hover:bg-white/[0.05] hover:text-slate-100"
                  >
                    <LayoutGrid className="size-3.5" />
                    Switch tool
                  </Button>
                </div>
              </header>

              <div className="min-h-0 flex-1">
                {mode === "design" ? (
                  <div className="flex h-full min-h-0 flex-col">
                    <div className="min-h-0 flex-1 p-3 pb-2">{body}</div>
                    <MuseCard
                      prompt={musePrompt}
                      onPrompt={setMusePrompt}
                      busy={museBusy}
                      image={museImage}
                      onGenerate={() => void runMuse(musePrompt)}
                    />
                  </div>
                ) : (
                  <div className="h-full p-3">{body}</div>
                )}
              </div>
            </section>
          ) : null}

          <Composer
            mode={mode}
            onMode={setMode}
            prompt={prompt}
            onPrompt={setPrompt}
            onSend={send}
          />
        </div>
      </div>
    </div>
  );
}
