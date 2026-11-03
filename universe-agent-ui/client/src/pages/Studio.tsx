/**
 * pages/Studio.tsx — Universe Studio: the SAME workspace as Universe
 * (stable sidebar with tasks + projects, the same chat column, the same
 * composer). The three tool cards live IN the flow, directly ABOVE the
 * composer — they never cover the chat:
 *
 *   Studio — the video editor powered by Universe (built-in StudioAVE)
 *   Design — websites, pages and presentations (built-in StudioDesign)
 *   3D     — Blender (built-in StudioBlender); "Connect Blender" while
 *            the addon is unreachable
 *
 * Clicking a card ONLY selects it (highlights it and unlocks the
 * composer) — no window, no dock, nothing opens on click. After the user
 * SENDS the task, the preview opens as a large right sidebar showing what
 * the agent is doing, while the normal Universe timeline (tool calls in
 * the chat flow) keeps streaming on the left. The same flow for all
 * three tools:
 *   · video → AVE's real editor UI embedded
 *   · design → the open-design workspace on Universe tokens
 *   · 3D → after send, a chooser: Background (headless work; tools +
 *     timeline here, renders at the end) or Live (the real Blender
 *     viewport pulled through the addon), both beside the timeline
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ExternalLink,
  Loader2,
  MonitorDot,
  Play,
  Presentation,
  RefreshCw,
  Square,
  Film,
  Cable,
  X,
} from "lucide-react";
import { toast } from "sonner";
import Sidebar from "@/components/desktop/Sidebar";
import ChatMessages from "@/components/desktop/ChatMessages";
import Composer, { type StudioTool } from "@/components/desktop/Composer";
import ConsentBanner from "@/components/desktop/ConsentBanner";
import QuestionBanner from "@/components/desktop/QuestionBanner";
import OfflineBanner from "@/components/desktop/OfflineBanner";
import PlanPill from "@/components/desktop/PlanPill";
import { api } from "@/lib/api";
import { useChat } from "@/stores/chat";
import { useUi } from "@/stores/ui";
import { useRail } from "@/lib/flow/railStore";
import Rail from "@/components/desktop/Rail";
import DesignWorkspace from "@/components/desktop/DesignWorkspace";
import "../components/desktop/design-system.css";
import "../components/desktop/desktop.css";
import "../components/desktop/redesign.css";
import "../components/desktop/settings-linear.css";
import "../components/desktop/flow.css";
import "../components/desktop/studio.css";
import {
  listServices,
  startService,
  stopService,
  type StudioService,
  type StudioServiceId,
} from "@/lib/studioApi";

/* ---- the three tool cards (built-in Studio tools) ------------------------ */

interface ToolCard {
  id: StudioTool;
  serviceId: StudioServiceId;
  name: string;
  sub: string;
  icon: typeof Film;
}

const CARDS: readonly ToolCard[] = [
  {
    id: "video",
    serviceId: "ave",
    name: "Studio",
    sub: "Video editor powered by Universe",
    icon: Film,
  },
  {
    id: "design",
    serviceId: "opendesign",
    name: "Design",
    sub: "Websites, pages and presentations",
    icon: Presentation,
  },
  {
    id: "3d",
    serviceId: "blender",
    name: "3D",
    sub: "Model and render with Blender",
    icon: MonitorDot,
  },
];

/* ---- status chip ----------------------------------------------------------- */

function StatusChip({ status }: { status: StudioService["status"] }) {
  const map: Record<string, { label: string; cls: string }> = {
    running: { label: "Running", cls: "st-running" },
    starting: { label: "Starting", cls: "st-starting" },
    stopped: { label: "Stopped", cls: "st-stopped" },
    unreachable: { label: "Unreachable", cls: "st-stopped" },
    unconfigured: { label: "Not configured", cls: "st-stopped" },
  };
  const meta = map[status] ?? map.stopped;
  return <span className={`studio-chip ${meta.cls}`}>{meta.label}</span>;
}

/* ---- the preview panel body -------------------------------------------------- */

