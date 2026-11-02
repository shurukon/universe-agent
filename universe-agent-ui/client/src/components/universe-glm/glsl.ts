/**
 * GLSL shader library for the Universe Agent memory sphere.
 * Every material here implements a distinct behavior of the light language:
 * soft filmic rolloff (manual ACES-style curve), depth haze, restrained bloom.
 */
import {
  AdditiveBlending,
  DoubleSide,
  ShaderMaterial,
  Vector3,
} from 'three'
import { shared } from './shared'

/* ------------------------------------------------------------------ */
/* shared chunks                                                       */
/* ------------------------------------------------------------------ */

export const GLSL_NOISE = /* glsl */ `
float hash13(vec3 p){ p = fract(p * 0.1031); p += dot(p, p.zyx + 31.32); return fract((p.x + p.y) * p.z); }
float vnoise(vec3 p){
  vec3 i = floor(p); vec3 f = fract(p); vec3 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(hash13(i), hash13(i + vec3(1.0,0.0,0.0)), u.x),
        mix(hash13(i + vec3(0.0,1.0,0.0)), hash13(i + vec3(1.0,1.0,0.0)), u.x), u.y),
    mix(mix(hash13(i + vec3(0.0,0.0,1.0)), hash13(i + vec3(1.0,0.0,1.0)), u.x),
        mix(hash13(i + vec3(0.0,1.0,1.0)), hash13(i + vec3(1.0,1.0,1.0)), u.x), u.y),
    u.z);
}
float fbm(vec3 p){ float a = 0.5; float s = 0.0; for (int i = 0; i < 4; i++){ s += a * vnoise(p); p *= 2.13; a *= 0.5; } return s; }
`

/** ACES-inspired filmic rolloff, applied inside shaders so additive HDR stacks never clip. */
export const GLSL_FILMIC = /* glsl */ `
vec3 filmic(vec3 x){
  x *= 0.86;
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}
`

