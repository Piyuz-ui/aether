"use client";

/** Active tasks, deadlines and the weekly timetable, kept in sync with the agent. */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { CalendarClock, Check, Plus } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { jelly, popIn } from "@/lib/motion";
import type { Task } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Chip, JiggleButton } from "../ui";
import { ConfirmDelete, Empty, ErrorNote, inputClass, PanelTitle } from "./shared";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function dueTone(t: Task) {
  if (t.overdue) return "coral";
  if (t.when.startsWith("due today") || t.when.startsWith("due tomorrow")) return "amber";
  return "glacier";
}

function TaskRow({ task, onChange }: { task: Task; onChange: () => void }) {
  const [done, setDone] = useState(false);
  return (
    <motion.div layout variants={popIn} initial="hidden" animate="show" exit="exit"
      className="glass-inset flex items-center gap-3 rounded-2xl px-3 py-2.5">
      <motion.button
        type="button"
        aria-label={`Mark "${task.title}" done`}
        onClick={async () => { setDone(true); await api.editTask(task.id, { done: true }); setTimeout(onChange, 350); }}
        whileTap={{ scale: 0.8 }}
        transition={jelly}
        className={clsx("grid size-5 shrink-0 place-items-center rounded-md border transition-colors",
          done ? "border-seafoam bg-seafoam text-abyss-950" : "border-mist/40 hover:border-seafoam")}
      >
        <AnimatePresence>
          {done && (
            <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={jelly}>
              <Check className="size-3.5" strokeWidth={3} />
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>
      <div className="min-w-0 flex-1">
        <div className={clsx("truncate text-sm", done ? "text-mist line-through" : "text-frost")}>{task.title}</div>
        <div className="mt-1 flex items-center gap-1.5">
          <Chip tone={dueTone(task)}>{task.when}</Chip>
          <span className="text-[10.5px] uppercase tracking-wider text-mist/70">{task.kind}</span>
        </div>
      </div>
      <ConfirmDelete onConfirm={async () => { await api.deleteTask(task.id); onChange(); }} />
    </motion.div>
  );
}

export function TasksPanel({ revision }: { revision: number }) {
  const tasks = useLive(() => api.tasks(), revision, 60_000);
  const classes = useLive(api.timetable, revision);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [kind, setKind] = useState<Task["kind"]>("task");
  const [err, setErr] = useState<ApiError | null>(null);

  async function add() {
    if (!title.trim()) return;
    try {
      await api.addTask(title.trim(), due.trim(), kind);
      setTitle("");
      setDue("");
      setErr(null);
      await tasks.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, String(e)));
    }
  }

  const today = (new Date().getDay() + 6) % 7; // Monday = 0, like the backend
  const byDay = DAYS.map((name, i) => ({ name, i, items: (classes.data ?? []).filter((c) => c.weekday === i) }))
    .filter((d) => d.items.length);

  return (
    <div className="space-y-6">
      <section>
        <PanelTitle title="Tasks & deadlines" hint="Dates can be natural: “next Friday 5pm”, “in 3 days”, “Dec 4”." />
        <div className="glass-inset mb-3 space-y-2 rounded-2xl p-3">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What's due?"
            onKeyDown={(e) => e.key === "Enter" && void add()} className={inputClass} aria-label="Task title" />
          <div className="flex gap-2">
            <input value={due} onChange={(e) => setDue(e.target.value)} placeholder="When? (optional)"
              onKeyDown={(e) => e.key === "Enter" && void add()} className={inputClass} aria-label="Due" />
            <select value={kind} onChange={(e) => setKind(e.target.value as Task["kind"])}
              className={`${inputClass} w-auto text-xs`} aria-label="Kind">
              {["task", "deadline", "exam", "reminder"].map((k) => <option key={k}>{k}</option>)}
            </select>
            <JiggleButton tone="primary" className="px-3 text-xs" onClick={() => void add()} disabled={!title.trim()} aria-label="Add task">
              <Plus className="size-4" />
            </JiggleButton>
          </div>
          {err && <ErrorNote message={err.message} />}
        </div>
        {tasks.error && <ErrorNote message={tasks.error.message} hint={tasks.error.hint} />}
        {tasks.data?.length === 0 && <Empty>No open tasks. 🎉</Empty>}
        <div className="space-y-2">
          <AnimatePresence initial={false}>
            {tasks.data?.map((t) => <TaskRow key={t.id} task={t} onChange={() => void tasks.reload()} />)}
          </AnimatePresence>
        </div>
      </section>

      <section>
        <PanelTitle title="Timetable" hint="Tell AETHER: “I have DBMS every Monday 9 to 10 in room 204”." />
        {byDay.length === 0 && <Empty>No classes yet.</Empty>}
        <div className="space-y-3">
          {byDay.map((d) => (
            <div key={d.name}>
              <div className={clsx("mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.14em]",
                d.i === today ? "text-seafoam" : "text-mist/80")}>
                <CalendarClock className="size-3" /> {d.name}{d.i === today && " · today"}
              </div>
              <div className="space-y-1.5">
                {d.items.map((c) => (
                  <motion.div key={c.id} layout variants={popIn} initial="hidden" animate="show"
                    className="glass-inset flex items-center gap-3 rounded-xl px-3 py-2">
                    <span className="font-mono text-xs text-glacier">{c.start}–{c.end}</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-frost">{c.course}</span>
                    {c.location && <span className="text-xs text-mist">{c.location}</span>}
                    <ConfirmDelete onConfirm={async () => { await api.deleteClass(c.id); await classes.reload(); }} />
                  </motion.div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
