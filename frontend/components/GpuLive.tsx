"use client";

/**
 * A whisper of live GPU activity inside a reply while it's being written:
 *
 *     ▁▃▆█▇  72% · 9.6/15.9 GB · 58°  pc
 *
 * Polls once a second, only while the reply streams, only when the answering model runs on a GPU
 * that reports numbers (a network PC with AETHER's hardware monitor). Otherwise it renders nothing.
 */
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { GpuLive as Gpu } from "@/lib/types";

const HISTORY = 14;

export function GpuLive({ active, model }: { active: boolean; model?: string }) {
  const [gpu, setGpu] = useState<Extract<Gpu, { available: true }> | null>(null);
  const [history, setHistory] = useState<number[]>([]);
  const gaveUp = useRef(false);

  useEffect(() => {
    if (!active || !model) return;
    // Only a model on a network computer ("…@pc") can report its GPU.
    if (!/@[a-z][a-z0-9-]*$/.test(model)) return;
    let alive = true;
    gaveUp.current = false;
    const tick = async () => {
      if (gaveUp.current || document.hidden) return;
      try {
        const g = await api.gpu(model);
        if (!alive) return;
        if (!g.available) { gaveUp.current = true; return; } // no monitor there: stop asking
        setGpu(g);
        if (g.util_pct != null) setHistory((h) => [...h.slice(-(HISTORY - 1)), g.util_pct as number]);
      } catch {
        /* a missed beat is fine */
      }
    };
    void tick();
    const t = window.setInterval(tick, 1000);
    return () => { alive = false; window.clearInterval(t); };
  }, [active, model]);

  const show = active && gpu !== null;
  const util = gpu?.util_pct;
  return (
    <AnimatePresence>
      {show && gpu && (
        <motion.div
          key="gpu"
          initial={{ opacity: 0, y: 2 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: 0.4 } }}
          className="flex items-center gap-2 font-mono text-[10.5px] text-mist/80"
          aria-label={`GPU ${util ?? "?"}% busy on ${gpu.host}`}
          title={`${gpu.name} on ${gpu.host}`}
        >
          <span className="flex h-2.5 items-end gap-[1.5px]" aria-hidden>
            {Array.from({ length: HISTORY }, (_, i) => {
              const v = history[history.length - HISTORY + i];
              return (
                <span key={i} className="w-[2px] rounded-[1px] bg-seafoam/70 transition-[height] duration-500"
                  style={{ height: v == null ? "1px" : `${Math.max(8, v)}%`, opacity: v == null ? 0.25 : 0.45 + v / 180 }} />
              );
            })}
          </span>
          <span className="text-frost/80">{util == null ? "–" : `${Math.round(util)}%`}</span>
          {gpu.vram_used_gb != null && gpu.vram_total_gb != null && <span>· {gpu.vram_used_gb}/{gpu.vram_total_gb} GB</span>}
          {gpu.temp_c != null && <span>· {Math.round(gpu.temp_c)}°</span>}
          <span className="text-mist/50">{gpu.host}</span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
