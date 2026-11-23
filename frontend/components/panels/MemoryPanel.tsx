"use client";

/** Memory Bank: everything AETHER remembers about you. View, edit, add, delete. */
import { AnimatePresence, motion } from "framer-motion";
import { Check, Pencil, Plus, X } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { jelly, popIn } from "@/lib/motion";
import type { Fact } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Chip, JiggleButton } from "../ui";
import { ConfirmDelete, Empty, ErrorNote, inputClass, PanelTitle } from "./shared";

const CATEGORIES: Fact["category"][] = ["profile", "academic", "goal", "preference", "general"];
const TONE = { profile: "glacier", academic: "periwinkle", goal: "seafoam", preference: "amber", general: "mist" } as const;

function FactRow({ fact, onChange }: { fact: Fact; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(fact.text);
  return (
    <motion.div layout variants={popIn} initial="hidden" animate="show" exit="exit"
      className="glass-inset group rounded-2xl p-3">
      {editing ? (
        <div className="space-y-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} className={inputClass} autoFocus />
          <div className="flex justify-end gap-1.5">
            <JiggleButton tone="ghost" className="px-2 py-1 text-xs" onClick={() => { setText(fact.text); setEditing(false); }}>
              <X className="size-3.5" /> Cancel
            </JiggleButton>
            <JiggleButton tone="primary" className="px-2.5 py-1 text-xs" disabled={text.trim().length < 3}
              onClick={async () => { await api.editFact(fact.id, { text }); setEditing(false); onChange(); }}>
              <Check className="size-3.5" /> Save
            </JiggleButton>
          </div>
        </div>
      ) : (
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm leading-relaxed text-frost">{fact.text}</p>
            <div className="mt-1.5 flex items-center gap-1.5">
              <Chip tone={TONE[fact.category]}>{fact.category}</Chip>
              <span className="text-[10.5px] text-mist/70">{fact.source === "auto" ? "learned" : "told"}</span>
            </div>
          </div>
          <button type="button" aria-label="Edit memory" title="Edit" onClick={() => setEditing(true)}
            className="grid size-7 place-items-center rounded-lg text-mist/70 opacity-60 hover:bg-white/10 hover:text-frost group-hover:opacity-100">
            <Pencil className="size-3.5" />
          </button>
          <ConfirmDelete label="Forget" onConfirm={async () => { await api.deleteFact(fact.id); onChange(); }} />
        </div>
      )}
    </motion.div>
  );
}

function TopicStats({ revision }: { revision: number }) {
  const stats = useLive(api.quizStats, revision, 20_000);
  if (!stats.data?.length) return null;
  return (
    <div className="glass-inset mb-3 rounded-2xl p-3">
      <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-mist">Quiz topics</div>
      <div className="space-y-2">
        {stats.data.slice(0, 8).map((t) => {
          const pct = Math.round(t.accuracy * 100);
          return (
            <div key={t.topic}>
              <div className="flex items-center justify-between text-xs">
                <span className="truncate text-frost">{t.topic}</span>
                <span className={t.weak ? "text-amber" : "text-mist"}>
                  {t.weak && "weak · "}{pct}% · {t.correct + t.wrong} answered
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                <motion.div className={t.weak ? "h-full rounded-full bg-amber" : "h-full rounded-full bg-gradient-to-r from-sky to-seafoam"}
                  initial={{ width: 0 }} animate={{ width: `${Math.max(4, pct)}%` }} transition={jelly} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function MemoryPanel({ revision }: { revision: number }) {
  const facts = useLive(api.facts, revision);
  const [text, setText] = useState("");
  const [category, setCategory] = useState<Fact["category"]>("general");

  async function add() {
    if (text.trim().length < 3) return;
    await api.addFact(text.trim().match(/^the user\b/i) ? text.trim() : `The user ${text.trim()}`, category);
    setText("");
    await facts.reload();
  }

  return (
    <div>
      <PanelTitle title="Memory Bank"
        hint="What AETHER knows about you. It learns from your chats, and you're always in control." />
      <div className="glass-inset mb-3 space-y-2 rounded-2xl p-3">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. prefers short answers with examples"
          onKeyDown={(e) => e.key === "Enter" && void add()} className={inputClass} aria-label="New memory" />
        <div className="flex items-center gap-2">
          <select value={category} onChange={(e) => setCategory(e.target.value as Fact["category"])}
            className={`${inputClass} w-auto py-1.5 text-xs`} aria-label="Category">
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <JiggleButton tone="primary" className="ml-auto px-3 py-1.5 text-xs" onClick={() => void add()} disabled={text.trim().length < 3}>
            <Plus className="size-3.5" /> Remember
          </JiggleButton>
        </div>
      </div>
      <TopicStats revision={revision} />
      {facts.error && <ErrorNote message={facts.error.message} hint={facts.error.hint} />}
      {facts.data?.length === 0 && <Empty>Nothing remembered yet. Tell AETHER about yourself!</Empty>}
      <div className="space-y-2">
        <AnimatePresence initial={false}>
          {facts.data?.map((f) => <FactRow key={`${f.id}-${f.updated}`} fact={f} onChange={() => void facts.reload()} />)}
        </AnimatePresence>
      </div>
    </div>
  );
}
