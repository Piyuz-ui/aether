"use client";

/** An agent step: a tool pill that expands (with a spring) into its arguments and output. */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  Ban, Brain, Calculator, CalendarClock, CalendarDays, CalendarPlus, Check, ChevronDown, CircleHelp,
  FileDown, FileText, FolderOpen, GraduationCap, Plug, Globe, Library, ListChecks, NotebookPen, ShieldAlert, Sparkles, SquareTerminal,
  Terminal, Trash2, Wrench, X, type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { filesIn, useOpenArtifact } from "@/lib/artifacts";
import { jelly, popIn } from "@/lib/motion";
import type { ThoughtStep, ToolStep } from "@/lib/types";

import { Spinner } from "./ui";

const ICONS: Record<string, LucideIcon> = {
  globe: Globe, "file-text": FileText, calculator: Calculator, "notebook-pen": NotebookPen,
  "folder-open": FolderOpen, "square-terminal": SquareTerminal, terminal: Terminal, "trash-2": Trash2,
  library: Library, "list-checks": ListChecks, "calendar-plus": CalendarPlus, "calendar-clock": CalendarClock,
  "calendar-days": CalendarDays, brain: Brain, wrench: Wrench, "circle-help": CircleHelp, "file-down": FileDown, "graduation-cap": GraduationCap, plug: Plug,
};

// Older transcripts don't carry icons, so fall back on the tool name.
const BY_NAME: Record<string, string> = {
  web_search: "globe", read_webpage: "file-text", python_calculator: "calculator", write_note: "notebook-pen",
  read_file: "file-text", list_files: "folder-open", run_python: "square-terminal",
  run_shell_command: "terminal", delete_file: "trash-2", search_knowledge_base: "library",
  list_documents: "library", add_task: "calendar-plus", manage_tasks: "list-checks", add_class: "calendar-clock",
  manage_timetable: "calendar-days", remember: "brain", recall_memory: "brain", forget: "brain",
  create_pdf: "file-down", make_quiz: "graduation-cap",
};

const pretty = (name: string) => name.includes("__") ? name.replace("__", " › ").replace(/_/g, " ") : name.replace(/_/g, " ");

// The argument that best describes a call, in order of preference.
const KEY_ARGS = ["title", "query", "expression", "path", "command", "url", "course", "fact", "action", "code"];

function summarize(args: Record<string, unknown>): string {
  const key = KEY_ARGS.find((k) => typeof args[k] === "string" || typeof args[k] === "number");
  const first = key ? args[key] : Object.values(args).find((v) => typeof v === "string" || typeof v === "number");
  const text = first === undefined ? "" : String(first).replace(/\s+/g, " ");
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

export function ToolPill({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(false);
  const openArtifact = useOpenArtifact();
  const artifact = step.status === "ok" && ["write_note", "create_pdf"].includes(step.name)
    ? filesIn(step.output)[0] : undefined;
  const Icon = ICONS[step.icon] ?? ICONS[BY_NAME[step.name] ?? "wrench"] ?? Wrench;
  const status = {
    running: { tone: "border-glacier/30 bg-glacier/[0.08]", badge: <Spinner /> },
    awaiting: { tone: "border-amber/40 bg-amber/[0.08]", badge: <ShieldAlert className="size-3.5 text-amber" /> },
    ok: { tone: "border-seafoam/25 bg-seafoam/[0.06]", badge: <Check className="size-3.5 text-seafoam" /> },
    error: { tone: "border-coral/30 bg-coral/[0.07]", badge: <X className="size-3.5 text-coral" /> },
    denied: { tone: "border-coral/30 bg-coral/[0.05]", badge: <Ban className="size-3.5 text-coral" /> },
  }[step.status];

  return (
    <motion.div layout variants={popIn} initial="hidden" animate="show" transition={jelly}
      className={clsx("overflow-hidden border backdrop-blur-md", status.tone, open ? "rounded-2xl" : "rounded-[20px]")}>
      <motion.button
        layout="position"
        type="button"
        onClick={() => setOpen((o) => !o)}
        whileTap={{ scale: 0.97 }}
        transition={jelly}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left"
      >
        <motion.span
          className="grid size-6 shrink-0 place-items-center rounded-lg bg-white/[0.07]"
          animate={step.status === "running" ? { rotate: [0, -8, 8, 0] } : { rotate: 0 }}
          transition={step.status === "running" ? { duration: 1.2, repeat: Infinity } : jelly}
        >
          <Icon className="size-3.5 text-glacier" />
        </motion.span>
        <span className="font-mono text-[12.5px] text-frost">{pretty(step.name)}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-mist">{summarize(step.args)}</span>
        {step.risk === "requires_approval" && step.status !== "ok" && (
          <span className="hidden text-[10px] uppercase tracking-wider text-amber sm:inline">gated</span>
        )}
        {step.duration !== undefined && step.status !== "running" && (
          <span className="font-mono text-[11px] text-mist/80">{step.duration.toFixed(2)}s</span>
        )}
        {status.badge}
        {artifact && (
          <span role="button" tabIndex={0} title="Preview"
            onClick={(e) => { e.stopPropagation(); openArtifact(artifact); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); openArtifact(artifact); } }}
            className="rounded-lg bg-glacier/15 px-2 py-0.5 font-sans text-[11px] font-medium text-glacier hover:bg-glacier/25">
            Open
          </span>
        )}
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={jelly}>
          <ChevronDown className="size-3.5 text-mist" />
        </motion.span>
      </motion.button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={jelly}
          >
            <div className="space-y-2 px-3 pb-3">
              <Section label="input">{JSON.stringify(step.args, null, 2)}</Section>
              {step.output !== undefined && (
                <Section label={step.status === "ok" ? "output" : "result"}>
                  {step.output.split("\n").filter((l) => !l.startsWith("[artifact] ")).join("\n")}
                </Section>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function Section({ label, children }: { label: string; children: string }) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-[0.14em] text-mist/70">{label}</div>
      <pre className="glass-inset soft-scroll max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl p-2.5 font-mono text-[11.5px] leading-relaxed text-frost/90">
        {children}
      </pre>
    </div>
  );
}

export function ThoughtLine({ step }: { step: ThoughtStep }) {
  return (
    <motion.div variants={popIn} initial="hidden" animate="show"
      className={clsx("flex gap-2 pl-1 text-[13px] italic leading-relaxed", step.retry ? "text-amber/90" : "text-periwinkle/90")}>
      <Sparkles className="mt-1 size-3 shrink-0 opacity-70" />
      <span>{step.text}</span>
    </motion.div>
  );
}
