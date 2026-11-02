'use client'

import { useEffect, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import {
  AdditiveBlending,
  BufferGeometry,
  BufferAttribute,
  Float32BufferAttribute,
  Line,
  LineBasicMaterial,
  Points,
} from 'three'
import type { UniverseData } from '@/lib/universe/generate'
import { hash32 } from '@/lib/universe/generate'
import { c3, PALETTE } from '@/lib/universe/palette'
import { makeStarfieldMaterial } from './glsl'
import { shared, useMirror } from './shared'
import { useUniverse } from './state'

const AGENTS = 8
const TRAIL = 26

interface AgentState {
  active: boolean
  wait: number
  t: number
  dur: number
  p0: [number, number, number]
  p1: [number, number, number]
  p2: [number, number, number]
  color: [number, number, number]
}

/**
 * Agent & task trajectories: tiny comet-like signal particles moving between
 * systems along short curved paths, carrying intent, context and results.
 * Extremely fine transparent trails, restrained speeds, quiet activity.
 */
export function Agents({ data }: { data: UniverseData }) {
  const parts = useMemo(() => {
    const lines: Line[] = []
    const colors = [
      c3(PALETTE.photonCyan, 0.7),
      c3(PALETTE.violet, 0.62),
      c3(PALETTE.coolWhite, 0.55),
    ]
    for (let i = 0; i < AGENTS; i++) {
      const g = new BufferGeometry()
      g.setAttribute('position', new Float32BufferAttribute(new Float32Array(TRAIL * 3), 3))
      const col = new Float32Array(TRAIL * 3)
      const c = colors[i % colors.length]
      for (let k = 0; k < TRAIL; k++) {
        const fade = Math.pow(k / (TRAIL - 1), 1.6)
        col[k * 3] = c[0] * fade
        col[k * 3 + 1] = c[1] * fade
        col[k * 3 + 2] = c[2] * fade
      }
      g.setAttribute('color', new BufferAttribute(col, 3))
      const mat = new LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        blending: AdditiveBlending,
        depthWrite: false,
        opacity: 0.85,
      })
      const line = new Line(g, mat)
      line.frustumCulled = false
      line.visible = false
      lines.push(line)
    }

    const headGeo = new BufferGeometry()
    headGeo.setAttribute('position', new Float32BufferAttribute(new Float32Array(AGENTS * 3), 3))
    headGeo.setAttribute('aColor', new Float32BufferAttribute(new Float32Array(AGENTS * 3), 3))
    headGeo.setAttribute('aSize', new Float32BufferAttribute(new Float32Array(AGENTS).fill(0.5), 1))
    headGeo.setAttribute('aTw', new Float32BufferAttribute(new Float32Array(AGENTS), 1))
    headGeo.setAttribute('aPh', new Float32BufferAttribute(new Float32Array(AGENTS), 1))
    headGeo.setAttribute('aRnd', new Float32BufferAttribute(new Float32Array(AGENTS).fill(0.95), 1))
    const headMat = makeStarfieldMaterial()
    headMat.uniforms.uIntensity.value = 1.35
    const heads = new Points(headGeo, headMat)
    heads.frustumCulled = false

    const st: AgentState[] = Array.from({ length: AGENTS }, (_, i) => ({
      active: false,
      wait: 2 + i * 1.7 + Math.random() * 3,
      t: 0,
      dur: 1,
      p0: [0, -9999, 0],
      p1: [0, 0, 0],
      p2: [0, 0, 0],
      color: colors[i % colors.length],
    }))
    return { lines, heads, headMat, st }
  }, [data])

  const P = useMirror(parts)

  useEffect(
    () => () => {
      for (const l of parts.lines) {
        l.geometry.dispose()
        ;(l.material as { dispose: () => void }).dispose()
      }
      parts.heads.geometry.dispose()
      ;(parts.heads.material as { dispose: () => void }).dispose()
    },
    [parts]
  )

  useEffect(() => {
    const onDispatch = () => {
      // execution mode: dispatch a burst of controlled flows core -> systems
      const p = P.current
      if (!p) return
      let n = 0
      for (const a of p.st) {
        if (a.active || n >= 5) continue
        n++
        spawnRoute(a, data, true)
      }
    }
    window.addEventListener('universe:dispatch', onDispatch)

    /* LIVE events (§6.3): agent_started wakes a comet from the core;
       tool_running routes it toward the tool's real system; handoff
       migrates it from agent A's system to agent B's. */
    const onLiveEvent = (e: Event) => {
      const p = P.current
      if (!p) return
      const ev = (e as CustomEvent).detail as {
        type: string
        agent?: string
        tool?: string
        from_agent?: string
        to_agent?: string
        solar_system?: string
      }
      const agentSlot = (name?: string) => {
        if (!name) return -1
        const h = hash32(name)
        return h % AGENTS
      }
      const systemFor = (name?: string) => {
        if (!name) return null
        const sys = data.systems.find((s) => s.realName === name || s.realName?.includes(name))
        return sys ?? null
      }
      if (ev.type === 'agent_started') {
        const i = agentSlot(ev.agent)
        const a = p.st[i]
        if (a && !a.active) spawnRoute(a, data, true) // ignite from the core
      }
      if (ev.type === 'tool_running' || ev.type === 'tool_requested') {
        const i = agentSlot(ev.agent)
        const a = p.st[i]
        const target = systemFor(ev.solar_system)
        if (a && target && !a.active) {
          // route this comet core -> the tool's real system
          a.active = true
          a.t = 0
          a.dur = 1.6
          a.p0 = [0, 0, 0]
          a.p2 = [...target.center] as [number, number, number]
          a.p1 = [
            (a.p0[0] + a.p2[0]) / 2 + 3,
            (a.p0[1] + a.p2[1]) / 2 + 2,
            (a.p0[2] + a.p2[2]) / 2,
          ]
        }
      }
      if (ev.type === 'handoff' && ev.from_agent && ev.to_agent) {
        const from = systemFor(ev.from_agent)
        const to = systemFor(ev.to_agent)
        const i = agentSlot(ev.from_agent)
        const a = p.st[i]
        if (a && from && to) {
          a.active = true
          a.t = 0
          a.dur = 3.2
          a.p0 = [...from.center] as [number, number, number]
          a.p2 = [...to.center] as [number, number, number]
          a.p1 = [
            (a.p0[0] + a.p2[0]) / 2,
            (a.p0[1] + a.p2[1]) / 2 + 5,
            (a.p0[2] + a.p2[2]) / 2,
          ]
        }
      }
    }
    window.addEventListener('universe:event', onLiveEvent)

    return () => {
      window.removeEventListener('universe:dispatch', onDispatch)
      window.removeEventListener('universe:event', onLiveEvent)
    }
  }, [P, data])

  useFrame(({ clock }, delta) => {
    const p = P.current
    if (!p) return
    const st = useUniverse.getState()
    const t = shared.frozen ? shared.frozenTime : clock.elapsedTime
    const reduced = st.reducedMotion

    p.headMat.uniforms.uTime.value = t
    p.headMat.uniforms.uScaleH.value = shared.scaleH
    p.headMat.uniforms.uMotion.value = reduced ? 0 : 1

    if (reduced) {
      for (const l of p.lines) l.visible = false
      p.heads.visible = false
      return
    }
    p.heads.visible = true

    const headPos = p.heads.geometry.getAttribute('position') as BufferAttribute
    const headCol = p.heads.geometry.getAttribute('aColor') as BufferAttribute

    for (let i = 0; i < AGENTS; i++) {
      const a = p.st[i]
      const line = p.lines[i]
      if (!a.active) {
        a.wait -= delta
        if (a.wait <= 0) spawnRoute(a, data, false)
        line.visible = false
        headPos.setXYZ(i, 0, -9999, 0)
        continue
      }
      a.t += delta / a.dur
      if (a.t >= 1.12) {
        a.active = false
        a.wait = 4 + Math.random() * 8
        line.visible = false
        headPos.setXYZ(i, 0, -9999, 0)
        continue
      }
      const k = Math.min(1, a.t)
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2
      line.visible = true
      // head + trailing positions sampled behind the head
      const lp = line.geometry.getAttribute('position') as BufferAttribute
      for (let s = 0; s < TRAIL; s++) {
        const back = Math.max(0, e - s * 0.0042)
        const q = bezier(a, back)
        lp.setXYZ(TRAIL - 1 - s, q[0], q[1], q[2])
      }
      lp.needsUpdate = true
      const h = bezier(a, e)
      headPos.setXYZ(i, h[0], h[1], h[2])
      headCol.setXYZ(i, a.color[0], a.color[1], a.color[2])
    }
    headPos.needsUpdate = true
    headCol.needsUpdate = true
    void t
  })

  return (
    <>
      {parts.lines.map((l, i) => (
        <primitive key={i} object={l} />
      ))}
      <primitive object={parts.heads} />
    </>
  )
}

