"use client";

/**
 * Reasoning, for models that think:
 *   ReasoningPill  "Reasoning: Med ⌄" next to the model picker (gpt-oss: Low · Med · High;
 *                   other thinking models: Off · On). Remembered in this browser.
 *   ThoughtCard    a collapsible frosted card above the reply: "Thinking…" live, then
 *                   "Thought for 3.8s", with the model's reasoning inside.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Brain, Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { jelly } from "@/lib/motion";
import { usePrefs, type Prefs } from "@/lib/prefs";
import type { AssistantMessage } from "@/lib/types";

type Level = Prefs["reasoning"];
const LEVELS: { id: Level; label: string; hint: string }[] = [
  { id: "low", label: "Low", hint: "Quick answers and simple tasks" },
  { id: "medium", label: "Med", hint: "Everyday chat (default)" },
  { id: "high", label: "High", hint: "Deep research, maths and debugging: slower" },
];

export function ReasoningPill({ kind, disabled }: { kind: "levels" | "toggle"; disabled?: boolean }) {
  const { prefs, setPref } = usePrefs();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", esc); };
  }, [open]);

  const options = kind === "levels" ? LEVELS
    : [{ id: "low" as Level, label: "Off", hint: "Answer straight away" }, { id: "high" as Level, label: "On", hint: "Think first: slower, more careful" }];
  const current = options.find((o) => o.id === prefs.reasoning) ?? (kind === "toggle" ? options[prefs.reasoning === "low" ? 0 : 1] : LEVELS[1]);

  return (
    <div ref={root} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
        aria-label={`Reasoning: ${current.label}. Change`} title="How hard the model thinks before answering"
        className="inline-flex h-7 items-center gap-1 rounded-lg border border-white/[0.07] bg-white/[0.03] px-2 text-[11.5px] text-frost/80 transition-colors hover:bg-white/[0.07] hover:text-frost disabled:pointer-events-none">
        <Brain className="size-3 text-periwinkle" />
        <span className="text-mist">{kind === "levels" ? "Reasoning:" : "Thinking:"}</span> {current.label}
        <ChevronDown className={clsx("size-3 text-mist transition-transform", !open && "rotate-180")} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div role="listbox" aria-label="Reasoning" initial={{ opacity: 0, y: 6, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, transition: { duration: 0.12 } }} transition={jelly} style={{ transformOrigin: "bottom left" }}
            className="glass-pop absolute bottom-full left-0 z-50 mb-2 w-60 rounded-2xl p-1.5">
            {options.map((o) => (
              <button key={o.id} type="button" role="option" aria-selected={o.id === current.id}
                onClick={() => { setPref("reasoning", o.id); setOpen(false); }}
                className={clsx("flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left transition-colors",
                  o.id === current.id ? "bg-sky/[0.14]" : "hover:bg-white/[0.07]")}>
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] text-frost">{o.label}</span>
                  <span className="block text-[11px] text-mist">{o.hint}</span>
                </span>
                {o.id === current.id && <Check className="size-3.5 text-seafoam" />}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ThoughtCard({ m }: { m: AssistantMessage }) {
  const live = m.status === "streaming" && m.thinkStart !== undefined;
  const [open, setOpen] = useState<boolean | null>(null);   // null: follow "open while thinking"
  const shown = open ?? live;
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { if (shown && live && body.current) body.current.scrollTop = body.current.scrollHeight; }, [m.reasoning, shown, live]);
  if (!m.reasoning?.trim()) return null;
  const secs = (m.thinkMs ?? 0) / 1000;
  return (
    <div className="glass overflow-hidden rounded-2xl">
      <button type="button" onClick={() => setOpen(!shown)} aria-expanded={shown}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-left text-[12px] text-mist transition-colors hover:text-frost">
        <Brain className={clsx("size-3.5", live ? "animate-pulse text-periwinkle" : "text-periwinkle/70")} />
        <span className="flex-1">
          {live ? "Thinking…" : secs >= 0.1 ? `Thought for ${secs < 10 ? secs.toFixed(1) : Math.round(secs)}s` : "Thought it through"}
          {m.reasoningLevel && m.reasoningLevel !== "on" && <span className="ml-1.5 text-mist/60">· {m.reasoningLevel}</span>}
        </span>
        <ChevronDown className={clsx("size-3.5 transition-transform", shown && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {shown && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }} className="overflow-hidden">
            <div ref={body} className="soft-scroll max-h-56 overflow-y-auto border-t border-white/[0.06] px-3.5 py-2.5 text-[12.5px] italic leading-relaxed text-mist/90 whitespace-pre-wrap">
              {m.reasoning.trim()}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
