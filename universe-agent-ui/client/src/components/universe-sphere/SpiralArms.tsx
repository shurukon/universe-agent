'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { BufferGeometry, Float32BufferAttribute, Group, Points } from 'three'
import type { UniverseData } from '@/lib/universe/generate'
import { makeStarfieldMaterial } from './glsl'
import { shared, useMirror } from './shared'
import { useUniverse } from './state'

/**
 * Cognitive spiral arms: hundreds of thousands of GPU point particles forming
 * volumetric arms with thickness, turbulence, height variation, dense clusters,
 * quiet gaps and faint nebular pockets. Slow rotation, micro-motion only.
 */
export function SpiralArms({ data }: { data: UniverseData }) {
  const group = useRef<Group>(null)
  const parts = useMemo(() => {
    const mk = (f: typeof data.armField, intensity: number) => {
      const g = new BufferGeometry()
      g.setAttribute('position', new Float32BufferAttribute(f.pos, 3))
      g.setAttribute('aColor', new Float32BufferAttribute(f.color, 3))
      g.setAttribute('aSize', new Float32BufferAttribute(f.size, 1))
      g.setAttribute('aTw', new Float32BufferAttribute(f.tw, 1))
      g.setAttribute('aPh', new Float32BufferAttribute(f.ph, 1))
      g.setAttribute('aRnd', new Float32BufferAttribute(f.rnd, 1))
      const m = makeStarfieldMaterial()
      m.uniforms.uIntensity.value = intensity
      return new Points(g, m)
    }
    return {
      arms: mk(data.armField, 1),
      dust: (() => {
        const g = new BufferGeometry()
        const f = data.dust
        g.setAttribute('position', new Float32BufferAttribute(f.pos, 3))
        g.setAttribute('aColor', new Float32BufferAttribute(f.color, 3))
        g.setAttribute('aSize', new Float32BufferAttribute(f.size, 1))
        g.setAttribute('aTw', new Float32BufferAttribute(f.tw, 1))
        g.setAttribute('aPh', new Float32BufferAttribute(f.ph, 1))
        g.setAttribute('aRnd', new Float32BufferAttribute(f.rnd, 1))
        const m = makeStarfieldMaterial()
        m.uniforms.uIntensity.value = 1
        return new Points(g, m)
      })(),
    }
  }, [data])

  const P = useMirror(parts)

  useEffect(
    () => () => {
      for (const p of [parts.arms, parts.dust]) {
        p.geometry.dispose()
        ;(p.material as { dispose: () => void }).dispose()
      }
    },
    [parts]
  )

  useFrame(({ clock }, delta) => {
    const p = P.current
    if (!p) return
    const reduced = useUniverse.getState().reducedMotion
    const t = shared.frozen ? shared.frozenTime : clock.elapsedTime
    for (const pt of [p.arms, p.dust]) {
      const u = (pt.material as ReturnType<typeof makeStarfieldMaterial>).uniforms
      u.uTime.value = t
      u.uScaleH.value = shared.scaleH
      u.uCull.value = shared.cull
      u.uMotion.value = reduced ? 0 : 1
    }
    if (group.current && !reduced) {
      // very slow gravitational drift; dust current drifts marginally faster
      group.current.rotation.y += delta * 0.0045
      p.dust.rotation.y += delta * 0.0016
    }
  })

  return (
    <group ref={group}>
      <primitive object={parts.arms} />
      <primitive object={parts.dust} />
    </group>
  )
}
