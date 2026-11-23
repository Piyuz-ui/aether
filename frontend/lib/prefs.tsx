"use client";

/**
 * User preferences (calm mode, voice, notifications) and the cursor parallax field,
 * shared through context and remembered in localStorage.
 */
import { MotionConfig, useMotionValue, useSpring, type MotionValue } from "framer-motion";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { drift } from "./motion";

export type Prefs = {
  calm: boolean; // reduced motion: no ambient drift, parallax or wobble
  speakReplies: boolean; // read answers aloud (browser speech synthesis, local)
  wakeWord: boolean; // listen for "Hey Aether"
  autoSendVoice: boolean; // send as soon as you stop talking
  notifications: boolean; // desktop reminders for deadlines
  drawerOpen: boolean;
  voice: string; // Kokoro voice preset id
  speed: number; // 0.85 – 1.25
};

const DEFAULTS: Prefs = {
  calm: false,
  speakReplies: false,
  wakeWord: false,
  autoSendVoice: true,
  notifications: false,
  drawerOpen: true,
  voice: "af_heart",
  speed: 1,
};

const KEY = "aether.prefs.v1";

type PrefsContext = {
  prefs: Prefs;
  setPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
  /** -1…1 cursor position, smoothed; 0 when calm mode is on. */
  parallax: { x: MotionValue<number>; y: MotionValue<number> };
};

const Ctx = createContext<PrefsContext | null>(null);

function load(): Prefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    const stored = raw ? (JSON.parse(raw) as Partial<Prefs>) : {};
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    // On narrow screens the drawer covers the chat, so it starts closed there.
    const wide = window.matchMedia?.("(min-width: 1024px)").matches ?? true;
    return { ...DEFAULTS, calm: reduce, drawerOpen: wide, ...stored };
  } catch {
    return DEFAULTS;
  }
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>(DEFAULTS);
  useEffect(() => setPrefs(load()), []);

  const setPref = useCallback(<K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    setPrefs((p) => {
      const next = { ...p, [key]: value };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* private mode: keep in memory only */
      }
      return next;
    });
  }, []);

  // Cursor parallax: raw position → slow spring, so layers drift rather than snap.
  const rawX = useMotionValue(0);
  const rawY = useMotionValue(0);
  const x = useSpring(rawX, drift);
  const y = useSpring(rawY, drift);

  useEffect(() => {
    if (prefs.calm) {
      rawX.set(0);
      rawY.set(0);
      return;
    }
    const onMove = (e: PointerEvent) => {
      rawX.set((e.clientX / window.innerWidth) * 2 - 1);
      rawY.set((e.clientY / window.innerHeight) * 2 - 1);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [prefs.calm, rawX, rawY]);

  const value = useMemo(() => ({ prefs, setPref, parallax: { x, y } }), [prefs, setPref, x, y]);

  return (
    <Ctx.Provider value={value}>
      {/* Calm mode switches every transform/layout animation off app-wide. */}
      <MotionConfig reducedMotion={prefs.calm ? "always" : "never"}>{children}</MotionConfig>
    </Ctx.Provider>
  );
}

export function usePrefs(): PrefsContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePrefs must be used inside <PrefsProvider>");
  return ctx;
}