function starVert(extra: string): string {
  return /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
attribute float aTw;
attribute float aPh;
attribute float aRnd;
uniform float uTime;
uniform float uScaleH;
uniform float uMotion;
uniform float uCull;
uniform float uPull;
uniform float uPullR;
varying vec3 vColor;
varying float vA;
void main() {
  vColor = aColor;
  vec3 p = position;
#ifdef CORE_PULL
  float pull = uPull * (0.35 + 0.65 * aRnd);
  p = mix(p, normalize(p) * uPullR, pull);
#endif
  ${extra}
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  float dist = max(0.1, -mv.z);
  float tw = 1.0 - uMotion * 0.42 * (0.5 + 0.5 * sin(uTime * (0.3 + aTw * 1.5) + aPh * 31.0));
  float haze = mix(1.0, 0.6, clamp((dist - 58.0) / 80.0, 0.0, 1.0));
  vA = tw * haze;
  float ps = aSize * uScaleH / dist;
  if (aRnd < uCull) { ps = 0.0; vA = 0.0; }
  gl_PointSize = clamp(ps, 0.0, 52.0);
  gl_Position = projectionMatrix * mv;
}
`
}

const STAR_FRAG = /* glsl */ `
uniform float uIntensity;
varying vec3 vColor;
varying float vA;
void main() {
  vec2 q = gl_PointCoord - 0.5;
  float d2 = dot(q, q) * 4.0;
  float core = exp(-d2 * 6.5);
  float halo = exp(-d2 * 2.1) * 0.15;
  float a = (core + halo) * vA * uIntensity;
  if (a < 0.004) discard;
  gl_FragColor = vec4(filmic(vColor * a), 1.0);
}
`

/* ------------------------------------------------------------------ */
/* material factories                                                  */
/* ------------------------------------------------------------------ */

export interface StarfieldUniforms {
  uTime: { value: number }
  uScaleH: { value: number }
  uMotion: { value: number }
  uCull: { value: number }
  uIntensity: { value: number }
  uPull: { value: number }
  uPullR: { value: number }
}

export function makeStarfieldMaterial(opts: { pull?: boolean } = {}): ShaderMaterial {
  const m = new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uScaleH: { value: shared.scaleH },
      uMotion: { value: 1 },
      uCull: { value: 0 },
      uIntensity: { value: 1 },
      uPull: { value: 0 },
      uPullR: { value: 2.4 },
    },
    vertexShader: starVert(opts.pull ? '' : ''),
    fragmentShader: GLSL_FILMIC + STAR_FRAG,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
    defines: opts.pull ? { CORE_PULL: '' } : {},
  })
  shared.cullUniforms.add(m.uniforms.uCull as { value: number })
  return m
}

/** System star sprites: react to hover / focus with a calm corona lift. */
export function makeSystemStarMaterial(): ShaderMaterial {
  const m = new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uScaleH: { value: shared.scaleH },
      uMotion: { value: 1 },
      uCull: { value: 0 },
      uIntensity: { value: 1 },
      uHoverSys: { value: -1 },
      uFocusSys: { value: -1 },
    },
    vertexShader: /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
attribute float aSysId;
uniform float uTime;
uniform float uScaleH;
uniform float uHoverSys;
uniform float uFocusSys;
varying vec3 vColor;
varying float vA;
void main() {
  vColor = aColor;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  float dist = max(0.1, -mv.z);
  float boost = 1.0;
  if (abs(aSysId - uHoverSys) < 0.5) boost = 1.35 + 0.2 * sin(uTime * 2.6);
  if (abs(aSysId - uFocusSys) < 0.5) boost = max(boost, 1.6);
  vA = boost * mix(1.0, 0.6, clamp((dist - 58.0) / 80.0, 0.0, 1.0));
  gl_PointSize = clamp(aSize * boost * uScaleH / dist, 0.0, 120.0);
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader:
      GLSL_FILMIC +
      /* glsl */ `
varying vec3 vColor;
varying float vA;
void main() {
  vec2 q = gl_PointCoord - 0.5;
  float d2 = dot(q, q) * 4.0;
  float core = exp(-d2 * 12.0);
  float glow = exp(-d2 * 3.2) * 0.18;
  float a = (core + glow) * vA;
  if (a < 0.004) discard;
  gl_FragColor = vec4(filmic(vColor * a), 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
  return m
}

/** Elliptical orbit tracks; illuminate sequentially when a system is focused. */
export function makeOrbitMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      uHoverSys: { value: -1 },
      uFocusSys: { value: -1 },
      uFocusT: { value: -10 },
      uBase: { value: 0.05 },
      uColor: { value: new Vector3(0.32, 0.38, 0.58) },
      uHot: { value: new Vector3(0.16, 0.62, 0.88) },
    },
    vertexShader: /* glsl */ `
attribute float aSysId;
attribute float aOrbIdx;
varying float vSys;
varying float vOrb;
void main() {
  vSys = aSysId;
  vOrb = aOrbIdx;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`,
    fragmentShader: /* glsl */ `
uniform float uHoverSys;
uniform float uFocusSys;
uniform float uFocusT;
uniform float uBase;
uniform vec3 uColor;
uniform vec3 uHot;
varying float vSys;
varying float vOrb;
void main() {
  float b = uBase;
  float seq = 0.0;
  if (abs(vSys - uHoverSys) < 0.5) b = max(b, uBase * 2.0);
  if (abs(vSys - uFocusSys) < 0.5) {
    float stag = vOrb * 0.3;
    float w = clamp((uFocusT - stag) * 1.25, 0.0, 1.0);
    seq = w * (0.62 + 0.38 * sin(uFocusT * 2.1 - vOrb * 1.7));
    b = max(b, uBase * 1.6 + seq * 0.6);
  }
  gl_FragColor = vec4(mix(uColor, uHot, seq) * b, 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
}

/**
 * Instanced planets. Every class has differentiated material response:
 * rocky mottling, oceanic rim-light, icy streaks, metallic seams, gas bands.
 * The galactic core is the light source -> terminator shadows rotate as
 * planets orbit. Hover sharpens the atmospheric rim only.
 */
export function makePlanetMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      uHoverPlanet: { value: -1 },
      uFocusSys: { value: -1 },
    },
    vertexShader: /* glsl */ `
attribute float aType;
attribute float aSeed;
attribute vec3 aTint;
attribute vec3 aRim;
attribute float aSysId;
attribute float aPlanetId;
varying vec3 vN;
varying vec3 vL;
varying vec3 vV;
varying vec3 vTint;
varying vec3 vRim;
varying float vType;
varying vec3 vSeed;
varying vec3 vNrm;
varying float vSys;
varying float vPid;
void main() {
  vec4 wp = instanceMatrix * vec4(position, 1.0);
  vec3 wn = normalize(mat3(instanceMatrix) * normal);
  vec4 mv = modelViewMatrix * wp;
  mat3 vm = mat3(modelViewMatrix);
  vN = normalize(vm * wn);
  vL = normalize(vm * normalize(-wp.xyz));
  vV = normalize(-mv.xyz);
  vNrm = wn;
  vSeed = vec3(aSeed * 13.1, aSeed * 7.7, aSeed * 3.3);
  vTint = aTint;
  vRim = aRim;
  vType = aType;
  vSys = aSysId;
  vPid = aPlanetId;
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader:
      GLSL_NOISE +
      GLSL_FILMIC +
      /* glsl */ `
uniform float uHoverPlanet;
uniform float uFocusSys;
varying vec3 vN;
varying vec3 vL;
varying vec3 vV;
varying vec3 vTint;
varying vec3 vRim;
varying float vType;
varying vec3 vSeed;
varying vec3 vNrm;
varying float vSys;
varying float vPid;
void main() {
  int T = int(vType + 0.5);
  float n1 = vnoise(vNrm * 2.3 + vSeed);
  float n2 = vnoise(vNrm * 5.4 + vSeed * 1.7);
  float n3 = vnoise(vNrm * 11.5 + vSeed * 2.3);
  vec3 base = vTint;
  float seam = 0.0;
  if (T == 0) {
    base = vTint * (0.55 + 0.5 * n1 + 0.28 * n3);
  } else if (T == 1) {
    float land = smoothstep(0.56, 0.63, n1 * 0.68 + n2 * 0.32);
    base = mix(vTint * (0.85 + 0.35 * n2), vec3(0.055, 0.06, 0.075), land);
  } else if (T == 2) {
    float streak = 0.78 + 0.22 * sin(vNrm.y * 13.0 + n2 * 6.0);
    base = vTint * (0.72 + 0.34 * n1) * streak;
  } else if (T == 3) {
    seam = smoothstep(0.47, 0.5, n2) * (1.0 - smoothstep(0.5, 0.56, n2));
    base = vTint * (0.5 + 0.4 * n1) + vec3(0.05, 0.055, 0.07) * seam;
  } else if (T == 4) {
    float band = 0.62 + 0.38 * sin(vNrm.y * 9.5 + n1 * 3.6 + vSeed.x);
    base = vTint * band * (0.82 + 0.3 * n2);
  } else {
    base = vTint * (0.5 + 0.55 * n3);
  }
  float ndl = dot(vN, vL);
  float day = smoothstep(-0.14, 0.3, ndl);
  vec3 col = base * (0.11 + 1.02 * day * (0.72 + 0.45 * max(ndl, 0.0)));
  float fres = pow(1.0 - max(dot(vN, vV), 0.0), 3.0);
  float hover = abs(vPid - uHoverPlanet) < 0.5 ? 1.0 : 0.0;
  float focus = abs(vSys - uFocusSys) < 0.5 ? 1.0 : 0.0;
  col += vRim * fres * (0.55 + 0.9 * hover + 0.22 * focus) * (0.45 + 0.55 * day);
  if (T == 3) {
    vec3 h = normalize(vL + vV);
    col += vec3(0.16, 0.175, 0.21) * pow(max(dot(vN, h), 0.0), 28.0) * (0.5 + 0.7 * seam);
  }
  gl_FragColor = vec4(filmic(col), 1.0);
}
`,
  })
}

