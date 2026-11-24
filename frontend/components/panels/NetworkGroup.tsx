"use client";

/**
 * Settings → Network & hardware: GPU computers on your network, their addresses, connection
 * and hardware. Folded away by default (set it up once, rarely touched), with a one-line status.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, MonitorSmartphone } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { useLive } from "@/lib/useLive";

import { HardwareSection } from "./HardwareSection";
import { HostsSection } from "./HostsSection";

export function NetworkGroup({ revision, onChanged, toast }: {
  revision: number;
  onChanged: () => void;
  toast: (t: string, b?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const hosts = useLive(api.hosts, revision, () => (open ? undefined : 30_000));
  const list = hosts.data ?? [];
  const summary = !hosts.data ? "…" : list.length === 0 ? "No other computers yet"
    : list.map((h) => `${h.name} · ${h.online ? `online${h.route === "tailscale" ? " · via Tailscale" : ""}` : "offline"}`).join(", ");

  return (
    <section>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="glass-inset flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-left transition-colors hover:bg-white/[0.03]">
        <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-white/[0.05] text-glacier">
          <MonitorSmartphone className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-frost">Network &amp; hardware</div>
          <div className="truncate text-[11px] text-mist">
            {list.some((h) => h.online) && <span className="mr-1 inline-block size-1.5 rounded-full bg-seafoam align-middle" />}
            {summary}
          </div>
        </div>
        <motion.span animate={{ rotate: open ? 180 : 0 }} className={clsx("text-mist")}>
          <ChevronDown className="size-4" />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.25 }} className="overflow-hidden">
            <div className="space-y-6 pl-2 pt-5">
              <HostsSection revision={revision} toast={toast} onChanged={onChanged} />
              <HardwareSection revision={revision} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
