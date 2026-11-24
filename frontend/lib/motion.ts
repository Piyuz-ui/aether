/**
 * Shared spring physics.
 *
 * `jelly` is the signature underdamped spring: it overshoots a little and settles,
 * so things feel soft and alive. Tune `damping`: 12–14 is bouncier jelly,
 * 18–22 is a smoother underwater float.
 */
import type { Transition, Variants } from "framer-motion";

export const jelly = { type: "spring", stiffness: 320, damping: 17, mass: 0.85 } as const satisfies Transition;

/** Calmer spring for large surfaces (drawers, modals) so they don't wobble too much. */
export const settle = { type: "spring", stiffness: 240, damping: 26, mass: 0.9 } as const satisfies Transition;

/** Soft, damped spring for 3D tilt and hover lift: responsive, never wobbly. */
export const tilt = { stiffness: 200, damping: 22, mass: 0.6 } as const;

/** Messages and results arrive from a little depth: tipped back, blurred, then settle flat. */
export const depthIn: Variants = {
  hidden: { opacity: 0, y: 18, scale: 0.96, rotateX: 9, filter: "blur(6px)", transformPerspective: 1100 },
  show: { opacity: 1, y: 0, scale: 1, rotateX: 0, filter: "blur(0px)", transformPerspective: 1100,
    transition: { type: "spring", stiffness: 210, damping: 24, mass: 0.9 } },
  exit: { opacity: 0, y: -6, scale: 0.97, transition: { duration: 0.18 } },
};

/** Slow spring for parallax: follows the cursor like something in water. */
export const drift = { stiffness: 45, damping: 18, mass: 1.2 } as const;

/** Squish on press, wobble back on release. */
export const squish = {
  whileHover: { scale: 1.04 },
  whileTap: { scale: 0.94 },
  transition: jelly,
} as const;

/** Elastic "liquid settle" for messages, tool cards and list items. */
export const popIn: Variants = {
  hidden: { opacity: 0, y: 14, scale: 0.96, filter: "blur(4px)" },
  show: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)", transition: jelly },
  exit: { opacity: 0, y: -6, scale: 0.97, transition: { duration: 0.18 } },
};
