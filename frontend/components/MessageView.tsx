"use client";

/** Chat messages: user bubbles, and assistant replies with their live agent trace. */
import clsx from "clsx";
import { motion } from "framer-motion";
import { AlertTriangle, Brain, Download, FileText, Gauge, Square } from "lucide-react";
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "@/lib/api";
import { jelly, popIn } from "@/lib/motion";
import type { AssistantMessage, Message } from "@/lib/types";

import { ThoughtLine, ToolPill } from "./ToolPill";

export const Markdown = memo(function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className={clsx("prose-aether text-frost/95", streaming && "caret")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

function Stats({ m }: { m: AssistantMessage }) {
  const s = m.stats;
  if (!s) return null;
  const items = [
    `${s.wall_s.toFixed(1)} s`,
    `${s.prompt_tokens.toLocaleString()} in · ${s.output_tokens.toLocaleString()} out`,
    `context ${Math.round(s.context_pct)}%`,
    `${Math.round(s.tokens_per_s)} tok/s`,
  ];
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10.5px] text-mist/75"
      title={`${s.model} · ${s.model_calls} model call(s) · ${s.context_used.toLocaleString()} of ${s.context_limit.toLocaleString()} context tokens`}>
      <Gauge className="size-3" />
      {items.map((t) => <span key={t}>{t}</span>)}
    </div>
  );
}

const FILE_RE = /\b((?:exports|notes)\/[^\s'"`),;]+?\.(?:pdf|md))\b/g;

/** Files the agent created this turn (PDFs, notes), from successful tool results. */
function createdFiles(m: AssistantMessage): string[] {
  const found = new Set<string>();
  for (const s of m.steps) {
    if (s.kind !== "tool" || s.status !== "ok" || !s.output) continue;
    if (!["create_pdf", "write_note"].includes(s.name)) continue;
    for (const match of s.output.matchAll(FILE_RE)) found.add(match[1]);
  }
  return [...found];
}

function FileChips({ files }: { files: string[] }) {
  if (!files.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {files.map((f) => {
        const isPdf = f.endsWith(".pdf");
        return (
          <motion.div key={f} variants={popIn} initial="hidden" animate="show"
            className="glass flex items-center gap-1 rounded-2xl py-1 pl-3 pr-1">
            <FileText className={clsx("size-4", isPdf ? "text-coral" : "text-periwinkle")} />
            <motion.a href={api.fileUrl(f)} target="_blank" rel="noreferrer" whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.95 }} transition={jelly}
              className="px-1.5 py-1 text-sm text-frost hover:text-glacier">{f.split("/").pop()}</motion.a>
            <motion.a href={api.fileUrl(f, true)} aria-label={`Download ${f}`} title="Download"
              whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} transition={jelly}
              className="grid size-8 place-items-center rounded-xl bg-gradient-to-br from-sky/30 to-periwinkle/25 text-glacier">
              <Download className="size-4" />
            </motion.a>
          </motion.div>
        );
      })}
    </div>
  );
}

function AssistantBubble({ m }: { m: AssistantMessage }) {
  const streaming = m.status === "streaming";
  const visible = m.content || m.draft;
  return (
    <div className="flex flex-col gap-2">
      {m.steps.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {m.steps.map((s, i) => s.kind === "tool"
            ? <ToolPill key={s.callId} step={s} />
            : <ThoughtLine key={`t${i}`} step={s} />)}
        </div>
      )}
      {m.thinking && streaming && (
        <div className="line-clamp-3 pl-1 text-[12.5px] italic text-mist/80">{m.thinking.slice(-400)}</div>
      )}
      {(visible || streaming) && m.status !== "error" && (
        <motion.div layout="position" className="glass rounded-3xl rounded-tl-lg px-5 py-4">
          {visible ? <Markdown text={visible} streaming={streaming} /> : <div className="caret h-5 text-mist" />}
          {m.status === "stopped" && (
            <div className="mt-2 flex items-center gap-1.5 text-xs text-amber"><Square className="size-3" /> stopped</div>
          )}
          <Stats m={m} />
        </motion.div>
      )}
      <FileChips files={createdFiles(m)} />
      {m.status === "error" && m.error && (
        <div className="rounded-3xl border border-coral/30 bg-coral/[0.08] px-5 py-4 text-sm">
          <div className="flex items-center gap-2 font-medium text-coral"><AlertTriangle className="size-4" />{m.error.message}</div>
          {m.error.hint && <div className="mt-1.5 font-mono text-xs text-frost/80">→ {m.error.hint}</div>}
        </div>
      )}
      {m.learned && m.learned.length > 0 && (
        <motion.div variants={popIn} initial="hidden" animate="show" className="flex flex-wrap gap-1.5 pl-1">
          {m.learned.map((f) => (
            <span key={f.id} className="inline-flex items-center gap-1.5 rounded-full border border-seafoam/25 bg-seafoam/[0.07] px-2.5 py-1 text-[11.5px] text-seafoam">
              <Brain className="size-3" /> remembered: {f.text}
            </span>
          ))}
        </motion.div>
      )}
    </div>
  );
}

export function MessageView({ message }: { message: Message }) {
  return (
    <motion.div variants={popIn} initial="hidden" animate="show" layout="position"
      className={clsx("flex w-full", message.role === "user" ? "justify-end" : "justify-start")}>
      {message.role === "user" ? (
        <div className="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg border border-sky/25 bg-gradient-to-br from-sky/25 to-periwinkle/15 px-4.5 py-3 text-[0.95rem] leading-relaxed text-frost shadow-[0_10px_30px_rgba(2,10,28,0.4)] backdrop-blur-xl">
          {message.content}
        </div>
      ) : (
        <div className="w-full max-w-[92%]"><AssistantBubble m={message} /></div>
      )}
    </motion.div>
  );
}
