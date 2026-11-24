"use client";

/** A chart (or any image) a tool made, shown inside the reply on a frosted glass card. */
import { motion } from "framer-motion";
import { Download, Expand } from "lucide-react";
import { useState } from "react";

import { API_BASE } from "@/lib/api";
import { depthIn } from "@/lib/motion";
import type { ChartArtifact, Step } from "@/lib/types";

import { TiltCard } from "./ui";

/** Every image the tools in a reply produced (live events, or `[artifact]` lines after a reload). */
export function chartsIn(steps: Step[]): ChartArtifact[] {
  const seen = new Set<string>();
  const out: ChartArtifact[] = [];
  for (const s of steps) {
    if (s.kind !== "tool" || s.status !== "ok") continue;
    for (const a of s.artifacts ?? artifactsFromOutput(s.output)) {
      if (!seen.has(a.path)) { seen.add(a.path); out.push(a); }
    }
  }
  return out;
}

export function artifactsFromOutput(output?: string): ChartArtifact[] {
  const out: ChartArtifact[] = [];
  for (const line of (output ?? "").split("\n")) {
    const m = /^\[artifact\] (artifacts\/[^\s/][^\s]*\.(?:png|jpe?g|svg|webp))$/i.exec(line.trim());
    if (m && !m[1].includes("..")) {
      out.push({ path: m[1], name: m[1].split("/").pop()!, url: `/${m[1]}`, download: `/api/files/${m[1]}?download=true` });
    }
  }
  return out;
}

export function ChartCard({ a }: { a: ChartArtifact }) {
  const [failed, setFailed] = useState(false);
  const abs = (u: string) => (/^https?:\/\//.test(u) ? u : `${API_BASE}${u}`);
  const src = abs(a.url);
  if (failed) return null;
  return (
    <TiltCard intensity={3} radius="rounded-3xl rounded-tl-lg">
    <motion.figure variants={depthIn} initial="hidden" animate="show"
      className="glass overflow-hidden rounded-3xl rounded-tl-lg">
      <a href={src} target="_blank" rel="noreferrer" className="group relative block bg-white/[0.97]" title="Open full size">
        {/* eslint-disable-next-line @next/next/no-img-element -- served by the local backend, sizes vary */}
        <img src={src} alt={`Chart: ${a.name}`} loading="lazy" onError={() => setFailed(true)}
          className="mx-auto max-h-[420px] w-full object-contain" />
        <span className="absolute right-2 top-2 grid size-7 place-items-center rounded-lg bg-black/40 text-white opacity-0 backdrop-blur transition-opacity group-hover:opacity-100">
          <Expand className="size-3.5" />
        </span>
      </a>
      <figcaption className="flex items-center gap-2 px-4 py-2.5">
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-mist">{a.name}</span>
        <a href={abs(a.download)} download={a.name}
          className="inline-flex items-center gap-1.5 rounded-full border border-glacier/30 bg-sky/[0.1] px-3 py-1 text-xs text-glacier transition-colors hover:bg-sky/[0.18] hover:text-frost">
          <Download className="size-3.5" /> Download
        </a>
      </figcaption>
    </motion.figure>
    </TiltCard>
  );
}
