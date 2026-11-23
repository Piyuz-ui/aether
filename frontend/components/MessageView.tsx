"use client";

/** Chat messages: user bubbles, and assistant replies with their live agent trace. */
import clsx from "clsx";
import { motion } from "framer-motion";
import { AlertTriangle, Brain, Cpu, Download, FileText, Square, Volume2 } from "lucide-react";
import { memo, useContext } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "@/lib/api";
import { filesIn, useOpenArtifact } from "@/lib/artifacts";
import { useVoice } from "@/lib/useNeuralVoice";
import { jelly, popIn } from "@/lib/motion";
import type { AssistantMessage, Message } from "@/lib/types";

import { QuizDeck } from "./QuizDeck";
import { useLandingId } from "./SendFlight";
import { ChartCard, chartsIn } from "./ChartCard";
import { CLOUD_BLOCKED, FeedbackContext, FeedbackRow } from "./Feedback";
import { GpuLive } from "./GpuLive";
import { TelemetryChip } from "./Telemetry";
import { ThoughtLine, ToolPill } from "./ToolPill";
import { Spinner } from "./ui";

// Charts are shown as cards above the reply; models sometimes also embed them as ![](artifacts/…).
const hideArtifactImages = (text: string) => text.replace(/!\[[^\]]*\]\(\/?artifacts\/[^)\s]+\)\s*/g, "");

export const Markdown = memo(function Markdown({ text: raw, streaming }: { text: string; streaming?: boolean }) {
  const text = hideArtifactImages(raw);
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

/** Files the agent created this turn (PDFs, notes), from successful tool results. */
function createdFiles(m: AssistantMessage): string[] {
  const found = new Set<string>();
  for (const s of m.steps) {
    if (s.kind !== "tool" || s.status !== "ok") continue;
    if (!["create_pdf", "write_note"].includes(s.name)) continue;
    filesIn(s.output).forEach((f) => found.add(f));
  }
  return [...found];
}

/** What gets read aloud: the answer, or a short line when a quiz card is shown instead. */
export function spokenText(m: AssistantMessage): string {
  return quizIds(m).length && m.content.length > 220 ? "Your quiz is ready. Tap an answer to begin." : m.content;
}

function ListenButton({ id, text }: { id: string; text: string }) {
  const voice = useVoice();
  const playing = voice.speakingId === id;
  const loading = voice.loadingId === id;
  return (
    <motion.button type="button" whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.92 }} transition={jelly}
      onClick={() => { voice.unlock(); if (playing || loading) voice.stop(); else voice.speak(id, text); }}
      aria-label={playing ? "Stop reading" : "Read this reply aloud"} title={playing ? "Stop" : "Listen"}
      className={clsx("mt-3 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors",
        playing ? "border-seafoam/40 bg-seafoam/[0.12] text-seafoam" : "border-white/[0.08] bg-abyss-950/30 text-mist hover:border-glacier/30 hover:text-frost")}>
      {loading ? <Spinner className="size-3" /> : playing ? <Equalizer /> : <Volume2 className="size-3.5" />}
      {playing ? "Stop" : loading ? "Preparing" : "Listen"}
    </motion.button>
  );
}

function Equalizer() {
  return (
    <span className="flex h-3 items-end gap-[2px]" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <motion.span key={i} className="w-[2px] rounded-full bg-seafoam"
          animate={{ height: ["30%", "100%", "45%", "85%", "30%"] }}
          transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.12, ease: "easeInOut" }} />
      ))}
    </span>
  );
}

