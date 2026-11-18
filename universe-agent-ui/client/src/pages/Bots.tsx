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
import React, { createElement, useCallback, useEffect, useRef, useState } from "react";
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

const PROBE_MS = 5000;

/** Electron's <webview>: its own renderer, so universe bots' preload runs and
 *  it gets window.ogb — the browser and computer-use capabilities an iframe
 *  cannot have. React renders unknown tags as custom elements. */
const CREATE_VIA_CHAT = "universe:create-bot-via-chat";

const CREATE_VIA_CHAT_PROMPT =
  "Create a new bot in Universe Bots. Ask me what it should do, agree the " +
  "name and the job with me, then make it.";

export default function Bots() {
  const [state, setState] = useState<UniverseBots | null>(null);
  const [botsPreload, setBotsPreload] = useState("");
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const webviewRef = useRef<HTMLElement | null>(null);
  const [, navigate] = useLocation();

  useEffect(() => {
    const bridge = (window as unknown as {
      universe?: { botsPreload?: () => Promise<string> };
    }).universe;
    void bridge?.botsPreload?.().then(setBotsPreload).catch(() => undefined);
  }, []);

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

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as { type?: string } | null;
      if (data?.type !== CREATE_VIA_CHAT) return;
      const chat = useChat.getState();
      chat.newConversation();
      navigate("/chat");
      void chat.sendGoal(CREATE_VIA_CHAT_PROMPT);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [navigate]);

  return (
    <div className="ubots-host">
      {state?.origin ? (
        <iframe
          ref={frameRef}
          className="ubots-frame"
          src={state.origin}
          title="universe bots"
          allow="clipboard-read; clipboard-write; microphone; camera"
        />
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
