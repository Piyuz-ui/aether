"use client";

/**
 * A glass side panel that opens smoothly. On wide screens it is docked: its slot's width animates
 * (so the chat glides over instead of jumping) while the panel itself keeps a fixed width, so its
 * contents never reflow mid-animation. On small screens it slides over the chat with a dimmed backdrop.
 */
import clsx from "clsx";
import { AnimatePresence, motion, type Transition } from "framer-motion";
import { useEffect, useState, type ReactNode } from "react";

import { usePrefs } from "@/lib/prefs";

/** Gentle ease-in-out: no first-frame lurch, no overshoot (a width that bounces makes the chat wobble). */
const glide: Transition = { type: "tween", duration: 0.36, ease: [0.4, 0, 0.2, 1] };
const quick: Transition = { duration: 0.12 };
const GAP = 12; // the panel's outer margin (Tailwind 3)

function useWide(): boolean {
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const q = window.matchMedia("(min-width: 1024px)");
    const on = () => setWide(q.matches);
    on();
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);
  return wide;
}

export function SidePanel({ open, side, width, sheetWidth = width, label, onClose, className, initial = true, children }: {
  open: boolean;
  side: "left" | "right";
  width: number;           // docked width in px
  sheetWidth?: number;     // slide-over width on small screens
  label: string;
  onClose: () => void;
  className?: string;
  initial?: boolean;       // animate on first render
  children: ReactNode;
}) {
  const wide = useWide();
  const { prefs } = usePrefs();
  const t = prefs.calm ? quick : glide;
  const sign = side === "left" ? -1 : 1;
  // Clip only while the slot is resizing, so the glass shadow isn't cut off when it's open.
  const [moving, setMoving] = useState(false);

  if (wide) {
    return (
      <AnimatePresence initial={initial}>
        {open && (
          <motion.div key={`${side}-slot`} className="relative h-dvh shrink-0"
            style={{ overflow: moving ? "hidden" : "visible" }}
            initial={{ width: 0 }} animate={{ width: width + GAP }} exit={{ width: 0 }} transition={t}
            onAnimationStart={() => setMoving(true)} onAnimationComplete={() => setMoving(false)}>
            <motion.aside aria-label={label}
              initial={{ x: sign * 24, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: sign * 24, opacity: 0 }}
              transition={t} style={{ width, [side]: GAP, willChange: "transform, opacity" }}
              className={clsx("glass absolute inset-y-3 overflow-hidden rounded-[28px]", className)}>
              {children}
            </motion.aside>
          </motion.div>
        )}
      </AnimatePresence>
    );
  }

  return (
    <AnimatePresence initial={initial}>
      {open && (
        <>
          <motion.div key={`${side}-dim`} className="fixed inset-0 z-30 bg-abyss-950/50 backdrop-blur-[2px]"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={t} onClick={onClose} />
          <motion.aside key={`${side}-sheet`} aria-label={label}
            initial={{ x: `${sign * 110}%` }} animate={{ x: 0 }} exit={{ x: `${sign * 110}%` }} transition={t}
            style={{ width: `min(${sheetWidth}px, calc(100vw - 24px))`, willChange: "transform" }}
            className={clsx("glass fixed inset-y-3 z-40 overflow-hidden rounded-[28px]",
              side === "left" ? "left-3" : "right-3", className)}>
            {children}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