function bezier(a: AgentState, k: number): [number, number, number] {
  const u = 1 - k
  const w0 = u * u
  const w1 = 2 * u * k
  const w2 = k * k
  return [
    a.p0[0] * w0 + a.p1[0] * w1 + a.p2[0] * w2,
    a.p0[1] * w0 + a.p1[1] * w1 + a.p2[1] * w2,
    a.p0[2] * w0 + a.p1[2] * w1 + a.p2[2] * w2,
  ]
}

function spawnRoute(a: AgentState, data: UniverseData, fromCore: boolean) {
  const sys = data.systems
  if (sys.length < 2) return
  const iA = Math.floor(Math.random() * sys.length)
  let iB = Math.floor(Math.random() * sys.length)
  if (iB === iA) iB = (iB + 3) % sys.length
  a.active = true
  a.t = 0
  if (fromCore) {
    const A = sys[iA].center
    const d = Math.hypot(A[0], A[1], A[2]) || 1
    a.p0 = [(A[0] / d) * 4.5, (A[1] / d) * 4.5, (A[2] / d) * 4.5]
    a.p2 = [...A] as [number, number, number]
    a.dur = 3.4 + Math.random() * 2.4
  } else {
    a.p0 = [...sys[iA].center] as [number, number, number]
    a.p2 = [...sys[iB].center] as [number, number, number]
    a.dur = 5.5 + Math.random() * 6
  }
  // curved control point: perpendicular offset -> arc through the volume
  const mx = (a.p0[0] + a.p2[0]) / 2
  const my = (a.p0[1] + a.p2[1]) / 2
  const mz = (a.p0[2] + a.p2[2]) / 2
  const dx = a.p2[0] - a.p0[0]
  const dy = a.p2[1] - a.p0[1]
  const dz = a.p2[2] - a.p0[2]
  const len = Math.hypot(dx, dy, dz) || 1
  const off = len * (0.14 + Math.random() * 0.16)
  const rx = dy * 0.6 - dz * 0.5
  const ry = dz * 0.6 - dx * 0.4
  const rz = dx * 0.5 - dy * 0.6
  const rl = Math.hypot(rx, ry, rz) || 1
  const sgn = Math.random() < 0.5 ? 1 : -1
  a.p1 = [mx + (rx / rl) * off * sgn, my + (ry / rl) * off * sgn, mz + (rz / rl) * off * sgn]
  const c = a.color
  a.color = [c[0], c[1], c[2]]
}
