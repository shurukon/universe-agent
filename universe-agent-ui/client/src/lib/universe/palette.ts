import { Color } from "three";

/**
 * Universe Agent light language (Linear × Perplexity). Color communicates
 * function:
 *  - Perplexity Teal -> active signal, listening, retrieval, thinking
 *  - Indigo/Violet   -> memory, compound context, long-term lattice
 *  - Cool white      -> verified knowledge, response
 *  - Amber (rare)    -> temporal / special gravitational states only
 */
export const PALETTE = {
  void: "#010104",
  space: "#020209",
  photonCyan: "#20808d",
  cyanSoft: "#5fb3c1",
  indigo: "#4a4fa8",
  indigoDeep: "#14163a",
  indigoMid: "#23255c",
  violet: "#8a7bd8",
  violetDim: "#5b5397",
  coolWhite: "#eaf4ff",
  starWhite: "#dce9fb",
  amber: "#b98748",
  rocky: "#6b6259",
  oceanDeep: "#16323e",
  icy: "#aebfd0",
  metal: "#4a4f5a",
  gas: "#5a5470",
  moon: "#55524e",
} as const;

/** Convert a palette hex into a linear-space RGB triple, optionally scaled. */
export function c3(hex: string, mul = 1): [number, number, number] {
  const c = new Color(hex);
  return [c.r * mul, c.g * mul, c.b * mul];
}

export function mix3(
  a: [number, number, number],
  b: [number, number, number],
  t: number
): [number, number, number] {
  return [
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ];
}
