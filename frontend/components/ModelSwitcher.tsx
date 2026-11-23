"use client";

/**
 * The model name in the header is a live switcher. It lists local Ollama models first, then the
 * models of every cloud provider you've added a key for, and hot-swaps the brain.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronDown, Cloud, Cpu, Eye, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { cloudProvider, modelName } from "@/lib/cloud";
import { jelly } from "@/lib/motion";
import type { ModelList } from "@/lib/types";

import { Spinner } from "./ui";

// Embedding-only models can't chat or call tools, so they're hidden from the list.
const NOT_CHAT = /embed|bge-|minilm|rerank/i;

function GroupLabel({ icon, children, badge }: { icon: React.ReactNode; children: React.ReactNode; badge?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 px-2.5 pb-1 pt-2 text-[10.5px] uppercase tracking-[0.14em] text-mist">
      {icon} <span className="min-w-0 flex-1 truncate">{children}</span> {badge}
    </div>
  );
}

export function ModelSwitcher({ current, disabled, onSwitched, toast, onOpenSettings }: {
  current: string | null;
  disabled?: boolean;
  onSwitched: (model: string) => void;
  toast: (title: string, body?: string) => void;
  /** Opens Settings, where cloud keys are added. */
  onOpenSettings?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ModelList | null>(null);
  const [failed, setFailed] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    api.models()
      .then((m) => { if (alive) { setData(m); setFailed(false); } })
      .catch((e: ApiError) => { if (alive) { setFailed(true); toast(e.message, e.hint); } });
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
      const cloud = cloudProvider(model);
      toast("Brain switched", cloud
        ? `Now thinking with ${modelName(model)} on ${cloud}. Your messages go to ${cloud} while it's selected.`
        : `Now thinking with ${model}, on this Mac`);
      setOpen(false);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, String(e));
      toast(err.message, err.hint);
    } finally {
      setSwitching(null);
    }
  }

  const local = data?.models.filter((n) => !NOT_CHAT.test(n)) ?? null;
  const cloud = data?.cloud?.filter((p) => p.configured) ?? [];
  const unset = data?.cloud?.filter((p) => !p.configured) ?? [];
  const currentCloud = cloudProvider(current);

  const row = (id: string, label: string, extra?: React.ReactNode) => (
    <motion.button
      key={id}
      type="button"
      role="option"
      aria-selected={id === current}
      onClick={() => void choose(id)}
      disabled={!!switching}
      whileTap={{ scale: 0.97 }}
      transition={jelly}
      className={clsx("flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left font-mono text-[12.5px] transition-colors",
        id === current ? "bg-sky/[0.14] text-frost" : "text-frost/85 hover:bg-white/[0.07]")}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {extra}
      {switching === id ? <Spinner /> : id === current ? <Check className="size-3.5 shrink-0 text-seafoam" /> : null}
    </motion.button>
  );

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
        aria-label={`Model: ${current ?? "unknown"}${currentCloud ? ` (cloud, ${currentCloud})` : ""}. Change model`}
        title={disabled ? "Finish the current answer to switch models" : undefined}
        className="-mx-1 inline-flex items-center gap-1 rounded-lg px-1 py-0.5 font-mono text-[11.5px] text-frost/80 transition-colors hover:bg-white/[0.07] hover:text-frost disabled:pointer-events-none"
      >
        {currentCloud && <Cloud className="size-3 text-periwinkle" aria-hidden />}
        {current ? modelName(current) : "no model"}
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
            className="glass-pop absolute left-0 top-full z-50 mt-2 w-72 rounded-2xl p-1.5"
          >
            {data === null && !failed && <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-mist"><Spinner /> Finding models…</div>}
            <div className="soft-scroll max-h-[22rem] overflow-y-auto">
              {data && (
                <>
                  <GroupLabel icon={<Cpu className="size-3" />}
                    badge={<span className="inline-flex items-center gap-0.5 normal-case tracking-normal text-seafoam"><ShieldCheck className="size-3" />private</span>}>
                    On this Mac
                  </GroupLabel>
                  {local?.length === 0 && (
                    <div className="px-2.5 py-1.5 text-xs text-mist">{data.local_error ? "Ollama is offline." : "No chat models found."}</div>
                  )}
                  {local?.map((m) => row(m, m))}
                </>
              )}
              {cloud.map((p) => (
                <div key={p.id}>
                  <GroupLabel icon={<Cloud className="size-3" />}
                    badge={<span className={clsx("normal-case tracking-normal", p.free ? "text-seafoam" : "text-amber")}>{p.free ? "free tier" : "paid"}</span>}>
                    {p.label}
                  </GroupLabel>
                  {p.error && <div className="px-2.5 py-1 text-[11px] text-coral">{p.error.message}</div>}
                  {p.models.map((m) => row(m.id, m.name, m.vision
                    ? <Eye className="size-3 shrink-0 text-mist" aria-label="can see images" />
                    : undefined))}
                </div>
              ))}
            </div>
            <div className="mt-1 space-y-1 border-t border-white/[0.07] px-2.5 pb-1 pt-2 text-[11px] leading-snug text-mist">
              {currentCloud ? (
                <p className="text-amber/90">
                  Cloud brain: your messages, and any memories or document passages AETHER adds to them, are
                  sent to {currentCloud}. Pick a model under “On this Mac” to stay fully local.
                </p>
              ) : (
                <p>
                  Add local models with <code className="font-mono text-glacier">ollama pull &lt;name&gt;</code>.
                </p>
              )}
              {unset.length > 0 && (
                <button type="button" onClick={() => { setOpen(false); onOpenSettings?.(); }}
                  className="text-left text-glacier underline-offset-2 hover:underline">
                  + Add a cloud brain ({unset.map((p) => p.label.replace(/^(Google|Anthropic) /, "")).join(", ")})
                </button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