/** Thin gas-giant rings with breaks and irregular illumination. */
export function makeGasRingMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: { uSeed: { value: 0 } },
    vertexShader: /* glsl */ `
varying vec2 vP;
void main() {
  vP = position.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`,
    fragmentShader:
      GLSL_NOISE +
      GLSL_FILMIC +
      /* glsl */ `
uniform float uSeed;
varying vec2 vP;
void main() {
  float r = length(vP);
  float t = clamp((r - 0.52) / 0.48, 0.0, 1.0);
  float ang = atan(vP.y, vP.x);
  float bands = 0.5 + 0.5 * sin(t * 26.0 + uSeed * 9.0);
  float breaks = step(0.25, vnoise(vec3(ang * 1.8 + uSeed, t * 5.0, uSeed)));
  float edge = smoothstep(0.0, 0.14, t) * (1.0 - smoothstep(0.78, 1.0, t));
  float a = bands * breaks * edge * 0.34;
  gl_FragColor = vec4(filmic(vec3(0.38, 0.36, 0.46) * a), 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    side: DoubleSide,
    blending: AdditiveBlending,
  })
}

/** Generic limb-fresnel material: sphere boundary, crystalline shells, response pulse. */
export function makeFresnelMaterial(color: [number, number, number], intensity: number, power: number) {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Vector3(...color) },
      uIntensity: { value: intensity },
      uPower: { value: power },
      uFade: { value: 1 },
    },
    vertexShader: /* glsl */ `
