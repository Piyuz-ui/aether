"use client";

/**
 * AETHER's living symbiote: a procedural, amoeba-like organism whose body language follows
 * what the agent is doing.
 *
 *   idle               slow breathing; one lazy pseudopod drifts around
 *   thinking           faster wobble, three tendrils probing, the nucleus swirls
 *   using_tool         one long pseudopod stretches out to grab the tool, with a pulse along it
 *   awaiting_approval  tendrils pulled in, the body contracts and turns amber, an attention ripple
 *   speaking           the membrane ripples with the real loudness of the voice
 *
 * The outline is recomputed every frame from layered sine "noise" plus Gaussian bumps
 * (pseudopods), then drawn as a smooth closed Catmull-Rom spline. Every parameter eases
 * toward the current state's targets, so the creature morphs between behaviours instead
 * of snapping. Calm mode freezes it into a still, gently rounded shape.
 */
import type { MotionValue } from "framer-motion";
import { useEffect, useId, useRef } from "react";

import { usePrefs } from "@/lib/prefs";
import type { AgentState } from "@/lib/types";

type RGB = [number, number, number];
type Params = {
  wobble: number;   // amplitude of the membrane's undulation (fraction of the radius)
  speed: number;    // how fast the membrane moves
  pods: number;     // how many probing tendrils (0–3), fractional while morphing
  podLen: number;   // tendril length (fraction of the radius)
  podSpin: number;  // how fast tendrils circle the body
  reach: number;    // one long, aimed pseudopod (using a tool)
  scale: number;    // body size
  swirl: number;    // nucleus and organelle speed
  ripple: number;   // attention ripple ring (awaiting approval)
  colors: [RGB, RGB, RGB];
};

const hex = (h: string): RGB => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];

const TARGETS: Record<AgentState, Params> = {
  idle: { wobble: 0.05, speed: 0.55, pods: 1, podLen: 0.3, podSpin: 0.18, reach: 0, scale: 1, swirl: 0.35, ripple: 0,
    colors: [hex("#38bdf8"), hex("#818cf8"), hex("#5eead4")] },
  thinking: { wobble: 0.085, speed: 1.7, pods: 3, podLen: 0.5, podSpin: 0.95, reach: 0, scale: 1.03, swirl: 1.6, ripple: 0,
    colors: [hex("#60a5fa"), hex("#818cf8"), hex("#c4b5fd")] },
  using_tool: { wobble: 0.06, speed: 1.2, pods: 1, podLen: 0.16, podSpin: 0.4, reach: 0.95, scale: 1.05, swirl: 1.1, ripple: 0,
    colors: [hex("#7dd3fc"), hex("#38bdf8"), hex("#5eead4")] },
  awaiting_approval: { wobble: 0.03, speed: 0.75, pods: 0, podLen: 0, podSpin: 0.1, reach: 0, scale: 0.9, swirl: 0.5, ripple: 1,
    colors: [hex("#fcd34d"), hex("#5eead4"), hex("#38bdf8")] },
};

const N = 72;      // outline points
const R = 30;      // body radius in viewBox units (viewBox is −60…60)
const POD_WIDTH = 0.19; // narrower = more tendril-like

function lerp(a: number, b: number, k: number) { return a + (b - a) * k; }
function rgb(c: RGB) { return `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`; }

/** Smooth closed path through the points (Catmull-Rom → cubic Béziers). */
function closedPath(xs: number[], ys: number[]): string {
  const n = xs.length;
  let d = `M${xs[0].toFixed(2)},${ys[0].toFixed(2)}`;
  for (let i = 0; i < n; i++) {
    const i0 = (i - 1 + n) % n, i2 = (i + 1) % n, i3 = (i + 2) % n;
    const c1x = xs[i] + (xs[i2] - xs[i0]) / 6, c1y = ys[i] + (ys[i2] - ys[i0]) / 6;
    const c2x = xs[i2] - (xs[i3] - xs[i]) / 6, c2y = ys[i2] - (ys[i3] - ys[i]) / 6;
    d += `C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${xs[i2].toFixed(2)},${ys[i2].toFixed(2)}`;
  }
  return `${d}Z`;
}

/** Shortest signed angle difference. */
function angDiff(a: number, b: number) {
  let d = a - b;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d < -Math.PI) d += 2 * Math.PI;
  return d;
}

