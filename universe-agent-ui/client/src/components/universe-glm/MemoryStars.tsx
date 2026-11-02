'use client'

import { useEffect, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import {
  BufferGeometry,
  Float32BufferAttribute,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  PlaneGeometry,
  Points,
  AdditiveBlending,
} from 'three'
import type { UniverseData } from '@/lib/universe/generate'
import { c3, PALETTE } from '@/lib/universe/palette'
import { makeHaloMaterial, makeStarfieldMaterial } from './glsl'
import { shared, useMirror } from './shared'
import { useUniverse } from './state'

/**
 * L3 crystallized-memory stars: facts, preferences, insights, decisions.
 * Cool white = verified knowledge, Photon Cyan = active retrieval,
 * restrained violet = complex contextual memory. On hover, only that star's
 * corona increases and short relationship traces appear toward its nearest
 * neighbours (precomputed kNN).
 */
export function MemoryStars({ data }: { data: UniverseData }) {
  const parts = useMemo(() => {
    const g = new BufferGeometry()
    const f = data.starField
    g.setAttribute('position', new Float32BufferAttribute(f.pos, 3))
    g.setAttribute('aColor', new Float32BufferAttribute(f.color, 3))
    g.setAttribute('aSize', new Float32BufferAttribute(f.size, 1))
    g.setAttribute('aTw', new Float32BufferAttribute(f.tw, 1))
    g.setAttribute('aPh', new Float32BufferAttribute(f.ph, 1))
    g.setAttribute('aRnd', new Float32BufferAttribute(f.rnd, 1))
    const m = makeStarfieldMaterial()
    m.uniforms.uIntensity.value = 1.05
    const points = new Points(g, m)

    const coronaGeo = new PlaneGeometry(1, 1)
    const coronaMat = makeHaloMaterial(c3(PALETTE.coolWhite, 1), 1)
    coronaMat.uniforms.uIntensity.value = 0
    const corona = new Mesh(coronaGeo, coronaMat)
    corona.visible = false

    const traceGeo = new BufferGeometry()
    traceGeo.setAttribute('position', new Float32BufferAttribute(new Float32Array(6 * 3 * 3), 3))
    const traceMat = new LineBasicMaterial({
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthWrite: false,
    })
    traceMat.color.setRGB(...c3(PALETTE.photonCyan, 0.85))
    const traces = new LineSegments(traceGeo, traceMat)
    traces.visible = false
    traces.frustumCulled = false

    return { points, corona, coronaMat, traces, traceMat, shownAt: -10,
             flare: null as { id: number; until: number } | null }
  }, [data])

  const P = useMirror(parts)

  /* LIVE skill activations (§6.3): the skill's L3 star flares at its real
     position. We reuse the hover-corona machinery: place it on the star
     that carries the skill's name and hold it for a few seconds. */
  useEffect(() => {
    const onLiveEvent = (e: Event) => {
      const p = P.current
      if (!p) return
      const ev = (e as CustomEvent).detail as { type: string; skill_name?: string; skill?: string }
      if (ev.type !== 'skill_activated') return
      const name = ev.skill_name || ev.skill
      if (!name) return
      const star = data.stars.find((s) => s.realName === name)
      if (!star) return
      p.flare = { id: star.id, until: performance.now() / 1000 + 4.5 }
    }
    window.addEventListener('universe:event', onLiveEvent)
    return () => window.removeEventListener('universe:event', onLiveEvent)
  }, [P, data])

  useEffect(
    () => () => {
      parts.points.geometry.dispose()
      ;(parts.points.material as { dispose: () => void }).dispose()
      parts.corona.geometry.dispose()
      ;(parts.corona.material as { dispose: () => void }).dispose()
      parts.traces.geometry.dispose()
      ;(parts.traces.material as { dispose: () => void }).dispose()
    },
    [parts]
  )

  useFrame(({ clock, invalidate }) => {
    const p = P.current
    if (!p) return
    const st = useUniverse.getState()
    const t = shared.frozen ? shared.frozenTime : clock.elapsedTime
    const u = (p.points.material as ReturnType<typeof makeStarfieldMaterial>).uniforms
    u.uTime.value = t
    u.uScaleH.value = shared.scaleH
    u.uCull.value = shared.cull
    u.uMotion.value = st.reducedMotion ? 0 : 1
    // a hovered dark core dims nearby memory subtly (inverse reaction)
    u.uIntensity.value = st.hover?.kind === 'core' ? 0.7 : 1.05

    const h = st.hover
    const flareNow =
      p.flare && p.flare.until > performance.now() / 1000 ? data.stars[p.flare.id] : null
    if (p.flare && p.flare.until <= performance.now() / 1000) p.flare = null
    const target = h && h.kind === 'star' ? data.stars[h.id] : flareNow
    if (target) {
      const s = target
      p.corona.visible = true
      p.corona.position.set(s.pos[0], s.pos[1], s.pos[2])
      p.coronaMat.uniforms.uSize.value = 2.1 + s.size
      p.coronaMat.uniforms.uIntensity.value = Math.min(
        flareNow ? 1.45 : 1.15,
        p.coronaMat.uniforms.uIntensity.value + (flareNow ? 0.12 : 0.08)
      )
      p.coronaMat.uniforms.uTime.value = t
      // relationship traces
      const arr = p.traces.geometry.getAttribute('position') as Float32BufferAttribute
      const knn = s.knn
      let seg = 0
      for (const j of knn) {
        const o = data.stars[j]
        arr.setXYZ(seg * 2, s.pos[0], s.pos[1], s.pos[2])
        arr.setXYZ(seg * 2 + 1, o.pos[0], o.pos[1], o.pos[2])
        seg++
      }
      for (let k = seg; k < 3; k++) {
        arr.setXYZ(k * 2, 0, 0, 0)
        arr.setXYZ(k * 2 + 1, 0, 0, 0)
      }
      arr.needsUpdate = true
      p.traces.visible = true
      p.shownAt = t
      p.traceMat.opacity = Math.min(0.4, p.traceMat.opacity + 0.03)
    } else {
      // skill flares decay slower than the hover corona (4.5s signature §6.3)
      const decay = p.flare ? 0.028 : 0.06
      if (p.traceMat.opacity > 0) {
        p.traceMat.opacity = Math.max(0, p.traceMat.opacity - 0.02)
        if (p.traceMat.opacity === 0) p.traces.visible = false
      }
      if (p.coronaMat.uniforms.uIntensity.value > 0) {
        p.coronaMat.uniforms.uIntensity.value = Math.max(
          0,
          p.coronaMat.uniforms.uIntensity.value - decay
        )
        if (p.coronaMat.uniforms.uIntensity.value === 0) p.corona.visible = false
      }
      // frameloop='demand' (frozen mini canvas): decay/animate runs inside
      // useFrame, so request the next frame explicitly — otherwise the flare
      // sets state but no frame ever renders it.
      if (p.flare) invalidate()
    }
  })

  return (
    <>
      <primitive object={parts.points} />
      <primitive object={parts.corona} />
      <primitive object={parts.traces} />
    </>
  )
}
