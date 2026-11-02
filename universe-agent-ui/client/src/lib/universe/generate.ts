/**
 * Procedural generation of the Universe Agent memory sphere.
 *
 * Every visual category maps to Universe Agent's memory model:
 *  - Cosmic Core            intent, planning, retrieval, execution
 *  - Cognitive spiral arms    knowledge currents flowing from the core
 *  - Solar systems            skill / capability domains
 *  - L3 memory stars          crystallized facts, preferences, insights
 *  - L1 planets               work loops, active tasks, experiential records
 *  - L2 moons / belts         snippets, files, notes, sub-context fragments
 *  - L4 nebulae               compound skills, dense multi-context memory
 *  - L5 dark-matter lattice   long-term memory, rules, remote association
 *  - Knowledge gates          skill invocations / rapid context transfer
 *  - Dark cores               deep consolidation wells (subordinate)
 */
import { Euler, Quaternion, Vector3 } from "three";
import {
  clamp,
  fbm2,
  gaussian,
  lerp,
  mulberry32,
  range,
  rangeInt,
  smoothstep,
  type RNG,
} from "./rng";
import { c3, mix3, PALETTE } from "./palette";

export const R = 30; // memory sphere radius
export const CORE_R = 6.2; // galactic core radius
export const ARM_COUNT = 5;
export const ARM_R0 = 4.8;

export type Quality = "high" | "medium" | "low" | "mini" | "spark" | "orb";

export interface ArmDef {
  index: number;
  thetaStart: number;
  twist: number;
  pitch: number;
  width: number;
}

export interface OrbitDef {
  radius: number;
  ecc: number;
  quat: [number, number, number, number];
  speed: number;
  phase0: number;
  dir: 1 | -1;
}

export interface PlanetDef {
  id: number;
  systemId: number;
  orbit: number;
  type: number; // 0 rocky 1 oceanic 2 icy 3 metallic 4 gas 5 moon
  size: number;
  seed: number;
  speed: number;
  phase: number;
  dir: 1 | -1;
  tint: [number, number, number];
  rim: [number, number, number];
  parent: number; // -1 for planets, planet id for moons
  moonOrbit: number;
  hasRing: boolean;
  /** real memory: the trusted skill this planet represents */
  realSkill?: {
    id: string;
    name: string;
    confidence: number;
    useCount: number;
    category: string;
    description: string;
  };
}

export interface SystemDef {
  id: number;
  center: [number, number, number];
  scale: number;
  complexity: number;
  radius: number;
  orbits: OrbitDef[];
  planetIds: number[];
  starColor: [number, number, number];
  starSize: number;
  hasBelt: boolean;
  beltR: number;
  beltSpread: number;
  beltCount: number;
  /** real memory: taxonomy solar system this visual system represents */
  realName: string;
  realDescription: string;
  realSkillCount: number;
}

export interface GateDef {
  id: number;
  systemId: number;
  pos: [number, number, number];
  radius: number;
  quat: [number, number, number, number];
  color: [number, number, number];
  spin: number;
  phase: number;
}

export interface DarkCoreDef {
  id: number;
  pos: [number, number, number];
  radius: number;
  quat: [number, number, number, number];
  disk: [number, number, number];
  ringInner: number;
  ringOuter: number;
}

export interface NebulaDef {
  pos: [number, number, number];
  radius: number;
  planes: number;
  seed: number;
  hue: number;
  breath: number;
  layerScales: number[];
  layerAlphas: number[];
  rots: number[];
}

export interface MemoryStarDef {
  id: number;
  pos: [number, number, number];
  color: [number, number, number];
  size: number;
  brightness: number;
  tw: number;
  ph: number;
  knn: number[];
  /** real memory: the high-confidence skill this star represents */
  realName?: string;
  realConfidence?: number;
}

export interface PointField {
  pos: Float32Array;
  color: Float32Array;
  size: Float32Array;
  tw: Float32Array;
  ph: Float32Array;
  rnd: Float32Array;
  count: number;
}

export interface UniverseData {
  quality: Quality;
  arms: ArmDef[];
  armField: PointField;
  coreParticles: PointField;
  stars: MemoryStarDef[];
  starField: PointField;
  systems: SystemDef[];
  systemStars: {
    pos: Float32Array;
    color: Float32Array;
    size: Float32Array;
    sysId: Float32Array;
    count: number;
  };
  orbitLines: {
    pos: Float32Array;
    sysId: Float32Array;
    orbIdx: Float32Array;
    count: number;
  };
  belts: {
    pos: Float32Array;
    color: Float32Array;
    size: Float32Array;
    sysId: Float32Array;
    count: number;
  };
  planets: PlanetDef[];
  gates: GateDef[];
  darkCores: DarkCoreDef[];
  nebulae: NebulaDef[];
  lattice: {
    pos: Float32Array;
    path: Float32Array;
    count: number;
    pathCount: number;
    nodes: PointField;
  };
  dust: PointField;
  bgStars: PointField;
}

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

const qFromEuler = (
  x: number,
  y: number,
  z: number
): [number, number, number, number] => {
  const q = new Quaternion().setFromEuler(new Euler(x, y, z, "XYZ"));
  return [q.x, q.y, q.z, q.w];
};

/** Point on an arm's mean logarithmic spiral curve. */
export function armPoint(
  a: ArmDef,
  t: number
): { x: number; y: number; z: number; theta: number; r: number } {
  const theta = a.thetaStart + t * a.twist;
  const r = ARM_R0 * Math.exp(a.pitch * t * a.twist);
  return { x: Math.cos(theta) * r, y: 0, z: Math.sin(theta) * r, theta, r };
}

interface FieldBuilder {
  pos: Float32Array;
  color: Float32Array;
  size: Float32Array;
  tw: Float32Array;
  ph: Float32Array;
  rnd: Float32Array;
  n: number;
}

function emptyField(cap: number): FieldBuilder {
  return {
    pos: new Float32Array(cap * 3),
    color: new Float32Array(cap * 3),
    size: new Float32Array(cap),
    tw: new Float32Array(cap),
    ph: new Float32Array(cap),
    rnd: new Float32Array(cap),
    n: 0,
  };
}

