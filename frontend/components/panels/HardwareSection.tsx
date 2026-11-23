"use client";

/**
 * Settings → Hardware: what's running your models, live. One card per machine (this Mac, plus
 * every GPU PC on your network): GPU, VRAM, RAM, load and temperature, and the models Ollama
 * has loaded right now, with how much of each sits on the GPU. For a PC: whether it's available,
 * and how fast and steady the network link to it is.
 */
import clsx from "clsx";
import { motion } from "framer-motion";
import { Cable, CheckCircle2, CircleAlert, CircleOff, Cpu, Gauge, MemoryStick, MonitorSmartphone, Thermometer, Wifi, Zap } from "lucide-react";

import { api } from "@/lib/api";
import { jelly } from "@/lib/motion";
import type { Availability, HardwareCard, LinkQuality, LoadedModel, NetworkLink } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Chip } from "../ui";
import { PanelTitle } from "./shared";

const MONITOR_DOWNLOAD =
  'Invoke-WebRequest https://raw.githubusercontent.com/Piyuz-ui/aether/main/pc/install-hardware-monitor.ps1 -OutFile "$env:USERPROFILE\\Downloads\\install-hardware-monitor.ps1"';
const MONITOR_RUN = 'powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\\Downloads\\install-hardware-monitor.ps1"';

const gb = (n: number | null | undefined) => (n == null ? "–" : `${n % 1 === 0 ? n.toFixed(0) : n.toFixed(1)} GB`);

function Meter({ label, used, total, icon, note }: {
  label: string;
  used: number | null | undefined;
  total: number | null | undefined;
  icon: React.ReactNode;
  note?: string;
}) {
  const pct = used != null && total ? Math.min(100, (100 * used) / total) : 0;
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[11.5px]">
        <span className="inline-flex items-center gap-1.5 text-mist">{icon}{label}</span>
        <span className="font-mono text-frost/90">
          {gb(used)} <span className="text-mist">/ {gb(total)}</span>
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <motion.div className={clsx("h-full rounded-full", pct > 90 ? "bg-coral" : pct > 75 ? "bg-amber" : "bg-gradient-to-r from-sky to-seafoam")}
          initial={false} animate={{ width: `${pct}%` }} transition={jelly} />
      </div>
      {note && <p className="mt-1 text-[10.5px] text-mist/80">{note}</p>}
    </div>
  );
}

function Stat({ icon, value, label }: { icon: React.ReactNode; value: string; label: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-2 py-1.5 text-center">
      <div className="flex items-center justify-center gap-1 font-mono text-[13px] text-frost">{icon}{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-mist/80">{label}</div>
    </div>
  );
}

const QUALITY_TONE = {
  excellent: "seafoam", good: "glacier", fair: "amber", weak: "coral", poor: "coral", unreachable: "coral",
} as const;
const QUALITY_TEXT: Record<keyof typeof QUALITY_TONE, string> = {
  excellent: "text-seafoam", good: "text-glacier", fair: "text-amber", weak: "text-coral", poor: "text-coral", unreachable: "text-coral",
};
const cap = (w: string) => w[0].toUpperCase() + w.slice(1);

/** Four signal bars, filled up to `bars`. */
function Bars({ bars, tone }: { bars: number; tone: keyof typeof QUALITY_TONE }) {
  const color = { seafoam: "bg-seafoam", glacier: "bg-glacier", amber: "bg-amber", coral: "bg-coral" }[QUALITY_TONE[tone]];
  return (
    <span className="inline-flex h-3.5 items-end gap-[2px]" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span key={i} className={clsx("w-[3px] rounded-sm", i < bars ? color : "bg-white/15")}
          style={{ height: `${35 + i * 21.6}%` }} />
      ))}
    </span>
  );
}

const signalBars = (q?: string | null) => ({ excellent: 4, good: 3, fair: 2, weak: 1 } as Record<string, number>)[q ?? ""] ?? 0;

