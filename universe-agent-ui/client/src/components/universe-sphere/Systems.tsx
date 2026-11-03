'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import {
  BufferGeometry,
  DynamicDrawUsage,
  Euler,
  Float32BufferAttribute,
  Group,
  InstancedBufferAttribute,
  InstancedMesh,
  LineSegments,
  Matrix4,
  Mesh,
  PlaneGeometry,
  Points,
  Quaternion,
  RingGeometry,
  SphereGeometry,
  TorusGeometry,
  Vector3,
} from 'three'
import type { UniverseData, OrbitDef, SystemDef } from '@/lib/universe/generate'
import { c3, PALETTE } from '@/lib/universe/palette'
import {
  makeGasRingMaterial,
  makeGateMaterial,
  makeHaloMaterial,
  makeOrbitMaterial,
  makePlanetMaterial,
  makeSystemStarMaterial,
} from './glsl'
import { shared, useMirror } from './shared'
import { useUniverse } from './state'

const UP = new Vector3(0, 1, 0)

/**
 * Skill & domain solar systems: luminous star, thin elliptical orbital tracks
 * at varied inclinations, planets (work loops), moons & asteroid belts
 * (snippets / fragments / sub-contexts) and rare knowledge gates.
 * Focus illuminates orbits sequentially and raises orbital activity slightly;
 * the rest of the sphere remains calm.
 */
