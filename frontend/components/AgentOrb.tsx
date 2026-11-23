"use client";

/**
 * The living agent orb: a fluid blob whose shape, tempo and aura follow the agent's state.
 *   idle               slow, hypnotic breathing drift
 *   thinking           gentle blue ripples, faster elastic morphing
 *   using_tool         luminous cyan pulse ring expanding outward
 *   awaiting_approval  soft bioluminescent wave that draws calm attention
 */
import { AnimatePresence, motion, useMotionValue, useTransform, type MotionValue } from "framer-motion";

import { jelly } from "@/lib/motion";
import { usePrefs } from "@/lib/prefs";
import type { AgentState } from "@/lib/types";

const SHAPES = [
  "42% 58% 70% 30% / 45% 45% 55% 55%",
  "70% 30% 46% 54% / 30% 29% 71% 70%",
  "50% 50% 34% 66% / 56% 68% 32% 44%",
  "46% 54% 50% 50% / 35% 61% 39% 65%",
  "42% 58% 70% 30% / 45% 45% 55% 55%",
];

const MOOD: Record<AgentState, { morph: number; breathe: number[]; scale: number; colors: string; glow: string }> = {
  idle: {
    morph: 9, breathe: [1, 1.045, 1], scale: 1,
    colors: "from-sky via-periwinkle to-seafoam", glow: "rgba(56,189,248,0.45)",
  },
  thinking: {
    morph: 2.6, breathe: [1, 1.08, 0.97, 1], scale: 1.04,
    colors: "from-sky via-[#818cf8] to-periwinkle", glow: "rgba(129,140,248,0.6)",
  },
  using_tool: {
    morph: 1.8, breathe: [1, 1.1, 1], scale: 1.08,
    colors: "from-glacier via-sky to-seafoam", glow: "rgba(125,211,252,0.75)",
  },
  awaiting_approval: {
    morph: 4.5, breathe: [1, 1.06, 1], scale: 1.02,
    colors: "from-amber via-seafoam to-glacier", glow: "rgba(252,211,77,0.55)",
  },
};

function Rings({ state, size }: { state: AgentState; size: number }) {
  if (state === "idle") return null;
  const color = state === "thinking" ? "rgba(129,140,248,0.55)"
    : state === "using_tool" ? "rgba(125,211,252,0.85)" : "rgba(94,234,212,0.7)";
  const count = state === "thinking" ? 3 : 2;
  const duration = state === "using_tool" ? 1.4 : state === "thinking" ? 2.4 : 3;
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <motion.span
          key={`${state}-${i}`}
          className="absolute inset-0 rounded-full"
          style={{ border: `${size > 80 ? 2 : 1.5}px solid ${color}` }}
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: [0.95, state === "using_tool" ? 1.9 : 1.6], opacity: [0.75, 0] }}
          transition={{ duration, repeat: Infinity, delay: (i * duration) / count, ease: "easeOut" }}
        />
      ))}
    </>
  );
}

export function AgentOrb({ state, size = 44, level }: {
  state: AgentState;
  size?: number;
  /** 0–1 loudness of AETHER's voice while it speaks (drives the swell and seafoam halo) */
  level?: MotionValue<number>;
}) {
  const { prefs } = usePrefs();
  const mood = MOOD[state];
  const calm = prefs.calm;
  const silent = useMotionValue(0);
  const voice = level ?? silent;
  const swell = useTransform(voice, (v) => 1 + v * 0.2);
  const haloOpacity = useTransform(voice, [0, 0.05, 1], [0, 0.35, 1]);
  const haloScale = useTransform(voice, (v) => 1.05 + v * 0.55);
  return (
    <motion.div className="relative grid place-items-center" style={{ width: size, height: size, scale: calm ? 1 : swell }}
      role="img" aria-label={`AETHER is ${state.replace("_", " ")}`}>
      {/* voice halo: breathes with the actual cadence of the speech */}
      <motion.div aria-hidden className="pointer-events-none absolute rounded-full blur-xl"
        style={{ inset: -size * 0.3, opacity: haloOpacity, scale: haloScale,
          background: "radial-gradient(circle, rgba(94,234,212,0.75), rgba(125,211,252,0.25) 55%, transparent 72%)" }} />
      {/* soft aura */}
      <motion.div
        className="absolute rounded-full blur-2xl"
        style={{ inset: -size * 0.35, background: `radial-gradient(circle, ${mood.glow}, transparent 70%)` }}
        animate={calm ? { opacity: 0.6 } : { opacity: [0.45, 0.85, 0.45] }}
        transition={{ duration: mood.morph, repeat: Infinity, ease: "easeInOut" }}
      />
      {!calm && (
        <div className="absolute inset-0">
          <AnimatePresence>
            <Rings state={state} size={size} />
          </AnimatePresence>
        </div>
      )}
      {/* the fluid body */}
      <motion.div
        className={`relative size-full bg-gradient-to-br ${mood.colors}`}
        style={{ boxShadow: `inset -6px -8px 18px rgba(5,12,26,0.35), inset 6px 6px 16px rgba(240,249,255,0.45)` }}
        animate={calm
          ? { borderRadius: "50%", scale: mood.scale }
          : { borderRadius: SHAPES, scale: mood.breathe.map((b) => b * mood.scale), rotate: [0, 18, -10, 0] }}
        transition={calm ? jelly : {
          borderRadius: { duration: mood.morph, repeat: Infinity, ease: "easeInOut" },
          scale: { duration: mood.morph * 0.8, repeat: Infinity, ease: "easeInOut" },
          rotate: { duration: mood.morph * 2, repeat: Infinity, ease: "easeInOut" },
        }}
      >
        {/* specular highlight floating on the surface */}
        <motion.span
          className="absolute rounded-full bg-white/70 blur-[3px]"
          style={{ width: size * 0.22, height: size * 0.14, top: size * 0.18, left: size * 0.22 }}
          animate={calm ? undefined : { x: [0, size * 0.04, 0], y: [0, size * 0.03, 0] }}
          transition={{ duration: mood.morph, repeat: Infinity, ease: "easeInOut" }}
        />
      </motion.div>
    </motion.div>
  );
}

export const STATE_LABEL: Record<AgentState, string> = {
  idle: "Ready",
  thinking: "Thinking",
  using_tool: "Using a tool",
  awaiting_approval: "Waiting for your approval",
};
