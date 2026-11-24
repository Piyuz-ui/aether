"use client";

/**
 * The left sidebar: only your chats. New chat, search, and every conversation grouped by date
 * (rename ✏️ / let AETHER name it ✨ / delete on each). Docked on wide screens, a slide-over on
 * small ones.
 */
import { AnimatePresence } from "framer-motion";
import { PanelLeftClose, Plus, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { SessionSummary } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { ChatRow } from "./panels/ChatsPanel";
import { SidePanel } from "./SidePanel";
import { Empty, ErrorNote } from "./panels/shared";

function group(sessions: SessionSummary[]): { label: string; items: SessionSummary[] }[] {
  const day = 86_400;
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const today = start.getTime() / 1000;
  const buckets: [string, (t: number) => boolean][] = [
    ["Today", (t) => t >= today],
    ["Yesterday", (t) => t >= today - day],
    ["Last 7 days", (t) => t >= today - 7 * day],
    ["Last 30 days", (t) => t >= today - 30 * day],
    ["Earlier", () => true],
  ];
  const out = buckets.map(([label]) => ({ label, items: [] as SessionSummary[] }));
  for (const s of sessions) out[buckets.findIndex(([, test]) => test(s.updated))].items.push(s);
  return out.filter((g) => g.items.length);
}

export function ChatSidebar({ open, onClose, revision, currentId, onOpen, onNew, onDeleted, toast }: {
  open: boolean;
  onClose: () => void;
  revision: number;
  currentId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
  onDeleted: (id: string) => void;
  toast: (t: string, b?: string) => void;
}) {
  const sessions = useLive(api.sessions, revision);
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = (sessions.data ?? []).filter((s) => s.turns > 0 && (!q || s.title.toLowerCase().includes(q)));
    return group(list);
  }, [sessions.data, query]);

  const body = (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 px-3 pb-2 pt-3">
        <span className="flex-1 pl-1 text-[13px] font-semibold tracking-wide text-frost">Chats</span>
        <button type="button" onClick={onClose} aria-label="Hide chats" title="Hide chats"
          className="grid size-8 place-items-center rounded-lg text-mist hover:bg-white/[0.07] hover:text-frost">
          <PanelLeftClose className="hidden size-4 lg:block" />
          <X className="size-4 lg:hidden" />
        </button>
      </div>
      <div className="space-y-2 px-3 pb-2">
        <button type="button" onClick={onNew}
          className="flex h-9 w-full items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 text-sm text-frost transition-colors hover:bg-white/[0.08]">
          <Plus className="size-4 text-glacier" /> New chat
        </button>
        <label className="relative block">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-mist" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats" aria-label="Search chats"
            className="h-8 w-full rounded-xl border border-white/[0.07] bg-abyss-950/40 pl-8 pr-2 text-[12.5px] text-frost placeholder:text-mist/55 focus:border-glacier/40 focus:outline-none" />
        </label>
      </div>
      <div className="soft-scroll min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {sessions.error && <div className="px-1"><ErrorNote message={sessions.error.message} hint={sessions.error.hint} /></div>}
        {sessions.data && groups.length === 0 && (
          <div className="px-1"><Empty>{query ? "No chats match." : "Your chats will show up here."}</Empty></div>
        )}
        {groups.map((g) => (
          <div key={g.label} className="mb-2">
            <div className="px-2 pb-1 pt-2 text-[10.5px] uppercase tracking-[0.14em] text-mist/80">{g.label}</div>
            <div className="space-y-0.5">
              <AnimatePresence initial={false}>
                {g.items.map((s) => (
                  <ChatRow key={`${s.id}:${s.title}`} s={s} current={s.id === currentId} toast={toast}
                    onOpen={() => { onOpen(s.id); if (window.innerWidth < 1024) onClose(); }}
                    onChanged={() => void sessions.reload()}
                    onDeleted={async () => { await api.deleteSession(s.id); onDeleted(s.id); await sessions.reload(); }} />
                ))}
              </AnimatePresence>
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <SidePanel open={open} side="left" width={270} sheetWidth={290} label="Chats" onClose={onClose} initial={false}>
      {body}
    </SidePanel>
  );
}
