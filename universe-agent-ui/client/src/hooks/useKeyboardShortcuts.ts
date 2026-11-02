/**
 * hooks/useKeyboardShortcuts.ts — the one global shortcut registry
 * (ZCode Phase 2). Mounted once by App; owns the keys the shell claims:
 *
 *   Ctrl+B  toggle sidebar
 *   Ctrl+K  command palette
 *   Ctrl+N  new task
 *   Ctrl+J  toggle the rail's Terminal pane (Phase C)
 *
 * Deliberately does NOT handle: Esc (the consent modal owns it), "/"
 * (the composer owns its own input surface), Enter (send path). Ctrl
 * combos never collide with plain text editing, so no focus checks.
 */
import { useEffect } from "react";
import { useLocation } from "wouter";
import { useChat } from "@/stores/chat";
import { useRail } from "@/lib/flow/railStore";
import { useUi } from "@/stores/ui";

export function useKeyboardShortcuts(): void {
  const [, navigate] = useLocation();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      const key = e.key.toLowerCase();
      if (key === "b") {
        e.preventDefault();
        useUi.getState().toggleSidebar();
      } else if (key === "k") {
        e.preventDefault();
        useUi.getState().openPalette();
      } else if (key === "n") {
        e.preventDefault();
        useChat.getState().newConversation();
        if (window.location.pathname !== "/chat") navigate("/chat");
        else if (window.location.search) navigate("/chat");
        const input =
          document.querySelector<HTMLTextAreaElement>(".composer textarea");
        input?.focus();
      } else if (key === "j") {
        e.preventDefault();
        // ZCode behavior: Ctrl+J toggles the terminal pane — open when
        // closed (or another tab is showing), close when it's on screen
        const rail = useRail.getState();
        if (rail.open && rail.tab === "terminal") rail.closeRail();
        else rail.openRail("terminal");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // wouter's navigate is stable; the handlers read stores imperatively
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
