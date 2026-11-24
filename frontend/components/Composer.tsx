"use client";

/** The message box: auto-growing input, push-to-talk mic, send / stop. */
import clsx from "clsx";
import { AnimatePresence, motion, useAnimate } from "framer-motion";
import { ArrowUp, FileText, Mic, Paperclip, Sparkles, Square, X } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react";

import type { SkillInfo } from "@/lib/types";
import { jelly } from "@/lib/motion";
import { usePrefs } from "@/lib/prefs";
import { ACCEPT_ATTR, type Attachment, type ImageAttachment } from "@/lib/useAttachments";

import { ghostStart, type Rect } from "./SendFlight";
import { useDictation } from "@/lib/voice";

export type ComposerHandle = { fill: (text: string) => void; focus: () => void };

export const Composer = forwardRef<ComposerHandle, {
  busy: boolean;
  onSend: (text: string, from?: Rect) => void;
  onStop: () => void;
  autoSendVoice: boolean;
  attachments: Attachment[];
  images: ImageAttachment[];
  indexing: boolean;
  onAttach: (files: File[]) => void;
  onRemoveAttachment: (id: number) => void;
  onRemoveImage: (id: string) => void;
  /** Shown under the text, inside the box (the model picker). */
  toolbar?: ReactNode;
  /** Slash commands: typing "/" lists them. */
  skills?: SkillInfo[];
}>(function Composer({ busy, onSend, onStop, autoSendVoice, attachments, images, indexing, onAttach, onRemoveAttachment, onRemoveImage, toolbar, skills = [] }, ref) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  // Slash commands: "/" (or "/de…") at the start, before any space, opens the menu.
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashClosed, setSlashClosed] = useState(false);
  const slashQuery = /^\/([a-z0-9-]*)$/i.exec(text)?.[1]?.toLowerCase();
  const slashMatches = slashQuery === undefined ? [] : skills.filter((s) => s.name.startsWith(slashQuery));
  const slashOpen = !slashClosed && slashMatches.length > 0;
  useEffect(() => { setSlashIndex(0); if (!text.startsWith("/")) setSlashClosed(false); }, [text]);
  const pickSkill = (name: string) => { setText(`/${name} `); setSlashClosed(true); area.current?.focus(); };
  const { prefs } = usePrefs();
  const [scope, animate] = useAnimate<HTMLDivElement>();
  const flash = useRef<HTMLSpanElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({
    fill: (t) => {
      setText(t);
      requestAnimationFrame(() => area.current?.focus());
    },
    focus: () => area.current?.focus(),
  }));

  const voice = useDictation(
    (live) => setText(live),
    (final) => {
      if (autoSendVoice && !busy) {
        onSend(final);
        setText("");
      }
    },
  );

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  function submit() {
    if (busy || indexing || (!text.trim() && !attachments.length && !images.length)) return;
    const from = area.current && text.trim() ? ghostStart(area.current.getBoundingClientRect()) : undefined;
    onSend(text, from);
    setText("");
    if (!prefs.calm && scope.current) {
      // the bar "gulps" the message: a soft squish and a glow that fades with it
      void animate(scope.current, { scale: [1, 0.982, 1.004, 1] }, { duration: 0.5, ease: "easeOut" });
      if (flash.current) void animate(flash.current, { opacity: [0, 1, 0] }, { duration: 0.65, ease: "easeOut" });
    }
  }

  return (
    <motion.div ref={scope} className={clsx(
      // z-30: its menus (skills, model, reasoning) open over the chat above it.
      "glass relative z-30 rounded-[26px] p-2 transition-[box-shadow,border-color] duration-300",
      // Keep the glass rim + drop shadow as a Tailwind shadow so the focus ring adds to it.
      "shadow-[inset_1px_1px_0_0_rgba(224,242,254,0.22),0_20px_50px_rgba(2,10,28,0.55)]",
      // The whole bar glows when you type, instead of a box around the text field.
      "focus-within:border-sky-200/40 focus-within:ring-2 focus-within:ring-sky-300/35",
    )}>
      <AnimatePresence>
        {slashOpen && (
          <motion.div role="listbox" aria-label="Skills" initial={{ opacity: 0, y: 8, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, transition: { duration: 0.12 } }} transition={jelly} style={{ transformOrigin: "bottom left" }}
            className="glass-pop absolute bottom-full left-2 right-2 z-50 mb-2 rounded-2xl p-1.5">
            <div className="flex items-center gap-1.5 px-2.5 pb-1.5 pt-1 text-[10.5px] uppercase tracking-[0.14em] text-mist">
              <Sparkles className="size-3" /> Skills <span className="ml-auto normal-case tracking-normal text-mist/60">↑↓ · Enter</span>
            </div>
            {slashMatches.map((s, i) => (
              <button key={s.name} type="button" role="option" aria-selected={i === slashIndex}
                onMouseEnter={() => setSlashIndex(i)} onMouseDown={(e) => { e.preventDefault(); pickSkill(s.name); }}
                className={clsx("flex w-full items-start gap-3 rounded-xl px-2.5 py-2 text-left transition-colors",
                  i === slashIndex ? "bg-sky/[0.14]" : "hover:bg-white/[0.06]")}>
                <span className="shrink-0 font-mono text-[13px] text-glacier">/{s.name}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] leading-snug text-frost/90">{s.description}</span>
                  {s.usage && s.usage !== `/${s.name}` && <span className="block font-mono text-[10.5px] text-mist">{s.usage}</span>}
                </span>
                {s.reasoning && <span className="shrink-0 rounded-md bg-periwinkle/15 px-1.5 py-0.5 text-[10px] text-periwinkle">{s.reasoning} reasoning</span>}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
      <span ref={flash} aria-hidden className="pointer-events-none absolute inset-0 rounded-[26px] opacity-0"
        style={{ boxShadow: "inset 0 0 0 1.5px rgba(125,211,252,0.7), 0 0 36px rgba(56,189,248,0.35)" }} />
      <AnimatePresence>
        {voice.listening && (
          <motion.div aria-hidden className="pointer-events-none absolute inset-0 rounded-[26px]"
            initial={{ opacity: 0 }} animate={{ opacity: [0.4, 1, 0.4] }} exit={{ opacity: 0 }}
            transition={{ duration: 1.6, repeat: Infinity }}
            style={{ boxShadow: "inset 0 0 0 1.5px rgba(94,234,212,0.6), 0 0 40px rgba(94,234,212,0.2)" }} />
        )}
      </AnimatePresence>
      <AnimatePresence initial={false}>
        {(attachments.length > 0 || images.length > 0) && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={jelly} className="overflow-hidden">
            <div className="flex flex-wrap gap-1.5 px-1.5 pb-2 pt-1">
              <AnimatePresence>
                {images.map((img) => (
                  <motion.div key={img.id} layout initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.6, opacity: 0 }} transition={jelly}
                    className="group relative size-14 overflow-hidden rounded-xl border border-white/15">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={img.dataUrl} alt={img.name} className="size-full object-cover" />
                    <button type="button" aria-label={`Remove ${img.name}`} onClick={() => onRemoveImage(img.id)}
                      className="absolute right-0.5 top-0.5 grid size-5 place-items-center rounded-md bg-abyss-950/70 text-frost opacity-80 hover:opacity-100">
                      <X className="size-3" />
                    </button>
                  </motion.div>
                ))}
                {attachments.map((a) => {
                  const working = ["queued", "reading", "embedding"].includes(a.status);
                  return (
                    <motion.div key={a.id} layout initial={{ scale: 0.7, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                      exit={{ scale: 0.7, opacity: 0 }} transition={jelly}
                      title={a.error ?? a.filename}
                      className={clsx("relative flex max-w-[240px] items-center gap-1.5 overflow-hidden rounded-xl border py-1 pl-2 pr-1 text-xs",
                        a.status === "error" ? "border-coral/40 bg-coral/10 text-coral" : "border-white/10 bg-white/[0.06] text-frost")}>
                      {working && (
                        <motion.span aria-hidden className="absolute inset-y-0 left-0 bg-sky/20"
                          animate={{ width: `${Math.max(8, a.progress * 100)}%` }} transition={jelly} />
                      )}
                      <FileText className={clsx("relative size-3.5 shrink-0", /\.pptx$/i.test(a.filename) ? "text-amber" : /\.pdf$/i.test(a.filename) ? "text-coral" : "text-periwinkle")} />
                      <span className="relative truncate">{a.filename}</span>
                      <span className="relative shrink-0 text-[10px] text-mist">
                        {working ? "indexing…" : a.status === "error" ? "failed" : "ready"}
                      </span>
                      <button type="button" aria-label={`Remove ${a.filename}`} onClick={() => onRemoveAttachment(a.id)}
                        className="relative grid size-5 place-items-center rounded-md text-mist hover:bg-white/10 hover:text-frost">
                        <X className="size-3" />
                      </button>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="flex items-end gap-2">
        <motion.button
          type="button"
          aria-label="Attach files"
          title="Attach PDFs, slides or notes"
          onClick={() => picker.current?.click()}
          whileHover={{ scale: 1.08 }}
          whileTap={{ scale: 0.9 }}
          transition={jelly}
          className="grid size-10 shrink-0 place-items-center rounded-2xl text-mist transition-colors hover:bg-white/[0.07] hover:text-frost"
        >
          <Paperclip className="size-[18px]" />
        </motion.button>
        <input ref={picker} type="file" multiple accept={ACCEPT_ATTR} className="hidden"
          onChange={(e) => { onAttach(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        <motion.button
          type="button"
          aria-label={voice.listening ? "Stop listening" : "Speak"}
          title={voice.listening ? "Stop listening" : "Push to talk"}
          onClick={voice.listening ? voice.stop : voice.start}
          whileHover={{ scale: 1.08 }}
          whileTap={{ scale: 0.9 }}
          transition={jelly}
          className={clsx("grid size-10 shrink-0 place-items-center rounded-2xl transition-colors",
            voice.listening ? "bg-seafoam/20 text-seafoam" : "text-mist hover:bg-white/[0.07] hover:text-frost")}
        >
          <motion.span animate={voice.listening ? { scale: [1, 1.2, 1] } : { scale: 1 }}
            transition={voice.listening ? { duration: 1, repeat: Infinity } : jelly}>
            <Mic className="size-[18px]" />
          </motion.span>
        </motion.button>
        <textarea
          ref={area}
          value={text}
          rows={1}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (slashOpen) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setSlashIndex((i) => (i + (e.key === "ArrowDown" ? 1 : slashMatches.length - 1)) % slashMatches.length);
                return;
              }
              if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey) {
                e.preventDefault();
                pickSkill(slashMatches[slashIndex].name);
                return;
              }
              if (e.key === "Escape") { e.preventDefault(); setSlashClosed(true); return; }
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          aria-expanded={slashOpen} aria-autocomplete="list"
          placeholder={voice.listening ? "Listening…"
            : images.length ? "Ask about the image, e.g. “save these classes to my timetable”…"
            : attachments.length ? (indexing ? "Reading your files… ask about them" : "Ask about the attached files…")
            : skills.length ? "Ask AETHER anything, type / for skills, or drop files here…"
            : "Ask AETHER anything, or drop files here…"}
          aria-label="Message AETHER"
          className="soft-scroll max-h-[220px] min-h-10 flex-1 resize-none bg-transparent px-1 py-2.5 text-[0.95rem] leading-relaxed text-frost placeholder:text-mist/60 outline-none focus:outline-none focus-visible:outline-none focus:ring-0"
        />
        <AnimatePresence mode="popLayout" initial={false}>
          {busy ? (
            <motion.button key="stop" type="button" aria-label="Stop" title="Stop" onClick={onStop}
              initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.6, opacity: 0 }}
              whileTap={{ scale: 0.9 }} transition={jelly}
              className="grid size-10 shrink-0 place-items-center rounded-2xl border border-coral/30 bg-coral/15 text-coral">
              <Square className="size-4 fill-current" />
            </motion.button>
          ) : (
            <motion.button key="send" type="button" aria-label="Send" title="Send (Enter)" onClick={submit}
              disabled={indexing || (!text.trim() && !attachments.length && !images.length)}
              initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              exit={{ y: -26, scale: 0.85, opacity: 0, transition: { duration: 0.28, ease: [0.3, 0, 0.2, 1] } }}
              whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.9 }} transition={jelly}
              className="grid size-10 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-sky to-periwinkle text-abyss-950 shadow-[0_6px_24px_rgba(56,189,248,0.4)] disabled:opacity-35 disabled:shadow-none">
              <ArrowUp className="size-[18px]" strokeWidth={2.5} />
            </motion.button>
          )}
        </AnimatePresence>
      </div>
      {toolbar && <div className="flex items-center gap-2 px-1.5 pb-0.5 pt-1">{toolbar}</div>}
      {voice.error && <div className="px-3 pb-1 pt-1.5 text-xs text-coral">{voice.error}</div>}
    </motion.div>
  );
});
