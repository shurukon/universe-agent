/**
 * lib/flow/railStore.ts — the shared right-rail state (M2/M3).
 * Chips inside the chat flow, the Files pane and the edge toggle all
 * drive the same rail through this store, so any surface can open a
 * preview without prop-drilling through the page.
 */
import { create } from "zustand";

export type RailTab =
  | "preview"
  | "browser"
  | "files"
  | "progress"
  | "terminal"
  | "activity"
  /** ZCode side-pane tabs */
  | "selectionchat"
  | "review"
  /** the ZCode "Open tab" picker — cards to choose which pane to show */
  | "open";

interface RailState {
  open: boolean;
  tab: RailTab;
  /** collapsed-to-capsule mode (ZCode Progress pane): the rail hides,
   *  a small capsule with the todo score stays; one click restores */
  capsule: boolean;
  strip: boolean;
  /** the file the Preview pane is showing (null = empty state) */
  previewPath: string | null;
  openRail: (tab?: RailTab) => void;
  openStrip: (tab?: RailTab) => void;
  closeRail: () => void;
  setTab: (tab: RailTab) => void;
  /** collapse the rail to its capsule (or back out of it) */
  toggleCapsule: () => void;
  /** open the rail on the Preview tab with a specific file */
  openPreview: (path: string) => void;
}

export const useRail = create<RailState>(set => ({
  open: false,
  tab: "files",
  capsule: false,
  strip: false,
  previewPath: null,
  openRail: tab =>
    set({
      open: true,
      capsule: false,
      strip: false,
      tab: tab ?? "open",
    }),
  openStrip: tab =>
    set({
      open: true,
      capsule: false,
      strip: true,
      tab: tab ?? "activity",
    }),
  closeRail: () => set({ open: false, strip: false }),
  setTab: tab => set({ tab }),
  toggleCapsule: () => set(s => ({ capsule: !s.capsule })),
  openPreview: path =>
    set({
      open: true,
      capsule: false,
      tab: "preview",
      previewPath: path,
    }),
}));