/** One line about how a machine itself is connected (Wi-Fi signal or cable). */
function OwnNetwork({ net }: { net?: NetworkLink | null }) {
  if (!net) return null;
  const wifi = net.type === "Wi-Fi";
  const strength = net.signal_dbm != null ? `${net.signal_dbm} dBm` : net.wifi_signal_pct != null ? `${net.wifi_signal_pct}%` : null;
  const extras = [net.band, net.standard, net.link_mbps ? `${Math.round(net.link_mbps)} Mbps` : null].filter(Boolean).join(" · ");
  return (
    <div className="flex items-center gap-2 text-[11.5px]">
      {wifi ? <Wifi className="size-3.5 shrink-0 text-mist" /> : <Cable className="size-3.5 shrink-0 text-mist" />}
      <span className="text-mist">{wifi ? "Wi-Fi" : net.type}</span>
      {wifi && net.quality && <><Bars bars={signalBars(net.quality)} tone={net.quality} />
        <span className={clsx("font-medium", QUALITY_TEXT[net.quality])}>{cap(net.quality)}</span></>}
      {!wifi && net.type === "Ethernet" && <span className="font-medium text-seafoam">Cable (best)</span>}
      <span className="min-w-0 flex-1 truncate text-right font-mono text-[10.5px] text-mist/80">
        {[strength, extras].filter(Boolean).join(" · ")}
      </span>
    </div>
  );
}

const AVAIL_STYLE: Record<Availability["state"], { tone: string; icon: React.ReactNode }> = {
  ready: { tone: "border-seafoam/25 bg-seafoam/[0.07] text-seafoam", icon: <CheckCircle2 className="size-4" /> },
  ollama_error: { tone: "border-amber/25 bg-amber/[0.07] text-amber", icon: <CircleAlert className="size-4" /> },
  no_ollama: { tone: "border-amber/25 bg-amber/[0.07] text-amber", icon: <CircleAlert className="size-4" /> },
  offline: { tone: "border-coral/25 bg-coral/[0.07] text-coral", icon: <CircleOff className="size-4" /> },
};

function AvailabilityBanner({ a }: { a: Availability }) {
  const st = AVAIL_STYLE[a.state];
  return (
    <div className={clsx("flex items-start gap-2 rounded-lg border px-2.5 py-2", st.tone)}>
      <span className="mt-px shrink-0">{st.icon}</span>
      <div className="min-w-0">
        <div className="text-[12.5px] font-medium">{a.label}</div>
        <div className="text-[11px] leading-snug text-frost/75">{a.detail}</div>
      </div>
    </div>
  );
}

function Connection({ link, route }: { link: LinkQuality; route?: string }) {
  if (link.quality === "unreachable") return null;
  const ms = (n: number | null) => (n == null ? "–" : `${n < 10 ? n.toFixed(1) : Math.round(n)} ms`);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] uppercase tracking-[0.14em] text-mist">
          Connection from this Mac{route === "tailscale" ? " · via Tailscale" : route === "home" ? " · home network" : ""}
        </span>
        <span className={clsx("inline-flex items-center gap-1.5 text-[12px] font-medium", QUALITY_TEXT[link.quality])}>
          <Bars bars={link.bars} tone={link.quality} /> {cap(link.quality)}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        <Stat icon={null} value={ms(link.latency_ms)} label="Delay" />
        <Stat icon={null} value={ms(link.jitter_ms)} label="Jitter" />
        <Stat icon={null} value={`${link.loss_pct}%`} label="Lost" />
      </div>
      <p className="text-[11px] leading-snug text-frost/80">{link.verdict}</p>
    </div>
  );
}

function LoadedRow({ m }: { m: LoadedModel }) {
  const onGpu = m.gpu_pct >= 99;
  return (
    <div className="flex items-center gap-2 text-[11.5px]">
      <span className="min-w-0 flex-1 truncate font-mono text-frost/90">{m.name}</span>
      <span className="font-mono text-mist">{gb(m.size_gb)}</span>
      <Chip tone={onGpu ? "seafoam" : m.gpu_pct > 0 ? "amber" : "mist"}>
        {onGpu ? "100% GPU" : m.gpu_pct > 0 ? `${m.gpu_pct}% GPU · ${100 - m.gpu_pct}% CPU` : "CPU"}
      </Chip>
    </div>
  );
}