varying vec3 vN;
varying vec3 vV;
void main() {
  vN = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader: /* glsl */ `
uniform vec3 uColor;
uniform float uIntensity;
uniform float uPower;
uniform float uFade;
varying vec3 vN;
varying vec3 vV;
void main() {
  float f = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), uPower);
  float a = f * uIntensity * uFade;
  if (a < 0.003) discard;
  gl_FragColor = vec4(uColor * a, 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
}

/** Inner volume glow: brightest facing the camera, fading to the limb. */
export function makeInnerGlowMaterial(color: [number, number, number], intensity: number, power = 3) {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Vector3(...color) },
      uIntensity: { value: intensity },
      uPower: { value: power },
      uFade: { value: 1 },
    },
    vertexShader: /* glsl */ `
varying vec3 vN;
varying vec3 vV;
void main() {
  vN = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader: /* glsl */ `
uniform vec3 uColor;
uniform float uIntensity;
uniform float uPower;
uniform float uFade;
varying vec3 vN;
varying vec3 vV;
void main() {
  float ndv = abs(dot(normalize(vN), normalize(vV)));
  float a = pow(ndv, uPower) * uIntensity * uFade;
  if (a < 0.003) discard;
  gl_FragColor = vec4(uColor * a, 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
}

/** Core cognitive mass: dark indigo engine with cyan plasma veins + rotating currents. */
export function makeCorePlasmaMaterial(base: [number, number, number], vein: [number, number, number]) {
  return new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uPlasma: { value: 0.5 },
      uCurrents: { value: 1 },
      uBase: { value: new Vector3(...base) },
      uVein: { value: new Vector3(...vein) },
    },
    vertexShader: /* glsl */ `
varying vec3 vObj;
varying vec3 vN;
varying vec3 vV;
void main() {
  vObj = position;
  vN = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader:
      GLSL_NOISE +
      GLSL_FILMIC +
      /* glsl */ `
uniform float uTime;
uniform float uPlasma;
uniform float uCurrents;
uniform vec3 uBase;
uniform vec3 uVein;
varying vec3 vObj;
varying vec3 vN;
varying vec3 vV;
void main() {
  float cs = uCurrents;
  float ca = cos(uTime * 0.05 * cs);
  float sa = sin(uTime * 0.05 * cs);
  vec3 p = vec3(vObj.x * ca - vObj.z * sa, vObj.y, vObj.x * sa + vObj.z * ca);
  float n = fbm(p * 0.85 + vec3(0.0, uTime * 0.03, uTime * 0.02));
  float veins = pow(vnoise(p * 2.6 - uTime * 0.07 * cs), 2.6) * (0.45 + 0.65 * n);
  float veins2 = pow(vnoise(p * 5.5 + uTime * 0.045 * cs), 3.5);
  float limb = 1.0 - abs(dot(normalize(vN), normalize(vV)));
  vec3 col = uBase * (0.55 + 0.5 * n) * (1.0 - 0.5 * limb);
  col += uVein * (veins * 1.1 + veins2 * 0.55) * (0.4 + 0.8 * uPlasma) * (0.95 - 0.55 * limb);
  // translucent mass: the heart glows through the center, shell denser at limb
  float a = mix(0.42, 0.66, limb);
  gl_FragColor = vec4(filmic(col), a);
}
`,
  transparent: true,
  depthWrite: false,
  })
}

/**
 * Billboarded graduated halos. uKind: 0 = wide gradient, 1 = star corona,
 * 2 = system boundary marker ring.
 */
