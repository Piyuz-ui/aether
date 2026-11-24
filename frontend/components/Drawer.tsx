"use client";

/** Side glass drawer with spring tabs: Vault · Memory · Tasks · Usage · Settings (chats live on the left). */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Brain, ChartColumn, Library, ListChecks, Settings2, X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { jelly } from "@/lib/motion";

import { SidePanel } from "./SidePanel";

export type DrawerTab = "vault" | "memory" | "tasks" | "usage" | "settings";

const TABS: { id: DrawerTab; label: string; icon: LucideIcon }[] = [
  { id: "vault", label: "Vault", icon: Library },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "usage", label: "Usage", icon: ChartColumn },
  { id: "settings", label: "Settings", icon: Settings2 },
];

export function Drawer({ open, tab, onTab, onClose, children }: {
  open: boolean;
  tab: DrawerTab;
  onTab: (t: DrawerTab) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <SidePanel open={open} side="right" width={400} label="Side panel" onClose={onClose} className="flex flex-col">
            <div className="flex items-center gap-1 p-2">
              <nav className="relative flex flex-1 gap-0.5 rounded-2xl bg-abyss-950/35 p-1" role="tablist">
                {TABS.map((t) => (
                  <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => onTab(t.id)}
                    className={clsx("relative flex flex-1 flex-col items-center gap-0.5 rounded-xl px-1 py-1.5 text-[10.5px] transition-colors",
                      tab === t.id ? "text-frost" : "text-mist hover:text-frost")}>
                    {tab === t.id && (
                      <motion.span layoutId="drawer-tab" transition={jelly}
                        className="absolute inset-0 rounded-xl bg-gradient-to-br from-sky/30 to-periwinkle/20 shadow-[inset_1px_1px_0_rgba(224,242,254,0.25)]" />
                    )}
                    <motion.span className="relative" whileTap={{ scale: 0.85 }} transition={jelly}>
                      <t.icon className="size-4" />
                    </motion.span>
                    <span className="relative">{t.label}</span>
                  </button>
                ))}
              </nav>
              <button type="button" onClick={onClose} aria-label="Close panel"
                className="grid size-9 place-items-center rounded-xl text-mist hover:bg-white/[0.07] hover:text-frost">
                <X className="size-4" />
              </button>
            </div>
            <div className="soft-scroll min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-2">
              <AnimatePresence mode="wait" initial={false}>
                <motion.div key={tab} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}>
                  {children}
                </motion.div>
              </AnimatePresence>
            </div>
    </SidePanel>
  );
}
