'use client'

import { useEffect, useRef } from 'react'
import type { Group } from 'three'

/**
 * Mirror a (useMemo-built) object into a ref so imperative render-loop code
 * can mutate it without violating React Compiler immutability rules.
 * Three.js objects are external system state; refs are the sanctioned escape.
 */
export function useMirror<T>(value: T): React.RefObject<T> {
  const ref = useRef(value)
  useEffect(() => {
    ref.current = value
  }, [value])
  return ref
}

export interface PickTarget {
  kind: 'system' | 'star' | 'gate' | 'core'
  id: number
  pos: [number, number, number]
  radius: number
}

/**
 * Cross-component runtime references (avoids re-render churn inside the
 * render loop). The sphere group rotates; the camera dollies and eases.
 *
 * CANVAS ISOLATION: the full-page universe and the MiniUniverse each mount their
 * own <Canvas>, but the module-level `shared` object used to be written by
 * both (frozen/cull/zoom/sphere). The embedded canvas holding `frozen=true`
 * after unmount would leave the full canvas frozen forever. Each canvas now
 * owns a snapshot via `createCanvasState()`; the module `shared` remains as
 * the full-canvas default for the imperative readers that don't take a state
 * prop, and every writer saves + restores it on unmount.
 */
export interface CanvasState {
  /** rotating memory-sphere group */
  sphere: Group | null
  /** drag inertia around the sphere's axis */
  rotVel: number
  /** pointer NDC, -2 when off-canvas */
  ndc: { x: number; y: number }
  /** camera dolly (aspect-aware limits, updated by Controls) */
  zoom: { dist: number; target: number; home: number; min: number; max: number }
  /** runtime particle cull (0 .. 0.65), raised by the perf governor */
  cull: number
  cullUniforms: Set<{ value: number }>
  /** pickable elements rebuilt once per scene */
  pickTargets: PickTarget[]
  /** scale factor for point sizing (drawingBufferHeight / (2 tan(fov/2))) */
  scaleH: number
  /** response confirmation shell request: -1 = start on next frame */
  responsePulseAt: number
  /** global animation freeze (MiniUniverse final frame): per-frame animators
   *  hold the last time value instead of advancing with the clock */
  frozen: boolean
  /** the held time value while frozen (captured at freeze instant) */
  frozenTime: number
  /** the three.js Clock instance shared by the canvas (set by EmbeddedScene
   *  so the freeze can capture the live time at the freeze instant) */
  frozenClock: { getElapsedTime(): number } | null
}

export function createCanvasState(): CanvasState {
  return {
    sphere: null,
    rotVel: 0,
    ndc: { x: -2, y: -2 },
    zoom: { dist: 66, target: 66, home: 66, min: 32, max: 148 },
    cull: 0,
    cullUniforms: new Set(),
    pickTargets: [],
    scaleH: 800,
    responsePulseAt: -10,
    frozen: false,
    frozenTime: 0,
    frozenClock: null,
  }
}

function snapshotCanvasState(s: CanvasState): CanvasState {
  return {
    ...s,
    ndc: { ...s.ndc },
    zoom: { ...s.zoom },
    cullUniforms: new Set(s.cullUniforms),
    pickTargets: [...s.pickTargets],
  }
}

function restoreCanvasState(s: CanvasState, snap: CanvasState): void {
  s.sphere = snap.sphere
  s.rotVel = snap.rotVel
  s.ndc = snap.ndc
  s.zoom = snap.zoom
  s.cull = snap.cull
  s.cullUniforms = snap.cullUniforms
  s.pickTargets = snap.pickTargets
  s.scaleH = snap.scaleH
  s.responsePulseAt = snap.responsePulseAt
  s.frozen = snap.frozen
  s.frozenTime = snap.frozenTime
  s.frozenClock = snap.frozenClock
}

/**
 * Scope the module `shared` state to one canvas for the duration of a mount:
 * saves the current values, installs a fresh isolated state, and restores on
 * unmount — so an embedded canvas can never leak `frozen`/`cull`/zoom into
 * the full-page universe (or vice versa).
 */
export function useIsolatedCanvasState(): void {
  useEffect(() => {
    const snap = snapshotCanvasState(shared)
    const fresh = createCanvasState()
    // install fresh per-canvas values (mutating the module object keeps the
    // existing `shared.xxx` import sites working unchanged)
    restoreCanvasState(shared, fresh)
    return () => {
      restoreCanvasState(shared, snap)
    }
  }, [])
}

export const shared: CanvasState = createCanvasState()

/** Desktop framing: preserve the reference composition while making the sphere distinctly more commanding. */
export const ZOOM_MIN = 32
export const ZOOM_MAX = 148
export const ZOOM_HOME = 66
