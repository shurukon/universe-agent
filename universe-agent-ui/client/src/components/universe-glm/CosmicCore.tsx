'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import {
  AdditiveBlending,
  BufferGeometry,
  Euler,
  Float32BufferAttribute,
  Group,
  LineBasicMaterial,
  LineLoop,
  Mesh,
  PlaneGeometry,
  Points,
  Quaternion,
  SphereGeometry,
} from 'three'
import { ARM_COUNT, armPoint, CORE_R, type UniverseData } from '@/lib/universe/generate'
import { c3, PALETTE } from '@/lib/universe/palette'
import {
  makeCorePlasmaMaterial,
  makeFresnelMaterial,
  makeHaloMaterial,
  makeInnerGlowMaterial,
  makeStarfieldMaterial,
} from './glsl'
import { shared, useMirror } from './shared'
import { useUniverse, type UniverseMode } from './state'

const MODE_TARGETS: Record<UniverseMode, { pull: number; plasma: number; currents: number; halo: number }> = {
  idle: { pull: 0, plasma: 0.5, currents: 1, halo: 1 },
  listening: { pull: 0.55, plasma: 0.62, currents: 1, halo: 1.9 },
  reasoning: { pull: 0.1, plasma: 0.85, currents: 4.6, halo: 1.25 },
  execution: { pull: 0, plasma: 0.72, currents: 2.2, halo: 1.15 },
  response: { pull: 0, plasma: 0.55, currents: 1.2, halo: 1.3 },
}

/**
 * The Cosmic Core: Universe Agent's cognitive engine. A dark indigo inner
 * mass with restrained cyan plasma, microscopic knowledge particles,
 * translucent crystalline shells and thin orbital paths crossing at different
 * inclinations. Graduated halos only — never a blown-out bloom.
 *
 * Light language per voice state:
 *  idle      slow breathing from deep cyan within indigo
 *  listening knowledge particles pull toward the center, halos grow clearer
 *  reasoning internal orbital currents rotate, light paths travel into arms
 *  execution controlled signal flows depart toward selected systems
 *  response  soft cool-white confirmation (handled by the sphere boundary)
 */
