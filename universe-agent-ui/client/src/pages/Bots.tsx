/**
 * pages/Bots.tsx — the universe bots surface.
 *
 * universe bots (OpenMausBot, renamed) is its own harness on 127.0.0.1:8799.
 * Its UI is framed here and fills the surface; the Universe | Studio | Bots
 * switcher floats on top of it, which is the only chrome this surface adds.
 *
 * The frame is the app's own origin, so the harness sees the same-origin
 * requests its desktop parent would, and the desktop adds the owner
 * capability that lets it act without a pairing code.
 *
 * The frame may also ask the Universe for something: today that is one
 * request, to make a bot by chatting. The answer belongs in a Universe
 * conversation, so the request opens one.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { api } from "@/lib/api";
import { useChat } from "@/stores/chat";
import WorkspaceSwitcher from "@/components/desktop/WorkspaceSwitcher";
import "../components/desktop/design-system.css";
import "../components/desktop/desktop.css";
import "../components/desktop/universe-bots.css";

type UniverseBots = {
  origin: string;
  up: boolean;
  app: string | null;
  version: string | null;
};

type BotsHostApi = {
  botsViewVisible?: (visible: boolean) => void;
  botsViewBounds?: (bounds: {
    x: number;
    y: number;
    width: number;
    height: number;
  }) => void;
  onNavigateSurface?: (cb: (surface: string) => void) => () => void;
  onCreateBotViaChat?: (cb: () => void) => () => void;
};

function hostApi(): BotsHostApi | null {
  return (window as unknown as { universe?: BotsHostApi }).universe ?? null;
}

const PROBE_MS = 5000;

const CREATE_VIA_CHAT_PROMPT =
  "Create a new bot in Universe Bots. Ask me what it should do, agree the " +
  "name and the job with me, then make it.";

export default function Bots() {
  const [state, setState] = useState<UniverseBots | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [, navigate] = useLocation();

  const probe = useCallback(async () => {
    try {
      setState(await api.universeBots());
    } catch {
      // The bridge is unreachable; the surface says so rather than guessing.
      setState(current => current ?? { origin: "", up: false, app: null, version: null });
    }
  }, []);

  useEffect(() => {
    void probe();
    const timer = window.setInterval(() => void probe(), PROBE_MS);
    return () => window.clearInterval(timer);
  }, [probe]);

  // The harness lives in a native WebContentsView owned by main. Show it only
  // while this route is mounted, and keep its bounds equal to the DOM host.
  useEffect(() => {
    const apiHost = hostApi();
    if (!apiHost) return;
    const sendBounds = () => {
      const rect = frameRef.current?.getBoundingClientRect();
      if (!rect) return;
      apiHost.botsViewBounds?.({
        x: rect.left,
        y: rect.top,
        width: rect.width,
        height: rect.height,
      });
    };
    const frame = requestAnimationFrame(() => {
      apiHost.botsViewVisible?.(true);
      sendBounds();
    });
    window.addEventListener("resize", sendBounds);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", sendBounds);
      apiHost.botsViewVisible?.(false);
    };
  }, []);

  // The transparent switcher view forwards a click here, where wouter lives.
  useEffect(() => {
    return hostApi()?.onNavigateSurface?.(surface => {
      if (surface === "universe" || surface === "studio") navigate(`/${surface}`);
    });
  }, [navigate]);

  // The bots view asks for a new bot by chat over its own desktop channel.
  useEffect(() => {
    const off = hostApi()?.onCreateBotViaChat?.(() => {
      const chat = useChat.getState();
      chat.newConversation();
      navigate("/chat");
      void chat.sendGoal(CREATE_VIA_CHAT_PROMPT);
    });
    return off;
  }, [navigate]);

  return (
    <div className="ubots-host">
      {state?.origin ? (
        hostApi() ? (
          // The real renderer is a native WebContentsView in main; this box
          // only defines its geometry and keeps the floating switcher above.
          <div ref={frameRef} className="ubots-frame" />
        ) : (
          <iframe
            className="ubots-frame"
            src={state.origin}
            title="universe bots"
            allow="clipboard-read; clipboard-write; microphone; camera"
          />
        )
      ) : (
        <div className="ubots-waiting">
          {state === null
            ? "Starting universe bots…"
            : "universe bots is not running. The desktop app starts it with Universe."}
        </div>
      )}
      <div className="ubots-bar">
        <WorkspaceSwitcher />
      </div>
    </div>
  );
}
