"use client";

/**
 * Live ReAct telemetry: a collapsible footer on every reply showing the exact tool chain,
 * how many reasoning steps it took, and local latency. Expand it for the full breakdown.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronDown, Cloud, Cpu, X, Zap } from "lucide-react";
import { useState } from "react";

import { cloudProvider, modelName, remoteHost } from "@/lib/cloud";

import { money } from "./panels/UsagePanel";
import { jelly } from "@/lib/motion";
import type { AssistantMessage, ToolStep } from "@/lib/types";

const pretty = (n: string) => n.replace(/_/g, " ");

export function TelemetryChip({ m }: { m: AssistantMessage }) {
  const [open, setOpen] = useState(false);
  const s = m.stats;
  const cloud = cloudProvider(s?.model);
  const host = remoteHost(s?.model);
  const tools = m.steps.filter((x): x is ToolStep => x.kind === "tool");
  if (!s && !tools.length) return null;

  const chain = tools.map((t) => t.name);
  const shown = chain.length > 4 ? [...chain.slice(0, 3), `+${chain.length - 3}`] : chain;
  const steps = s?.model_calls ?? tools.length + 1;
  const timeFor = (i: number) => s?.tools?.[i]?.seconds ?? tools[i]?.duration;

  return (
    <div className="mt-3">
      <motion.button
        type="button"
        onClick={() => setOpen((o) => !o)}
        whileTap={{ scale: 0.97 }}
        transition={jelly}
        aria-expanded={open}
        aria-label="Agent telemetry"
        className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-white/[0.08] bg-abyss-950/30 px-2.5 py-1 font-mono text-[10.5px] text-mist transition-colors hover:border-glacier/25 hover:text-frost"
      >
        <Zap className="size-3 shrink-0 text-glacier" />
        <span className="shrink-0">{steps} step{steps === 1 ? "" : "s"}</span>
        {shown.length > 0 && (
          <>
            <span className="text-mist/50">·</span>
            <span className="min-w-0 truncate">{shown.map(pretty).join(" → ")}</span>
          </>
        )}
        {s && (
          <>
            <span className="text-mist/50">·</span>
            <span className="shrink-0 text-frost/85">{s.wall_s.toFixed(1)}s</span>
          </>
        )}
        <span className="text-mist/50">·</span>
        {s?.cost_usd != null && s.cost_usd > 0 && (
          <><span className="text-mist/50">·</span><span className="shrink-0 font-mono text-frost/85">{money(s.cost_usd)}</span></>
        )}
        {cloud
          ? <span className="shrink-0 text-amber">cloud · {cloud}</span>
          : <span className="shrink-0 text-seafoam">{host ? `local · ${host}` : "local"}</span>}
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={jelly} className="shrink-0">
          <ChevronDown className="size-3" />
        </motion.span>
      </motion.button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={jelly} className="overflow-hidden">
            <div className="glass-inset mt-2 space-y-3 rounded-2xl p-3 font-mono text-[11px] text-mist">
              {tools.length > 0 && (
                <ol className="space-y-1">
                  {tools.map((t, i) => {
                    const secs = timeFor(i);
                    return (
                      <li key={t.callId} className="flex items-center gap-2">
                        <span className="w-4 text-right text-mist/60">{i + 1}</span>
                        {t.status === "ok"
                          ? <Check className="size-3 text-seafoam" />
                          : <X className={clsx("size-3", t.status === "denied" ? "text-amber" : "text-coral")} />}
                        <span className="text-frost/90">{pretty(t.name)}</span>
                        {t.status === "denied" && <span className="text-amber">denied</span>}
                        <span className="ml-auto">{secs !== undefined ? `${secs.toFixed(2)}s` : ""}</span>
                      </li>
                    );
                  })}
                </ol>
              )}
              {s && (
                <>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
                    <Stat label="total" value={`${s.wall_s.toFixed(2)}s`} />
                    <Stat label="steps" value={String(s.model_calls)} />
                    <Stat label="tokens in" value={s.prompt_tokens.toLocaleString()} />
                    <Stat label="tokens out" value={s.output_tokens.toLocaleString()} />
                    <Stat label="speed" value={`${Math.round(s.tokens_per_s)} tok/s`} />
                    <Stat label="tool time" value={`${s.tools.reduce((a, x) => a + x.seconds, 0).toFixed(2)}s`} />
                  </div>
                  <div>
                    <div className="mb-1 flex justify-between">
                      <span>context window</span>
                      <span className="text-frost/85">{s.context_used.toLocaleString()} / {s.context_limit.toLocaleString()} ({Math.round(s.context_pct)}%)</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                      <motion.div className={clsx("h-full rounded-full", s.context_pct > 85 ? "bg-coral" : "bg-gradient-to-r from-sky to-seafoam")}
                        initial={{ width: 0 }} animate={{ width: `${Math.min(100, s.context_pct)}%` }} transition={jelly} />
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 text-mist/80">
                    {cloud
                      ? <><Cloud className="size-3" /> {modelName(s.model)} · ran on {cloud}&apos;s servers</>
                      : host
                        ? <><Cpu className="size-3" /> {modelName(s.model)} · ran on {host} (your network) via Ollama</>
                        : <><Cpu className="size-3" /> {s.model} · running locally via Ollama</>}
                  </div>
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[9.5px] uppercase tracking-[0.12em] text-mist/60">{label}</div>
      <div className="text-frost/90">{value}</div>
    </div>
  );
}
