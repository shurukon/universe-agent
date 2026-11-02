/**
 * lib/flow/agentTint.ts — the FINAL per-agent universe tints (M5).
 *
 * The trailing universe keeps one fixed shape/rotation; only its base color
 * shifts with the working agent and returns to the resting brand color
 * (BASE_TINT) between agents. The table below is the decided, stable
 * palette: twelve distinct hues, spaced around the wheel, all readable
 * against the near-black chat surface at ~34px.
 */

/** The resting tint: no rotation — the brand teal/cyan as it is built. */
export const BASE_TINT = 0;

/** Decided hue-rotation per agent (degrees). universe = 0: the trailing
 *  universe IS Universe Agent's identity — it keeps the resting brand color
 *  while subagent activity shows as colored branch rows in the flow. Legacy
 *  rows keep their colors so old histories still render. */
const TINTS: Record<string, number> = {
  universe: 0,
  general: 0, // pre-v22 rows
  planning: 215, // steel blue (legacy rows)
  research: 160, // spring teal
  code: 262, // violet
  review: 40, // amber
  security: 350, // crimson
  data: 125, // green (legacy rows)
  design: 310, // magenta
  browser: 85, // lime (legacy rows)
  devops: 25, // orange (legacy rows)
  file: 235, // indigo (legacy rows)
  api: 195, // sky (legacy rows)
  write: 285, // orchid (legacy rows)
};

/** Fallback for future/unknown agents: a quiet sky tone. */
const UNKNOWN_TINT = 200;

/** hue-rotate degrees for the active agent, or 0 (resting brand color). */
export function agentTintDeg(agent: string | null | undefined): number {
  if (!agent) return BASE_TINT;
  return TINTS[agent] ?? UNKNOWN_TINT;
}

/** CSS filter string for the universe canvas container. */
export function tintFilter(deg: number): string {
  return deg === 0 ? "none" : `hue-rotate(${deg}deg)`;
}
