"use client";

import { AnimatePresence, motion, useTransform, type MotionValue } from "framer-motion";
import clsx from "clsx";
import {
  AlertTriangle, BookOpen, Brain, CalendarRange, ChartColumn, Globe, Library, PanelLeft, PanelRight, Plus, RefreshCw, UploadCloud, Waves, Wrench, type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { cloudProvider, hostLabel, remoteHost } from "@/lib/cloud";
import { api, ApiError } from "@/lib/api";
import { ArtifactContext } from "@/lib/artifacts";
import { jelly, popIn } from "@/lib/motion";
import { usePrefs } from "@/lib/prefs";
import { useReminders } from "@/lib/reminders";
import type { AgentState, Health } from "@/lib/types";
import { useAgent } from "@/lib/useAgent";
import { useAttachments } from "@/lib/useAttachments";
import { useLive } from "@/lib/useLive";
import { useVoice, type Feeder } from "@/lib/useNeuralVoice";
import { useWakeWord } from "@/lib/voice";

import { AgentOrb, STATE_LABEL } from "./AgentOrb";
import { HeroOrb } from "./three";
import { ApprovalModal } from "./ApprovalModal";
import { ArtifactPreview } from "./ArtifactPreview";
import { Composer, type ComposerHandle } from "./Composer";
import { Drawer, type DrawerTab } from "./Drawer";
import { MessageView, spokenText } from "./MessageView";
import { FeedbackContext } from "./Feedback";
import { ChatSidebar } from "./ChatSidebar";
import { ModelSwitcher } from "./ModelSwitcher";
import { ReasoningPill } from "./Reasoning";
import { UsagePanel } from "./panels/UsagePanel";
import { LandingContext, SendGhost, type Flight, type Rect } from "./SendFlight";
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
  { icon: ChartColumn, title: "Data & plot", text: "Plot my study hours this week as a bar chart: Mon 2, Tue 3.5, Wed 1, Thu 4, Fri 2.5, Sat 5, Sun 3." },
];

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Still up?" : h < 12 ? "Good morning." : h < 17 ? "Good afternoon." : "Good evening.";
}

/** A small glowing dot: seafoam when ready, sky while working, amber awaiting approval, coral offline. */
function LiveDot({ state, online }: { state: AgentState; online: boolean }) {
  const color = !online ? "#fda4af" : state === "awaiting_approval" ? "#fcd34d" : state === "idle" ? "#5eead4" : "#7dd3fc";
  return (
    <span className="relative grid size-2 shrink-0 place-items-center" aria-hidden>
      <motion.span className="absolute inset-0 rounded-full" style={{ background: color }}
        animate={{ scale: [1, 2.4, 1], opacity: [0.55, 0, 0.55] }}
        transition={{ duration: state === "idle" ? 2.6 : 1.2, repeat: Infinity, ease: "easeOut" }} />
      <span className="relative size-2 rounded-full" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
    </span>
  );
}

/** Whisper-quiet proof that this is a stateful agent: tools · documents · memories. */
const PACKS: Record<string, string> = { core: "Your stuff", research: "Research", code_data: "Code & data", mcp: "MCP servers" };
const LEVEL_TONE: Record<string, string> = { SAFE: "text-seafoam", WRITE: "text-glacier", HIGH_RISK: "text-amber" };

/** Whisper-quiet proof that this is a stateful agent: tools · documents · memories. Each one opens
 *  its place: docs → Vault, memories → Memory, tools → which packs exist and how risky they are. */
