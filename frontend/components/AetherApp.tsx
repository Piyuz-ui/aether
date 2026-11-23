"use client";

import { AnimatePresence, motion, useTransform } from "framer-motion";
import { AlertTriangle, BookOpen, CalendarRange, Calculator, Globe, PanelRight, Plus, RefreshCw, UploadCloud, Waves } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { jelly, popIn } from "@/lib/motion";
import { usePrefs } from "@/lib/prefs";
import { useReminders } from "@/lib/reminders";
import type { AgentState, Health } from "@/lib/types";
import { useAgent } from "@/lib/useAgent";
import { useAttachments } from "@/lib/useAttachments";
import { speak, useWakeWord } from "@/lib/voice";

import { AgentOrb, STATE_LABEL } from "./AgentOrb";
import { ApprovalModal } from "./ApprovalModal";
import { Composer, type ComposerHandle } from "./Composer";
import { Drawer, type DrawerTab } from "./Drawer";
import { MessageView } from "./MessageView";
import { ChatsPanel } from "./panels/ChatsPanel";
import { MemoryPanel } from "./panels/MemoryPanel";
import { SettingsPanel } from "./panels/SettingsPanel";
import { TasksPanel } from "./panels/TasksPanel";
import { VaultPanel } from "./panels/VaultPanel";
import { IconButton, TiltCard } from "./ui";

type Toast = { id: number; title: string; body?: string };

const SUGGESTIONS = [
  { icon: Globe, title: "Research & save", text: "Research what's new in Python 3.15 and save a short note with sources." },
  { icon: CalendarRange, title: "Plan my week", text: "What deadlines and classes do I have this week? Help me plan around them." },
  { icon: BookOpen, title: "Study my notes", text: "Quiz me on my uploaded notes, one question at a time." },
  { icon: Calculator, title: "Quick maths", text: "What's 17.5% of 2480, split between 3 people?" },
];

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Still up?" : h < 12 ? "Good morning." : h < 17 ? "Good afternoon." : "Good evening.";
}

