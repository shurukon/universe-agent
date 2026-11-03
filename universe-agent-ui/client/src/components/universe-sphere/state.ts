'use client'

import { create } from 'zustand'
import type { Quality } from '@/lib/universe/generate'

export type UniverseMode = 'idle' | 'listening' | 'reasoning' | 'execution' | 'response'

export interface TargetRef {
  kind: 'system' | 'star' | 'gate' | 'core'
  id: number
  pos: [number, number, number]
}

interface UniverseState {
  mode: UniverseMode
  hover: TargetRef | null
  selected: TargetRef | null
  focusSystem: number
  hoverPlanet: number
  reducedMotion: boolean
  quality: Quality
  setMode: (m: UniverseMode) => void
  setHover: (t: TargetRef | null) => void
  select: (t: TargetRef | null) => void
  setFocusSystem: (id: number) => void
  setHoverPlanet: (id: number) => void
  setReducedMotion: (b: boolean) => void
  setQuality: (q: Quality) => void
}

export const useUniverse = create<UniverseState>((set) => ({
  mode: 'idle',
  hover: null,
  selected: null,
  focusSystem: -1,
  hoverPlanet: -1,
  reducedMotion: false,
  quality: 'high',
  setMode: (mode) => set({ mode }),
  setHover: (hover) => set({ hover }),
  select: (t) =>
    set({
      selected: t,
      hover: null,
      focusSystem: t && t.kind === 'system' ? t.id : -1,
      hoverPlanet: -1,
    }),
  setFocusSystem: (focusSystem) => set({ focusSystem }),
  setHoverPlanet: (hoverPlanet) => set({ hoverPlanet }),
  setReducedMotion: (reducedMotion) => set({ reducedMotion }),
  setQuality: (quality) => set({ quality }),
}))