function PreviewBody({
  tool,
  services,
  busyId,
  onStart,
}: {
  tool: StudioTool;
  services: StudioService[] | null;
  busyId: StudioServiceId | null;
  onStart: (svc: StudioService) => void;
}) {
  const card = CARDS.find(c => c.id === tool)!;
  const service =
    services?.find(s => s.id === card.serviceId) ?? null;
  const [viewport, setViewport] = useState<string | null>(null);
  const [viewportErr, setViewportErr] = useState("");
  const [viewportBusy, setViewportBusy] = useState(false);
  const [mode3d, setMode3d] = useState<"choose" | "background" | "live">(
    "choose"
  );

  const refreshViewport = useCallback(async () => {
    setViewportBusy(true);
    setViewportErr("");
    try {
      const r = await api.blenderViewport();
      setViewport(`data:image/png;base64,${r.b64}`);
    } catch (e) {
      setViewport(null);
      setViewportErr(
        e instanceof Error && e.message
          ? e.message
          : "Blender viewport is unreachable"
      );
    } finally {
      setViewportBusy(false);
    }
  }, []);

  // live 3D pulls one real viewport frame every 5s
  useEffect(() => {
    if (tool !== "3d" || mode3d !== "live") return;
    void refreshViewport();
    const id = window.setInterval(() => void refreshViewport(), 5000);
    return () => window.clearInterval(id);
  }, [tool, mode3d, refreshViewport]);

  if (!service) {
    return (
      <div className="studio-dock-pending">
        <Loader2 className="size-4 animate-spin" /> Reaching the bridge…
      </div>
    );
  }

  if (tool === "3d") {
    if (mode3d === "choose") {
      return (
        <div className="studio-dock-pending studio-3d-choose">
          <p className="studio-3d-q">
            How should Universe run this 3D task?
          </p>
          <div className="studio-3d-opts">
            <button
              type="button"
              className="studio-3d-opt"
              onClick={() => setMode3d("background")}
            >
              <b>Background</b>
              <span>
                Headless — tool calls + the timeline stream in the chat;
                the finished renders appear here when the task ends.
              </span>
            </button>
            <button
              type="button"
              className="studio-3d-opt"
              onClick={() => setMode3d("live")}
            >
              <b>Live</b>
              <span>
                Watch the real Blender viewport here while Universe drives
                it — tool calls and the timeline stream beside it.
              </span>
            </button>
          </div>
        </div>
      );
    }
    if (mode3d === "live") {
      return (
        <div className="studio-viewport">
          {viewport ? (
            <img src={viewport} alt="Blender viewport (live)" />
          ) : viewportBusy ? (
            <div className="studio-dock-pending">
              <Loader2 className="size-4 animate-spin" /> Reading the
              Blender viewport…
            </div>
          ) : (
            <div className="studio-dock-pending studio-viewport-err">
              {viewportErr || "Blender viewport is unreachable"}
              <button
                type="button"
                className="studio-dock-open"
                onClick={() => void refreshViewport()}
              >
                <RefreshCw className="size-3.5" /> Retry
              </button>
            </div>
          )}
          <button
            type="button"
            className="studio-dock-open"
            onClick={() => void refreshViewport()}
            disabled={viewportBusy}
          >
            <RefreshCw className={viewportBusy ? "size-3.5 spin" : "size-3.5"} />
            Refresh viewport
          </button>
        </div>
      );
    }
    return (
      <div className="studio-dock-pending">
        <Loader2 className="size-4 animate-spin" /> Working in the
        background — tool calls and the timeline stream in the chat; the
        finished renders appear here.
      </div>
    );
  }

  if (service.status === "running") {
    // Design gets its REAL workspace (Universe design system over the
    // daemon's live data); AVE embeds its own editor UI.
    if (tool === "design") return <DesignWorkspace />;
    return (
      <iframe
        key={service.url}
        src={`${service.url}${service.url.includes("?") ? "&" : "?"}embed=1`}
        title={card.name}
        className="studio-embed"
      />
    );
  }
  if (service.status === "starting") {
    return (
      <div className="studio-dock-pending">
        <Loader2 className="size-4 animate-spin" /> Starting {service.name}…
      </div>
    );
  }
  return (
    <div className="studio-dock-pending">
      {service.name} is not running.
      <button
        type="button"
        className="studio-dock-open"
        disabled={busyId === service.id}
        onClick={() => onStart(service)}
      >
        <Play className="size-3.5" /> Start {service.name}
      </button>
    </div>
  );
}

/* ---- the preview right sidebar (opens AFTER the task is sent) --------------- */