const pushPt = (
  f: FieldBuilder,
  x: number,
  y: number,
  z: number,
  c: [number, number, number],
  size: number,
  tw: number,
  ph: number,
  rnd: number
) => {
  const i = f.n++;
  f.pos[i * 3] = x;
  f.pos[i * 3 + 1] = y;
  f.pos[i * 3 + 2] = z;
  f.color[i * 3] = c[0];
  f.color[i * 3 + 1] = c[1];
  f.color[i * 3 + 2] = c[2];
  f.size[i] = size;
  f.tw[i] = tw;
  f.ph[i] = ph;
  f.rnd[i] = rnd;
};

const finalize = (f: FieldBuilder): PointField => ({
  pos: f.pos.subarray(0, f.n * 3),
  color: f.color.subarray(0, f.n * 3),
  size: f.size.subarray(0, f.n),
  tw: f.tw.subarray(0, f.n),
  ph: f.ph.subarray(0, f.n),
  rnd: f.rnd.subarray(0, f.n),
  count: f.n,
});

/* ------------------------------------------------------------------ */
/* quality profiles                                                    */
/* ------------------------------------------------------------------ */

const PROFILE: Record<
  Quality,
  {
    armPoints: number;
    pockets: number;
    stars: number;
    systems: number;
    coreParticles: number;
    dust: number;
    bg: number;
    belts: number;
    latticePaths: number;
  }
> = {
  high: {
    armPoints: 168000,
    pockets: 150,
    stars: 950,
    systems: 60,
    coreParticles: 2600,
    dust: 1400,
    bg: 780,
    belts: 1,
    latticePaths: 34,
  },
  medium: {
    armPoints: 112000,
    pockets: 112,
    stars: 780,
    systems: 54,
    coreParticles: 2000,
    dust: 1000,
    bg: 620,
    belts: 0.8,
    latticePaths: 28,
  },
  low: {
    armPoints: 68000,
    pockets: 80,
    stars: 600,
    systems: 46,
    coreParticles: 1400,
    dust: 640,
    bg: 460,
    belts: 0.6,
    latticePaths: 22,
  },
  // in-chat thinking indicator (MiniUniverse): a tiny fraction of the full
  // sphere — real arms + core + real systems + top stars only
  mini: {
    armPoints: 9000,
    pockets: 14,
    stars: 90,
    systems: 24,
    coreParticles: 420,
    dust: 0,
    bg: 90,
    belts: 0,
    latticePaths: 0,
  },
  // inline thinking spark (ThinkingSpark): the same memory sphere at ~1.5%
  // of mini — same builder, same hash32 placement, same materials. A 44px
  // canvas never needs more than a few hundred points; arms + core only,
  // no systems/stars/dust/bg/belts/lattice.
  spark: {
    armPoints: 260,
    pockets: 0,
    stars: 0,
    systems: 0,
    coreParticles: 60,
    dust: 0,
    bg: 0,
    belts: 0,
    latticePaths: 0,
  },
  // trailing thinking orb (TrailingUniverse, M1 flow): the permanent thinking
  // loader — a touch richer than spark so the handoff color shift reads
  // clearly at ~32px, still arms + core only.
  orb: {
    armPoints: 340,
    pockets: 0,
    stars: 0,
    systems: 0,
    coreParticles: 70,
    dust: 0,
    bg: 0,
    belts: 0,
    latticePaths: 0,
  },
};

/* ------------------------------------------------------------------ */
/* real memory snapshot (exported from ~/.universe by universe_bridge.py)   */
/* ------------------------------------------------------------------ */

export interface RealOrbit {
  id: string;
  solar_system_id: string;
  name: string;
  description: string;
  skill_count: number;
}

export interface RealSystem {
  id: string;
  name: string;
  description: string;
  skill_count: number;
  orbits: RealOrbit[];
}

export interface RealSkill {
  id: string;
  name: string;
  solar_system_id: string;
  orbit_id: string;
  target_agent: string;
  confidence: number;
  use_count: number;
  category: string;
  description: string;
  status: string;
}

export interface RealUniverseMemory {
  generated_at: number;
  counts: { skills: number; systems: number; orbits: number; trusted: number };
  systems: RealSystem[];
  /** top-confidence skills — become the brightest L3 stars */
  stars: RealSkill[];
  /** every skill grouped by its real orbit id — become planets */
  skillsByOrbit?: Record<string, RealSkill[]>;
}

/**
 * Deterministic 32-bit hash of a string — stable system placement across runs.
 * Shared by the big 3D universe AND the mini memory-universe (MiniUniverse) so a
 * skill lights up at the SAME relative position on both surfaces (§6). */
