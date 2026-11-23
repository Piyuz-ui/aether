"use client";

/** Past conversations: reopen any of them and keep going. */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { MessageSquare } from "lucide-react";

import { api } from "@/lib/api";
import { popIn } from "@/lib/motion";
import { useLive } from "@/lib/useLive";

import { ConfirmDelete, Empty, ErrorNote, PanelTitle } from "./shared";

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function ChatsPanel({ revision, currentId, onOpen, onDeleted }: {
  revision: number;
  currentId: string | null;
  onOpen: (id: string) => void;
  onDeleted: (id: string) => void;
}) {
  const sessions = useLive(api.sessions, revision);
  const list = (sessions.data ?? []).filter((s) => s.turns > 0);
  return (
    <div>
      <PanelTitle title="Chats" hint="Every conversation is saved locally. Pick one to continue it." />
      {sessions.error && <ErrorNote message={sessions.error.message} hint={sessions.error.hint} />}
      {sessions.data && list.length === 0 && <Empty>No past chats yet.</Empty>}
      <div className="space-y-1.5">
        <AnimatePresence initial={false}>
          {list.map((s) => (
            <motion.div key={s.id} layout variants={popIn} initial="hidden" animate="show" exit="exit"
              className={clsx("group flex items-center gap-2 rounded-2xl px-2 py-1.5 transition-colors",
                s.id === currentId ? "bg-sky/[0.12] ring-1 ring-sky/30" : "hover:bg-white/[0.05]")}>
              <button type="button" onClick={() => onOpen(s.id)} className="flex min-w-0 flex-1 items-center gap-2.5 py-1 text-left">
                <MessageSquare className={clsx("size-4 shrink-0", s.id === currentId ? "text-glacier" : "text-mist")} />
                <span className="min-w-0">
                  <span className="block truncate text-sm text-frost">{s.title}</span>
                  <span className="text-[11px] text-mist">{s.turns} turn{s.turns === 1 ? "" : "s"} · {ago(s.updated)}</span>
                </span>
              </button>
              <ConfirmDelete onConfirm={async () => { await api.deleteSession(s.id); onDeleted(s.id); await sessions.reload(); }} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
