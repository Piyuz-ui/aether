"use client";

/** Side glass drawer with spring tabs: Vault · Memory · Tasks · Chats · Settings. */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Brain, Library, ListChecks, MessagesSquare, Settings2, X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { jelly, settle } from "@/lib/motion";

export type DrawerTab = "vault" | "memory" | "tasks" | "chats" | "settings";

const TABS: { id: DrawerTab; label: string; icon: LucideIcon }[] = [
  { id: "vault", label: "Vault", icon: Library },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "chats", label: "Chats", icon: MessagesSquare },
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
    <AnimatePresence>
      {open && (
        <>
          {/* small screens: dim the chat behind the drawer */}
          <motion.div className="fixed inset-0 z-30 bg-abyss-950/50 backdrop-blur-[2px] lg:hidden"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.aside
            key="drawer"
            initial={{ x: 60, opacity: 0, scale: 0.97 }}
            animate={{ x: 0, opacity: 1, scale: 1 }}
            exit={{ x: 60, opacity: 0, scale: 0.97 }}
            transition={settle}
            className="glass fixed inset-y-3 right-3 z-40 flex w-[min(400px,calc(100vw-24px))] flex-col overflow-hidden rounded-[28px] lg:static lg:inset-auto lg:z-auto lg:my-3 lg:mr-3 lg:h-[calc(100dvh-24px)] lg:shrink-0"
            aria-label="Side panel"
          >
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
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
