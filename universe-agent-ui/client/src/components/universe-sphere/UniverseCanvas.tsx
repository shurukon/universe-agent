"use client";

import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import {
  ACESFilmicToneMapping,
  Group,
  PerspectiveCamera,
  Vector2,
} from "three";
import type { UniverseData } from "@/lib/universe/generate";
import { MemorySphere } from "./MemorySphere";
import { shared, useIsolatedCanvasState } from "./shared";
import { useUniverse, type UniverseMode } from "./state";

/** Keeps point sizing resolution-independent. */
function ScaleHSync() {
  const { camera, gl } = useThree();
  const v = useMemo(() => new Vector2(), []);
  useFrame(() => {
    const cam = camera as PerspectiveCamera;
    shared.scaleH =
      gl.getDrawingBufferSize(v).y / (2 * Math.tan((cam.fov * Math.PI) / 360));
  });
  return null;
}

/**
 * Voice-mode driver. External control (no visible UI):
 *   window.dispatchEvent(new CustomEvent('universe:mode', { detail: 'listening' }))
 *   window.__universe.setMode('reasoning')
 * Reasoning completes into a soft response confirmation, then eases home.
 */
function ModeDriver() {
  const lastRef = useRef({ mode: "idle" as UniverseMode, at: 0 });
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const st = useUniverse.getState();
    const last = lastRef.current;
    if (st.mode !== last.mode) {
      last.mode = st.mode;
      last.at = t;
      if (st.mode === "response") shared.responsePulseAt = -1;
    }
    if (st.mode === "reasoning" && t - last.at > 6.5) st.setMode("response");
    else if (st.mode === "response" && t - last.at > 2.8) st.setMode("idle");
  });
  return null;
}

/**
 * The embedded scene — the ONLY mode left: MiniUniverse and ThinkingSpark
 * mount the scene in a tiny transparent canvas. Cube-corner camera, slow
 * auto-rotation scaled by live agent activity (events per second → spin),
 * no drag/wheel controls, cheap post chain, render loop paused when frozen.
 * The full-page /universe view was deleted: no universe page with a background
 * remains, only these background-free indicators.
 */
function EmbeddedScene({
  data,
  autoRotate,
  frozen,
  energy = 0,
}: {
  data: UniverseData;
  autoRotate: boolean;
  frozen: boolean;
  /** 0..1 live agent-activity level (events/sec, eased). Drives spin speed
   *  so the universe visibly "thinks harder" when agents work */
  energy?: number;
}) {
  // per-canvas isolation: saves module `shared` on mount, installs fresh
  // values, restores on unmount — a frozen mini canvas can never leak
  // `frozen=true`/`cull`/zoom into another canvas (or vice versa).
  useIsolatedCanvasState();
  const groupRef = useRef<Group>(null);
  const clockRef = useRef(0);
  const energyRef = useRef(energy);
  const threeClock = useThree(s => s.clock);
  energyRef.current = energy;
  useFrame((_, delta) => {
    if (frozen) return; // final frame stays — loop cost drops to ~zero
    clockRef.current += delta;
    if (autoRotate && groupRef.current) {
      // idle drift → working spin: activity speeds the visible thought.
      // The ease is per-frame so bursts (tools, handoffs) read as flurries.
      const spin = 0.18 + Math.min(energyRef.current, 1) * 0.55;
      groupRef.current.rotation.y += delta * spin;
    }
  });
  // propagate the freeze to the per-shader animators (Systems, Agents,
  // MemoryStars, CosmicCore read shared.frozen and hold their time)
  useEffect(() => {
    shared.frozenClock = threeClock;
    shared.frozen = frozen;
    if (frozen) {
      // hold the FINAL frame: capture the live clock at the freeze instant
      shared.frozenTime = threeClock.getElapsedTime();
    }
  }, [frozen, threeClock]);
  return (
    <>
      <ScaleHSync />
      <ModeDriver />
      <group ref={groupRef} rotation={[0.28, 0.65, 0.03]}>
        <MemorySphere data={data} embedded />
      </group>
    </>
  );
}

export interface UniverseCanvasProps {
  data: UniverseData;
  /** kept for call-site compatibility; embedded is now the only mode */
  embedded?: boolean;
  width?: number;
  height?: number;
  /** slow auto-rotation instead of drag */
  autoRotate?: boolean;
  /** honor prefers-reduced-motion */
  reducedMotion?: boolean;
  /** goal ended — hold the final frame (render loop to demand) */
  frozen?: boolean;
  /** kept for call-site compatibility (no interactive mode remains) */
  interactive?: boolean;
  /** 0..1 live activity level: spin energy while agents work */
  energy?: number;
}

export default function UniverseCanvas({
  data,
  embedded,
  width,
  height,
  autoRotate = true,
  reducedMotion = false,
  frozen: frozenProp = false,
  interactive,
  energy,
}: UniverseCanvasProps) {
  void embedded; // one mode now; the prop stays for call-site compatibility
  void interactive;
  const frozen = frozenProp || reducedMotion; // final frame stays — loop cost drops to ~zero
  return (
    <Canvas
      className="absolute inset-0"
      style={{ width, height }}
      gl={{
        antialias: false,
        alpha: true,
        stencil: false,
        powerPreference: "low-power",
      }}
      dpr={[1, 1.25]}
      frameloop={frozen ? "demand" : "always"}
      camera={{ fov: 40, near: 0.5, far: 600, position: [0, 22, 62] }}
      onCreated={({ gl }) => {
        gl.toneMapping = ACESFilmicToneMapping;
        gl.toneMappingExposure = 1.18;
        gl.setClearColor("#010104", 0);
      }}
    >
      <EmbeddedScene
        data={data}
        autoRotate={autoRotate && !reducedMotion}
        frozen={frozen}
        energy={energy}
      />
      <EffectComposer multisampling={0} enabled={!reducedMotion}>
        <Bloom
          mipmapBlur
          intensity={0.85}
          luminanceThreshold={0.42}
          luminanceSmoothing={0.3}
          radius={0.6}
        />
      </EffectComposer>
    </Canvas>
  );
}