function StudioPreview({
  tool,
  services,
  busyId,
  onStart,
  onStop,
  onClose,
}: {
  tool: StudioTool;
  services: StudioService[] | null;
  busyId: StudioServiceId | null;
  onStart: (svc: StudioService) => void;
  onStop: (svc: StudioService) => void;
  onClose: () => void;
}) {
  const card = CARDS.find(c => c.id === tool)!;
  const service =
    services?.find(s => s.id === card.serviceId) ?? null;
  return (
    <aside className="studio-preview" aria-label={`${card.name} preview`}>
      <header className="studio-preview-head">
        <card.icon className="size-4" />
        <h2>{card.name}</h2>
        {service && <StatusChip status={service.status} />}
        <span className="studio-preview-sp" />
        {service?.status === "running" && tool !== "3d" && (
          <button
            type="button"
            className="studio-dock-open"
            onClick={() => window.open(service.url, "_blank", "noopener")}
            title="Open in a new window"
          >
            <ExternalLink className="size-3.5" /> Open
          </button>
        )}
        {service && tool !== "3d" && service.status === "running" && (
          <button
            type="button"
            className="studio-dock-open"
            disabled={busyId === service.id}
            onClick={() => onStop(service)}
          >
            <Square className="size-3" /> Stop
          </button>
        )}
        <button
          type="button"
          className="studio-dock-open"
          onClick={onClose}
          title="Close the preview"
        >
          <X className="size-3.5" />
        </button>
      </header>
      <div className="studio-preview-body">
        <PreviewBody
          tool={tool}
          services={services}
          busyId={busyId}
          onStart={onStart}
        />
      </div>
    </aside>
  );
}

/* ---- the page ---------------------------------------------------------------- */