function quizIds(m: AssistantMessage): number[] {
  return m.steps.flatMap((s) => {
    if (s.kind !== "tool" || s.name !== "make_quiz" || s.status !== "ok") return [];
    const id = s.output?.match(/Quiz #(\d+)/)?.[1];
    return id ? [Number(id)] : [];
  });
}

function FileChips({ files }: { files: string[] }) {
  const open = useOpenArtifact();
  if (!files.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {files.map((f) => {
        const isPdf = f.endsWith(".pdf");
        return (
          <motion.div key={f} variants={popIn} initial="hidden" animate="show"
            className="glass flex items-center gap-1 rounded-2xl py-1 pl-3 pr-1">
            <FileText className={clsx("size-4", isPdf ? "text-coral" : "text-periwinkle")} />
            <motion.button type="button" onClick={() => open(f)} whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.95 }} transition={jelly} title="Preview"
              className="px-1.5 py-1 text-sm text-frost hover:text-glacier">{f.split("/").pop()}</motion.button>
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

function AssistantBubble({ m, turn }: { m: AssistantMessage; turn: number }) {
  const { switchToFree } = useContext(FeedbackContext);
  const charts = chartsIn(m.steps);
  const streaming = m.status === "streaming";
  const quizzes = quizIds(m);
  // Small models often repeat the quiz as text even though the cards show it: keep it short.
  const visible = quizzes.length && !streaming && m.content.length > 220
    ? "Your quiz is ready. Tap an answer to begin. 👇"
    : m.content || m.draft;
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
      {charts.length > 0 && (
        <div className={clsx("grid gap-2.5", charts.length > 1 && "sm:grid-cols-2")}>
          {charts.map((a) => <ChartCard key={a.path} a={a} />)}
        </div>
      )}
      {(visible || streaming) && m.status !== "error" && (
        <motion.div layout="position" className="glass rounded-3xl rounded-tl-lg px-5 py-4">
          {visible ? <Markdown text={visible} streaming={streaming} /> : <div className="caret h-5 text-mist" />}
          {m.status === "stopped" && (
            <div className="mt-2 flex items-center gap-1.5 text-xs text-amber"><Square className="size-3" /> stopped</div>
          )}
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div className="flex min-w-0 flex-col gap-1">
              <GpuLive active={streaming} model={m.model} />
              <TelemetryChip m={m} />
            </div>
            {m.status === "done" && m.content && <ListenButton id={m.id} text={spokenText(m)} />}
          </div>
        </motion.div>
      )}
      {quizzes.map((id) => <QuizDeck key={id} quizId={id} />)}
      <FeedbackRow m={m} turn={turn} />
      <FileChips files={createdFiles(m)} />
      {m.status === "error" && m.error && (
        <div className="rounded-3xl border border-coral/30 bg-coral/[0.08] px-5 py-4 text-sm">
          <div className="flex items-center gap-2 font-medium text-coral"><AlertTriangle className="size-4" />{m.error.message}</div>
          {m.error.hint && <div className="mt-1.5 font-mono text-xs text-frost/80">→ {m.error.hint}</div>}
          {CLOUD_BLOCKED.test(m.error.message) && switchToFree && (
            <button type="button" onClick={() => switchToFree(turn)}
              className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-seafoam/30 bg-seafoam/[0.08] px-3 py-1 text-xs text-seafoam transition-colors hover:bg-seafoam/[0.15]">
              <Cpu className="size-3.5" /> Switch to a free model &amp; ask again
            </button>
          )}
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

/** `turn`: for a reply, its index among the chat's replies (0 = first). */
export function MessageView({ message, turn = 0 }: { message: Message; turn?: number }) {
  const landing = useLandingId();
  return (
    <motion.div variants={popIn} initial={landing === message.id ? "show" : "hidden"} animate="show" layout="position"
      className={clsx("flex w-full", message.role === "user" ? "justify-end" : "justify-start")}>
      {message.role === "user" ? (
        <div className="flex max-w-[85%] flex-col items-end gap-2">
          {message.images && message.images.length > 0 && (
            <div className="flex flex-wrap justify-end gap-2">
              {message.images.map((src, i) => (
                <motion.img key={i} src={src} alt={`Attached image ${i + 1}`} variants={popIn} initial="hidden" animate="show"
                  className="max-h-48 max-w-[260px] rounded-2xl border border-white/15 object-cover shadow-[0_10px_30px_rgba(2,10,28,0.45)]" />
              ))}
            </div>
          )}
          {!message.images?.length && !!message.imageCount && (
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-2.5 py-0.5 text-[11px] text-mist">
              🖼 {message.imageCount} image{message.imageCount > 1 ? "s" : ""} attached
            </span>
          )}
        <div data-bubble={message.id} style={landing === message.id ? { opacity: 0 } : undefined}
          className="max-w-full whitespace-pre-wrap rounded-3xl rounded-br-lg border border-sky/25 bg-gradient-to-br from-sky/25 to-periwinkle/15 px-4.5 py-3 text-[0.95rem] leading-relaxed text-frost shadow-[0_10px_30px_rgba(2,10,28,0.4)] backdrop-blur-xl">
          {message.content}
        </div>
        </div>
      ) : (
        <div className="w-full max-w-[92%]"><AssistantBubble m={message} turn={turn} /></div>
      )}
    </motion.div>
  );
}
