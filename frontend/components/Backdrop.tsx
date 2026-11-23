"use client";

/**
 * Layer 0: the living ambient backdrop. Slow, organically morphing light orbs behind a
 * frosted grain, drifting gently against the cursor (Layer 1 parallax).
 */
import { motion, useTransform } from "framer-motion";

import { usePrefs } from "@/lib/prefs";

type Orb = {
  color: string;
  size: string;
  top: string;
  left: string;
  depth: number; // parallax strength in px
  duration: number;
  path: { x: string[]; y: string[] };
};

const ORBS: Orb[] = [
  { color: "rgba(56,189,248,0.55)", size: "52vmax", top: "-18%", left: "-12%", depth: 28, duration: 34,
    path: { x: ["0%", "8%", "-4%", "0%"], y: ["0%", "6%", "10%", "0%"] } },
  { color: "rgba(129,140,248,0.45)", size: "46vmax", top: "35%", left: "55%", depth: -36, duration: 41,
    path: { x: ["0%", "-10%", "4%", "0%"], y: ["0%", "-8%", "6%", "0%"] } },
  { color: "rgba(45,212,191,0.32)", size: "38vmax", top: "60%", left: "-8%", depth: 22, duration: 29,
    path: { x: ["0%", "12%", "6%", "0%"], y: ["0%", "-6%", "-12%", "0%"] } },
  { color: "rgba(125,211,252,0.28)", size: "30vmax", top: "-10%", left: "62%", depth: -18, duration: 37,
    path: { x: ["0%", "-6%", "-12%", "0%"], y: ["0%", "10%", "4%", "0%"] } },
];

const BLOBS = [
  "42% 58% 70% 30% / 45% 45% 55% 55%",
  "70% 30% 46% 54% / 30% 29% 71% 70%",
  "50% 50% 34% 66% / 56% 68% 32% 44%",
  "42% 58% 70% 30% / 45% 45% 55% 55%",
];

function OrbLayer({ orb, calm }: { orb: Orb; calm: boolean }) {
  const { parallax } = usePrefs();
  const px = useTransform(parallax.x, (v) => v * orb.depth);
  const py = useTransform(parallax.y, (v) => v * orb.depth);
  return (
    <motion.div className="absolute" style={{ top: orb.top, left: orb.left, x: px, y: py }}>
      <motion.div
        style={{
          width: orb.size,
          height: orb.size,
          background: `radial-gradient(circle at 40% 40%, ${orb.color}, transparent 68%)`,
          filter: "blur(40px)",
        }}
        animate={calm ? undefined : { x: orb.path.x, y: orb.path.y, borderRadius: BLOBS, scale: [1, 1.08, 0.96, 1] }}
        transition={{ duration: orb.duration, repeat: Infinity, ease: "easeInOut" }}
      />
    </motion.div>
  );
}

export function Backdrop() {
  const { prefs } = usePrefs();
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      {/* deep base gradient: abyss → midnight, never pure black */}
      <div className="absolute inset-0 bg-[radial-gradient(120%_90%_at_20%_0%,#0e2442_0%,#0a192f_45%,#050c1a_100%)]" />
      {ORBS.map((orb, i) => (
        <OrbLayer key={i} orb={orb} calm={prefs.calm} />
      ))}
      {/* a soft vignette keeps the edges calm and focuses the centre */}
      <div className="absolute inset-0 bg-[radial-gradient(80%_70%_at_50%_45%,transparent_40%,rgba(5,12,26,0.65)_100%)]" />
      <div className="grain" />
    </div>
  );
}
