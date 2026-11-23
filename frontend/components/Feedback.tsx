"use client";

/**
 * "How's this going?" under each of a chat's first three replies: 👍 / 👎. A 👎 asks what went
 * wrong (one tap) and offers to ask again, or to try another model. Saved with the chat.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { RotateCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { createContext, useContext, useState } from "react";

import { api } from "@/lib/api";
import { jelly } from "@/lib/motion";
import type { AssistantMessage, Feedback as Rating } from "@/lib/types";

export const FEEDBACK_REPLIES = 3; // ask about the first this-many replies of a chat

type Ctx = {
  sessionId: string | null;
  retry: (turn: number) => void;
  rate: (id: string, f: Rating) => void;
  /** Switch to a free local model, then ask that reply's question again. */
  switchToFree?: (turn: number) => void;
};
export const FeedbackContext = createContext<Ctx>({ sessionId: null, retry: () => {}, rate: () => {} });

/** Cloud errors a free local model gets you past: no credit, quota/rate limit, key or setup problems. */
export const CLOUD_BLOCKED = /no credit left|rate limit or free quota|rejected the API key|isn't set up yet/i;

const REASONS = ["Wrong answer", "Too slow", "Didn't use a tool", "Misunderstood me"];

export function FeedbackRow({ m, turn }: { m: AssistantMessage; turn: number }) {
  const { sessionId, retry, rate } = useContext(FeedbackContext);
  const [thanks, setThanks] = useState(false);
  if (turn >= FEEDBACK_REPLIES || m.status !== "done" || !sessionId) return null;
  const given = m.feedback?.rating;

  async function send(rating: Rating["rating"], reason = "") {
    rate(m.id, { rating, reason });
    if (rating === "good") { setThanks(true); window.setTimeout(() => setThanks(false), 2200); }
    try { await api.feedback(sessionId!, turn, rating, reason, m.stats?.model ?? ""); } catch { /* not critical */ }
  }

  const btn = (rating: "good" | "bad") => (
    <motion.button type="button" whileTap={{ scale: 0.85 }} transition={jelly}
      onClick={() => void send(rating, rating === given ? m.feedback?.reason : "")}
      aria-label={rating === "good" ? "This is going well" : "This isn't going well"} aria-pressed={given === rating}
      className={clsx("grid size-6 place-items-center rounded-md transition-colors",
        given === rating
          ? rating === "good" ? "bg-seafoam/15 text-seafoam" : "bg-coral/15 text-coral"
          : "text-mist/70 hover:bg-white/[0.06] hover:text-frost")}>
      {rating === "good" ? <ThumbsUp className="size-3.5" /> : <ThumbsDown className="size-3.5" />}
    </motion.button>
  );

  return (
    <div className="pl-1">
      <div className="flex items-center gap-1.5 text-[11px] text-mist/80">
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={thanks ? "t" : given ?? "q"} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            {thanks ? "Thanks!" : given === "bad" ? "Sorry about that." : given === "good" ? "Glad it's going well." : "How's this going?"}
          </motion.span>
        </AnimatePresence>
        {btn("good")}
        {btn("bad")}
      </div>
      <AnimatePresence>
        {given === "bad" && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden">
            <div className="flex flex-wrap items-center gap-1.5 pt-1.5">
              {REASONS.map((r) => (
                <button key={r} type="button" onClick={() => void send("bad", r)}
                  className={clsx("rounded-full border px-2.5 py-0.5 text-[11px] transition-colors",
                    m.feedback?.reason === r ? "border-coral/40 bg-coral/10 text-coral" : "border-white/10 text-mist hover:text-frost")}>
                  {r}
                </button>
              ))}
              <button type="button" onClick={() => retry(turn)}
                className="inline-flex items-center gap-1 rounded-full border border-glacier/30 bg-sky/[0.08] px-2.5 py-0.5 text-[11px] text-glacier hover:text-frost">
                <RotateCcw className="size-3" />Ask again
              </button>
            </div>
            <p className="pt-1 text-[10.5px] text-mist/70">Or pick another brain in the model menu at the top, then ask again.</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
