"use client";

/** Model switcher, system health, motion, voice and reminder settings. */
import { CalendarDays, Cpu, Database, RefreshCw } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { usePrefs } from "@/lib/prefs";
import { requestNotifications } from "@/lib/reminders";
import type { Health } from "@/lib/types";
import { useLive } from "@/lib/useLive";
import { speechSupported } from "@/lib/voice";

import { Chip, JiggleButton, Toggle } from "../ui";
import { ErrorNote, inputClass, PanelTitle } from "./shared";

export function SettingsPanel({ health, onHealthChange, revision, toast }: {
  health: Health | null;
  onHealthChange: () => void;
  revision: number;
  toast: (t: string, b?: string) => void;
}) {
  const { prefs, setPref } = usePrefs();
  const models = useLive(api.models, revision);
  const [switching, setSwitching] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const voiceOk = typeof window !== "undefined" && speechSupported();

  async function choose(model: string) {
    setSwitching(true);
    try {
      await api.setModel(model);
      await models.reload();
      onHealthChange();
      toast("Brain switched", model);
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setSwitching(false);
    }
  }

  return (
    <div className="space-y-6">
      <section>
        <PanelTitle title="Brain" hint="Any Ollama model that supports tool calling."
          action={<JiggleButton tone="ghost" className="px-2 py-1" aria-label="Refresh" onClick={() => { void models.reload(); onHealthChange(); }}>
            <RefreshCw className="size-3.5" />
          </JiggleButton>} />
        <div className="glass-inset space-y-3 rounded-2xl p-3">
          <div className="flex items-center gap-2">
            <Cpu className="size-4 text-glacier" />
            <select disabled={switching || !models.data} value={models.data?.current ?? ""}
              onChange={(e) => void choose(e.target.value)} className={inputClass} aria-label="Model">
              {models.data?.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          {models.error && <ErrorNote message={models.error.message} hint={models.error.hint} />}
          {err && <ErrorNote message={err.message} hint={err.hint} />}
          {health?.model_note && <p className="text-xs text-amber">{health.model_note}</p>}
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            <Chip tone={health?.ollama ? "seafoam" : "coral"}>Ollama {health?.ollama ? "online" : "offline"}</Chip>
            <Chip tone={health?.embeddings_available === false ? "amber" : "periwinkle"}>
              <Database className="size-3" />
              {health?.embed_model ?? "embeddings"} {health?.embeddings_available === false ? "missing" : ""}
            </Chip>
            {health && <Chip>{health.tools} tools</Chip>}
            {health && <Chip>{(health.context_length / 1024).toFixed(0)}k context</Chip>}
          </div>
          {health?.embeddings_hint && <p className="font-mono text-[11px] text-frost/75">→ {health.embeddings_hint}</p>}
        </div>
      </section>

      <section>
        <PanelTitle title="Feel" />
        <div className="glass-inset rounded-2xl p-1">
          <Toggle checked={prefs.calm} onChange={(v) => setPref("calm", v)} label="Calm mode"
            hint="Reduced motion: no drifting light, parallax or wobble." />
        </div>
      </section>

      <section>
        <PanelTitle title="Voice" hint={voiceOk
          ? "Speech recognition is provided by your browser. Chrome uses Google's servers; Safari can work on-device. Spoken replies are always local."
          : "Your browser doesn't support voice input. Try Chrome, Edge or Safari."} />
        <div className="glass-inset rounded-2xl p-1">
          <Toggle checked={prefs.autoSendVoice} onChange={(v) => setPref("autoSendVoice", v)} label="Send when I stop talking" />
          <Toggle checked={prefs.speakReplies} onChange={(v) => setPref("speakReplies", v)} label="Read replies aloud" />
          <Toggle checked={prefs.wakeWord} onChange={(v) => setPref("wakeWord", v && voiceOk)} label="“Hey Aether” wake word"
            hint="Listens while this tab is open. Say “Hey Aether, …”." />
        </div>
      </section>

      <section>
        <PanelTitle title="Reminders" />
        <div className="glass-inset rounded-2xl p-1">
          <Toggle checked={prefs.notifications} label="Deadline notifications"
            hint="A desktop alert when something is due within 24 hours, and when it's overdue."
            onChange={async (v) => {
              if (v && !(await requestNotifications())) {
                toast("Notifications are blocked", "Allow them for this site in your browser settings.");
                return;
              }
              setPref("notifications", v);
            }} />
        </div>
        <a href={api.calendarUrl()} download="aether-deadlines.ics"
          className="mt-2 flex items-center gap-2 rounded-2xl px-3 py-2.5 text-sm text-glacier hover:bg-white/[0.04]">
          <CalendarDays className="size-4" /> Export deadlines to your calendar (.ics)
        </a>
      </section>
    </div>
  );
}
