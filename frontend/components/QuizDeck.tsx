"use client";

/**
 * Interactive glass quiz cards rendered inside the chat. Multiple-choice cards glow or shake on
 * an answer; flashcards flip in 3D. Every answer is sent to the backend for weak-topic tracking.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Check, GraduationCap, RotateCcw, Sparkles, Trophy, X } from "lucide-react";
import { useEffect, useState } from "react";

import { API_BASE } from "@/lib/api";
import { jelly, settle } from "@/lib/motion";

import { JiggleButton, Spinner } from "./ui";

type Question =
  | { type: "mcq"; question: string; options: string[]; answer: number; explanation: string; topic: string }
  | { type: "flashcard"; question: string; answer: string; explanation: string; topic: string };

type Quiz = { id: number; title: string; questions: Question[] };

async function report(quizId: number, index: number, correct: boolean): Promise<{ weak: boolean; topic: string } | null> {
  try {
    const r = await fetch(`${API_BASE}/api/quizzes/${quizId}/answer`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ index, correct }),
    });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

export function QuizDeck({ quizId }: { quizId: number }) {
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<number[]>([]);
  const [pos, setPos] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [graded, setGraded] = useState<boolean | null>(null);
  const [results, setResults] = useState<Record<number, boolean>>({});
  const [weak, setWeak] = useState<string[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/quizzes/${quizId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("This quiz is no longer available."))))
      .then((q: Quiz) => { setQuiz(q); setOrder(q.questions.map((_, i) => i)); })
      .catch((e: Error) => setError(e.message));
  }, [quizId]);

  if (error) return <div className="glass-inset rounded-2xl px-4 py-3 text-sm text-coral">{error}</div>;
  if (!quiz) return <div className="flex items-center gap-2 px-1 text-sm text-mist"><Spinner /> Loading quiz…</div>;

  const done = pos >= order.length;
  const index = order[pos];
  const q = quiz.questions[index];
  const answered = q?.type === "mcq" ? picked !== null : graded !== null;

  async function answer(correct: boolean) {
    setResults((r) => ({ ...r, [index]: correct }));
    const res = await report(quiz!.id, index, correct);
    if (res?.weak) setWeak((w) => (w.includes(res.topic) ? w : [...w, res.topic]));
  }

  function next() {
    setPos((p) => p + 1);
    setPicked(null);
    setFlipped(false);
    setGraded(null);
  }

  function retryMissed() {
    const missed = order.filter((i) => results[i] === false);
    setOrder(missed.length ? missed : quiz!.questions.map((_, i) => i));
    setPos(0);
    setPicked(null);
    setFlipped(false);
    setGraded(null);
  }

  const score = Object.values(results).filter(Boolean).length;

  return (
    <div className="glass overflow-hidden rounded-3xl">
      <div className="flex items-center gap-2.5 border-b border-white/[0.07] px-4 py-3">
        <span className="grid size-8 place-items-center rounded-xl bg-gradient-to-br from-seafoam/25 to-sky/20 text-seafoam">
          <GraduationCap className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-frost">{quiz.title}</div>
          <div className="text-[11px] text-mist">{done ? "Finished" : `Question ${pos + 1} of ${order.length}`}</div>
        </div>
        <div className="flex gap-1" aria-hidden>
          {order.map((i, n) => (
            <motion.span key={`${i}-${n}`} layout transition={jelly}
              className={clsx("h-1.5 rounded-full",
                n === pos && !done ? "w-5 bg-glacier" : "w-1.5",
                n !== pos || done ? (results[i] === true ? "bg-seafoam" : results[i] === false ? "bg-coral" : "bg-white/15") : "")} />
          ))}
        </div>
      </div>

      <div className="p-4" style={{ perspective: 1100 }}>
        <AnimatePresence mode="wait" initial={false}>
          {done ? (
            <motion.div key="done" initial={{ opacity: 0, scale: 0.92 }} animate={{ opacity: 1, scale: 1 }} transition={jelly}
              className="py-3 text-center">
              <motion.div className="mx-auto grid size-14 place-items-center rounded-2xl bg-amber/15 text-amber"
                animate={{ rotate: [0, -8, 8, 0], scale: [1, 1.08, 1] }} transition={{ duration: 1.2 }}>
                <Trophy className="size-7" />
              </motion.div>
              <div className="mt-3 text-2xl font-semibold text-frost">{score} / {Object.keys(results).length}</div>
              <div className="mt-1 text-sm text-mist">
                {score === Object.keys(results).length ? "Perfect! Every answer right." : "Nice work. Missed ones are worth a second look."}
              </div>
              {weak.length > 0 && (
                <div className="mx-auto mt-4 max-w-sm rounded-2xl border border-amber/25 bg-amber/[0.07] px-3 py-2.5 text-left text-xs text-amber">
                  <div className="font-medium">Added to your Memory Bank as weak topics:</div>
                  <div className="mt-0.5 text-frost/85">{weak.join(" · ")}</div>
                </div>
              )}
              <div className="mt-5 flex justify-center gap-2">
                <JiggleButton onClick={retryMissed}><RotateCcw className="size-4" />
                  {Object.values(results).some((v) => !v) ? "Retry missed" : "Go again"}</JiggleButton>
              </div>
            </motion.div>
          ) : q.type === "mcq" ? (
            <motion.div key={`q${pos}`} initial={{ opacity: 0, x: 40 }} animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -40 }} transition={settle}>
              <div className="mb-1 text-[10.5px] uppercase tracking-[0.14em] text-periwinkle">{q.topic}</div>
              <p className="text-[15px] font-medium leading-relaxed text-frost">{q.question}</p>
              <div className="mt-4 grid gap-2">
                {q.options.map((opt, i) => {
                  const isAnswer = i === q.answer;
                  const chosen = picked === i;
                  const state = picked === null ? "idle" : isAnswer ? "right" : chosen ? "wrong" : "dim";
                  return (
                    <motion.button key={i} type="button" disabled={picked !== null}
                      onClick={() => { setPicked(i); void answer(isAnswer); }}
                      whileHover={picked === null ? { scale: 1.015, x: 3 } : undefined}
                      whileTap={picked === null ? { scale: 0.97 } : undefined}
                      animate={state === "wrong" ? { x: [0, -8, 8, -5, 5, 0] } : state === "right" && chosen ? { scale: [1, 1.04, 1] } : {}}
                      transition={state === "wrong" ? { duration: 0.42 } : jelly}
                      className={clsx("flex items-center gap-3 rounded-2xl border px-3.5 py-2.5 text-left text-sm transition-colors",
                        state === "idle" && "border-white/10 bg-white/[0.04] text-frost hover:border-glacier/40 hover:bg-white/[0.07]",
                        state === "right" && "border-seafoam/50 bg-seafoam/[0.12] text-frost shadow-[0_0_24px_rgba(94,234,212,0.18)]",
                        state === "wrong" && "border-coral/50 bg-coral/[0.1] text-frost",
                        state === "dim" && "border-white/[0.06] bg-transparent text-mist/70")}>
                      <span className={clsx("grid size-6 shrink-0 place-items-center rounded-lg font-mono text-[11px]",
                        state === "right" ? "bg-seafoam text-abyss-950" : state === "wrong" ? "bg-coral text-abyss-950" : "bg-white/[0.08] text-mist")}>
                        {state === "right" ? <Check className="size-3.5" /> : state === "wrong" ? <X className="size-3.5" /> : String.fromCharCode(65 + i)}
                      </span>
                      <span>{opt}</span>
                    </motion.button>
                  );
                })}
              </div>
              <Explanation show={answered} text={q.explanation} correct={picked === q.answer} onNext={next}
                last={pos === order.length - 1} />
            </motion.div>
          ) : (
            <motion.div key={`f${pos}`} initial={{ opacity: 0, x: 40 }} animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -40 }} transition={settle}>
              <motion.button type="button" onClick={() => setFlipped((f) => !f)} aria-label="Flip card"
                animate={{ rotateY: flipped ? 180 : 0 }} transition={jelly}
                style={{ transformStyle: "preserve-3d" }}
                className="relative block h-44 w-full text-left">
                <span className="glass-inset absolute inset-0 flex flex-col justify-center rounded-2xl p-5" style={{ backfaceVisibility: "hidden" }}>
                  <span className="text-[10.5px] uppercase tracking-[0.14em] text-periwinkle">{q.topic}</span>
                  <span className="mt-1.5 text-[15px] font-medium leading-relaxed text-frost">{q.question}</span>
                  <span className="mt-3 flex items-center gap-1 text-[11px] text-mist"><Sparkles className="size-3" /> Tap to flip</span>
                </span>
                <span className="absolute inset-0 flex flex-col justify-center rounded-2xl border border-seafoam/30 bg-seafoam/[0.08] p-5"
                  style={{ backfaceVisibility: "hidden", transform: "rotateY(180deg)" }}>
                  <span className="text-[10.5px] uppercase tracking-[0.14em] text-seafoam">Answer</span>
                  <span className="mt-1.5 text-[15px] leading-relaxed text-frost">{q.answer}</span>
                </span>
              </motion.button>
              {flipped && graded === null && (
                <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={jelly}
                  className="mt-3 flex justify-center gap-2">
                  <JiggleButton tone="danger" onClick={() => { setGraded(false); void answer(false); }}><X className="size-4" /> Missed it</JiggleButton>
                  <JiggleButton tone="approve" onClick={() => { setGraded(true); void answer(true); }}><Check className="size-4" /> Got it</JiggleButton>
                </motion.div>
              )}
              <Explanation show={graded !== null} text={q.explanation} correct={!!graded} onNext={next}
                last={pos === order.length - 1} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function Explanation({ show, text, correct, onNext, last }: {
  show: boolean; text: string; correct: boolean; onNext: () => void; last: boolean;
}) {
  return (
    <AnimatePresence>
      {show && (
        <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} transition={jelly}
          className="overflow-hidden">
          <div className="mt-3 flex items-end gap-3">
            <p className={clsx("flex-1 text-[13px] leading-relaxed", correct ? "text-seafoam" : "text-frost/85")}>
              <span className="font-medium">{correct ? "Correct! " : "Not quite. "}</span>{text}
            </p>
            <JiggleButton tone="primary" onClick={onNext} className="shrink-0">
              {last ? "Finish" : "Next"} <ArrowRight className="size-4" />
            </JiggleButton>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