export default function Studio() {
  const sidebarOpen = useUi(s => s.sidebarOpen);
  const railOpen = useRail(s => s.open);
  const openRail = useRail(s => s.openRail);
  const closeRail = useRail(s => s.closeRail);
  // the picked tool card — MANDATORY before the composer unlocks. Clicking
  // a card ONLY selects it; nothing opens until the task is sent.
  const [tool, setTool] = useState<StudioTool | null>(null);
  const [services, setServices] = useState<StudioService[] | null>(null);
  const [busyId, setBusyId] = useState<StudioServiceId | null>(null);
  // the preview sidebar opens when a sent task starts running and stays
  // pinned until the user closes it or switches the tool
  const [previewPinned, setPreviewPinned] = useState(false);
  const tasks = useChat(s => s.tasks);
  const isEmpty = useChat(s => s.messages.length === 0);
  const isExpanded = useChat(s => s.messages.length > 0);
  const fetchingRef = useRef(false);

  const running = tasks.find(t => t.status === "running") ?? null;
  const runningGoalId = running?.turn_id ?? null;

  // a new send re-pins the preview (a manual close only hides it until
  // the next task starts)
  useEffect(() => {
    if (runningGoalId && tool !== null) setPreviewPinned(true);
  }, [runningGoalId, tool]);
  const previewOpen = previewPinned && tool !== null;

  // activity auto-open (the Universe timeline in the rail) — only when the
  // preview sidebar is not occupying the right edge; the chat flow itself
  // always streams the tool calls
  const hasRealWork = useChat(s => {
    if (!runningGoalId) return false;
    const evs = s.eventsByGoal[runningGoalId];
    if (!evs) return false;
    return evs.some(
      e =>
        e.type === "tool_running" ||
        e.type === "tool_finished" ||
        e.type === "tool_failed"
    );
  });
  useEffect(() => {
    if (hasRealWork && runningGoalId && !previewOpen) openRail("activity");
  }, [hasRealWork, runningGoalId, previewOpen, openRail]);

  const refresh = useCallback(async () => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      setServices(await listServices());
    } catch {
      /* keep the last known statuses */
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

  const startSvc = useCallback(
    async (svc: StudioService) => {
      if (busyId) return;
      setBusyId(svc.id);
      try {
        const updated = await startService(svc.id);
        setServices(prev =>
          prev ? prev.map(s => (s.id === updated.id ? updated : s)) : prev
        );
        toast.success(
          updated.status === "running"
            ? `${svc.name} is running`
            : `${svc.name} is starting`
        );
      } catch (e) {
        toast.error(
          `Could not start ${svc.name}`,
          {
            description:
              e instanceof Error && e.message ? e.message : "bridge unreachable",
          }
        );
      } finally {
        setBusyId(null);
      }
    },
    [busyId]
  );

  const stopSvc = useCallback(async (svc: StudioService) => {
    setBusyId(svc.id);
    try {
      await stopService(svc.id);
      setServices(prev =>
        prev
          ? prev.map(s =>
              s.id === svc.id ? { ...s, status: "stopped" as const } : s
            )
          : prev
      );
      toast.success(`${svc.name} stopped`);
    } catch (e) {
      toast.error(
        `Could not stop ${svc.name}`,
        {
          description:
            e instanceof Error && e.message ? e.message : "bridge unreachable",
        }
      );
    } finally {
      setBusyId(null);
    }
  }, []);

  return (
    <div className="app-root">
      <OfflineBanner />
      <div className={`app${sidebarOpen ? "" : " sb-closed"}`}>
        {/* the left sidebar NEVER hides in Studio — same tasks, same
            projects, same tokens; only the Automations row is absent */}
        <Sidebar open={sidebarOpen} surface="studio" />
        <main
          className={`main studio-main${isEmpty ? " empty" : ""}${isExpanded ? " main--expanded" : ""}`}
        >
          <PlanPill />
          <ChatMessages />
          {/* the tool picker lives IN the flow, directly above the
              composer — it never covers the chat */}
          {tool === null && (
            <div className="studio-picker" role="group" aria-label="Pick a tool">
              <div className="studio-picker-cards">
                <div className="studio-cards-col">
                  {CARDS.filter(c => c.id !== "3d").map(card => {
                    const Icon = card.icon;
                    return (
                      <button
                        key={card.id}
                        type="button"
                        className="studio-card"
                        onClick={() => setTool(card.id)}
                        aria-pressed={false}
                      >
                        <span className="studio-card-icon">
                          <Icon size={16} strokeWidth={1.9} />
                        </span>
                        <span className="studio-card-body">
                          <span className="studio-card-title">{card.name}</span>
                          <span className="studio-card-sub">{card.sub}</span>
                          <span className="studio-card-powered">
                            built into studio
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
                <BlenderCard
                  service={services?.find(s => s.id === "blender") ?? null}
                  active={false}
                  busy={busyId === "blender"}
                  onPick={() => {
                    setTool("3d");
                    const svc = services?.find(s => s.id === "blender");
                    if (svc && svc.status !== "running") {
                      toast.info("Connect Blender", {
                        description:
                          "Open Blender and enable the Universe 3D addon " +
                          "(Edit → Preferences → Add-ons) — Universe " +
                          "connects to it on port 9876.",
                      });
                    }
                  }}
                />
              </div>
            </div>
          )}
          <ConsentBanner />
          <QuestionBanner />
          <Composer
            centered={isEmpty}
            variant="studio"
            studioTool={tool}
            onStudioTool={setTool}
          />
        </main>
        {/* the preview sidebar — opens AFTER the task is sent, never on
            click; the timeline keeps streaming in the chat beside it */}
        {previewOpen && (
          <StudioPreview
            tool={tool as StudioTool}
            services={services}
            busyId={busyId}
            onStart={svc => void startSvc(svc)}
            onStop={svc => void stopSvc(svc)}
            onClose={() => setPreviewPinned(false)}
          />
        )}
        {/* the right rail MAY hide — it auto-opens with the timeline while
            a task runs and the preview is closed */}
        {railOpen && (
          <Rail
            browserActive={false}
            onClose={closeRail}
          />
        )}
      </div>
    </div>
  );
}

/* ---- the square 3D card (right) ------------------------------------------------ */

function BlenderCard({
  service,
  active,
  busy,
  onPick,
}: {
  service: StudioService | null;
  active: boolean;
  busy: boolean;
  onPick: () => void;
}) {
  const connected = service?.status === "running";
  return (
    <button
      type="button"
      className={`studio-card studio-card-tall${active ? " picked" : ""}`}
      onClick={onPick}
      aria-pressed={active}
    >
      <span className="studio-card-eyebrow">
        {connected ? (
          <>
            <Cable size={11} strokeWidth={2} /> Blender connected
          </>
        ) : (
          <>
            <Cable size={11} strokeWidth={2} /> Connect Blender
          </>
        )}
      </span>
      <span className="studio-card-art">
        <MonitorDot size={26} strokeWidth={1.5} />
      </span>
      <span className="studio-card-title-c">3D</span>
      <span className="studio-card-sub-c">
        {connected
          ? "Addon reachable on port 9876 — model and render"
          : "Open Blender with the Universe 3D addon to connect"}
      </span>
      {service?.status === "starting" && (
        <span className="studio-card-note">
          <Loader2 className="size-3 animate-spin" /> waiting for the addon…
        </span>
      )}
      {busy && <span className="sr-only">starting</span>}
    </button>
  );
}
