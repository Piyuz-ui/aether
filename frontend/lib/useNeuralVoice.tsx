"use client";

/**
 * Gapless neural voice playback.
 *
 *   text → sentences → POST /api/tts/speak (two in flight: the current one and the next)
 *        → decodeAudioData → AudioBufferSourceNodes scheduled back-to-back on the AudioContext clock
 *        → gain → analyser (drives the orb) → speakers
 *
 * Because every clip is scheduled at the exact time the previous one ends, there are no gaps or
 * stutters between sentences, in Safari too. stop() cancels in-flight requests, stops scheduled
 * sources and clears the queue at once. If no neural engine is installed, it falls back to the
 * browser's built-in speechSynthesis.
 */
import { useMotionValue, type MotionValue } from "framer-motion";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { API_BASE } from "./api";
import { usePrefs } from "./prefs";
import { prepareForSpeech, splitSentences } from "./speechText";
import { speak as browserSpeak, stopSpeaking as browserStop } from "./voice";

export type TtsVoice = { id: string; name: string; description: string; accent: string };
export type TtsStatus = {
  engine: "kokoro-mlx" | "kokoro-onnx" | "fallback" | string;
  state: "ready" | "idle" | "loading" | "downloading" | "needs_download" | "unavailable";
  progress: number | null;
  voices: TtsVoice[];
  error: { message: string; hint?: string } | null;
  hint: string | null;
};

export type Feeder = { push: (text: string, segment?: string | number) => void; end: (text?: string) => void };

type Session = {
  id: string;
  abort: AbortController;
  pending: { seq: number; text: string }[];
  ready: Map<number, AudioBuffer | null>;
  nextSeq: number;          // next sequence number to assign
  nextToSchedule: number;   // next sequence number to put on the timeline
  inFlight: number;
  scheduledEnd: number;     // AudioContext time when the last scheduled clip ends
  sources: Set<AudioBufferSourceNode>;
  finished: boolean;        // no more text will arrive
  fallbackText: string[];   // used when the neural engine is unavailable
};

type VoiceApi = {
  status: TtsStatus | null;
  speakingId: string | null;
  loadingId: string | null;
  level: MotionValue<number>;
  unlock: () => void;
  speak: (id: string, text: string) => void;
  stream: (id: string) => Feeder;
  stop: () => void;
  preview: (voiceId: string) => void;
  refresh: () => Promise<void>;
};

const Ctx = createContext<VoiceApi | null>(null);
const PREFETCH = 2;