export function hash32(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/* ------------------------------------------------------------------ */
/* main builder                                                        */
/* ------------------------------------------------------------------ */

export function buildUniverse(
  quality: Quality,
  real?: RealUniverseMemory | null
): UniverseData {
  const P = PROFILE[quality];
  const rng = mulberry32(0x6a1c7ea3);

  /* ---- spiral arms ---- */
  const arms: ArmDef[] = [];
  for (let i = 0; i < ARM_COUNT; i++) {
    const twist = 7.4 + rng() * 3.0;
    const r1 = 27.2 + rng() * 1.5;
    arms.push({
      index: i,
      thetaStart: (i / ARM_COUNT) * Math.PI * 2 + (rng() - 0.5) * 0.55,
      twist,
      pitch: Math.log(r1 / ARM_R0) / twist,
      width: 1.0 + rng() * 0.75,
    });
  }

  const innerC = c3(PALETTE.cyanSoft, 0.8);
  const midC = c3(PALETTE.coolWhite, 0.46);
  const outerC = c3(PALETTE.indigo, 0.52);
  const activeC = c3(PALETTE.photonCyan, 1.25);
  const violetC = c3(PALETTE.violet, 0.85);

  const armF = emptyField(P.armPoints * 3 + P.pockets * 110);
  const scatterOnArm = (a: ArmDef, t: number, s: number) => {
    const p = armPoint(a, t);
    const width = a.width * (0.55 + 0.55 * t) * s;
    const px = -Math.sin(p.theta);
    const pz = Math.cos(p.theta);
    const off = gaussian(rng) * width;
    const rz = 1 + gaussian(rng) * 0.026;
    return {
      x: p.x * rz + px * off,
      y: gaussian(rng) * (0.5 + 2.1 * t) * s,
      z: p.z * rz + pz * off,
      r: p.r,
      theta: p.theta,
    };
  };

  for (let i = 0; i < P.armPoints; i++) {
    const a = arms[rangeInt(rng, 0, ARM_COUNT - 1)];
    const t = Math.pow(rng(), 1.32);
    const d = fbm2(a.thetaStart + t * a.twist * 1.15, t * 4.4 + a.index * 9.7);
    if (rng() > 0.3 + 0.72 * d) continue;
    const s = scatterOnArm(a, t, 1);
    if (s.x * s.x + s.y * s.y + s.z * s.z > R * 0.968 * (R * 0.968)) continue;
    const u = clamp((s.r - ARM_R0) / (R - ARM_R0), 0, 1);
    let c = mix3(innerC, midC, smoothstep(0, 0.55, u));
    c = mix3(c, outerC, smoothstep(0.5, 1, u));
    let bright = (0.42 + 0.5 * d) * (0.75 + 0.4 * rng());
    let size = 0.1 + 0.34 * Math.pow(rng(), 2.1);
    const roll = rng();
    if (roll < 0.042) {
      c = activeC;
      bright *= 1.5;
      size *= 1.35;
    } else if (roll < 0.062) {
      c = violetC;
      bright *= 1.15;
    } else if (roll < 0.075) {
      // rare bright knowledge knot
      bright *= 2.1;
      size = 0.5 + rng() * 0.5;
    }
    const edge = smoothstep(R, R * 0.9, Math.hypot(s.x, s.y, s.z));
    bright *= edge;
    pushPt(
      armF,
      s.x,
      s.y,
      s.z,
      [c[0] * bright, c[1] * bright, c[2] * bright],
      size,
      rng(),
      rng() * 6.28,
      rng()
    );
  }

  // star-forming pockets: dense bright clusters riding the arms
  for (let i = 0; i < P.pockets; i++) {
    const a = arms[rangeInt(rng, 0, ARM_COUNT - 1)];
    const t = 0.22 + 0.72 * Math.pow(rng(), 0.85);
    const base = scatterOnArm(a, t, 0.42);
    const n = rangeInt(rng, 42, 105);
    const sig = 0.32 + 0.5 * rng();
    const hue = rng();
    for (let k = 0; k < n; k++) {
      const x = base.x + gaussian(rng) * sig;
      const y = base.y + gaussian(rng) * sig * 0.55;
      const z = base.z + gaussian(rng) * sig;
      const br = range(rng, 0.9, 1.9);
      const c = mix3(
        c3(PALETTE.coolWhite, 1.0),
        c3(PALETTE.photonCyan, 1.1),
        hue * 0.6
      );
      pushPt(
        armF,
        x,
        y,
        z,
        [c[0] * br, c[1] * br, c[2] * br],
        0.09 + 0.2 * rng(),
        rng(),
        rng() * 6.28,
        rng()
      );
    }
  }

  const armField = finalize(armF);

  /* ---- L3 memory stars ---- */
  const starBases: [number, number, number][] = [];
  const placeAlongField = (
    rMin: number,
    rMax: number
  ): [number, number, number] => {
    if (rng() < 0.7) {
      const a = arms[rangeInt(rng, 0, ARM_COUNT - 1)];
      const t = 0.15 + 0.82 * rng();
      const s = scatterOnArm(a, t, 2.6);
      return [s.x, s.y, s.z];
    }
    // uniform in shell
    for (let tries = 0; tries < 24; tries++) {
      const u = rng() * 2 - 1;
      const phi = rng() * Math.PI * 2;
      const rr = Math.cbrt(lerp(rMin ** 3, rMax ** 3, rng()));
      const x = Math.cos(phi) * Math.sqrt(1 - u * u) * rr;
      const y = u * rr * 0.72;
      const z = Math.sin(phi) * Math.sqrt(1 - u * u) * rr;
      if (Math.hypot(x, y, z) > rMin) return [x, y, z];
    }
    return [rMin + 2, 0, 0];
  };
  // isolated
  const nIso = Math.round(P.stars * 0.6);
  for (let i = 0; i < nIso; i++) {
    starBases.push(placeAlongField(8.5, 27.5));
  }
  // topic clusters / constellations
  const nCl = P.stars - nIso;
  const clusterCount = 12;
  for (let ci = 0; ci < clusterCount; ci++) {
    const a = arms[rangeInt(rng, 0, ARM_COUNT - 1)];
    const t = 0.2 + 0.74 * rng();
    const bc = scatterOnArm(a, t, 1.5);
    const per = rangeInt(
      rng,
      Math.max(6, Math.floor((nCl / clusterCount) * 0.6)),
      Math.ceil((nCl / clusterCount) * 1.4)
    );
    const sig = range(rng, 1.4, 2.8);
    for (let k = 0; k < per && starBases.length < P.stars; k++) {
      starBases.push([
        bc.x + gaussian(rng) * sig,
        bc.y + gaussian(rng) * sig * 0.6,
        bc.z + gaussian(rng) * sig,
      ]);
    }
  }

  const whiteC = c3(PALETTE.starWhite, 1);
  const cyanC = c3(PALETTE.photonCyan, 1);
  const vioC = c3(PALETTE.violet, 1);
  const stars: MemoryStarDef[] = [];
  // Real-memory mode: the brightest L3 stars ARE the top-confidence skills
  // (60 exported by universe_bridge), placed on the arms deterministically.
  const realStars = real?.stars ?? [];
  for (let i = 0; i < starBases.length; i++) {
    const base = starBases[i];
    const dist = Math.hypot(base[0], base[1], base[2]);
    if (dist < 7.6 || dist > R * 0.955) continue;
    const imp = Math.pow(rng(), 1.7);
    const roll = rng();
    const c = roll < 0.52 ? whiteC : roll < 0.78 ? cyanC : vioC;
    const b = (0.42 + 0.66 * imp) * smoothstep(R, R * 0.88, dist);
    const rs = i < realStars.length ? realStars[i] : undefined;
    stars.push({
      id: stars.length,
      pos: base,
      color: [c[0] * b, c[1] * b, c[2] * b],
      size: rs
        ? 0.4 + 1.7 * Math.pow(rs.confidence || 0.5, 2)
        : 0.32 + 1.5 * Math.pow(imp, 1.6),
      brightness: b,
      tw: rng(),
      ph: rng() * 6.28,
      knn: [],
      realName: rs?.name,
      realConfidence: rs?.confidence,
    });
  }
  // kNN for relationship traces (brute force, init only)
  for (let i = 0; i < stars.length; i++) {
    const a = stars[i].pos;
    const best: { j: number; d: number }[] = [];
    for (let j = 0; j < stars.length; j++) {
      if (i === j) continue;
      const b = stars[j].pos;
      const dx = a[0] - b[0];
      const dy = a[1] - b[1];
      const dz = a[2] - b[2];
      const d = dx * dx + dy * dy + dz * dz;
      if (best.length < 3) {
        best.push({ j, d });
        best.sort((x, y) => x.d - y.d);
      } else if (d < best[2].d) {
        best[2] = { j, d };
        best.sort((x, y) => x.d - y.d);
      }
    }
    stars[i].knn = best.map(b => b.j);
  }

  const starFieldF = emptyField(stars.length);
  for (const s of stars) {
    pushPt(
      starFieldF,
      s.pos[0],
      s.pos[1],
      s.pos[2],
      s.color,
      s.size,
      s.tw,
      s.ph,
      rng()
    );
  }
  const starField = finalize(starFieldF);

  /* ---- solar systems (skill / domain clusters) ---- */
  const systems: SystemDef[] = [];
  const v = new Vector3();
  const minSep = (
    a: [number, number, number],
    b: [number, number, number],
    ra: number,
    rb: number
  ) =>
    Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) > (ra + rb) * 0.55 + 1.6;

  // Real-memory mode: one solar system per real taxonomy system, sized and
  // orbited by the exact real values. Deterministic placement from the
  // system's name so the universe is stable across sessions.
  if (real && real.systems.length > 0) {
    const maxSkills = Math.max(...real.systems.map(s => s.skill_count), 1);
    for (const rs of real.systems) {
      const h = hash32(rs.name);
      const rpos = mulberry32(h);
      const armIdx = h % ARM_COUNT;
      const a = arms[armIdx];
      const t = 0.24 + (0.66 * ((h >>> 8) % 1000)) / 1000;
      const s = scatterOnArm(a, t, 1.35);
      let center: [number, number, number] = [s.x, s.y, s.z];
      let d = Math.hypot(...center);
      // every real system must appear: pull into the valid band deterministically
      if (d > 28.2) {
        const k = 26.5 / d;
        center = [center[0] * k, center[1] * k, center[2] * k];
        d = Math.hypot(...center);
      }
      if (d < 9.2) {
        const k = 10.4 / Math.max(d, 0.001);
        center = [center[0] * k, center[1] * k, center[2] * k];
      }
      // visual scale ∝ real skill count (sqrt so 780 vs 0 is dramatic but sane)
      const skillFrac = Math.sqrt(rs.skill_count / maxSkills);
      const scale = 0.55 + 1.9 * skillFrac;
      const radius = 1.5 + 2.4 * scale;
      // skip only if it genuinely overlaps another real system (rare with 11)
      if (!systems.every(s2 => minSep(s2.center, center, s2.radius, radius))) {
        // nudge radially outward until it fits — real systems never drop
        let tries = 0;
        while (
          tries < 24 &&
          !systems.every(s2 => minSep(s2.center, center, s2.radius, radius))
        ) {
          const ang = tries * 2.399963; // golden-angle spiral of nudges
          const dd = Math.hypot(...center);
          const nk =
            clamp(dd + (tries % 2 === 0 ? 1.3 : -1.3), 10.5, 27.5) /
            Math.max(dd, 0.001);
          const jx = Math.sin(ang) * 0.7;
          const jz = Math.cos(ang) * 0.7;
          center = [center[0] * nk + jx, center[1], center[2] * nk + jz];
          tries++;
        }
      }
      // real orbit count for this system, clamped for visual sanity
      const orbitCount = clamp(rs.orbits.length, 1, 6);
      const orbits: OrbitDef[] = [];
      let prevR = 0;
      for (let i = 0; i < orbitCount; i++) {
        const frac =
          orbitCount === 1 ? 0.55 : 0.3 + (i / (orbitCount - 1)) * 0.62;
        const radiusO = radius * frac * (0.92 + rpos() * 0.18);
        if (radiusO - prevR < 0.42) continue;
        prevR = radiusO;
        orbits.push({
          radius: radiusO,
          ecc: rpos() * 0.28,
          quat: qFromEuler(
            (rpos() - 0.5) * 0.62,
            rpos() * Math.PI * 2,
            (rpos() - 0.5) * 0.62
          ),
          speed: ((range(rpos, 0.09, 0.17) / Math.pow(radiusO, 0.85)) *
            (rpos() < 0.5 ? 1 : -1)) as 1 | -1,
          phase0: rpos() * Math.PI * 2,
          dir: rpos() < 0.5 ? 1 : -1,
        });
      }
      if (orbits.length === 0)
        orbits.push({
          radius: radius * 0.55,
          ecc: 0,
          quat: qFromEuler(0, 0, 0),
          speed: 0.12,
          phase0: 0,
          dir: 1,
        });
      // star colour: stable per system from its name hash
      const roll = ((h >>> 16) % 100) / 100;
      const starColor =
        roll < 0.5
          ? c3(PALETTE.coolWhite, range(rpos, 0.85, 1.2))
          : roll < 0.76
            ? c3(PALETTE.cyanSoft, range(rpos, 0.9, 1.25))
            : c3(PALETTE.violet, range(rpos, 0.85, 1.15));
      // belt presence/count from real density: rich systems get a dust belt
      const hasBelt = rs.skill_count > 0 && skillFrac > 0.34 && P.belts >= 0.8;
      const beltIdx = hasBelt
        ? Math.min(orbits.length - 1, Math.floor(orbitCount / 2))
        : 0;
      systems.push({
        id: systems.length,
        center,
        scale,
        complexity: skillFrac,
        radius,
        orbits,
        planetIds: [],
        starColor,
        starSize: 0.72 + 1.05 * Math.pow(scale, 0.9),
        hasBelt,
        beltR: hasBelt ? orbits[beltIdx].radius * 0.66 : 0,
        beltSpread: hasBelt ? orbits[beltIdx].radius * 0.1 : 0,
        beltCount: hasBelt
          ? clamp(Math.round(rs.skill_count * 0.06), 34, 90)
          : 0,
        realName: rs.name,
        realDescription: rs.description,
        realSkillCount: rs.skill_count,
      });
    }
  }

  let guard = 0;
  const sysCount = real && real.systems.length > 0 ? 0 : P.systems;
  while (systems.length < sysCount && guard < sysCount * 60) {
    guard++;
    let center: [number, number, number];
    if (rng() < 0.66) {
      const a = arms[rangeInt(rng, 0, ARM_COUNT - 1)];
      const t = 0.22 + 0.72 * rng();
      const s = scatterOnArm(a, t, 1.35);
      center = [s.x, s.y, s.z];
    } else {
      const u = rng() * 2 - 1;
      const phi = rng() * Math.PI * 2;
      const rr = Math.cbrt(lerp(11 ** 3, 27.5 ** 3, rng()));
      center = [
        Math.cos(phi) * Math.sqrt(1 - u * u) * rr,
        u * rr * 0.7,
        Math.sin(phi) * Math.sqrt(1 - u * u) * rr,
      ];
    }
    const scale = 0.55 + 2.05 * Math.pow(rng(), 1.5);
    const radius = 1.5 + 2.4 * scale;
    const d = Math.hypot(...center);
    if (d < 9.2 || d > 28.2) continue;
    if (!systems.every(s => minSep(s.center, center, s.radius, radius)))
      continue;
    const complexity = rng();
    const orbitCount = clamp(1 + Math.floor(Math.pow(rng(), 1.35) * 3.6), 1, 4);
    const orbits: OrbitDef[] = [];
    let prevR = 0;
    for (let i = 0; i < orbitCount; i++) {
      const frac =
        orbitCount === 1 ? 0.55 : 0.3 + (i / (orbitCount - 1)) * 0.62;
      const radiusO = radius * frac * (0.92 + rng() * 0.18);
      if (radiusO - prevR < 0.42) continue;
      prevR = radiusO;
      orbits.push({
        radius: radiusO,
        ecc: rng() * 0.28,
        quat: qFromEuler(
          (rng() - 0.5) * 0.62,
          rng() * Math.PI * 2,
          (rng() - 0.5) * 0.62
        ),
        speed: ((range(rng, 0.09, 0.17) / Math.pow(radiusO, 0.85)) *
          (rng() < 0.5 ? 1 : -1)) as 1 | -1,
        phase0: rng() * Math.PI * 2,
        dir: rng() < 0.5 ? 1 : -1,
      });
    }
    if (orbits.length === 0) continue;
    const roll = rng();
    const starColor =
      roll < 0.5
        ? c3(PALETTE.coolWhite, range(rng, 0.85, 1.2))
        : roll < 0.76
          ? c3(PALETTE.cyanSoft, range(rng, 0.9, 1.25))
          : c3(PALETTE.violet, range(rng, 0.85, 1.15));
    const hasBelt = rng() < 0.24 * P.belts && orbits.length >= 1;
    const beltIdx = hasBelt ? rangeInt(rng, 0, orbits.length - 1) : 0;
    systems.push({
      id: systems.length,
      center,
      scale,
      complexity,
      radius,
      orbits,
      planetIds: [],
      starColor,
      starSize: 0.72 + 1.05 * Math.pow(scale, 0.9),
      hasBelt,
      beltR: hasBelt ? orbits[beltIdx].radius * 0.66 : 0,
      beltSpread: hasBelt ? orbits[beltIdx].radius * 0.1 : 0,
      beltCount: hasBelt ? Math.round(range(rng, 34, 90) * P.belts) : 0,
      realName: "",
      realDescription: "",
      realSkillCount: 0,
    });
  }

  /* ---- planets + moons ---- */
  const planets: PlanetDef[] = [];
  const rockyT = c3(PALETTE.rocky, 1);
  const oceanT = c3(PALETTE.oceanDeep, 1.6);
  const icyT = c3(PALETTE.icy, 1);
  const metalT = c3(PALETTE.metal, 1);
  const gasT = c3(PALETTE.gas, 1);
  const moonT = c3(PALETTE.moon, 0.9);
  const rimCyan = c3(PALETTE.photonCyan, 0.85);
  const rimPale = c3(PALETTE.coolWhite, 0.55);
  const rimIndigo = c3(PALETTE.indigo, 0.7);
  const rimViolet = c3(PALETTE.violet, 0.55);

  for (const sys of systems) {
    // Real-memory mode: planets are real skills drawn from this system's real
    // orbits, distributed across the visual orbits by real orbit density.
    const realPlan: { orbitIdx: number; skill: RealSkill }[] = [];
    if (real && sys.realName) {
      const rs = real.systems.find(x => x.name === sys.realName);
      if (rs && rs.orbits.length > 0) {
        const maxPlanets = 8;
        const totalOrbitSkills = rs.orbits.reduce(
          (acc, o) => acc + o.skill_count,
          0
        );
        for (let oi = 0; oi < rs.orbits.length; oi++) {
          const ro = rs.orbits[oi];
          if (ro.skill_count <= 0 || totalOrbitSkills <= 0) continue;
          const share = Math.max(
            1,
            Math.round((ro.skill_count / totalOrbitSkills) * maxPlanets)
          );
          const pool = real.skillsByOrbit?.[ro.id] ?? [];
          const sorted = pool
            .slice()
            .sort((x, y) => (y.confidence || 0) - (x.confidence || 0));
          for (let k = 0; k < Math.min(share, sorted.length); k++) {
            realPlan.push({ orbitIdx: oi, skill: sorted[k] });
          }
          if (realPlan.length >= maxPlanets) break;
        }
      }
    }
    const pCount = clamp(
      1 + Math.floor(Math.pow(rng(), 0.8) * 3.4),
      1,
      sys.orbits.length
    );
    const planetCount = sys.realName ? realPlan.length : pCount;
    const orbitIdx = sys.orbits.map((_, i) => i);
    for (let i = orbitIdx.length - 1; i > 0; i--) {
      const j = rangeInt(rng, 0, i);
      [orbitIdx[i], orbitIdx[j]] = [orbitIdx[j], orbitIdx[i]];
    }
    const realOrbitIdx = realPlan.map(p => p.orbitIdx);
    for (let i = 0; i < planetCount; i++) {
      // real mode: a real skill always lands on an existing visual orbit
      // (visual orbits may be fewer than real ones — spacing skips)
      const orbIdx = sys.realName
        ? realOrbitIdx[i] % sys.orbits.length
        : orbitIdx[i];
      const orb = sys.orbits[orbIdx];
      const roll = rng();
      const type =
        roll < 0.38
          ? 0
          : roll < 0.58
            ? 1
            : roll < 0.74
              ? 2
              : roll < 0.92
                ? 3
                : 4;
      const sizeBase = 0.7 + 0.6 * sys.scale;
      const size =
        type === 4
          ? range(rng, 0.24, 0.42) * sizeBase
          : range(rng, 0.09, 0.22) * Math.pow(sizeBase, 0.9);
      let tint: [number, number, number];
      let rim: [number, number, number];
      if (type === 0) {
        tint = mix3(rockyT, mix3(metalT, moonT, rng() * 0.5), rng());
        rim = rimIndigo;
      } else if (type === 1) {
        tint = oceanT;
        rim = rimCyan;
      } else if (type === 2) {
        tint = icyT;
        rim = rimPale;
      } else if (type === 3) {
        tint = metalT;
        rim = rimPale;
      } else {
        tint = mix3(
          gasT,
          mix3(c3(PALETTE.indigoMid, 1.2), metalT, rng()),
          rng()
        );
        rim = rimViolet;
      }
      const pid = planets.length;
      const realSkillData =
        sys.realName && realPlan[i]
          ? {
              id: realPlan[i].skill.id,
              name: realPlan[i].skill.name,
              confidence: realPlan[i].skill.confidence ?? 0,
              useCount: realPlan[i].skill.use_count ?? 0,
              category: realPlan[i].skill.category ?? "",
              description: (realPlan[i].skill.description ?? "").slice(0, 140),
            }
          : undefined;
      // planet size reflects the real skill's confidence
      const confScale = realSkillData
        ? 0.75 + 0.6 * (realSkillData.confidence || 0.5)
        : 1;
      planets.push({
        id: pid,
        systemId: sys.id,
        orbit: orbIdx,
        type,
        size: size * confScale,
        seed: rng(),
        speed: range(rng, 0.8, 1.25),
        phase: rng() * Math.PI * 2,
        dir: orb.dir,
        tint,
        rim,
        parent: -1,
        moonOrbit: 0,
        hasRing: type === 4 && rng() < 0.65,
        realSkill: realSkillData,
      });
      sys.planetIds.push(pid);
      // moons: snippets / fragments / sub-contexts (procedural only — real
      // skills are planets; empty systems get no dead decoration)
      if (!sys.realName && type !== 4 && rng() < 0.3) {
        const mCount = 1 + (rng() < 0.25 ? 1 : 0);
        for (let m = 0; m < mCount; m++) {
          planets.push({
            id: planets.length,
            systemId: sys.id,
            orbit: -1,
            type: 5,
            size: size * range(rng, 0.2, 0.34),
            seed: rng(),
            speed: range(rng, 0.5, 1.1),
            phase: rng() * Math.PI * 2,
            dir: rng() < 0.5 ? 1 : -1,
            tint: moonT,
            rim: rimIndigo,
            parent: pid,
            moonOrbit: size * range(rng, 2.1, 3.4),
            hasRing: false,
          });
        }
      }
    }
  }

  /* ---- merged system star sprites ---- */
  const ssF = {
    pos: new Float32Array(systems.length * 3),
    color: new Float32Array(systems.length * 3),
    size: new Float32Array(systems.length),
    sysId: new Float32Array(systems.length),
    count: systems.length,
  };
  systems.forEach((s, i) => {
    ssF.pos.set(s.center, i * 3);
    ssF.color.set(s.starColor, i * 3);
    ssF.size[i] = s.starSize;
    ssF.sysId[i] = s.id;
  });

  /* ---- merged orbit lines ---- */
  const SEGS = 84;
  const orbitSegs: number[] = [];
  const orbitSys: number[] = [];
  const orbitIdxArr: number[] = [];
  for (const sys of systems) {
    sys.orbits.forEach((o, oi) => {
      const a = o.radius;
      const b = a * Math.sqrt(1 - o.ecc * o.ecc);
      const c = -a * o.ecc;
      const q = new Quaternion(o.quat[0], o.quat[1], o.quat[2], o.quat[3]);
      let prev: Vector3 | null = null;
      for (let i = 0; i <= SEGS; i++) {
        const ang = (i / SEGS) * Math.PI * 2;
        const p = new Vector3(Math.cos(ang) * a + c, 0, Math.sin(ang) * b)
          .applyQuaternion(q)
          .add(v.set(...sys.center));
        if (prev) {
          orbitSegs.push(prev.x, prev.y, prev.z, p.x, p.y, p.z);
          orbitSys.push(sys.id, sys.id);
          orbitIdxArr.push(oi, oi);
        }
        prev = p;
      }
    });
  }

  /* ---- asteroid belts (fragments / snippets) ---- */
  const beltPts: number[] = [];
  const beltCols: number[] = [];
  const beltSizes: number[] = [];
  const beltSys: number[] = [];
  const dustC = c3(PALETTE.indigo, 0.85);
  for (const sys of systems) {
    if (!sys.hasBelt) continue;
    const q = new Quaternion();
    for (let i = 0; i < sys.beltCount; i++) {
      const ang = rng() * Math.PI * 2;
      const rr = sys.beltR + gaussian(rng) * sys.beltSpread;
      const p = new Vector3(
        Math.cos(ang) * rr,
        gaussian(rng) * sys.beltSpread * 0.32,
        Math.sin(ang) * rr
      )
        .applyQuaternion(q)
        .add(v.set(...sys.center));
      const br = range(rng, 0.35, 0.85);
      beltPts.push(p.x, p.y, p.z);
      beltCols.push(dustC[0] * br, dustC[1] * br, dustC[2] * br);
      beltSizes.push(range(rng, 0.06, 0.16));
      beltSys.push(sys.id);
    }
  }

  /* ---- knowledge gates ---- */
  const gates: GateDef[] = [];
  for (const sys of systems) {
    if (rng() >= 0.13) continue;
    const cyan = rng() < 0.7;
    gates.push({
      id: gates.length,
      systemId: sys.id,
      pos: [...sys.center] as [number, number, number],
      radius: sys.radius * range(rng, 0.5, 0.66),
      quat: qFromEuler(
        range(rng, 0.2, Math.PI - 0.2),
        rng() * Math.PI * 2,
        range(rng, 0.2, Math.PI - 0.2)
      ),
      color: cyan ? c3(PALETTE.photonCyan, 0.9) : c3(PALETTE.violet, 0.9),
      spin: range(rng, 0.14, 0.32) * (rng() < 0.5 ? 1 : -1),
      phase: rng() * 6.28,
    });
    if (gates.length >= 8) break;
  }

  /* ---- dark cores (subordinate consolidation wells) ---- */
  const darkCores: DarkCoreDef[] = [];
  while (darkCores.length < 4) {
    const u = rng() * 2 - 1;
    const phi = rng() * Math.PI * 2;
    const rr = range(rng, 14.5, 24);
    const p: [number, number, number] = [
      Math.cos(phi) * Math.sqrt(1 - u * u) * rr,
      u * rr * 0.66,
      Math.sin(phi) * Math.sqrt(1 - u * u) * rr,
    ];
    if (
      !systems.every(
        s =>
          Math.hypot(
            s.center[0] - p[0],
            s.center[1] - p[1],
            s.center[2] - p[2]
          ) > 5.4
      )
    )
      continue;
    const isAmber = darkCores.length === 3; // exactly one rare amber accretion state
    darkCores.push({
      id: darkCores.length,
      pos: p,
      radius: range(rng, 0.5, 0.92),
      quat: qFromEuler(
        (rng() - 0.5) * 0.9,
        rng() * Math.PI * 2,
        (rng() - 0.5) * 0.9
      ),
      disk: isAmber ? c3(PALETTE.amber, 0.6) : c3(PALETTE.indigo, 0.8),
      ringInner: 1.35,
      ringOuter: 2.5,
    });
  }

  /* ---- L4 compound-memory nebulae ---- */
  const nebulae: NebulaDef[] = [];
  while (nebulae.length < 11) {
    let pos: [number, number, number];
    if (rng() < 0.7) {
      const a = arms[rangeInt(rng, 0, ARM_COUNT - 1)];
      const t = 0.42 + 0.52 * rng();
      const s = scatterOnArm(a, t, 1.4);
      pos = [s.x, s.y, s.z];
    } else {
      const u = rng() * 2 - 1;
      const phi = rng() * Math.PI * 2;
      const rr = range(rng, 13, 26);
      pos = [
        Math.cos(phi) * Math.sqrt(1 - u * u) * rr,
        u * rr * 0.7,
        Math.sin(phi) * Math.sqrt(1 - u * u) * rr,
      ];
    }
    const d = Math.hypot(...pos);
    if (d < 11 || d > 27) continue;
    const radius = range(rng, 3.2, 7.0);
    const planes = rangeInt(rng, 3, 5);
    const layerScales: number[] = [];
    const layerAlphas: number[] = [];
    const rots: number[] = [];
    for (let i = 0; i < planes; i++) {
      layerScales.push(radius * (1 - i * 0.16) * range(rng, 0.85, 1.1));
      layerAlphas.push(range(rng, 0.075, 0.16));
      rots.push(rng() * 6.28);
    }
    nebulae.push({
      pos,
      radius,
      planes,
      seed: rng() * 40,
      hue: clamp(gaussian(rng) * 0.26 + 0.5, 0.05, 0.95),
      breath: rng() * 6.28,
      layerScales,
      layerAlphas,
      rots,
    });
  }

  /* ---- L5 dark-matter lattice ---- */
  const latPos: number[] = [];
  const latPath: number[] = [];
  const nodePts = emptyField(512);
  const bandMin = R * 0.845;
  const bandMax = R * 0.975;
  for (let p = 0; p < P.latticePaths; p++) {
    const dir0 = new Vector3(
      gaussian(rng),
      gaussian(rng) * 0.6,
      gaussian(rng)
    ).normalize();
    const cur = dir0.clone().multiplyScalar(range(rng, bandMin, bandMax));
    const dir = new Vector3(
      gaussian(rng),
      gaussian(rng) * 0.5,
      gaussian(rng)
    ).normalize();
    const steps = rangeInt(rng, 6, 11);
    const prev: Vector3 | null = null;
    const pushSeg = (a: Vector3, b: Vector3) => {
      latPos.push(a.x, a.y, a.z, b.x, b.y, b.z);
      latPath.push(p, p);
    };
    const walk = (from: Vector3, d: Vector3, n: number) => {
      let c = from.clone();
      const dd = d.clone();
      for (let i = 0; i < n; i++) {
        const len = range(rng, 1.3, 2.1);
        dd.add(
          new Vector3(
            gaussian(rng),
            gaussian(rng),
            gaussian(rng)
          ).multiplyScalar(0.42)
        ).normalize();
        const next = c.clone().add(dd.clone().multiplyScalar(len));
        const rr = next.length();
        if (rr > bandMax) next.multiplyScalar(lerp(1, bandMax / rr, 0.75));
        if (rr < bandMin) next.multiplyScalar(lerp(1, bandMin / rr, 0.75));
        pushSeg(c, next);
        if (rng() < 0.2) {
          const np = emptyField(16);
          for (let k = 0; k < rangeInt(rng, 7, 13); k++) {
            pushPt(
              np,
              next.x + gaussian(rng) * 0.5,
              next.y + gaussian(rng) * 0.5,
              next.z + gaussian(rng) * 0.5,
              c3(PALETTE.indigo, range(rng, 0.5, 1.1)),
              range(rng, 0.18, 0.42),
              rng(),
              rng() * 6.28,
              rng()
            );
          }
          for (let k = 0; k < np.n; k++) {
            pushPt(
              nodePts,
              np.pos[k * 3],
              np.pos[k * 3 + 1],
              np.pos[k * 3 + 2],
              [np.color[k * 3], np.color[k * 3 + 1], np.color[k * 3 + 2]],
              np.size[k],
              np.tw[k],
              np.ph[k],
              np.rnd[k]
            );
          }
        }
        c = next;
      }
    };
    walk(cur, dir, steps);
    void prev;
  }

  /* ---- knowledge dust currents ---- */
  const dustF = emptyField(P.dust);
  for (let i = 0; i < P.dust; i++) {
    const u = rng() * 2 - 1;
    const phi = rng() * Math.PI * 2;
    const rr = Math.cbrt(lerp(7.5 ** 3, 28 ** 3, rng()));
    const x = Math.cos(phi) * Math.sqrt(1 - u * u) * rr;
    const y = u * rr * 0.6;
    const z = Math.sin(phi) * Math.sqrt(1 - u * u) * rr;
    const br = range(rng, 0.1, 0.3);
    pushPt(
      dustF,
      x,
      y,
      z,
      [dustC[0] * br, dustC[1] * br, dustC[2] * br],
      range(rng, 0.1, 0.3),
      rng(),
      rng() * 6.28,
      rng()
    );
  }

  /* ---- far background stars (outside the sphere) ---- */
  const bgF = emptyField(P.bg);
  const bgWhite = c3(PALETTE.starWhite, 1);
  const bgInd = c3(PALETTE.indigo, 1);
  for (let i = 0; i < P.bg; i++) {
    const u = rng() * 2 - 1;
    const phi = rng() * Math.PI * 2;
    const rr = range(rng, 95, 175);
    const x = Math.cos(phi) * Math.sqrt(1 - u * u) * rr;
    const y = u * rr;
    const z = Math.sin(phi) * Math.sqrt(1 - u * u) * rr;
    const c = rng() < 0.75 ? bgWhite : bgInd;
    const br = range(rng, 0.25, 0.75);
    pushPt(
      bgF,
      x,
      y,
      z,
      [c[0] * br, c[1] * br, c[2] * br],
      range(rng, 0.5, 1.4),
      rng() * 0.4,
      rng() * 6.28,
      rng()
    );
  }

  return {
    quality,
    arms,
    armField,
    coreParticles: buildCoreParticles(P.coreParticles, rng),
    stars,
    starField,
    systems,
    systemStars: ssF,
    orbitLines: {
      pos: new Float32Array(orbitSegs),
      sysId: new Float32Array(orbitSys),
      orbIdx: new Float32Array(orbitIdxArr),
      count: orbitSys.length,
    },
    belts: {
      pos: new Float32Array(beltPts),
      color: new Float32Array(beltCols),
      size: new Float32Array(beltSizes),
      sysId: new Float32Array(beltSys),
      count: beltSys.length,
    },
    planets,
    gates,
    darkCores,
    nebulae,
    lattice: {
      pos: new Float32Array(latPos),
      path: new Float32Array(latPath),
      count: latPath.length,
      pathCount: P.latticePaths,
      nodes: finalize(nodePts),
    },
    dust: finalize(dustF),
    bgStars: finalize(bgF),
  };
}