function EmptyState({ state, onPick }: { state: AgentState; onPick: (t: string) => void }) {
  return (
    <motion.div variants={popIn} initial="hidden" animate="show" className="mx-auto flex max-w-2xl flex-col items-center px-4 pt-[8vh] text-center">
      <AgentOrb state={state} size={132} />
      <h1 className="mt-10 text-3xl font-semibold tracking-tight text-frost sm:text-4xl">{greeting()}</h1>
      <p className="mt-3 max-w-md text-[15px] leading-relaxed text-mist">
        I&apos;m AETHER, your local study, research and productivity agent. I can search the web, read your
        notes, track deadlines and remember what matters, all on this computer.
      </p>
      <div className="mt-9 grid w-full gap-3 sm:grid-cols-2">
        {SUGGESTIONS.map((s, i) => (
          <motion.div key={s.title} variants={popIn} initial="hidden" animate="show" transition={{ ...jelly, delay: 0.08 * i }}>
            <TiltCard intensity={7}>
              <motion.button type="button" onClick={() => onPick(s.text)} whileTap={{ scale: 0.96 }} transition={jelly}
                className="glass flex h-full w-full items-start gap-3 rounded-3xl p-4 text-left hover:bg-white/[0.09] shadow-[inset_0_1px_0_rgba(224,242,254,0.2),inset_1px_1px_0_0_rgba(224,242,254,0.22),0_20px_50px_rgba(2,10,28,0.55)]">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-sky/25 to-periwinkle/20 text-glacier">
                  <s.icon className="size-4" />
                </span>
                <span>
                  <span className="block text-sm font-medium text-frost">{s.title}</span>
                  <span className="mt-0.5 block text-[13px] leading-snug text-mist">{s.text}</span>
                </span>
              </motion.button>
            </TiltCard>
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}

export function AetherApp() {
  const { prefs, setPref, parallax } = usePrefs();
  const [health, setHealth] = useState<Health | null>(null);
  const [tab, setTab] = useState<DrawerTab>("vault");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const toast = useCallback((title: string, body?: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, title, body }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4800);
  }, []);

  const agent = useAgent({
    onAnswer: (text) => prefs.speakReplies && speak(text),
    onLearned: (facts) => facts.length && toast("🧠 Remembered", facts.map((f) => f.text).join(" · ")),
  });

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await api.health());
    } catch (e) {
      const err = e as { message?: string; hint?: string };
      setHealth({ ok: false, version: "", ollama: false, model: null, model_note: null, embed_model: "",
        embeddings_available: null, embeddings_hint: null, tools: 0, context_length: 0,
        error: { message: err.message ?? "Can't reach the AETHER backend.", hint: err.hint } });
    }
  }, []);
  useEffect(() => { void loadHealth(); }, [loadHealth]);

  const files = useAttachments(toast);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  function sendWithFiles(text: string) {
    const ready = files.items.filter((a) => a.status === "ready");
    const goal = ready.length
      ? `${text.trim() || "Summarise the attached files."}\n\n(Attached to my Knowledge Vault just now: ${ready.map((a) => a.filename).join(", ")}. Search them to answer.)`
      : text;
    files.clear();
    void agent.send(goal);
  }

  useReminders(prefs.notifications, agent.revision, (t, b) => toast(t, b));
  const wakeArmed = useWakeWord(prefs.wakeWord, agent.busy, (text) => void agent.send(text));

  // Follow the conversation as it streams, unless you've scrolled up to read.
  const last = agent.messages[agent.messages.length - 1];
  const lastLen = last ? (last.role === "assistant" ? last.content.length + last.draft.length + last.steps.length : 1) : 0;
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 220;
    if (near) el.scrollTo({ top: el.scrollHeight, behavior: prefs.calm ? "auto" : "smooth" });
  }, [agent.messages.length, lastLen, prefs.calm]);

  // Layer 1: the glass plane drifts gently against the backdrop.
  const planeX = useTransform(parallax.x, (v) => v * -7);
  const planeY = useTransform(parallax.y, (v) => v * -5);

  const state: AgentState = agent.busy ? agent.agentState : "idle";
  const empty = agent.messages.length === 0;

  return (
    <>
      <main className="relative z-10 flex h-dvh">
        <motion.section
          style={{ x: planeX, y: planeY }}
          className="relative flex min-w-0 flex-1 flex-col"
          onDragEnter={(e) => {
            if (!e.dataTransfer.types.includes("Files")) return;
            e.preventDefault();
            dragDepth.current += 1;
            setDragging(true);
          }}
          onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
          onDragLeave={() => {
            dragDepth.current = Math.max(0, dragDepth.current - 1);
            if (dragDepth.current === 0) setDragging(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            dragDepth.current = 0;
            setDragging(false);
            void files.add(Array.from(e.dataTransfer.files));
            composer.current?.focus();
          }}
        >
          <AnimatePresence>
            {dragging && (
              <motion.div className="pointer-events-none absolute inset-3 z-20 grid place-items-center rounded-[32px] border-2 border-dashed border-glacier/60 bg-abyss-950/55 backdrop-blur-md"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <motion.div className="glass-strong flex flex-col items-center rounded-[28px] px-10 py-8 text-center"
                  initial={{ scale: 0.85, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.9 }} transition={jelly}>
                  <motion.div animate={{ y: [0, -8, 0] }} transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}>
                    <UploadCloud className="size-10 text-glacier" />
                  </motion.div>
                  <div className="mt-3 text-lg font-semibold text-frost">Drop to add to AETHER</div>
                  <div className="mt-1 text-sm text-mist">PDF · PowerPoint (.pptx) · Markdown · TXT</div>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
          {/* header */}
          <header className="px-3 pt-3 sm:px-5">
            <div className="glass mx-auto flex max-w-4xl items-center gap-3 rounded-[22px] py-2 pl-3 pr-2">
              <AgentOrb state={state} size={34} />
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold tracking-[0.22em] text-frost">AETHER</div>
                <AnimatePresence mode="wait" initial={false}>
                  <motion.div key={state + String(wakeArmed)} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}
                    className={state === "awaiting_approval" ? "text-xs text-amber" : "text-xs text-mist"}>
                    {wakeArmed ? "Listening…" : STATE_LABEL[state]}
                    {health?.model && state === "idle" && !wakeArmed && <span className="text-mist/60"> · {health.model}</span>}
                  </motion.div>
                </AnimatePresence>
              </div>
              <IconButton label="New chat" onClick={() => { agent.newChat(); composer.current?.focus(); }}>
                <Plus className="size-[18px]" />
              </IconButton>
              <IconButton label={prefs.calm ? "Calm mode on" : "Calm mode off"} onClick={() => setPref("calm", !prefs.calm)}
                className={prefs.calm ? "bg-seafoam/15 text-seafoam" : undefined}>
                <Waves className="size-[18px]" />
              </IconButton>
              <IconButton label={prefs.drawerOpen ? "Hide panel" : "Show panel"} onClick={() => setPref("drawerOpen", !prefs.drawerOpen)}
                className={prefs.drawerOpen ? "bg-white/[0.07] text-frost" : undefined}>
                <PanelRight className="size-[18px]" />
              </IconButton>
            </div>
            <AnimatePresence>
              {health && !health.ok && health.error && (
                <motion.div variants={popIn} initial="hidden" animate="show" exit="exit"
                  className="mx-auto mt-2 flex max-w-4xl items-start gap-3 rounded-[20px] border border-coral/30 bg-coral/[0.08] px-4 py-3 backdrop-blur-xl">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-coral" />
                  <div className="min-w-0 flex-1 text-sm">
                    <div className="text-coral">{health.error.message}</div>
                    {health.error.hint && <div className="mt-0.5 font-mono text-xs text-frost/80">→ {health.error.hint}</div>}
                  </div>
                  <IconButton label="Check again" onClick={() => void loadHealth()}><RefreshCw className="size-4" /></IconButton>
                </motion.div>
              )}
            </AnimatePresence>
          </header>

          {/* conversation */}
          <div ref={scroller} className="soft-scroll min-h-0 flex-1 overflow-y-auto">
            {empty ? (
              <EmptyState state={state} onPick={(t) => composer.current?.fill(t)} />
            ) : (
              <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:px-6">
                {agent.messages.map((m) => <MessageView key={m.id} message={m} />)}
              </div>
            )}
          </div>

          {/* composer */}
          <div className="px-3 pb-3 sm:px-5 sm:pb-5">
            <div className="mx-auto max-w-3xl">
              <Composer ref={composer} busy={agent.busy} onSend={sendWithFiles} onStop={agent.stop}
                autoSendVoice={prefs.autoSendVoice} attachments={files.items} indexing={files.indexing || files.uploading}
                onAttach={(f) => void files.add(f)} onRemoveAttachment={files.remove} />
              <p className="mt-2 text-center text-[11px] text-mist/60">
                Runs locally on Ollama · risky actions always ask first
              </p>
            </div>
          </div>
        </motion.section>

        <Drawer open={prefs.drawerOpen} tab={tab} onTab={setTab} onClose={() => setPref("drawerOpen", false)}>
          {tab === "vault" && <VaultPanel revision={agent.revision} toast={toast} />}
          {tab === "memory" && <MemoryPanel revision={agent.revision} />}
          {tab === "tasks" && <TasksPanel revision={agent.revision} />}
          {tab === "chats" && (
            <ChatsPanel revision={agent.revision} currentId={agent.sessionId}
              onOpen={(id) => void agent.loadSession(id)}
              onDeleted={(id) => id === agent.sessionId && agent.newChat()} />
          )}
          {tab === "settings" && (
            <SettingsPanel health={health} onHealthChange={() => void loadHealth()} revision={agent.revision} toast={toast} />
          )}
        </Drawer>
      </main>

      <ApprovalModal approval={agent.approval} onDecide={agent.decide} />

      {/* toasts */}
      <div className="pointer-events-none fixed right-4 top-4 z-[60] flex w-[min(340px,calc(100vw-32px))] flex-col gap-2" aria-live="polite">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div key={t.id} layout initial={{ opacity: 0, x: 40, scale: 0.9 }} animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.95 }} transition={jelly}
              className="glass-strong pointer-events-auto rounded-2xl px-4 py-3">
              <div className="text-sm font-medium text-frost">{t.title}</div>
              {t.body && <div className="mt-0.5 line-clamp-3 text-xs text-mist">{t.body}</div>}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </>
  );
}
