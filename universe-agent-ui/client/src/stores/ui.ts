/**
 * stores/ui.ts — shell chrome state (ZCode Phase 2): sidebar visibility,
 * the command palette, and the session navigation history the TopBar's
 * back/forward buttons drive.
 *
 * Deliberately separate from stores/chat.ts: this is UI chrome, not
 * conversation data — a back navigation must never touch the message
 * pipeline, and the palette must survive page-level remounts.
 */
import { create } from "zustand";

export type PaletteTab = "all" | "actions" | "tasks" | "files";

interface NavState {
  /** the visited URLs, oldest first — the back/forward stacks share it */
  entries: string[];
  /** current index into entries; -1 until the first location lands */
  index: number;
}

interface UiStore {
  sidebarOpen: boolean;
  toggleSidebar: (force?: boolean) => void;

  /** Computer Use chip (ZCode parity): a real local toggle — the flag is
   *  part of this build's state ("dev sim ignores it") and shown pressed */
  computerUse: boolean;
  toggleComputerUse: () => void;

  paletteOpen: boolean;
  paletteTab: PaletteTab;
  openPalette: (tab?: PaletteTab) => void;
  closePalette: () => void;
  setPaletteTab: (t: PaletteTab) => void;

  // ---- open task tabs (ZCode's tab strip) ----
  /** goal ids dismissed from the strip — the tasks themselves survive in
   *  the sidebar and in chat state; opening one re-adds its tab */
  closedTabs: string[];
  closeTab: (turnId: string) => void;
  reopenTab: (turnId: string) => void;

  // ---- composer seed (Edit on a sent message: prefill + focus) ----
  /** the threaded model continues the session, so ZCode's Edit-and-resend
   *  maps to seeding the composer with the message text and focusing it */
  composerSeed: { text: string; seq: number } | null;
  seedComposer: (text: string) => void;
  clearSeed: () => void;

  // ---- session navigation history (TopBar back/forward) ----
  nav: NavState;
  /** record a location change NOT caused by back/forward itself */
  recordNav: (location: string) => void;
  canBack: () => boolean;
  canForward: () => boolean;
  /** move in history; returns the target URL or null at the boundary */
  navBack: () => string | null;
  navForward: () => string | null;
}

const NAV_LIMIT = 50;

export const useUi = create<UiStore>((set, get) => ({
  sidebarOpen: true,
  toggleSidebar: force =>
    set(s => ({ sidebarOpen: force !== undefined ? force : !s.sidebarOpen })),

  composerSeed: null,
  seedComposer: text => set({ composerSeed: { text, seq: Date.now() } }),
  clearSeed: () => set(s => (s.composerSeed ? { composerSeed: null } : s)),

  computerUse: false,
  toggleComputerUse: () => set(s => ({ computerUse: !s.computerUse })),

  paletteOpen: false,
  paletteTab: "all",
  openPalette: tab =>
    set(s => ({ paletteOpen: true, paletteTab: tab ?? s.paletteTab })),
  closePalette: () => set({ paletteOpen: false }),
  setPaletteTab: t => set({ paletteTab: t }),

  nav: { entries: [], index: -1 },
  closedTabs: [],
  closeTab: turnId =>
    set(s => ({
      closedTabs: s.closedTabs.includes(turnId)
        ? s.closedTabs
        : [...s.closedTabs, turnId],
    })),
  reopenTab: turnId =>
    set(s => ({ closedTabs: s.closedTabs.filter(id => id !== turnId) })),
  recordNav: location => {
    // wouter fires this for every location change. Back/forward nav also
    // lands here — those moves are applied by navBack/navForward BEFORE
    // wouter delivers the new location, so a location equal to the
    // adjacent history entry is a step (no push); everything else pushes.
    const { nav } = get();
    if (nav.index >= 0 && nav.entries[nav.index + 1] === location) {
      set({ nav: { entries: nav.entries, index: nav.index + 1 } });
      return;
    }
    if (nav.index > 0 && nav.entries[nav.index - 1] === location) {
      set({ nav: { entries: nav.entries, index: nav.index - 1 } });
      return;
    }
    if (nav.entries[nav.index] === location) return; // same URL re-render
    const entries = [...nav.entries.slice(0, nav.index + 1), location].slice(
      -NAV_LIMIT
    );
    set({ nav: { entries, index: entries.length - 1 } });
  },
  canBack: () => get().nav.index > 0,
  canForward: () => get().nav.index < get().nav.entries.length - 1,
  navBack: () => {
    const { nav } = get();
    if (nav.index <= 0) return null;
    const target = nav.entries[nav.index - 1];
    set({ nav: { entries: nav.entries, index: nav.index - 1 } });
    return target;
  },
  navForward: () => {
    const { nav } = get();
    if (nav.index >= nav.entries.length - 1) return null;
    const target = nav.entries[nav.index + 1];
    set({ nav: { entries: nav.entries, index: nav.index + 1 } });
    return target;
  },
}));
