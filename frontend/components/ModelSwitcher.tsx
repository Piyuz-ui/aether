"use client";

/** The model name in the header is a live switcher: lists installed Ollama models, hot-swaps the brain. */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronDown, Cpu } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { jelly } from "@/lib/motion";

import { Spinner } from "./ui";

// Embedding-only models can't chat or call tools, so they're hidden from the list.
const NOT_CHAT = /embed|bge-|minilm|rerank/i;

export function ModelSwitcher({ current, disabled, onSwitched, toast }: {
  current: string | null;
  disabled?: boolean;
  onSwitched: (model: string) => void;
  toast: (title: string, body?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<string[] | null>(null);
  const [switching, setSwitching] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    api.models()
      .then((m) => alive && setModels(m.models.filter((name) => !NOT_CHAT.test(name))))
      .catch((e: ApiError) => { if (alive) { setModels([]); toast(e.message, e.hint); } });
    const onDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      alive = false;
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, toast]);

  async function choose(model: string) {
    if (model === current) { setOpen(false); return; }
    setSwitching(model);
    try {
      await api.setModel(model);
      onSwitched(model);
      toast("Brain switched", `Now thinking with ${model}`);
      setOpen(false);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, String(e));
      toast(err.message, err.hint);
    } finally {
      setSwitching(null);
    }
  }

  return (
    <div ref={root} className="relative inline-flex">
      <motion.button
        type="button"
        disabled={disabled || !current}
        onClick={() => setOpen((o) => !o)}
        whileTap={{ scale: 0.95 }}
        transition={jelly}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Model: ${current ?? "unknown"}. Change model`}
        title={disabled ? "Finish the current answer to switch models" : undefined}
        className="-mx-1 inline-flex items-center gap-1 rounded-lg px-1 py-0.5 font-mono text-[11.5px] text-frost/80 transition-colors hover:bg-white/[0.07] hover:text-frost disabled:pointer-events-none"
      >
        {current ?? "no model"}
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={jelly}>
          <ChevronDown className="size-3 text-mist" />
        </motion.span>
      </motion.button>
      <AnimatePresence>
        {open && (
          <motion.div
            role="listbox"
            aria-label="Choose a model"
            initial={{ opacity: 0, y: -6, scale: 0.94 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.97, transition: { duration: 0.12 } }}
            transition={jelly}
            style={{ transformOrigin: "top left" }}
            className="glass-pop absolute left-0 top-full z-50 mt-2 w-64 rounded-2xl p-1.5"
          >
            <div className="flex items-center gap-1.5 px-2.5 pb-1.5 pt-1 text-[10.5px] uppercase tracking-[0.14em] text-mist">
              <Cpu className="size-3" /> Installed models
            </div>
            {models === null && <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-mist"><Spinner /> Asking Ollama…</div>}
            {models?.length === 0 && <div className="px-2.5 py-2 text-xs text-mist">No chat models found.</div>}
            <div className="soft-scroll max-h-72 overflow-y-auto">
              {models?.map((m) => (
                <motion.button
                  key={m}
                  type="button"
                  role="option"
                  aria-selected={m === current}
                  onClick={() => void choose(m)}
                  disabled={!!switching}
                  whileTap={{ scale: 0.97 }}
                  transition={jelly}
                  className={clsx("flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left font-mono text-[12.5px] transition-colors",
                    m === current ? "bg-sky/[0.14] text-frost" : "text-frost/85 hover:bg-white/[0.07]")}
                >
                  <span className="min-w-0 flex-1 truncate">{m}</span>
                  {switching === m ? <Spinner /> : m === current ? <Check className="size-3.5 text-seafoam" /> : null}
                </motion.button>
              ))}
            </div>
            <div className="mt-1 border-t border-white/[0.07] px-2.5 pb-1 pt-2 text-[11px] leading-snug text-mist">
              Add more with <code className="font-mono text-glacier">ollama pull &lt;name&gt;</code>. Tool-calling
              models (qwen, llama 3.1+, mistral) work best.
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