export function CosmicCore({ data }: { data: UniverseData }) {
  const parts = useMemo(() => {
    // cognitive mass
    const plasmaGeo = new SphereGeometry(CORE_R * 0.92, 48, 32)
    const plasmaMat = makeCorePlasmaMaterial(
      c3(PALETTE.indigoDeep, 1.3),
      c3(PALETTE.photonCyan, 1.0)
    )
    const plasma = new Mesh(plasmaGeo, plasmaMat)

    // plasma heart: restrained interior glow so the core never reads flat or dark
    const heart = new Mesh(
      new SphereGeometry(CORE_R * 0.7, 32, 24),
      makeInnerGlowMaterial(c3(PALETTE.photonCyan, 0.85), 0.5, 2.8)
    )

    // crystalline network shells
    const shell1 = new Mesh(
      new SphereGeometry(CORE_R * 1.35, 32, 24),
      makeFresnelMaterial(c3(PALETTE.cyanSoft, 0.75), 0.09, 3.2)
    )
    const shell2 = new Mesh(
      new SphereGeometry(CORE_R * 1.78, 28, 20),
      makeFresnelMaterial(c3(PALETTE.violet, 0.55), 0.06, 3.8)
    )

    const coreParticles = (() => {
      const f = data.coreParticles
      const g = new BufferGeometry()
      g.setAttribute('position', new Float32BufferAttribute(f.pos, 3))
      g.setAttribute('aColor', new Float32BufferAttribute(f.color, 3))
      g.setAttribute('aSize', new Float32BufferAttribute(f.size, 1))
      g.setAttribute('aTw', new Float32BufferAttribute(f.tw, 1))
      g.setAttribute('aPh', new Float32BufferAttribute(f.ph, 1))
      g.setAttribute('aRnd', new Float32BufferAttribute(f.rnd, 1))
      const m = makeStarfieldMaterial({ pull: true })
      m.uniforms.uIntensity.value = 1.1
      m.uniforms.uPullR.value = CORE_R * 0.45
      const points = new Points(g, m)
      points.frustumCulled = false
      return points
    })()
    const pMat = coreParticles.material as ReturnType<typeof makeStarfieldMaterial>

    // thin orbital paths crossing at different inclinations
    const orbitPaths = new Group()
    const orbitMat = new LineBasicMaterial({
      transparent: true,
      opacity: 0.11,
      blending: AdditiveBlending,
      depthWrite: false,
    })
    orbitMat.color.setRGB(...c3(PALETTE.cyanSoft, 0.5))
    for (let i = 0; i < 4; i++) {
      const r = CORE_R * (1.55 + i * 0.38)
      const pts: number[] = []
      for (let k = 0; k <= 96; k++) {
        const a = (k / 96) * Math.PI * 2
        pts.push(Math.cos(a) * r, 0, Math.sin(a) * r)
      }
      const geo = new BufferGeometry()
      geo.setAttribute('position', new Float32BufferAttribute(pts, 3))
      const loop = new LineLoop(geo, orbitMat)
      loop.quaternion.copy(
        new Quaternion().setFromEuler(
          new Euler(0.4 + i * 0.5, (i / 4) * Math.PI, 0.35 + i * 0.22, 'XYZ')
        )
      )
      orbitPaths.add(loop)
    }

    // graduated halos
    const haloGeo = new PlaneGeometry(1, 1)
    const haloIn = makeHaloMaterial(c3(PALETTE.photonCyan, 0.36), 0)
    haloIn.uniforms.uSize.value = CORE_R * 2.8
    haloIn.uniforms.uIntensity.value = 0.7
    const haloInMesh = new Mesh(haloGeo, haloIn)
    haloInMesh.position.z = -0.4
    const haloOut = makeHaloMaterial(c3(PALETTE.indigo, 0.38), 0)
    haloOut.uniforms.uSize.value = CORE_R * 6.0
    haloOut.uniforms.uIntensity.value = 0.55
    const haloOutMesh = new Mesh(haloGeo, haloOut)
    haloOutMesh.position.z = -1.2

    // reasoning light paths into the arms
    const PULSES = 5
    const pulseGeo = new BufferGeometry()
    pulseGeo.setAttribute(
      'position',
      new Float32BufferAttribute(new Float32Array(PULSES * 3), 3)
    )
    pulseGeo.setAttribute('aColor', new Float32BufferAttribute(new Float32Array(PULSES * 3).fill(0.6), 3))
    const pulseCol = new Float32Array(PULSES * 3)
    const pc = c3(PALETTE.photonCyan, 1.3)
    for (let i = 0; i < PULSES; i++) {
      pulseCol[i * 3] = pc[0]
      pulseCol[i * 3 + 1] = pc[1]
      pulseCol[i * 3 + 2] = pc[2]
    }
    pulseGeo.setAttribute('aColor', new Float32BufferAttribute(pulseCol, 3))
    pulseGeo.setAttribute('aSize', new Float32BufferAttribute(new Float32Array(PULSES).fill(0.85), 1))
    pulseGeo.setAttribute('aTw', new Float32BufferAttribute(new Float32Array(PULSES), 1))
    pulseGeo.setAttribute('aPh', new Float32BufferAttribute(new Float32Array(PULSES), 1))
    pulseGeo.setAttribute('aRnd', new Float32BufferAttribute(new Float32Array(PULSES).fill(0.9), 1))
    const pulseMat = makeStarfieldMaterial()
    pulseMat.uniforms.uIntensity.value = 1.6
    const pulses = new Points(pulseGeo, pulseMat)
    pulses.visible = false
    pulses.frustumCulled = false

    const core = new Group()
    core.add(plasma, heart, coreParticles, orbitPaths, haloInMesh, haloOutMesh, pulses)
    // shells added separately so they counter-rotate around their own axes
    return {
      core,
      plasma,
      plasmaMat,
      particles: coreParticles,
      pMat,
      orbitPaths,
      orbitMat,
      haloIn,
      haloOut,
      pulses,
      pulseMat,
      shells: [shell1, shell2],
      modeRef: { mode: 'idle' as UniverseMode },
    }
  }, [data])

  const P = useMirror(parts)

  useEffect(() => {
    parts.core.add(...parts.shells)
    return () => {
      for (const s of parts.shells) {
        s.geometry.dispose()
        ;(s.material as { dispose: () => void }).dispose()
      }
    }
  }, [parts])

  // reasoning pulse state: t in 0..1 along an arm
  const pulseState = useRef(
    Array.from({ length: 5 }, (_, i) => ({ arm: i % ARM_COUNT, t: -1 - i * 0.35, dur: 2.6 }))
  )
  const execSent = useRef('')

  /* the voice level from the mic (§8): the core breathes with your voice */
  const voiceLevelRef = useRef(0)
  useEffect(() => {
    const onVoice = (e: Event) => {
      voiceLevelRef.current = (e as CustomEvent).detail as number
    }
    window.addEventListener('universe:voice', onVoice)
    return () => window.removeEventListener('universe:voice', onVoice)
  }, [])

  useFrame(({ clock }, delta) => {
    const p = P.current
    if (!p) return
    const t = shared.frozen ? shared.frozenTime : clock.elapsedTime
    const st = useUniverse.getState()
    const reduced = st.reducedMotion
    const targets = MODE_TARGETS[st.mode]

    const damp = (cur: number, to: number, lambda: number) =>
      cur + (to - cur) * (1 - Math.exp(-lambda * delta))

    // breathing: deep cyan within indigo — plus the live mic level, so the
    // core's glow tracks the user's actual voice amplitude (voice-first §8)
    const vl = voiceLevelRef.current
    voiceLevelRef.current = Math.max(0, vl - delta * 2.2) // decay when frames stop
    const breath = reduced
      ? 0.5
      : 0.5 + 0.5 * Math.sin(t * 0.45) + vl * 1.1
    p.plasmaMat.uniforms.uTime.value = t
    p.plasmaMat.uniforms.uPlasma.value = damp(
      p.plasmaMat.uniforms.uPlasma.value,
      targets.plasma * (0.8 + 0.25 * breath) * (1 + vl * 0.35),
      2.2
    )
    p.plasmaMat.uniforms.uCurrents.value = damp(
      p.plasmaMat.uniforms.uCurrents.value,
      reduced ? 1 : targets.currents,
      2
    )

    p.pMat.uniforms.uTime.value = t
    p.pMat.uniforms.uScaleH.value = shared.scaleH
    p.pMat.uniforms.uCull.value = shared.cull
    p.pMat.uniforms.uMotion.value = reduced ? 0 : 1
    p.pMat.uniforms.uPull.value = damp(p.pMat.uniforms.uPull.value, reduced ? 0 : targets.pull, 2.4)

    // halo clarity grows when listening
    p.haloIn.uniforms.uTime.value = t
    p.haloOut.uniforms.uTime.value = t
    const haloTarget = (reduced ? 1 : targets.halo) * (0.9 + 0.12 * breath)
    p.haloIn.uniforms.uIntensity.value = damp(p.haloIn.uniforms.uIntensity.value, 0.7 * haloTarget, 2.4)
    p.haloOut.uniforms.uIntensity.value = damp(p.haloOut.uniforms.uIntensity.value, 0.5 * haloTarget, 2.4)

    // micro rotation: shells counter-rotate, orbit paths precess slowly
    if (!reduced) {
      p.shells[0].rotation.y += delta * 0.045
      p.shells[0].rotation.x += delta * 0.012
      p.shells[1].rotation.y -= delta * 0.03
      p.shells[1].rotation.z += delta * 0.016
      p.orbitPaths.rotation.y += delta * 0.02 * (st.mode === 'reasoning' ? 2.6 : 1)
    }
    p.orbitMat.opacity = st.mode === 'reasoning' ? 0.2 : 0.11

    // reasoning: light paths travel from the core into relevant spiral arms
    if (st.mode === 'reasoning' && !reduced) {
      p.pulses.visible = true
      const posAttr = p.pulses.geometry.getAttribute('position') as Float32BufferAttribute
      for (let i = 0; i < pulseState.current.length; i++) {
        const ps = pulseState.current[i]
        ps.t += delta / ps.dur
        if (ps.t > 1.25) {
          ps.t = -Math.random() * 1.2
          ps.arm = Math.floor(Math.random() * ARM_COUNT)
        }
        if (ps.t < 0) {
          posAttr.setXYZ(i, 0, -9999, 0)
          continue
        }
        const arm = data.arms[ps.arm % data.arms.length]
        const q = armPoint(arm, Math.min(0.98, ps.t))
        const jit = 0.8
        posAttr.setXYZ(i, q.x + Math.sin(i * 7 + ps.arm) * jit, q.y + Math.sin(i * 13) * jit * 0.6, q.z + Math.cos(i * 5 + ps.arm) * jit)
      }
      posAttr.needsUpdate = true
      p.pulseMat.uniforms.uTime.value = t
      p.pulseMat.uniforms.uScaleH.value = shared.scaleH
    } else {
      p.pulses.visible = false
    }

    // execution: dispatch controlled signal flows core -> systems
    if (st.mode === 'execution') {
      const sig = `exec-${Math.floor(t)}`
      if (execSent.current !== sig) {
        execSent.current = sig
        window.dispatchEvent(new CustomEvent('universe:dispatch', { detail: { fromCore: true } }))
      }
    }
  })

  return <primitive object={parts.core} />
}

export { MODE_TARGETS as CORE_MODE_TARGETS }
