"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { jelly } from "@/lib/motion";

export function PanelTitle({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div>
        <h3 className="text-[15px] font-semibold text-frost">{title}</h3>
        {hint && <p className="mt-0.5 text-xs leading-relaxed text-mist">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="glass-inset rounded-2xl px-4 py-6 text-center text-sm text-mist">{children}</div>;
}

export function ErrorNote({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-coral/30 bg-coral/[0.07] px-3 py-2.5 text-xs text-coral">
      {message}
      {hint && <div className="mt-1 font-mono text-frost/75">→ {hint}</div>}
    </div>
  );
}

/** Two-tap delete: the first tap turns into "Delete?", the second confirms. */
export function ConfirmDelete({ onConfirm, label = "Delete" }: { onConfirm: () => void; label?: string }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 2500);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <motion.button
      type="button"
      layout
      onClick={() => (armed ? onConfirm() : setArmed(true))}
      whileTap={{ scale: 0.9 }}
      transition={jelly}
      aria-label={armed ? `Confirm ${label.toLowerCase()}` : label}
      title={label}
      className={armed
        ? "rounded-xl bg-coral/20 px-2 py-1 text-[11px] font-medium text-coral"
        : "grid size-7 place-items-center rounded-lg text-mist/70 hover:bg-coral/10 hover:text-coral"}
    >
      <AnimatePresence mode="popLayout" initial={false}>
        {armed ? (
          <motion.span key="c" initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }}>{label}?</motion.span>
        ) : (
          <motion.span key="i" initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }}>
            <Trash2 className="size-3.5" />
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  );
}

export const inputClass =
  "w-full rounded-xl border border-white/10 bg-abyss-950/40 px-3 py-2 text-sm text-frost placeholder:text-mist/55 focus:border-glacier/50 focus:outline-none";