export function Systems({ data }: { data: UniverseData }) {
  const parts = useMemo(() => {
    /* ---- merged system star sprites ---- */
    const ssGeo = new BufferGeometry()
    const ss = data.systemStars
    ssGeo.setAttribute('position', new Float32BufferAttribute(ss.pos, 3))
    ssGeo.setAttribute('aColor', new Float32BufferAttribute(ss.color, 3))
    ssGeo.setAttribute('aSize', new Float32BufferAttribute(ss.size, 1))
    ssGeo.setAttribute('aSysId', new Float32BufferAttribute(ss.sysId, 1))
    const ssMat = makeSystemStarMaterial()
    const systemStars = new Points(ssGeo, ssMat)
    systemStars.frustumCulled = false

    /* ---- merged elliptical orbit lines ---- */
    const ol = data.orbitLines
    const olGeo = new BufferGeometry()
    olGeo.setAttribute('position', new Float32BufferAttribute(ol.pos, 3))
    olGeo.setAttribute('aSysId', new Float32BufferAttribute(ol.sysId, 1))
    olGeo.setAttribute('aOrbIdx', new Float32BufferAttribute(ol.orbIdx, 1))
    const olMat = makeOrbitMaterial()
    const orbits = new LineSegments(olGeo, olMat)
    orbits.frustumCulled = false

    /* ---- instanced planets + moons (one draw call) ---- */
    const planets = data.planets
    const sphereGeo = new SphereGeometry(1, 26, 18)
    const pMat = makePlanetMaterial()
    const planetMesh = new InstancedMesh(sphereGeo, pMat, planets.length)
    planetMesh.instanceMatrix.setUsage(DynamicDrawUsage)
    planetMesh.frustumCulled = false
    const aType = new Float32Array(planets.length)
    const aSeed = new Float32Array(planets.length)
    const aTint = new Float32Array(planets.length * 3)
    const aRim = new Float32Array(planets.length * 3)
    const aSysId = new Float32Array(planets.length)
    const aPid = new Float32Array(planets.length)
    planets.forEach((p, i) => {
      aType[i] = p.type
      aSeed[i] = p.seed
      aTint.set(p.tint, i * 3)
      aRim.set(p.rim, i * 3)
      aSysId[i] = p.systemId
      aPid[i] = p.id
    })
    planetMesh.geometry.setAttribute('aType', new InstancedBufferAttribute(aType, 1))
    planetMesh.geometry.setAttribute('aSeed', new InstancedBufferAttribute(aSeed, 1))
    planetMesh.geometry.setAttribute('aTint', new InstancedBufferAttribute(aTint, 3))
    planetMesh.geometry.setAttribute('aRim', new InstancedBufferAttribute(aRim, 3))
    planetMesh.geometry.setAttribute('aSysId', new InstancedBufferAttribute(aSysId, 1))
    planetMesh.geometry.setAttribute('aPlanetId', new InstancedBufferAttribute(aPid, 1))

    /* ---- gas-giant rings ---- */
    const ringPlanets = planets.filter((p) => p.hasRing)
    const ringMesh =
      ringPlanets.length > 0
        ? new InstancedMesh(new RingGeometry(0.52, 1.0, 56), makeGasRingMaterial(), ringPlanets.length)
        : null
    if (ringMesh) {
      ringMesh.instanceMatrix.setUsage(DynamicDrawUsage)
      ringMesh.frustumCulled = false
    }

    /* ---- asteroid belts ---- */
    let beltPoints: Points | null = null
    const belts = data.belts
    if (belts.count > 0) {
      const bg = new BufferGeometry()
      bg.setAttribute('position', new Float32BufferAttribute(belts.pos, 3))
      bg.setAttribute('aColor', new Float32BufferAttribute(belts.color, 3))
      bg.setAttribute('aSize', new Float32BufferAttribute(belts.size, 1))
      bg.setAttribute('aTw', new Float32BufferAttribute(new Float32Array(belts.count), 1))
      bg.setAttribute('aPh', new Float32BufferAttribute(new Float32Array(belts.count), 1))
      bg.setAttribute('aRnd', new Float32BufferAttribute(new Float32Array(belts.count), 1))
      const bm = makeSystemStarMaterial()
      bm.uniforms.uIntensity.value = 0.5
      beltPoints = new Points(bg, bm)
      beltPoints.frustumCulled = false
    }

    /* ---- knowledge gates ---- */
    const gateGroup = new Group()
    const gateMats: ReturnType<typeof makeGateMaterial>[] = []
    for (const g of data.gates) {
      const geo = new TorusGeometry(g.radius, Math.max(0.028, g.radius * 0.03), 10, 72)
      const mat = makeGateMaterial(g.color)
      gateMats.push(mat)
      const mesh = new Mesh(geo, mat)
      mesh.position.set(...g.pos)
      mesh.quaternion.set(...g.quat)
      mesh.frustumCulled = false
      gateGroup.add(mesh)
    }

    /* ---- transparent volumetric boundary marker (focus/hover) ---- */
    const markerMat = makeHaloMaterial(c3(PALETTE.cyanSoft, 0.4), 2)
    markerMat.uniforms.uIntensity.value = 0
    const marker = new Mesh(new PlaneGeometry(1, 1), markerMat)
    marker.visible = false
    marker.frustumCulled = false

    return {
      systemStars,
      ssMat,
      orbits,
      olMat,
      planetMesh,
      pMat,
      ringMesh,
      ringPlanets,
      beltPoints,
      gateGroup,
      gateMats,
      marker,
      markerMat,
    }
  }, [data])

  const P = useMirror(parts)

  /* per-frame mutable state */
  const focusRef = useRef({ focusT: -10, focusId: -1 })
  const posCache = useRef(new Map<number, Vector3>())
  const s = useMemo(
    () => ({
      m: new Matrix4(),
      q: new Quaternion(),
      q2: new Quaternion(),
      e: new Euler(),
      v: new Vector3(),
      v2: new Vector3(),
      sc: new Vector3(),
    }),
    []
  )
  /* orbit quaternions materialized once */
  const orbitQuats = useMemo(
    () => data.systems.map((sys) => sys.orbits.map((o) => new Quaternion(...o.quat))),
    [data]
  )

  useEffect(
    () => () => {
      parts.systemStars.geometry.dispose()
      ;(parts.systemStars.material as { dispose: () => void }).dispose()
      parts.orbits.geometry.dispose()
      ;(parts.orbits.material as { dispose: () => void }).dispose()
      parts.planetMesh.geometry.dispose()
      ;(parts.planetMesh.material as { dispose: () => void }).dispose()
      if (parts.ringMesh) {
        parts.ringMesh.geometry.dispose()
        ;(parts.ringMesh.material as { dispose: () => void }).dispose()
      }
      if (parts.beltPoints) {
        parts.beltPoints.geometry.dispose()
        ;(parts.beltPoints.material as { dispose: () => void }).dispose()
      }
      parts.marker.geometry.dispose()
      ;(parts.marker.material as { dispose: () => void }).dispose()
    },
    [parts]
  )

  useFrame(({ clock, camera, gl }, delta) => {
    const parts = P.current
    if (!parts) return
    const t = shared.frozen ? shared.frozenTime : clock.elapsedTime
    const st = useUniverse.getState()
    const reduced = st.reducedMotion
    const time = reduced ? 0 : t

    /* focus timing for sequential orbit illumination */
    const f = focusRef.current
    if (st.focusSystem !== f.focusId) {
      f.focusId = st.focusSystem
      f.focusT = 0
    } else {
      f.focusT += delta
    }

    parts.ssMat.uniforms.uTime.value = t
    parts.ssMat.uniforms.uHoverSys.value = st.hover?.kind === 'system' ? st.hover.id : -1
    parts.ssMat.uniforms.uFocusSys.value = st.focusSystem
    parts.olMat.uniforms.uHoverSys.value = st.hover?.kind === 'system' ? st.hover.id : -1
    parts.olMat.uniforms.uFocusSys.value = st.focusSystem
    parts.olMat.uniforms.uFocusT.value = f.focusT
    parts.pMat.uniforms.uFocusSys.value = st.focusSystem
    parts.pMat.uniforms.uHoverPlanet.value = st.hoverPlanet

    /* planets + moons */
    const cache = posCache.current
    cache.clear()
    const planets = data.planets
    for (let i = 0; i < planets.length; i++) {
      const p = planets[i]
      const sys = data.systems[p.systemId]
      if (p.parent >= 0) {
        const parentPos = cache.get(p.parent)
        if (!parentPos) continue
        const ang = p.phase + time * p.speed * p.dir * 1.4
        s.v.set(
          Math.cos(ang) * p.moonOrbit,
          Math.sin(ang * 0.7) * p.moonOrbit * 0.12,
          Math.sin(ang) * p.moonOrbit
        )
        s.v.add(parentPos)
        s.sc.setScalar(p.size)
        s.m.compose(s.v, s.q.identity(), s.sc)
      } else {
        const orb = sys.orbits[p.orbit]
        const quat = orbitQuats[p.systemId][p.orbit]
        const ang = orb.phase0 + time * p.speed * orb.speed * orb.dir
        const a = orb.radius
        const b = a * Math.sqrt(1 - orb.ecc * orb.ecc)
        s.v.set(Math.cos(ang) * a - a * orb.ecc, 0, Math.sin(ang) * b)
        s.v.applyQuaternion(quat).add(s.v2.set(...sys.center))
        cache.set(p.id, s.v.clone())
        // gentle spin around own axis + slight orbital activity lift on focus
        s.e.set(0, time * 0.14 * p.speed * p.dir, 0)
        s.q.setFromAxisAngle(UP, s.e.y)
        s.sc.setScalar(p.size)
        s.m.compose(s.v, s.q, s.sc)
      }
      parts.planetMesh.setMatrixAt(i, s.m)
    }
    parts.planetMesh.instanceMatrix.needsUpdate = true

    /* gas rings follow their planets */
    if (parts.ringMesh) {
      for (let i = 0; i < parts.ringPlanets.length; i++) {
        const p = parts.ringPlanets[i]
        const pos = cache.get(p.id)
        if (!pos) continue
        s.e.set(0.42 + p.seed * 0.5, p.seed * 5, 0.12)
        s.q.setFromEuler(s.e)
        s.sc.setScalar(p.size * 2.35)
        s.m.compose(pos, s.q, s.sc)
        parts.ringMesh.setMatrixAt(i, s.m)
      }
      parts.ringMesh.instanceMatrix.needsUpdate = true
    }

    /* belts */
    if (parts.beltPoints) {
      const bu = (parts.beltPoints.material as ReturnType<typeof makeSystemStarMaterial>).uniforms
      bu.uTime.value = t
      bu.uScaleH.value = shared.scaleH
      bu.uCull.value = shared.cull
      bu.uMotion.value = reduced ? 0 : 1
    }

    /* gates: delicate rotation + hover brightening */
    for (let i = 0; i < data.gates.length; i++) {
      const g = data.gates[i]
      const mesh = parts.gateGroup.children[i] as Mesh
      const mat = parts.gateMats[i]
      mat.uniforms.uTime.value = t
      const hovered = st.hover?.kind === 'gate' && st.hover.id === g.id
      const target = hovered ? 1 : 0
      mat.uniforms.uHover.value += (target - mat.uniforms.uHover.value) * Math.min(1, delta * 6)
      if (!reduced) mesh.rotation.z += delta * g.spin
    }

    /* transparent volumetric boundary on hover/focus */
    const markerSys =
      st.hover?.kind === 'system' ? st.hover.id : st.focusSystem >= 0 ? st.focusSystem : -1
    if (markerSys >= 0) {
      const sys = data.systems[markerSys]
      parts.marker.visible = true
      parts.marker.position.set(...sys.center)
      parts.markerMat.uniforms.uSize.value = sys.radius * 1.12
      const target =
        st.focusSystem === markerSys ? 0.3 + 0.07 * Math.sin(t * 1.6) : 0.12 + 0.03 * Math.sin(t * 1.6)
      parts.markerMat.uniforms.uIntensity.value +=
        (target - parts.markerMat.uniforms.uIntensity.value) * Math.min(1, delta * 5)
      parts.markerMat.uniforms.uTime.value = t
    } else {
      parts.markerMat.uniforms.uIntensity.value *= Math.max(0, 1 - delta * 6)
      if (parts.markerMat.uniforms.uIntensity.value < 0.005) parts.marker.visible = false
    }

    /* planet-level hover inside the focused system (screen-space proximity) */
    if (st.focusSystem >= 0 && shared.ndc.x > -2) {
      const sys = data.systems[st.focusSystem]
      let bestId = -1
      let bestD = 26 // px threshold
      const h = gl.domElement.height / (window.devicePixelRatio || 1)
      const w = gl.domElement.width / (window.devicePixelRatio || 1)
      void h
      for (const pid of sys.planetIds) {
        const pos = cache.get(pid)
        if (!pos) continue
        s.v.copy(pos)
        // to world through the rotating sphere group
        if (shared.sphere) s.v.applyMatrix4(shared.sphere.matrixWorld)
        s.v.project(camera)
        const px = (s.v.x * 0.5 + 0.5) * w
        const py = (-s.v.y * 0.5 + 0.5) * h
        const nx = ((shared.ndc.x * 0.5 + 0.5) * w)
        const ny = ((-shared.ndc.y * 0.5 + 0.5) * h)
        const d = Math.hypot(px - nx, py - ny)
        if (d < bestD) {
          bestD = d
          bestId = pid
        }
      }
      if (bestId !== st.hoverPlanet) st.setHoverPlanet(bestId)
    } else if (st.hoverPlanet !== -1) {
      st.setHoverPlanet(-1)
    }
  })

  return (
    <>
      <primitive object={parts.systemStars} />
      <primitive object={parts.orbits} />
      <primitive object={parts.planetMesh} />
      {parts.ringMesh && <primitive object={parts.ringMesh} />}
      {parts.beltPoints && <primitive object={parts.beltPoints} />}
      <primitive object={parts.gateGroup} />
      <primitive object={parts.marker} />
    </>
  )
}

export type { SystemDef, OrbitDef }
