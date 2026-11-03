/**
 * pages/Studio.tsx — Universe Studio: the SAME workspace as Universe
 * (stable sidebar with tasks + projects, the same chat column, the same
 * composer) with exactly two differences: no Automations row and no
 * permission modes. The three tool cards live here and ONLY here:
 *
 *   Studio — the video editor powered by Universe (AVE embedded full)
 *   Design — websites, pages and presentations (open-design embedded)
 *   3D     — Blender; "Connect Blender" while the addon is unreachable
 *
 * Picking a tool is MANDATORY before the composer unlocks. The send goes
 * to the same agent engine (studio_tool rides the goal); the workspace
 * dock then opens over the chat:
 *   · design/video → the tool's real interface embedded (left sidebar never
 *     hides; the Universe timeline keeps streaming in the chat + Activity)
 *   · 3D → after send, when Blender is connected, a chooser: Background
 *     (headless work; tools + timeline here, result images at the end) or
 *     Live (the real Blender viewport pulled through the addon + timeline).
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
  type StudioService,
  type StudioServiceId,
} from "@/lib/studioApi";

/* ---- the three tool cards (Studio-only, Manus-language on Universe
        tokens) -------------------------------------------------------------- */

interface ToolCard {
  id: StudioTool;
  serviceId: StudioServiceId;
  name: string;
  sub: string;
  powered: string;
  icon: typeof Film;
}

const CARDS: readonly ToolCard[] = [
  {
    id: "video",
    serviceId: "ave",
    name: "Studio",
    sub: "Video editor powered by Universe",
    powered: "AVE · MCP",
    icon: Film,
  },
  {
    id: "design",
    serviceId: "opendesign",
    name: "Design",
    sub: "Websites, pages and presentations",
    powered: "open-design",
    icon: Presentation,
  },
  {
    id: "3d",
    serviceId: "blender",
    name: "3D",
    sub: "Model and render with Blender",
    powered: "blender-mcp",
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

/* ---- the workspace dock ----------------------------------------------------- */

function WorkspaceDock({
  tool,
  services,
  busyId,
  onStart,
  onSwitch,
}: {
  tool: StudioTool;
  services: StudioService[] | null;
  busyId: StudioServiceId | null;
  onStart: (svc: StudioService) => void;
  onSwitch: () => void;
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

  const embed = (() => {
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
                  Headless — tools + timeline stream here; the finished
                  renders appear when the task ends.
                </span>
              </button>
              <button
                type="button"
                className="studio-3d-opt"
                onClick={() => setMode3d("live")}
              >
                <b>Live</b>
                <span>
                  Watch the real Blender viewport while Universe drives it —
                  tool calls and the timeline side by side.
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
  })();

  return (
    <section className="studio-dock">
      <header className="studio-dock-head">
        <card.icon className="size-4" />
        <h2>{card.name}</h2>
        {service && <StatusChip status={service.status} />}
        <span className="studio-dock-sp" />
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
        {tool === "3d" && mode3d !== "choose" && (
          <button
            type="button"
            className="studio-dock-open"
            onClick={() => setMode3d("choose")}
          >
            Switch run mode
          </button>
        )}
        {service && tool !== "3d" && service.status === "running" && (
          <button
            type="button"
            className="studio-dock-open"
            disabled={busyId === service.id}
            onClick={() => {
              void import("@/lib/studioApi").then(m =>
                m.stopService(service.id).then(() =>
                  toast.success(`${service.name} stopped`)
                )
              );
            }}
          >
            <Square className="size-3" /> Stop
          </button>
        )}
        <button
          type="button"
          className="studio-dock-open"
          onClick={onSwitch}
          title="Back to the tool cards"
        >
          <X className="size-3.5" /> Switch tool
        </button>
      </header>
      <div className="studio-dock-body">{embed}</div>
    </section>
  );
}

/* ---- the page ---------------------------------------------------------------- */

export default function Studio() {
  const sidebarOpen = useUi(s => s.sidebarOpen);
  const railOpen = useRail(s => s.open);
  const openRail = useRail(s => s.openRail);
  const closeRail = useRail(s => s.closeRail);
  // the picked tool card — MANDATORY before the composer unlocks
  const [tool, setTool] = useState<StudioTool | null>(null);
  const [services, setServices] = useState<StudioService[] | null>(null);
  const [busyId, setBusyId] = useState<StudioServiceId | null>(null);
  const tasks = useChat(s => s.tasks);
  const isEmpty = useChat(s => s.messages.length === 0);
  const isExpanded = useChat(s => s.messages.length > 0);
  const fetchingRef = useRef(false);

  const running = tasks.find(t => t.status === "running") ?? null;
  const runningGoalId = running?.turn_id ?? null;

  // activity auto-open (the Universe timeline in the rail)
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
    if (hasRealWork && runningGoalId) openRail("activity");
  }, [hasRealWork, runningGoalId, openRail]);

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
          <ConsentBanner />
          <QuestionBanner />
          {tool !== null && (
            <WorkspaceDock
              tool={tool}
              services={services}
              busyId={busyId}
              onStart={svc => void startSvc(svc)}
              onSwitch={() => setTool(null)}
            />
          )}
          <Composer
            centered={isEmpty}
            variant="studio"
            studioTool={tool}
            onStudioTool={setTool}
          />
        </main>
        {/* the right rail MAY hide — it auto-opens with the timeline while
            a task runs and closes like in Universe */}
        {railOpen && (
          <Rail
            browserActive={false}
            onClose={closeRail}
          />
        )}
      </div>

      {/* the empty-state tool cards — the mandatory picker */}
      {isEmpty && (
        <div
          className="studio-cards-holder"
          style={{ left: sidebarOpen ? 264 : 0 }}
          aria-hidden={tool !== null}
        >
          <div className="studio-cards" role="group" aria-label="Pick a tool">
            <div className="studio-cards-col">
              {CARDS.filter(c => c.id !== "3d").map(card => {
                const Icon = card.icon;
                const active = tool === card.id;
                return (
                  <button
                    key={card.id}
                    type="button"
                    className={`studio-card${active ? " picked" : ""}`}
                    onClick={() => setTool(card.id)}
                    aria-pressed={active}
                  >
                    <span className="studio-card-icon">
                      <Icon size={16} strokeWidth={1.9} />
                    </span>
                    <span className="studio-card-body">
                      <span className="studio-card-title">
                        {card.name}
                        {active && (
                          <Check size={13} strokeWidth={2.4} aria-hidden="true" />
                        )}
                      </span>
                      <span className="studio-card-sub">{card.sub}</span>
                      <span className="studio-card-powered">
                        powered by {card.powered}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
            <BlenderCard
              service={services?.find(s => s.id === "blender") ?? null}
              active={tool === "3d"}
              busy={busyId === "blender"}
              onPick={() => {
                setTool("3d");
                const svc = services?.find(s => s.id === "blender");
                if (svc && svc.status !== "running") {
                  toast.info("Connect Blender", {
                    description:
                      "Open Blender and enable the blender-mcp addon " +
                      "(Edit → Preferences → Add-ons) — Universe connects " +
                      "to it on port 9876.",
                  });
                }
              }}
            />
          </div>
        </div>
      )}
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
          : "Open Blender with the blender-mcp addon to connect"}
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
