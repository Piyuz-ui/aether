"use client";

/** Past conversations: reopen any of them and keep going, rename them, or let AETHER name them. */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Check, MessageSquare, Pencil, Sparkles, ThumbsDown, X } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { popIn } from "@/lib/motion";
import type { SessionSummary } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Spinner } from "../ui";
import { ConfirmDelete, Empty, ErrorNote, inputClass, PanelTitle } from "./shared";

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

const iconBtn = "grid size-7 shrink-0 place-items-center rounded-lg text-mist transition-colors hover:bg-white/[0.08] hover:text-frost";

function ChatRow({ s, current, onOpen, onDeleted, onChanged, toast }: {
  s: SessionSummary;
  current: boolean;
  onOpen: () => void;
  onDeleted: () => Promise<void>;
  onChanged: () => void;
  toast?: (t: string, b?: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(s.title);
  const [naming, setNaming] = useState(false);

  async function save() {
    const t = title.trim();
    setEditing(false);
    if (!t || t === s.title) { setTitle(s.title); return; }
    try { await api.renameSession(s.id, t); onChanged(); }
    catch (e) { setTitle(s.title); toast?.(e instanceof ApiError ? e.message : "Couldn't rename it"); }
  }

  async function autoName() {
    setNaming(true);
    try {
      const r = await api.retitleSession(s.id);
      setTitle(r.title);
      onChanged();
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      toast?.(err?.message ?? "Couldn't name it right now", err?.hint);
    } finally {
      setNaming(false);
    }
  }

  return (
    <motion.div layout variants={popIn} initial="hidden" animate="show" exit="exit"
      className={clsx("group flex items-center gap-1.5 rounded-2xl px-2 py-1.5 transition-colors",
        current ? "bg-sky/[0.12] ring-1 ring-sky/30" : "hover:bg-white/[0.05]")}>
      {editing ? (
        <form className="flex min-w-0 flex-1 items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120}
            onKeyDown={(e) => { if (e.key === "Escape") { setTitle(s.title); setEditing(false); } }}
            onFocus={(e) => e.currentTarget.select()} aria-label="Chat name" className={clsx(inputClass, "h-8 text-sm")} />
          <button type="submit" className={iconBtn} aria-label="Save name"><Check className="size-3.5" /></button>
          <button type="button" className={iconBtn} aria-label="Cancel" onClick={() => { setTitle(s.title); setEditing(false); }}>
            <X className="size-3.5" />
          </button>
        </form>
      ) : (
        <>
          <button type="button" onClick={onOpen} className="flex min-w-0 flex-1 items-center gap-2.5 py-1 text-left">
            <MessageSquare className={clsx("size-4 shrink-0", current ? "text-glacier" : "text-mist")} />
            <span className="min-w-0">
              <span className="flex items-center gap-1">
                <span className="truncate text-sm text-frost">{title}</span>
                {s.title_source === "ai" && <Sparkles className="size-3 shrink-0 text-periwinkle/70" aria-label="named by AETHER" />}
              </span>
              <span className="flex items-center gap-1.5 text-[11px] text-mist">
                {s.turns} turn{s.turns === 1 ? "" : "s"} · {ago(s.updated)}
                {(s.bad ?? 0) > 0 && <span className="inline-flex items-center gap-0.5 text-coral/80"><ThumbsDown className="size-2.5" />{s.bad}</span>}
              </span>
            </span>
          </button>
          <div className="flex items-center opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
            <button type="button" className={iconBtn} onClick={() => void autoName()} disabled={naming}
              aria-label="Let AETHER name this chat" title="Let AETHER name it">
              {naming ? <Spinner /> : <Sparkles className="size-3.5" />}
            </button>
            <button type="button" className={iconBtn} onClick={() => { setTitle(s.title); setEditing(true); }}
              aria-label="Rename this chat" title="Rename">
              <Pencil className="size-3.5" />
            </button>
          </div>
          <ConfirmDelete onConfirm={onDeleted} />
        </>
      )}
    </motion.div>
  );
}

export function ChatsPanel({ revision, currentId, onOpen, onDeleted, toast }: {
  revision: number;
  currentId: string | null;
  onOpen: (id: string) => void;
  onDeleted: (id: string) => void;
  toast?: (t: string, b?: string) => void;
}) {
  const sessions = useLive(api.sessions, revision);
  const list = (sessions.data ?? []).filter((s) => s.turns > 0);
  return (
    <div>
      <PanelTitle title="Chats" hint="Saved on this computer. AETHER names each chat by its topic; ✏️ to rename, ✨ to let it name it again." />
      {sessions.error && <ErrorNote message={sessions.error.message} hint={sessions.error.hint} />}
      {sessions.data && list.length === 0 && <Empty>No past chats yet.</Empty>}
      <div className="space-y-1.5">
        <AnimatePresence initial={false}>
          {list.map((s) => (
            <ChatRow key={`${s.id}:${s.title}`} s={s} current={s.id === currentId} toast={toast}
              onOpen={() => onOpen(s.id)} onChanged={() => void sessions.reload()}
              onDeleted={async () => { await api.deleteSession(s.id); onDeleted(s.id); await sessions.reload(); }} />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
