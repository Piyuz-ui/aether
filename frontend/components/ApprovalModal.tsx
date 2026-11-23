"use client";

/** The safety gate: pops in on a spring when the agent wants to do something risky. */
import { AnimatePresence, motion } from "framer-motion";
import { ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { jelly, settle } from "@/lib/motion";
import type { Approval } from "@/lib/types";

import { JiggleButton, TiltCard } from "./ui";

export function ApprovalModal({ approval, onDecide }: {
  approval: Approval | null;
  onDecide: (approved: boolean) => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const denyRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setBusy(false);
    if (!approval) return;
    // Focus the safe choice; Escape denies.
    const t = setTimeout(() => denyRef.current?.focus(), 60);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") void decide(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval?.approvalId]);

  async function decide(ok: boolean) {
    if (busy) return;
    setBusy(true);
    await onDecide(ok);
  }

  return (
    <AnimatePresence>
      {approval && (
        <motion.div
          key={approval.approvalId}
          className="fixed inset-0 z-50 grid place-items-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="approval-title"
        >
          <motion.div className="absolute inset-0 bg-abyss-950/60 backdrop-blur-sm" onClick={() => void decide(false)} />
          <motion.div
            initial={{ scale: 0.82, y: 30, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.92, y: 10, opacity: 0, transition: { duration: 0.18 } }}
            transition={jelly}
            className="relative w-full max-w-lg"
          >
            <TiltCard intensity={4} className="glass-strong relative overflow-hidden rounded-[28px] p-6">
              {/* bioluminescent wave around the edge */}
              <motion.div aria-hidden className="pointer-events-none absolute inset-0 rounded-[28px]"
                style={{ boxShadow: "inset 0 0 0 1.5px rgba(252,211,77,0.45), 0 0 50px rgba(94,234,212,0.18)" }}
                animate={{ opacity: [0.45, 1, 0.45] }}
                transition={{ duration: 2.8, repeat: Infinity, ease: "easeInOut" }} />
              <div className="flex items-start gap-4">
                <motion.div className="grid size-12 shrink-0 place-items-center rounded-2xl bg-amber/15 text-amber"
                  animate={{ scale: [1, 1.08, 1] }} transition={{ duration: 2.2, repeat: Infinity }}>
                  <ShieldAlert className="size-6" />
                </motion.div>
                <div className="min-w-0">
                  <div className="text-[11px] uppercase tracking-[0.18em] text-amber/90">Safety check</div>
                  <h2 id="approval-title" className="mt-1 text-lg font-semibold text-frost">
                    AETHER wants to run <span className="font-mono text-glacier">{approval.name}</span>
                  </h2>
                  <p className="mt-2 text-sm leading-relaxed text-frost/85">{approval.summary}</p>
                </div>
              </div>
              <div className="mt-5">
                <div className="mb-1.5 text-[10px] uppercase tracking-[0.16em] text-mist/70">Exact action</div>
                <pre className="glass-inset soft-scroll max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-2xl p-3 font-mono text-[12px] leading-relaxed text-frost/90">
                  {JSON.stringify(approval.args, null, 2)}
                </pre>
              </div>
              <motion.div className="mt-6 flex justify-end gap-2.5" transition={settle}>
                <JiggleButton ref={denyRef} tone="danger" disabled={busy} onClick={() => void decide(false)}>
                  <ShieldX className="size-4" /> Deny
                </JiggleButton>
                <JiggleButton tone="approve" disabled={busy} onClick={() => void decide(true)}>
                  <ShieldCheck className="size-4" /> Approve
                </JiggleButton>
              </motion.div>
              <p className="mt-3 text-right text-[11px] text-mist/70">Esc denies · nothing runs until you choose</p>
            </TiltCard>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
