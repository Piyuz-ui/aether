"use client";

/** Small tactile building blocks: jiggly buttons, tilt cards, chips, toggles. */
import clsx from "clsx";
import { motion, useMotionValue, useSpring, useTransform, type HTMLMotionProps } from "framer-motion";
import type { ReactNode } from "react";

import { jelly } from "@/lib/motion";

type Tone = "glass" | "primary" | "danger" | "approve" | "ghost";

const TONES: Record<Tone, string> = {
  glass: "glass text-frost hover:bg-white/[0.09]",
  primary:
    "bg-gradient-to-br from-sky to-periwinkle text-abyss-950 font-semibold shadow-[0_8px_30px_rgba(56,189,248,0.35)]",
  approve:
    "bg-gradient-to-br from-seafoam to-glacier text-abyss-950 font-semibold shadow-[0_8px_30px_rgba(94,234,212,0.35)]",
  danger: "bg-coral/15 text-coral border border-coral/30 hover:bg-coral/25",
  ghost: "text-mist hover:text-frost hover:bg-white/[0.06]",
};

export function JiggleButton({
  tone = "glass",
  className,
  children,
  ...rest
}: HTMLMotionProps<"button"> & { tone?: Tone; children?: ReactNode }) {
  return (
    <motion.button
      type="button"
      whileHover={rest.disabled ? undefined : { scale: 1.04 }}
      whileTap={rest.disabled ? undefined : { scale: 0.94 }}
      transition={jelly}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-2xl px-3.5 py-2 text-sm transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-45",
        TONES[tone],
        className,
      )}
      {...rest}
    >
      {children}
    </motion.button>
  );
}

export function IconButton({
  label,
  className,
  children,
  ...rest
}: HTMLMotionProps<"button"> & { label: string; children?: ReactNode }) {
  return (
    <motion.button
      type="button"
      aria-label={label}
      title={label}
      whileHover={{ scale: 1.08 }}
      whileTap={{ scale: 0.9 }}
      transition={jelly}
      className={clsx(
        "grid size-9 place-items-center rounded-xl text-mist transition-colors hover:bg-white/[0.07] hover:text-frost",
        className,
      )}
      {...rest}
    >
      {children}
    </motion.button>
  );
}

/** A card that tilts toward the cursor in 3D, then floats back on a spring. */
export function TiltCard({
  className,
  children,
  intensity = 6,
  ...rest
}: HTMLMotionProps<"div"> & { intensity?: number; children?: ReactNode }) {
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const rx = useSpring(useTransform(my, [-0.5, 0.5], [intensity, -intensity]), jelly);
  const ry = useSpring(useTransform(mx, [-0.5, 0.5], [-intensity, intensity]), jelly);
  return (
    <motion.div
      style={{ rotateX: rx, rotateY: ry, transformPerspective: 900 }}
      onPointerMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        mx.set((e.clientX - r.left) / r.width - 0.5);
        my.set((e.clientY - r.top) / r.height - 0.5);
      }}
      onPointerLeave={() => {
        mx.set(0);
        my.set(0);
      }}
      className={className}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

export function Chip({ tone = "mist", children, className }: {
  tone?: "mist" | "glacier" | "seafoam" | "amber" | "coral" | "periwinkle";
  children: ReactNode;
  className?: string;
}) {
  const tones = {
    mist: "bg-white/[0.05] text-mist border-white/10",
    glacier: "bg-glacier/10 text-glacier border-glacier/25",
    seafoam: "bg-seafoam/10 text-seafoam border-seafoam/25",
    amber: "bg-amber/10 text-amber border-amber/30",
    coral: "bg-coral/10 text-coral border-coral/30",
    periwinkle: "bg-periwinkle/10 text-periwinkle border-periwinkle/25",
  } as const;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
      tones[tone], className)}>
      {children}
    </span>
  );
}

export function Toggle({ checked, onChange, label, hint }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-4 rounded-2xl px-3 py-2.5 text-left hover:bg-white/[0.04]"
    >
      <span>
        <span className="block text-sm text-frost">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-mist">{hint}</span>}
      </span>
      <span className={clsx("relative h-6 w-11 shrink-0 rounded-full border transition-colors",
        checked ? "border-sky/50 bg-sky/30" : "border-white/10 bg-white/[0.06]")}>
        <motion.span
          className={clsx("absolute top-0.5 size-[18px] rounded-full shadow-md",
            checked ? "bg-glacier" : "bg-mist/70")}
          animate={{ left: checked ? 22 : 3 }}
          transition={jelly}
        />
      </span>
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <motion.span
      aria-hidden
      className={clsx("inline-block size-3.5 rounded-full border-2 border-glacier/25 border-t-glacier", className)}
      animate={{ rotate: 360 }}
      transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
    />
  );
}