/** Core knowledge particles: two counter-rotating populations around the core. */
function buildCoreParticles(n: number, rng: RNG): PointField {
  const f = emptyField(n);
  const cyan = c3(PALETTE.photonCyan, 1.0);
  const indigo = c3(PALETTE.indigo, 1.0);
  const white = c3(PALETTE.coolWhite, 1);
  for (let i = 0; i < n; i++) {
    const disc = rng() < 0.62;
    let x: number, y: number, z: number;
    if (disc) {
      const rr = Math.abs(gaussian(rng)) * 3.4 + 1.4;
      const ang = rng() * Math.PI * 2;
      x = Math.cos(ang) * rr;
      z = Math.sin(ang) * rr;
      y = gaussian(rng) * 0.55;
    } else {
      const u = rng() * 2 - 1;
      const phi = rng() * Math.PI * 2;
      const rr = Math.cbrt(rng()) * 8.2 + 0.8;
      x = Math.cos(phi) * Math.sqrt(1 - u * u) * rr;
      y = u * rr;
      z = Math.sin(phi) * Math.sqrt(1 - u * u) * rr;
    }
    const roll = rng();
    const c = roll < 0.55 ? cyan : roll < 0.85 ? indigo : white;
    const br = range(rng, 0.4, 1.15);
    pushPt(
      f,
      x,
      y,
      z,
      [c[0] * br, c[1] * br, c[2] * br],
      range(rng, 0.1, 0.3),
      rng(),
      rng() * 6.28,
      rng()
    );
  }
  return finalize(f);
}

/** Convert a sphere-local position into world space through the sphere group. */
export function localToWorldFactory(group: {
  matrixWorld: import("three").Matrix4;
}) {
  const v = new Vector3();
  return (p: [number, number, number]) =>
    v.set(p[0], p[1], p[2]).applyMatrix4(group.matrixWorld);
}
