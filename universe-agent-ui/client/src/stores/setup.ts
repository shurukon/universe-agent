/**
 * stores/setup.ts — first-run gate state (W3).
 *
 * The whole app routes through /welcome until the bridge says
 * setup_complete=true. One source of truth: GET /api/setup.
 */
import { create } from "zustand";
import { api } from "@/lib/api";

interface SetupStore {
  /** has the first GET /api/setup answered (bridge may still be starting) */
  checked: boolean;
  setupComplete: boolean;
  hasProvider: boolean;
  assistantName: string;
  check: () => Promise<void>;
  /** after a wizard provider install — don't wait for the next poll */
  markProvider: () => void;
  complete: (name: string) => Promise<string>;
}

export const useSetup = create<SetupStore>(set => ({
  checked: false,
  setupComplete: false,
  hasProvider: false,
  assistantName: "Universe",
  check: async () => {
    try {
      const r = await api.getSetup();
      set({
        checked: true,
        setupComplete: r.setup_complete,
        hasProvider: r.has_provider,
        assistantName: r.assistant_name,
      });
    } catch {
      // bridge unreachable: don't trap the user behind a loading screen —
      // the banner reports the outage and /welcome itself can retry
      set({ checked: true });
    }
  },
  /** after any provider install (wizard OR settings) — push the fresh
   *  truth into BOTH mirrors at once so they can never diverge again */
  markProvider: () => {
    set({ hasProvider: true });
    // lazy import avoids a module cycle (chat.ts never imports setup.ts)
    void import("@/stores/chat").then(m => {
      void m.useChat.getState().refreshProvider();
    });
  },
  complete: async (name: string) => {
    const r = await api.completeSetup(name);
    set({ setupComplete: true, assistantName: r.assistant_name });
    return r.assistant_name;
  },
}));