export function VoiceProvider({ children }: { children: ReactNode }) {
  const { prefs } = usePrefs();
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;
  const [status, setStatus] = useState<TtsStatus | null>(null);
  const statusRef = useRef<TtsStatus | null>(null);
  statusRef.current = status;
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const level = useMotionValue(0);

  const audio = useRef<{ ctx: AudioContext; gain: GainNode; analyser: AnalyserNode } | null>(null);
  const session = useRef<Session | null>(null);
  const raf = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/tts/status`);
      if (r.ok) setStatus(await r.json());
    } catch {
      /* backend offline: keep the last known status */
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  // Keep the model warm once someone wants voice, so the first sentence starts quickly.
  useEffect(() => {
    if (!prefs.speakReplies || !status || status.engine === "fallback" || status.state === "ready") return;
    if (status.state === "loading" || status.state === "downloading") {
      const t = setTimeout(() => void refresh(), 1200);
      return () => clearTimeout(t);
    }
    void fetch(`${API_BASE}/api/tts/warmup`, { method: "POST" }).finally(() => void refresh());
  }, [prefs.speakReplies, status, refresh]);

  const ensureAudio = useCallback(() => {
    if (!audio.current) {
      const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AC();
      const gain = ctx.createGain();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.6;
      gain.connect(analyser);
      analyser.connect(ctx.destination);
      audio.current = { ctx, gain, analyser };
    }
    if (audio.current.ctx.state === "suspended") void audio.current.ctx.resume();
    return audio.current;
  }, []);

  /** Call inside a click/keypress so browsers allow audio later (autoplay policies). */
  const unlock = useCallback(() => { ensureAudio(); }, [ensureAudio]);

  const stopLevelLoop = useCallback(() => {
    if (raf.current !== null) cancelAnimationFrame(raf.current);
    raf.current = null;
    level.set(0);
  }, [level]);

  const startLevelLoop = useCallback(() => {
    if (raf.current !== null || !audio.current) return;
    const { analyser } = audio.current;
    const bins = new Uint8Array(analyser.frequencyBinCount);
    let smooth = 0;
    const tick = () => {
      analyser.getByteFrequencyData(bins);
      let sum = 0;
      for (let i = 2; i < 64; i++) sum += bins[i]; // ~200 Hz–3 kHz: where the voice lives
      const v = Math.min(1, (sum / 62 / 255) * 1.8);
      smooth = smooth * 0.72 + v * 0.28;
      level.set(prefsRef.current.calm ? 0 : smooth);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [level]);

  const finish = useCallback((s: Session) => {
    if (session.current !== s) return;
    session.current = null;
    setSpeakingId(null);
    setLoadingId(null);
    stopLevelLoop();
  }, [stopLevelLoop]);

  const stop = useCallback(() => {
    const s = session.current;
    session.current = null;
    if (s) {
      s.abort.abort();
      s.sources.forEach((src) => { try { src.stop(); } catch { /* already stopped */ } });
      s.sources.clear();
      s.pending = [];
    }
    browserStop();
    setSpeakingId(null);
    setLoadingId(null);
    stopLevelLoop();
  }, [stopLevelLoop]);

  const maybeDone = useCallback((s: Session) => {
    if (s.finished && !s.pending.length && !s.inFlight && s.nextToSchedule >= s.nextSeq && !s.sources.size) finish(s);
  }, [finish]);

  const schedule = useCallback((s: Session) => {
    const a = audio.current;
    if (!a || session.current !== s) return;
    while (s.ready.has(s.nextToSchedule)) {
      const buf = s.ready.get(s.nextToSchedule)!;
      s.ready.delete(s.nextToSchedule);
      s.nextToSchedule += 1;
      if (!buf) continue; // that sentence failed: skip it, keep the flow going
      const src = a.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(a.gain);
      const at = Math.max(a.ctx.currentTime + 0.04, s.scheduledEnd);
      src.start(at);
      s.scheduledEnd = at + buf.duration;
      s.sources.add(src);
      src.onended = () => { s.sources.delete(src); maybeDone(s); };
      setLoadingId(null);
      setSpeakingId(s.id);
      startLevelLoop();
    }
    maybeDone(s);
  }, [maybeDone, startLevelLoop]);

  const pump = useCallback((s: Session) => {
    const a = audio.current;
    if (!a || session.current !== s) return;
    while (s.inFlight < PREFETCH && s.pending.length) {
      const { seq, text } = s.pending.shift()!;
      s.inFlight += 1;
      const { voice, speed } = prefsRef.current;
      fetch(`${API_BASE}/api/tts/speak`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice, speed }),
        signal: s.abort.signal,
      })
        .then(async (r) => {
          if (r.status === 503) throw new Error("engine-unavailable");
          if (!r.ok) return null;
          return a.ctx.decodeAudioData(await r.arrayBuffer());
        })
        .then((buf) => {
          if (session.current !== s) return;
          s.ready.set(seq, buf);
          s.inFlight -= 1;
          schedule(s);
          pump(s);
        })
        .catch((e: Error) => {
          if (s.abort.signal.aborted || session.current !== s) return;
          s.inFlight -= 1;
          if (e.message === "engine-unavailable") {
            // No neural engine: hand the rest to the browser voice so nothing goes unsaid.
            const rest = [text, ...s.pending.map((p) => p.text)].join(" ");
            s.pending = [];
            browserSpeak(rest);
            void refresh();
            finish(s);
            return;
          }
          s.ready.set(seq, null);
          schedule(s);
          pump(s);
        });
    }
  }, [schedule, finish, refresh]);

  const begin = useCallback((id: string): Session => {
    stop();
    const s: Session = {
      id, abort: new AbortController(), pending: [], ready: new Map(), nextSeq: 0, nextToSchedule: 0,
      inFlight: 0, scheduledEnd: 0, sources: new Set(), finished: false, fallbackText: [],
    };
    session.current = s;
    setLoadingId(id);
    return s;
  }, [stop]);

  const enqueue = useCallback((s: Session, sentences: string[]) => {
    if (!sentences.length || session.current !== s) return;
    if (statusRef.current?.engine === "fallback") {
      s.fallbackText.push(...sentences);
      return;
    }
    for (const text of sentences) s.pending.push({ seq: s.nextSeq++, text });
    ensureAudio();
    pump(s);
  }, [ensureAudio, pump]);

  const end = useCallback((s: Session) => {
    s.finished = true;
    if (statusRef.current?.engine === "fallback") {
      if (s.fallbackText.length) browserSpeak(s.fallbackText.join(" "));
      finish(s);
      return;
    }
    maybeDone(s);
  }, [finish, maybeDone]);

  const speak = useCallback((id: string, text: string) => {
    const s = begin(id);
    enqueue(s, splitSentences(prepareForSpeech(text)));
    end(s);
  }, [begin, enqueue, end]);

  /** Speak a reply while it is still being written: complete sentences go out as they arrive. */
  const stream = useCallback((id: string): Feeder => {
    const s = begin(id);
    let segment: string | number | undefined;
    let lastText = "";
    let emitted = 0;
    const flush = (text: string, final: boolean) => {
      const parts = splitSentences(prepareForSpeech(text));
      const complete = final ? parts : parts.slice(0, -1); // the last one may still be growing
      if (complete.length > emitted) enqueue(s, complete.slice(emitted));
      emitted = Math.max(emitted, complete.length);
    };
    return {
      push(text, seg) {
        if (session.current !== s) return;
        if (seg !== undefined && segment !== undefined && seg !== segment) {
          flush(lastText, true); // a new step began: the previous text is complete
          emitted = 0;
        }
        segment = seg ?? segment;
        lastText = text;
        flush(text, false);
      },
      end(text) {
        if (session.current !== s) return;
        flush(text ?? lastText, true);
        end(s);
      },
    };
  }, [begin, enqueue, end]);

  const preview = useCallback((voiceId: string) => {
    const name = statusRef.current?.voices.find((v) => v.id === voiceId)?.name ?? "AETHER";
    const s = begin(`preview:${voiceId}`);
    const { speed } = prefsRef.current;
    ensureAudio();
    s.inFlight += 1;
    fetch(`${API_BASE}/api/tts/speak`, {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: s.abort.signal,
      body: JSON.stringify({ text: `Hi, I'm ${name}. This is how AETHER will sound when it reads your replies.`, voice: voiceId, speed }),
    })
      .then(async (r) => (r.ok ? audio.current!.ctx.decodeAudioData(await r.arrayBuffer()) : null))
      .then((buf) => { s.inFlight -= 1; s.ready.set(s.nextSeq++, buf); s.finished = true; schedule(s); })
      .catch(() => { if (!s.abort.signal.aborted) finish(s); });
  }, [begin, ensureAudio, schedule, finish]);

  useEffect(() => () => { stop(); void audio.current?.ctx.close(); }, [stop]);

  const value = useMemo<VoiceApi>(() => ({ status, speakingId, loadingId, level, unlock, speak, stream, stop, preview, refresh }),
    [status, speakingId, loadingId, level, unlock, speak, stream, stop, preview, refresh]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useVoice(): VoiceApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useVoice must be used inside <VoiceProvider>");
  return v;
}
