"use client";

/**
 * The send animation: your message lifts off the text area and glides into the chat.
 *
 * On send, a "ghost" of the text appears exactly where it was typed (same font, size and line
 * positions, so it looks like the words themselves came loose), then springs to the spot where
 * the real message bubble has just been laid out, morphing from bare text into the sky-blue
 * bubble on the way. The real bubble stays invisible until the ghost lands on top of it, then
 * they swap in the same frame, so the hand-off can't be seen.
 */
import { motion } from "framer-motion";
import { createContext, useContext, useLayoutEffect, useState } from "react";

export type Rect = { left: number; top: number; width: number; height: number };
export type Flight = { id: string; text: string; from: Rect };

/** The id of the message bubble that is still "in flight" (rendered invisible meanwhile). */
export const LandingContext = createContext<string | null>(null);
export const useLandingId = () => useContext(LandingContext);

// Bubble padding (px-4.5 / py-3) and text-area text inset (px-1 / py-2.5), in px.
const BUBBLE_PAD_X = 18, BUBBLE_PAD_Y = 12, AREA_PAD_X = 4, AREA_PAD_Y = 10;

/** Where the text was sitting inside the text area (its first character's top-left). */
export function ghostStart(textarea: DOMRect): Rect {
  return { left: textarea.left + AREA_PAD_X, top: textarea.top + AREA_PAD_Y, width: textarea.width, height: textarea.height };
}

// Transform-only flight (FLIP): the ghost is laid out at the bubble's final size from the first
// frame, so text wraps once and never re-flows; only GPU-composited translate/opacity animate.
const flightSpring = { type: "spring", stiffness: 170, damping: 24, mass: 0.9 } as const;

export function SendGhost({ flight, onLanded, prepareLanding }: {
  flight: Flight;
  onLanded: () => void;
  /** Scroll the chat so the landing spot is final before it's measured. */
  prepareLanding: () => void;
}) {
  const [to, setTo] = useState<Rect | null>(null);

  // Measure before the first paint (layout effect), so the very first frame is already right.
  useLayoutEffect(() => {
    prepareLanding();
    let tries = 0;
    let raf = 0;
    const measure = () => {
      const el = document.querySelector<HTMLElement>(`[data-bubble="${flight.id}"]`);
      if (el) {
        const r = el.getBoundingClientRect();
        setTo({ left: r.left, top: r.top, width: r.width, height: r.height });
      } else if (tries++ < 30) {
        raf = requestAnimationFrame(measure);
      } else {
        onLanded(); // nowhere to land: just reveal the real bubble
      }
    };
    measure();
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flight.id]);

  if (!to) return null;
  // Offset that puts the ghost's text exactly where the typed text was.
  const dx = flight.from.left - (to.left + BUBBLE_PAD_X);
  const dy = flight.from.top - (to.top + BUBBLE_PAD_Y);
  return (
    <motion.div
      aria-hidden
      data-send-ghost
      className="pointer-events-none fixed z-[70] whitespace-pre-wrap rounded-3xl rounded-br-lg px-4.5 py-3 text-[0.95rem] leading-relaxed text-frost"
      style={{ left: to.left, top: to.top, width: to.width, height: to.height, willChange: "transform" }}
      initial={{ x: dx, y: dy }}
      animate={{ x: 0, y: 0 }}
      transition={flightSpring}
      onAnimationComplete={onLanded}
    >
      {/* bare text → frosted bubble: the bubble's skin fades in as it flies */}
      <motion.span aria-hidden
        className="absolute inset-0 rounded-[inherit] border border-sky/25 bg-gradient-to-br from-sky/25 to-periwinkle/15 shadow-[0_10px_30px_rgba(2,10,28,0.4)] backdrop-blur-xl"
        initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }}
        transition={{ opacity: { duration: 0.35, ease: "easeOut", delay: 0.04 }, scale: flightSpring }} />
      {/* a soft comet glow that blooms and fades with the motion */}
      <motion.span aria-hidden className="absolute -inset-3 rounded-[inherit] bg-sky/25 blur-xl"
        initial={{ opacity: 0 }} animate={{ opacity: [0, 0.85, 0] }} transition={{ duration: 0.7, ease: "easeOut" }} />
      <motion.span className="relative block" initial={{ scale: 1 }} animate={{ scale: [1, 1.03, 1] }}
        transition={{ duration: 0.5, ease: "easeOut" }} style={{ transformOrigin: "0% 0%" }}>
        {flight.text}
      </motion.span>
    </motion.div>
  );
}