function AgentStats({ tools, revision, onOpenTab }: { tools: number; revision: number; onOpenTab: (t: DrawerTab) => void }) {
  const docs = useLive(api.documents, revision, 30_000);
  const facts = useLive(api.facts, revision, 30_000);
  const [packsOpen, setPacksOpen] = useState(false);
  const toolList = useLive(api.tools, revision);
  const ready = docs.data?.filter((d) => d.status === "ready").length;
  const packs = Object.entries((toolList.data ?? []).reduce<Record<string, Record<string, number>>>((acc, t) => {
    const p = (acc[t.pack ?? "core"] ??= {});
    p[t.level ?? "SAFE"] = (p[t.level ?? "SAFE"] ?? 0) + 1;
    return acc;
  }, {}));
  const items: [LucideIcon, string, number | undefined, () => void][] = [
    [Wrench, "tools", tools, () => setPacksOpen((o) => !o)],
    [Library, "docs", ready, () => onOpenTab("vault")],
    [Brain, "memories", facts.data?.length, () => onOpenTab("memory")],
  ];
  return (
    <div className="relative hidden items-center gap-1 pr-1 font-mono text-[11px] text-mist/85 md:flex" aria-label="Agent state">
      {items.map(([Icon, label, n, open]) => (
        <button key={label} type="button" onClick={open} title={label === "tools" ? "See tool packs" : `Open ${label === "docs" ? "the Vault" : "Memory"}`}
          className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-1.5 py-1 transition-colors hover:bg-white/[0.07] hover:text-frost">
          <Icon className="size-3 text-glacier/70" />
          <span className="text-frost/85">{n ?? "–"}</span> {n === 1 ? label.replace(/s$/, "") : label}
        </button>
      ))}
      <AnimatePresence>
        {packsOpen && (
          <motion.div initial={{ opacity: 0, y: -6, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, transition: { duration: 0.12 } }} transition={jelly} style={{ transformOrigin: "top left" }}
            className="glass-pop absolute left-0 top-full z-50 mt-2 w-64 rounded-2xl p-3 font-sans">
            <div className="mb-2 text-[10.5px] uppercase tracking-[0.14em] text-mist">Tool packs</div>
            <div className="space-y-1.5">
              {packs.map(([pack, levels]) => (
                <div key={pack} className="flex items-center gap-2 text-[12px]">
                  <span className="flex-1 text-frost">{PACKS[pack] ?? pack}</span>
                  {Object.entries(levels).map(([lvl, n]) => (
                    <span key={lvl} className={clsx("font-mono text-[10.5px]", LEVEL_TONE[lvl])} title={lvl.replace("_", " ").toLowerCase()}>
                      {n} {lvl === "HIGH_RISK" ? "risky" : lvl.toLowerCase()}
                    </span>
                  ))}
                </div>
              ))}
            </div>
            <p className="mt-2.5 text-[10.5px] leading-snug text-mist">
              Local models get only the 6–8 most relevant tools per message; risky ones always ask you first.
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Where answers come from, in the hero text. */
function whereItRuns(model?: string | null): string {
  const cloud = cloudProvider(model);
  if (cloud) return `with ${cloud} in the cloud for this chat`;
  const host = remoteHost(model);
  if (host) return `on your ${hostLabel(host)}'s GPU over your own network`;
  return "all on this Mac";
}

function EmptyState({ state, onPick, level, model }: { state: AgentState; onPick: (t: string) => void; level: MotionValue<number>; model?: string | null }) {
  return (
    <motion.div variants={popIn} initial="hidden" animate="show" className="mx-auto flex max-w-2xl flex-col items-center px-4 pt-[8vh] text-center">
      <HeroOrb state={state} size={132} level={level} />
      <h1 className="mt-10 text-3xl font-semibold tracking-tight text-frost sm:text-4xl">{greeting()}</h1>
      <p className="mt-3 max-w-md text-balance text-[15px] leading-relaxed text-mist">
        I&apos;m AETHER, your local study, research and productivity agent. I can search the web, read your
        notes, track deadlines and remember what matters, {whereItRuns(model)}.
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

/** The line under the composer: where the current model actually runs. */
function runsOn(model?: string | null): string {
  const cloud = cloudProvider(model);
  if (cloud) return `Runs on ${cloud} in the cloud: your messages go there`;
  const host = remoteHost(model);
  if (host) return `Runs on your ${hostLabel(host)} over your own network`;
  return "Runs locally on Ollama";
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

  const voice = useVoice();
  const agent = useAgent({
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
  const [artifact, setArtifact] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  // Auto-speak: read the reply aloud while it's still being written, sentence by sentence.
  const feeder = useRef<{ id: string; feed: Feeder } | null>(null);
  const lastMsg = agent.messages[agent.messages.length - 1];
  const live = lastMsg?.role === "assistant" ? lastMsg : undefined;
  const liveStatus = live?.status;
  const liveText = live ? (live.status === "streaming" ? live.draft : spokenText(live)) : "";
  const liveSegment = live?.steps.length ?? 0;
  useEffect(() => {
    if (!prefs.speakReplies || !live) return;
    if (liveStatus === "error" || liveStatus === "stopped") {
      if (feeder.current?.id === live.id) feeder.current = null;
      return;
    }
    if (!feeder.current || feeder.current.id !== live.id) {
      if (liveStatus !== "streaming") return; // only replies being written now, not reopened chats
      feeder.current = { id: live.id, feed: voice.stream(live.id) };
    }
    if (liveStatus === "streaming") feeder.current.feed.push(liveText, liveSegment);
    else if (liveStatus === "done") feeder.current.feed.end(liveText);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveText, liveSegment, liveStatus, live?.id, prefs.speakReplies]);

  const [flight, setFlight] = useState<Flight | null>(null);

  function sendWithFiles(text: string, from?: Rect) {
    voice.stop();           // a new question interrupts whatever is being read
    voice.unlock();         // this click lets the browser play audio for the reply
    feeder.current = null;
    const ready = files.items.filter((a) => a.status === "ready" && !a.path);
    const tables = files.items.filter((a) => a.path);
    const images = files.images.map((i) => i.dataUrl);
    let goal = text;
    if (ready.length) {
      goal = `${goal.trim() || "Summarise the attached files."}\n\n(Attached to my Knowledge Vault just now: ${ready.map((a) => a.filename).join(", ")}. Search them to answer.)`;
    }
    if (tables.length) {
      goal = `${goal.trim() || "Analyse the attached data."}\n\n(Data file${tables.length > 1 ? "s" : ""} attached: ${tables.map((a) => a.path).join(", ")}. Inspect with analyze_dataset; use python_sandbox for charts.)`;
    }
    files.clear();
    const userId = `u-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    const shown = goal.trim() || (images.length ? "What's in this image?" : "");
    // The text lifts off the box and glides into the chat (skipped in calm mode).
    if (from && shown && !prefs.calm) setFlight({ id: userId, text: shown, from });
    const skill = /^\/([a-z][a-z0-9-]*)(?:\s|$)/.exec(goal.trim())?.[1] ?? null;
    void agent.send(goal, images, { userId, skill: skill && skillNames.has(skill) ? skill : null,
      reasoning: health?.reasoning ? prefs.reasoning : null });
  }

  // Each reply's place in the chat (0 = first), and "ask again" for the 👎 under it.
  const replyIndex = new Map<string, number>();
  agent.messages.filter((m) => m.role === "assistant").forEach((m, i) => replyIndex.set(m.id, i));
  const feedbackCtx = {
    sessionId: agent.sessionId,
    rate: agent.rate,
    // A cloud model failed on billing / quota / setup: switch to a free model on this Mac and ask again.
    switchToFree: async (turn: number) => {
      try {
        const list = await api.models();
        const local = list.models.filter((n) => !/embed|bge-|minilm|rerank/i.test(n));
        const pick = local.includes("qwen2.5:7b") ? "qwen2.5:7b" : local[0];
        if (!pick) { toast("No local model to switch to", "Install one with: ollama pull qwen2.5:7b"); return; }
        await api.setModel(pick);
        setHealth((h) => (h ? { ...h, model: pick, model_note: null, cloud: false, provider: null } : h));
        void loadHealth();
        toast("Switched to a free model", `${pick} runs on this Mac. Asking again…`);
        feedbackCtx.retry(turn);
      } catch (e) {
        toast(e instanceof ApiError ? e.message : "Couldn't switch models", e instanceof ApiError ? e.hint : undefined);
      }
    },
    retry: (turn: number) => {
      if (agent.busy) return;
      const at = agent.messages.findIndex((m) => replyIndex.get(m.id) === turn && m.role === "assistant");
      const question = [...agent.messages.slice(0, at)].reverse().find((m) => m.role === "user");
      if (question && question.role === "user" && question.content.trim()) void agent.send(question.content);
    },
  };

  useReminders(prefs.notifications, agent.revision, (t, b) => toast(t, b));
  const wakeArmed = useWakeWord(prefs.wakeWord, agent.busy, (text) => void agent.send(text));

  // Follow the conversation as it streams, unless you've scrolled up to read.
  const last = agent.messages[agent.messages.length - 1];
  const lastLen = last ? (last.role === "assistant" ? last.content.length + last.draft.length + last.steps.length : 1) : 0;
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 220;
    if (near) el.scrollTo({ top: el.scrollHeight, behavior: prefs.calm || flight ? "auto" : "smooth" });
  }, [agent.messages.length, lastLen, prefs.calm]);

  // Layer 1: the glass plane drifts gently against the backdrop.
  const planeX = useTransform(parallax.x, (v) => v * -7);
  const planeY = useTransform(parallax.y, (v) => v * -5);

  const state: AgentState = agent.busy ? agent.agentState : "idle";
  const skills = useLive(api.skills, agent.revision);
  const skillNames = new Set((skills.data ?? []).map((s) => s.name));
  // The model the latest answer actually ran on (it can differ from the selected one: if the PC
  // couldn't be reached, AETHER answered on this Mac). Hero + footer follow it.
  const lastReply = [...agent.messages].reverse().find((m) => m.role === "assistant");
  const runModel = (lastReply?.role === "assistant" && lastReply.model) || health?.model;
  const empty = agent.messages.length === 0;

  return (
    <ArtifactContext.Provider value={setArtifact}>
      <main className="relative z-10 flex h-dvh">
        <ChatSidebar open={prefs.chatsOpen} onClose={() => setPref("chatsOpen", false)} revision={agent.revision}
          currentId={agent.sessionId} toast={toast}
          onOpen={(id) => void agent.loadSession(id)}
          onNew={() => { agent.newChat(); composer.current?.focus(); if (window.innerWidth < 1024) setPref("chatsOpen", false); }}
          onDeleted={(id) => id === agent.sessionId && agent.newChat()} />
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
                  <div className="mt-1 text-sm text-mist">Images · PDF · Word · PowerPoint · Excel/CSV · Markdown · code</div>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
          {/* header */}
          <header className="relative z-30 px-3 pt-3 sm:px-5">
            <div className="glass mx-auto flex max-w-4xl items-center gap-3 rounded-[22px] py-2 pl-2 pr-2">
              {!prefs.chatsOpen && (
                <IconButton label="Show chats" hint="Your conversations" onClick={() => setPref("chatsOpen", true)}>
                  <PanelLeft className="size-[18px]" />
                </IconButton>
              )}
              <AgentOrb state={state} size={34} level={voice.level} />
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold tracking-[0.22em] text-frost">AETHER</div>
                <div className="flex min-w-0 items-center gap-1.5 text-xs">
                  <LiveDot state={state} online={!!health?.ok} />
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.span key={state + String(wakeArmed) + String(health?.ok)}
                      initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}
                      className={clsx("whitespace-nowrap",
                        state === "awaiting_approval" ? "text-amber" : health && !health.ok ? "text-coral" : "text-frost/80")}>
                      {wakeArmed ? "Listening…" : health && !health.ok && state === "idle" ? "Offline" : STATE_LABEL[state]}
                    </motion.span>
                  </AnimatePresence>

                </div>
              </div>
              {health?.ok && <AgentStats tools={health.tools} revision={agent.revision}
                onOpenTab={(t) => { setTab(t); setPref("drawerOpen", true); }} />}
              <IconButton label="New chat" hint="Start a fresh conversation" onClick={() => { agent.newChat(); composer.current?.focus(); }}>
                <Plus className="size-[18px]" />
              </IconButton>
              <IconButton label={prefs.calm ? "Calm mode: on" : "Calm mode: off"}
                hint={prefs.calm ? "Turn the motion back on" : "Reduce motion: no drift or wobble"}
                onClick={() => setPref("calm", !prefs.calm)}
                className={prefs.calm ? "bg-seafoam/15 text-seafoam" : undefined}>
                <Waves className="size-[18px]" />
              </IconButton>
              <IconButton label={prefs.drawerOpen ? "Hide side panel" : "Show side panel"}
                hint="Vault · Memory · Tasks · Usage · Settings" tooltipAlign="end"
                onClick={() => setPref("drawerOpen", !prefs.drawerOpen)}
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
              <EmptyState state={state} level={voice.level} model={runModel} onPick={(t) => composer.current?.fill(t)} />
            ) : (
              <LandingContext.Provider value={flight?.id ?? null}>
                <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:px-6">
                  <FeedbackContext.Provider value={feedbackCtx}>
                    {agent.messages.map((m) => <MessageView key={m.id} message={m} turn={replyIndex.get(m.id) ?? 0} />)}
                  </FeedbackContext.Provider>
                </div>
              </LandingContext.Provider>
            )}
          </div>

          {/* composer */}
          <div className="px-3 pb-3 sm:px-5 sm:pb-5">
            <div className="mx-auto max-w-3xl">
              <Composer ref={composer} busy={agent.busy} onSend={sendWithFiles} onStop={() => { agent.stop(); voice.stop(); }}
                autoSendVoice={prefs.autoSendVoice} attachments={files.items} images={files.images}
                indexing={files.indexing || files.uploading}
                onAttach={(f) => void files.add(f)} onRemoveAttachment={files.remove} onRemoveImage={files.removeImage}
                skills={skills.data ?? []}
                toolbar={health?.model ? (
                  <>
                    <ModelSwitcher up current={health.model} disabled={agent.busy} toast={toast}
                      onOpenSettings={() => { setTab("settings"); setPref("drawerOpen", true); }}
                      onSwitched={(m) => { setHealth((h) => (h ? { ...h, model: m, model_note: null } : h)); void loadHealth(); }} />
                    {health.reasoning && <ReasoningPill kind={health.reasoning} disabled={agent.busy} />}
                  </>
                ) : undefined} />
              <p className="mt-2 text-center text-[11px] text-mist/75">
                {runsOn(runModel)} · risky actions always ask first
              </p>
            </div>
          </div>
        </motion.section>

        <Drawer open={prefs.drawerOpen} tab={tab} onTab={setTab} onClose={() => setPref("drawerOpen", false)}>
          {tab === "vault" && <VaultPanel revision={agent.revision} toast={toast} />}
          {tab === "memory" && <MemoryPanel revision={agent.revision} />}
          {tab === "tasks" && <TasksPanel revision={agent.revision} />}
          {tab === "usage" && <UsagePanel revision={agent.revision} />}
          {tab === "settings" && (
            <SettingsPanel health={health} onHealthChange={() => void loadHealth()} revision={agent.revision} toast={toast} />
          )}
        </Drawer>
      </main>

      {flight && (
        <SendGhost key={flight.id} flight={flight} onLanded={() => setFlight(null)}
          prepareLanding={() => { const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; }} />
      )}
      <ApprovalModal approval={agent.approval} onDecide={agent.decide} />
      <ArtifactPreview path={artifact} onClose={() => setArtifact(null)} onOpen={setArtifact} toast={toast} />

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
    </ArtifactContext.Provider>
  );
}