export function makeHaloMaterial(color: [number, number, number], kind: 0 | 1 | 2) {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Vector3(...color) },
      uIntensity: { value: 1 },
      uSize: { value: 10 },
      uTime: { value: 0 },
      uBreath: { value: 0.25 },
      uKind: { value: kind },
    },
    vertexShader: /* glsl */ `
uniform float uSize;
varying vec2 vUv;
void main() {
  vUv = position.xy;
  vec4 mv = modelViewMatrix * vec4(0.0, 0.0, 0.0, 1.0);
  mv.xy += position.xy * uSize;
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader: /* glsl */ `
uniform vec3 uColor;
uniform float uIntensity;
uniform float uTime;
uniform float uBreath;
uniform float uKind;
varying vec2 vUv;
void main() {
  float d = length(vUv) * 2.0;
  float a;
  if (uKind < 0.5) {
    a = exp(-d * d * 4.4) * (1.0 - smoothstep(0.72, 1.0, d));
  } else if (uKind < 1.5) {
    a = exp(-d * d * 7.5) * 1.15 + exp(-d * d * 2.6) * 0.22;
  } else {
    a = smoothstep(0.34, 0.44, d) * (1.0 - smoothstep(0.52, 0.66, d));
  }
  a *= uIntensity * (1.0 + uBreath * sin(uTime * 0.4));
  if (a < 0.003) discard;
  gl_FragColor = vec4(uColor * a, 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
}

/** Volumetric-ish compound memory nebulae: layered transparent noise billboards. */
export function makeNebulaMaterial(cyan: [number, number, number], violet: [number, number, number]) {
  return new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uBreath: { value: 1 },
      uCa: { value: new Vector3(...cyan) },
      uCb: { value: new Vector3(...violet) },
    },
    vertexShader: /* glsl */ `
attribute vec3 aCenter;
attribute float aScale;
attribute float aSeed;
attribute float aHue;
attribute float aAlpha;
attribute float aRot;
uniform float uTime;
uniform float uBreath;
varying vec2 vQ;
varying float vSeed;
varying float vHue;
varying float vA;
void main() {
  vec4 mv = modelViewMatrix * vec4(aCenter, 1.0);
  float s = aScale * (1.0 + uBreath * 0.055 * sin(uTime * 0.13 + aSeed * 3.1));
  float c = cos(aRot + uTime * 0.011);
  float si = sin(aRot + uTime * 0.011);
  vec2 q = vec2(position.x * c - position.y * si, position.x * si + position.y * c);
  vQ = q;
  mv.xy += q * s;
  vSeed = aSeed;
  vHue = aHue;
  vA = aAlpha;
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader:
      GLSL_NOISE +
      GLSL_FILMIC +
      /* glsl */ `
uniform float uTime;
uniform vec3 uCa;
uniform vec3 uCb;
varying vec2 vQ;
varying float vSeed;
varying float vHue;
varying float vA;
void main() {
  float d = length(vQ) * 2.0;
  float ang = atan(vQ.y, vQ.x);
  float n = fbm(vec3(vQ * 2.3, vSeed + uTime * 0.02));
  float n2 = vnoise(vec3(ang * 1.7 + vSeed, vSeed * 2.0, uTime * 0.03));
  float body = smoothstep(1.05, 0.1, d + (n - 0.5) * 0.9);
  float hue = clamp(vHue + (n2 - 0.5) * 0.6 + 0.14 * sin(uTime * 0.045 + vSeed * 2.0), 0.0, 1.0);
  vec3 col = mix(uCa, uCb, hue);
  float a = body * vA * (0.78 + 0.22 * sin(uTime * 0.1 + vSeed * 5.0));
  if (a < 0.004) discard;
  gl_FragColor = vec4(filmic(col * a * 1.7), 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
}

/** Dark-core accretion disks: precise, quiet, indigo / rare amber. */
export function makeDiskMaterial(color: [number, number, number]) {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Vector3(...color) },
      uTime: { value: 0 },
      uSeed: { value: Math.random() * 10 },
      uIntensity: { value: 0.85 },
      uInner: { value: 1.35 },
      uOuter: { value: 2.5 },
      uHover: { value: 0 },
    },
    vertexShader: /* glsl */ `
varying vec2 vP;
void main() {
  vP = position.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`,
    fragmentShader:
      GLSL_NOISE +
      GLSL_FILMIC +
      /* glsl */ `
uniform vec3 uColor;
uniform float uTime;
uniform float uSeed;
uniform float uIntensity;
uniform float uInner;
uniform float uOuter;
uniform float uHover;
varying vec2 vP;
void main() {
  float r = length(vP);
  float t = (r - uInner) / (uOuter - uInner);
  if (t < 0.0 || t > 1.0) discard;
  float ang = atan(vP.y, vP.x);
  float rot = uTime * (0.2 + uHover * 0.25) + uSeed;
  vec2 sp = vec2(cos(ang - rot), sin(ang - rot)) * r;
  float streaks = 0.45 + 0.55 * vnoise(vec3(sp * 1.9, uSeed * 7.0));
  float band = pow(max(0.0, 1.0 - abs(t - 0.42) * 1.8), 2.2);
  float gaps = 0.7 + 0.3 * vnoise(vec3(ang * 3.1 + uSeed, r * 0.65, uSeed));
  float a = band * streaks * gaps * uIntensity * (1.0 + uHover * 0.45);
  a *= smoothstep(0.0, 0.07 + uHover * 0.06, t);
  gl_FragColor = vec4(filmic(uColor * a), 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    side: DoubleSide,
    blending: AdditiveBlending,
  })
}

/** Knowledge gates: quiet transfer structures, rotate delicately, tiny particle flow. */
export function makeGateMaterial(color: [number, number, number]) {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Vector3(...color) },
      uTime: { value: 0 },
      uSeed: { value: Math.random() * 10 },
      uHover: { value: 0 },
      uIntensity: { value: 1 },
    },
    vertexShader: /* glsl */ `
varying vec3 vP;
void main() {
  vP = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`,
    fragmentShader: /* glsl */ `
uniform vec3 uColor;
uniform float uTime;
uniform float uSeed;
uniform float uHover;
uniform float uIntensity;
varying vec3 vP;
void main() {
  float ang = atan(vP.y, vP.x);
  float shimmer = 0.75 + 0.25 * sin(uTime * 0.8 + uSeed + ang * 2.0);
  float flowPos = fract(uTime * 0.045 + uSeed);
  float dAng = abs(atan(sin(ang - flowPos * 6.2831), cos(ang - flowPos * 6.2831)));
  float flow = exp(-dAng * dAng * 110.0);
  float a = (0.1 + flow * 0.45) * uIntensity * (1.0 + uHover * 0.85) * shimmer;
  gl_FragColor = vec4(uColor * a, 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
}

/** L5 dark-matter lattice: barely visible until a long-term relationship lights up. */
export function makeLatticeMaterial(color: [number, number, number]) {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Vector3(...color) },
      uBase: { value: 0.075 },
      uPulsePath: { value: -1 },
      uPulseT: { value: -10 },
    },
    vertexShader: /* glsl */ `
attribute float aPath;
attribute float aRnd;
varying float vPath;
varying float vRnd;
void main() {
  vPath = aPath;
  vRnd = aRnd;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`,
    fragmentShader: /* glsl */ `
uniform vec3 uColor;
uniform float uBase;
uniform float uPulsePath;
uniform float uPulseT;
varying float vPath;
varying float vRnd;
void main() {
  float b = uBase * (0.7 + 0.6 * vRnd);
  if (abs(vPath - uPulsePath) < 0.5) {
    float w = exp(-pow((uPulseT - 0.5) * 3.0, 2.0));
    b += w * 0.8 * (0.4 + 0.6 * vRnd);
  }
  if (b < 0.004) discard;
  gl_FragColor = vec4(uColor * b, 1.0);
}
`,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
}

/** Dark cognitive core: inverse reaction on focus - interior darkens, ring sharpens. */
export function makeDarkCoreMaterial(color: [number, number, number]) {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Vector3(...color) },
      uHover: { value: 0 },
    },
    vertexShader: /* glsl */ `
varying vec3 vN;
varying vec3 vV;
void main() {
  vN = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`,
    fragmentShader: /* glsl */ `
uniform vec3 uColor;
uniform float uHover;
varying vec3 vN;
varying vec3 vV;
void main() {
  float ndv = abs(dot(normalize(vN), normalize(vV)));
  float f = pow(1.0 - ndv, 2.1 + uHover * 1.1);
  vec3 col = uColor * f * (0.9 - uHover * 0.4) + vec3(0.004, 0.005, 0.011);
  gl_FragColor = vec4(col, 1.0);
}
`,
    transparent: true,
    depthWrite: true,
    blending: AdditiveBlending,
  })
}
