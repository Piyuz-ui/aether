"use client";

/**
 * Lazy, browser-only entry points for the 3D layers. WebGL never renders on the server
 * (no hydration mismatch), and anything without WebGL, or with Calm mode on, falls back to
 * the 2D versions.
 */
import type { MotionValue } from "framer-motion";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { usePrefs } from "@/lib/prefs";
import type { AgentState } from "@/lib/types";

import { AgentOrb } from "../AgentOrb";

const Orb3DCanvas = dynamic(() => import("./Orb3D"), { ssr: false });
const Particles3DCanvas = dynamic(() => import("./Particles3D"), { ssr: false });

let webglOk: boolean | null = null;
/** WebGL available? (Checked once; null until the browser has answered.) */
export function useWebGL(): boolean | null {
  const [ok, setOk] = useState<boolean | null>(webglOk);
  useEffect(() => {
    if (webglOk === null) {
      try {
        const c = document.createElement("canvas");
        webglOk = !!(c.getContext("webgl2") || c.getContext("webgl"));
      } catch {
        webglOk = false;
      }
    }
    setOk(webglOk);
  }, []);
  return ok;
}

/** The home-screen orb: 3D where possible, the 2D creature otherwise. `size` is the orb's own diameter. */
export function HeroOrb({ state, level, size = 132 }: { state: AgentState; level?: MotionValue<number>; size?: number }) {
  const { prefs, parallax } = usePrefs();
  const webgl = useWebGL();
  if (prefs.calm || webgl === false) return <AgentOrb state={state} size={size} level={level} />;
  // The canvas is larger than the orb so its glow and ripples aren't clipped; the negative
  // margin keeps the layout exactly where the 2D orb sat.
  const box = Math.round(size * 1.6), pad = (box - size) / 2;
  return (
    <div className="relative" style={{ width: box, height: box, margin: -pad }}>
      <div className="pointer-events-none absolute inset-[22%] rounded-full bg-sky/30 blur-2xl" aria-hidden />
      {webgl && <Orb3DCanvas state={state} level={level} parallax={parallax} />}
    </div>
  );
}

/** The 3D particle field behind everything (nothing at all in Calm mode). */
export function ParticleField() {
  const { prefs, parallax } = usePrefs();
  const webgl = useWebGL();
  if (prefs.calm || !webgl) return null;
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      <Particles3DCanvas parallax={parallax} />
    </div>
  );
}