function MachineCard({ c }: { c: HardwareCard }) {
  const sys = c.system;
  const gpu = sys?.gpus[0];
  const isMac = c.id === "local";
  return (
    <div className="space-y-3 rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
      <div className="flex items-center gap-3">
        <div className={clsx("grid size-9 shrink-0 place-items-center rounded-lg",
          c.online ? "bg-seafoam/[0.12] text-seafoam" : "bg-white/[0.05] text-mist")}>
          {isMac ? <Cpu className="size-4" /> : <MonitorSmartphone className="size-4" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-frost">{c.label}</span>
            {isMac && <Chip tone={c.online ? "seafoam" : "coral"}>{c.online ? "Ollama on" : "Ollama off"}</Chip>}
            {!isMac && c.link && c.link.quality !== "unreachable" && (
              <Chip tone={QUALITY_TONE[c.link.quality]}><Bars bars={c.link.bars} tone={c.link.quality} />{cap(c.link.quality)} link</Chip>
            )}
          </div>
          <div className="truncate text-[11px] text-mist">
            {[sys?.hostname, sys?.cpu].filter(Boolean).join(" · ") || c.url}
          </div>
        </div>
      </div>

      {c.availability && <AvailabilityBanner a={c.availability} />}
      {c.link && <Connection link={c.link} route={c.route} />}
      <OwnNetwork net={sys?.network} />

      {gpu && (
        <div className="space-y-2.5">
          <div className="flex items-center gap-1.5 text-[13px] font-medium text-frost">
            <Zap className="size-3.5 text-amber" /> {gpu.name}
          </div>
          {!sys?.unified_memory && (
            <div className="grid grid-cols-3 gap-1.5">
              <Stat icon={<Gauge className="size-3 text-mist" />} value={gpu.util_pct == null ? "–" : `${gpu.util_pct}%`} label="GPU load" />
              <Stat icon={<Thermometer className="size-3 text-mist" />} value={gpu.temp_c == null ? "–" : `${gpu.temp_c}°`} label="Temp" />
              <Stat icon={<Zap className="size-3 text-mist" />} value={gpu.power_w == null ? "–" : `${Math.round(gpu.power_w)} W`} label="Power" />
            </div>
          )}
          {sys?.unified_memory ? (
            <Meter label="Unified memory" icon={<MemoryStick className="size-3" />} used={sys.ram_used_gb} total={sys.ram_total_gb}
              note="Shared by the CPU and GPU (Apple silicon), so models use this memory directly." />
          ) : (
            <>
              <Meter label="VRAM" icon={<Zap className="size-3" />} used={gpu.vram_used_gb} total={gpu.vram_total_gb} />
              <Meter label="RAM" icon={<MemoryStick className="size-3" />} used={sys?.ram_used_gb} total={sys?.ram_total_gb} />
            </>
          )}
        </div>
      )}

      {c.monitor === "missing" && c.online && (
        <div className="space-y-1.5 rounded-lg border border-amber/20 bg-amber/[0.06] p-2.5 text-[11px] leading-relaxed text-frost/85">
          <p>To see {c.label}&apos;s GPU, VRAM and RAM here, install AETHER&apos;s hardware monitor on it. In PowerShell (<i>Run as administrator</i>):</p>
          <code className="block break-all rounded-md bg-black/30 px-2 py-1.5 font-mono text-[10.5px] text-glacier">{MONITOR_DOWNLOAD}</code>
          <code className="block break-all rounded-md bg-black/30 px-2 py-1.5 font-mono text-[10.5px] text-glacier">{MONITOR_RUN}</code>
        </div>
      )}

      {c.online && (
        <div className="space-y-1.5">
          <div className="text-[10.5px] uppercase tracking-[0.14em] text-mist">Loaded models</div>
          {c.loaded.length === 0
            ? <p className="text-[11px] text-mist/80">None right now. A model loads when you send it a message and unloads after a few idle minutes.</p>
            : c.loaded.map((m) => <LoadedRow key={m.name} m={m} />)}
        </div>
      )}
    </div>
  );
}

export function HardwareSection({ revision }: { revision: number }) {
  const hw = useLive(api.hardware, revision, () => 3000);
  return (
    <section>
      <PanelTitle title="Hardware" hint="What runs your local models, live: GPU, memory and what's loaded on each machine." />
      <div className="glass-inset space-y-2.5 rounded-2xl p-3">
        {!hw.data && !hw.error && <p className="text-xs text-mist">Reading hardware…</p>}
        {hw.error && <p className="text-xs text-coral">{hw.error.message}</p>}
        {hw.data?.map((c) => <MachineCard key={c.id} c={c} />)}
      </div>
    </section>
  );
}