export function AgentOrb({ state, size = 44, level }: {
  state: AgentState;
  size?: number;
  /** 0–1 loudness of AETHER's voice while it speaks */
  level?: MotionValue<number>;
}) {
  const { prefs } = usePrefs();
  const uid = useId().replace(/:/g, "");
  const svgRef = useRef<SVGSVGElement>(null);
  const body = useRef<SVGPathElement>(null);
  const glow = useRef<SVGPathElement>(null);
  const rim = useRef<SVGPathElement>(null);
  const nucleus = useRef<SVGPathElement>(null);
  const ring = useRef<SVGPathElement>(null);
  const stops = useRef<(SVGStopElement | null)[]>([]);
  const organelles = useRef<(SVGCircleElement | null)[]>([]);
  const stateRef = useRef(state);
  stateRef.current = state;
  const levelRef = useRef(level);
  levelRef.current = level;
  const params = useRef<Params>({ ...TARGETS[state], colors: TARGETS[state].colors.map((c) => [...c]) as Params["colors"] });

  useEffect(() => {
    const xs = new Array<number>(N), ys = new Array<number>(N);
    const nx = new Array<number>(28), ny = new Array<number>(28);
    let t = 0;
    let last = performance.now();
    let raf = 0;
    const phases = [0.3, 1.7, 2.9, 4.4, 5.1];

    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const p = params.current;
      const target = TARGETS[stateRef.current];
      const voice = Math.min(1, levelRef.current?.get() ?? 0);
      const k = 1 - Math.exp(-dt * 2.6); // easing toward the state's behaviour
      p.wobble = lerp(p.wobble, target.wobble + voice * 0.11, k);
      p.speed = lerp(p.speed, target.speed + voice * 2.4, k);
      p.pods = lerp(p.pods, target.pods, k);
      p.podLen = lerp(p.podLen, target.podLen, k);
      p.podSpin = lerp(p.podSpin, target.podSpin, k);
      p.reach = lerp(p.reach, target.reach, k);
      p.scale = lerp(p.scale, target.scale + voice * 0.12, k);
      p.swirl = lerp(p.swirl, target.swirl, k);
      p.ripple = lerp(p.ripple, target.ripple, k);
      for (let c = 0; c < 3; c++) for (let j = 0; j < 3; j++) p.colors[c][j] = lerp(p.colors[c][j], target.colors[c][j], k);
      t += dt * p.speed;

      // Pseudopods: angle and extension of each tendril this frame.
      const pods: { a: number; e: number; w: number }[] = [];
      for (let i = 0; i < 3; i++) {
        const presence = Math.max(0, Math.min(1, p.pods - i)); // fades tendrils in and out
        if (presence <= 0.001) continue;
        const a = i * 2.094 + t * p.podSpin * 0.6 + 0.5 * Math.sin(t * 0.7 + i * 1.3);
        const e = p.podLen * presence * (0.55 + 0.45 * Math.sin(t * 1.3 + i * 2.1));
        pods.push({ a, e, w: POD_WIDTH });
      }
      if (p.reach > 0.01) { // the tool-grabbing arm, aimed up-right, with a pulse travelling out
        pods.push({ a: -0.55 + 0.12 * Math.sin(t * 0.9), e: p.reach * (0.88 + 0.12 * Math.sin(t * 4.2)), w: 0.16 });
      }

      for (let i = 0; i < N; i++) {
        const th = (i / N) * Math.PI * 2;
        let r = 1;
        for (let h = 2; h <= 6; h++) r += (p.wobble / Math.pow(h - 1, 0.7)) * Math.sin(h * th + t * (0.6 + h * 0.23) + phases[h - 2]);
        for (const pod of pods) {
          const d = angDiff(th, pod.a);
          r += pod.e * Math.exp(-(d * d) / (2 * pod.w * pod.w));
        }
        const rr = R * p.scale * r;
        xs[i] = Math.cos(th) * rr;
        ys[i] = Math.sin(th) * rr;
      }
      const d = closedPath(xs, ys);
      body.current?.setAttribute("d", d);
      glow.current?.setAttribute("d", d);
      rim.current?.setAttribute("d", d);

      // Nucleus: a smaller blob drifting and swirling inside the body.
      const ncx = 6 * Math.sin(t * 0.37 * p.swirl + 1), ncy = 5 * Math.cos(t * 0.29 * p.swirl);
      for (let i = 0; i < 28; i++) {
        const th = (i / 28) * Math.PI * 2;
        const r = R * 0.36 * p.scale * (1 + 0.12 * Math.sin(3 * th + t * 2.1 * p.swirl) + 0.07 * Math.sin(5 * th - t * 1.3));
        nx[i] = ncx + Math.cos(th) * r;
        ny[i] = ncy + Math.sin(th) * r;
      }
      nucleus.current?.setAttribute("d", closedPath(nx, ny));

      // Organelles orbit inside; faster when it's thinking.
      organelles.current.forEach((o, i) => {
        if (!o) return;
        const orbit = R * (0.48 + i * 0.1) * p.scale;
        const ang = t * p.swirl * (0.8 + i * 0.35) + i * 2.2;
        o.setAttribute("cx", (Math.cos(ang) * orbit * 0.8).toFixed(2));
        o.setAttribute("cy", (Math.sin(ang * 1.1) * orbit * 0.7).toFixed(2));
      });

      // Attention ripple while waiting for approval.
      if (ring.current) {
        const phase = (t * 0.5) % 1;
        const s = 1.05 + phase * 0.45;
        ring.current.setAttribute("d", closedPath(xs.map((x) => x * s), ys.map((y) => y * s)));
        ring.current.setAttribute("opacity", (p.ripple * (1 - phase) * 0.8).toFixed(3));
      }

      const [c1, c2, c3] = p.colors;
      stops.current[0]?.setAttribute("stop-color", rgb(c1));
      stops.current[1]?.setAttribute("stop-color", rgb(c2));
      stops.current[2]?.setAttribute("stop-color", rgb(c3));
      if (svgRef.current) svgRef.current.style.setProperty("--sym-glow", rgb(c1));
    };

    if (prefs.calm) {
      // Calm mode: one still, softly rounded frame.
      params.current = { ...TARGETS[state], wobble: 0.025, pods: 0, reach: 0, ripple: 0,
        colors: TARGETS[state].colors.map((c) => [...c]) as Params["colors"] };
      last = performance.now();
      draw(last + 16);
      return;
    }
    const loop = (now: number) => {
      draw(now);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  // Redraw a still frame in calm mode whenever the state changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs.calm, prefs.calm ? state : null]);

  const view = 124; // room for tendrils beyond the body
  const g = `sym-${uid}`;
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}
      role="img" aria-label={`AETHER is ${state.replace("_", " ")}`}>
      <svg ref={svgRef} viewBox={`${-view / 2} ${-view / 2} ${view} ${view}`} aria-hidden
        className="pointer-events-none absolute overflow-visible"
        style={{ width: size * 1.75, height: size * 1.75 }}>
        <defs>
          <radialGradient id={`${g}-body`} cx="38%" cy="32%" r="78%">
            <stop ref={(el) => { stops.current[0] = el; }} offset="0%" stopColor="#38bdf8" stopOpacity="0.95" />
            <stop ref={(el) => { stops.current[1] = el; }} offset="58%" stopColor="#818cf8" stopOpacity="0.85" />
            <stop ref={(el) => { stops.current[2] = el; }} offset="100%" stopColor="#5eead4" stopOpacity="0.9" />
          </radialGradient>
          <radialGradient id={`${g}-core`} cx="40%" cy="35%" r="70%">
            <stop offset="0%" stopColor="#f0f9ff" stopOpacity="0.85" />
            <stop offset="45%" stopColor="#a5b4fc" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#0e2442" stopOpacity="0.55" />
          </radialGradient>
          <radialGradient id={`${g}-sheen`} cx="35%" cy="28%" r="45%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
          </radialGradient>
          <filter id={`${g}-blur`} x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="7" />
          </filter>
        </defs>
        {/* bioluminescent glow behind the body */}
        <path ref={glow} fill="var(--sym-glow, #38bdf8)" opacity="0.55" filter={`url(#${g}-blur)`} />
        {/* attention ripple (awaiting approval) */}
        <path ref={ring} fill="none" stroke="#fcd34d" strokeWidth="1.4" opacity="0" />
        {/* membrane */}
        <path ref={body} fill={`url(#${g}-body)`} />
        {/* nucleus and organelles */}
        <path ref={nucleus} fill={`url(#${g}-core)`} opacity="0.9" />
        {[2.4, 1.7, 1.3].map((r, i) => (
          <circle key={i} ref={(el) => { organelles.current[i] = el; }} r={r} fill="#f0f9ff" opacity={0.75 - i * 0.15} />
        ))}
        {/* glossy rim and specular sheen */}
        <path ref={rim} fill={`url(#${g}-sheen)`} stroke="rgba(240,249,255,0.45)" strokeWidth="0.9" />
      </svg>
    </div>
  );
}

export const STATE_LABEL: Record<AgentState, string> = {
  idle: "Ready",
  thinking: "Thinking",
  using_tool: "Using a tool",
  awaiting_approval: "Waiting for your approval",
};
