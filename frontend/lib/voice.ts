"use client";

/**
 * Voice: push-to-talk dictation, an optional "Hey Aether" wake word, and spoken replies.
 *
 * Uses the browser's Web Speech API. Speech *synthesis* runs locally. Speech *recognition*
 * is provided by the browser: Chrome sends audio to Google's service, while Safari on macOS
 * can recognise on-device. The settings panel says so.
 */
import { useCallback, useEffect, useRef, useState } from "react";

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
};

function makeRecognition(): Recognition | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) return null;
  const r = new Ctor();
  r.lang = navigator.language || "en-US";
  r.interimResults = true;
  return r;
}

export const speechSupported = () => makeRecognition() !== null;

/** Push-to-talk: start() listens, interim text streams into onText, onFinal fires when you stop. */
export function useDictation(onText: (text: string) => void, onFinal: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<Recognition | null>(null);
  const finalText = useRef("");
  const cbs = useRef({ onText, onFinal });
  cbs.current = { onText, onFinal };

  const stop = useCallback(() => rec.current?.stop(), []);

  const start = useCallback(() => {
    const r = makeRecognition();
    if (!r) {
      setError("Voice input isn't supported in this browser. Try Chrome, Edge or Safari.");
      return;
    }
    stopSpeaking();
    finalText.current = "";
    r.continuous = false;
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal) finalText.current += res[0].transcript;
        else interim += res[0].transcript;
      }
      cbs.current.onText((finalText.current + interim).trim());
    };
    r.onerror = (e) => setError(e.error === "not-allowed" ? "Microphone access was blocked." : `Voice error: ${e.error}`);
    r.onend = () => {
      setListening(false);
      rec.current = null;
      if (finalText.current.trim()) cbs.current.onFinal(finalText.current.trim());
    };
    rec.current = r;
    setError(null);
    setListening(true);
    r.start();
  }, []);

  useEffect(() => () => rec.current?.abort(), []);
  return { listening, start, stop, error };
}

/** Always-on listener that wakes on "Hey Aether" and hands over what follows. */
export function useWakeWord(enabled: boolean, paused: boolean, onCommand: (text: string) => void) {
  const [armed, setArmed] = useState(false);
  const cb = useRef(onCommand);
  cb.current = onCommand;

  useEffect(() => {
    if (!enabled || paused) return;
    const r = makeRecognition();
    if (!r) return;
    let alive = true;
    r.continuous = true;
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (!e.results[i].isFinal) continue;
        const said = e.results[i][0].transcript.trim();
        const m = said.match(/\b(?:hey|hi|okay|ok)[ ,]+(?:aether|ether|either|ather|eather)\b[,.!]?\s*(.*)/i);
        if (m) {
          setArmed(true);
          if (m[1]?.trim()) cb.current(m[1].trim());
          setTimeout(() => setArmed(false), 1500);
        }
      }
    };
    r.onerror = () => undefined;
    r.onend = () => {
      if (alive) {
        try {
          r.start(); // browsers stop continuous recognition periodically; keep it going
        } catch {
          /* already started */
        }
      }
    };
    try {
      r.start();
    } catch {
      /* mic busy */
    }
    return () => {
      alive = false;
      r.abort();
    };
  }, [enabled, paused]);

  return armed;
}

function speakable(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " I've put the code on screen. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[#*_>|~-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function speak(markdown: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const text = speakable(markdown);
  if (!text) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.slice(0, 1200));
  u.rate = 1.03;
  u.pitch = 1;
  window.speechSynthesis.speak(u);
}

export function stopSpeaking() {
  if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
}
