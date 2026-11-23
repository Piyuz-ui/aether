"use client";

/** Model switcher, system health, motion, voice and reminder settings. */
import clsx from "clsx";
import { motion } from "framer-motion";
import { AudioLines, CalendarDays, Cloud, Cpu, Database, ExternalLink, KeyRound, Plug, RefreshCw, Trash2, Volume2 } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { jelly } from "@/lib/motion";
import { usePrefs } from "@/lib/prefs";
import { useVoice } from "@/lib/useNeuralVoice";
import { requestNotifications } from "@/lib/reminders";
import type { CloudProvider, Health } from "@/lib/types";
import { useLive } from "@/lib/useLive";
import { speechSupported } from "@/lib/voice";

import { Chip, JiggleButton, Toggle } from "../ui";
import { HardwareSection } from "./HardwareSection";
import { HostsSection } from "./HostsSection";
import { ErrorNote, inputClass, PanelTitle } from "./shared";

const ENGINE_LABEL: Record<string, string> = {
  "kokoro-mlx": "Kokoro · Apple GPU (MLX)", "kokoro-onnx": "Kokoro · local neural", fallback: "Browser voice (fallback)",
};

function NeuralVoiceSection() {
  const { prefs, setPref } = usePrefs();
  const voice = useVoice();
  const st = voice.status;
  const neural = st && st.engine !== "fallback";
  const stateText = !st ? "checking…" : st.state === "downloading"
    ? `downloading model ${Math.round((st.progress ?? 0) * 100)}%`
    : st.state === "needs_download" ? "model downloads on first use (~350 MB)"
    : st.state === "loading" ? "warming up…" : st.state === "ready" ? "warm & ready" : st.state === "idle" ? "loads on first use" : st.state;
  return (
    <section>
      <PanelTitle title="AETHER's voice" hint="Natural speech generated on this Mac by Kokoro-82M. Nothing leaves your computer." />
      <div className="glass-inset space-y-4 rounded-2xl p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip tone={neural ? "seafoam" : "amber"}><AudioLines className="size-3" />{ENGINE_LABEL[st?.engine ?? ""] ?? st?.engine ?? "voice"}</Chip>
          <Chip tone={st?.state === "ready" ? "glacier" : "mist"}>{stateText}</Chip>
        </div>
        {st?.hint && <p className="font-mono text-[11px] text-frost/75">→ {st.hint}</p>}
        {st?.error && <p className="text-[11px] text-coral">{st.error.message} {st.error.hint}</p>}

        <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Voice">
          {(st?.voices ?? []).map((v) => {
            const selected = prefs.voice === v.id;
            const previewing = voice.speakingId === `preview:${v.id}` || voice.loadingId === `preview:${v.id}`;
            return (
              <motion.button key={v.id} type="button" role="radio" aria-checked={selected}
                onClick={() => { setPref("voice", v.id); if (neural) { voice.unlock(); voice.preview(v.id); } }}
                whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} transition={jelly}
                className={clsx("relative rounded-2xl border px-3 py-2.5 text-left transition-colors",
                  selected ? "border-glacier/50 bg-sky/[0.14]" : "border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06]")}>
                <div className="flex items-center gap-1.5 text-sm font-medium text-frost">
                  {v.name}
                  {previewing && <Volume2 className="size-3.5 animate-pulse text-seafoam" />}
                </div>
                <div className="text-[11px] text-mist">{v.description}</div>
                <div className="text-[10px] uppercase tracking-wider text-mist/60">{v.accent}</div>
              </motion.button>
            );
          })}
        </div>

        <label className="block">
          <div className="mb-1.5 flex justify-between text-xs">
            <span className="text-frost">Speaking speed</span>
            <span className="font-mono text-glacier">{prefs.speed.toFixed(2)}×</span>
          </div>
          <input type="range" min={0.85} max={1.25} step={0.05} value={prefs.speed}
            onChange={(e) => setPref("speed", Number(e.target.value))}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-sky" aria-label="Speaking speed" />
          <div className="mt-1 flex justify-between text-[10px] text-mist/60"><span>0.85×</span><span>1×</span><span>1.25×</span></div>
        </label>

        <div className="-mx-2">
          <Toggle checked={prefs.speakReplies} onChange={(v) => { if (v) voice.unlock(); else voice.stop(); setPref("speakReplies", v); }}
            label="Auto-speak replies" hint="Starts talking at the first finished sentence, while the rest is still being written." />
        </div>
      </div>
    </section>
  );
}

function CloudProviderRow({ p, onChanged, toast }: {
  p: CloudProvider;
  onChanged: () => void;
  toast: (t: string, b?: string) => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const saved = await api.setProviderKey(p.id, key.trim());
      setKey("");
      toast(`${p.label} connected`, `${saved.models.length} model${saved.models.length === 1 ? "" : "s"} added to the model menu.`);
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.removeProviderKey(p.id);
      toast(`${p.label} removed`, "Its key was deleted from backend/.env.");
      onChanged();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Couldn't remove the key");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Cloud className="size-4 text-periwinkle" />
        <span className="text-sm text-frost">{p.label}</span>
        <Chip tone={p.free ? "seafoam" : "amber"}>{p.free ? "free tier" : "paid"}</Chip>
        {p.configured && <Chip tone="glacier">connected · {p.models.length} models</Chip>}
        <span className="flex-1" />
        {p.configured && (
          <JiggleButton tone="ghost" className="px-2 py-1" aria-label={`Remove ${p.label} key`} disabled={busy} onClick={() => void remove()}>
            <Trash2 className="size-3.5" />
          </JiggleButton>
        )}
      </div>
      <p className="text-[11px] leading-snug text-mist">{p.note}</p>
      {!p.configured && (
        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (key.trim().length >= 8) void save(); }}>
          <KeyRound className="size-4 shrink-0 text-mist" />
          <input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)}
            placeholder={`Paste your ${p.key_env}`} aria-label={`${p.label} API key`} className={inputClass} />
          <JiggleButton type="submit" className="shrink-0 px-3 py-1.5 text-xs" disabled={busy || key.trim().length < 8}>
            {busy ? "Checking…" : "Connect"}
          </JiggleButton>
        </form>
      )}
      {!p.configured && (
        <a href={p.key_url} target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-glacier underline-offset-2 hover:underline">
          Get a key <ExternalLink className="size-3" />
        </a>
      )}
      {err && <ErrorNote message={err.message} hint={err.hint} />}
      {p.error && <ErrorNote message={p.error.message} hint={p.error.hint} />}
    </div>
  );
}

function CloudSection({ revision, onChanged, toast }: {
  revision: number;
  onChanged: () => void;
  toast: (t: string, b?: string) => void;
}) {
  const providers = useLive(api.providers, revision);
  return (
    <section>
      <PanelTitle title="Cloud brains" hint="Optional. Local models stay the default; a cloud model is only used while you pick it." />
      <div className="glass-inset space-y-2 rounded-2xl p-3">
        <p className="text-[11px] leading-relaxed text-amber/90">
          While a cloud model is selected, your messages (plus any memories and document passages AETHER adds) are
          sent to that provider. Embeddings, documents and memory stay on this Mac. Keys are checked, then saved
          only to <code className="font-mono">backend/.env</code>, which git ignores.
        </p>
        {providers.error && <ErrorNote message={providers.error.message} hint={providers.error.hint} />}
        {([["Free tiers", true], ["Paid", false]] as const).map(([title, free]) => {
          const group = providers.data?.filter((p) => p.free === free) ?? [];
          if (!group.length) return null;
          // Connected providers first, so what you use is at the top.
          const sorted = [...group].sort((a, b) => Number(b.configured) - Number(a.configured));
          return (
            <div key={title} className="space-y-2">
              <div className="px-1 pt-1 text-[10.5px] uppercase tracking-[0.14em] text-mist">
                {title} · {group.filter((p) => p.configured).length}/{group.length} connected
              </div>
              {sorted.map((p) => (
                <CloudProviderRow key={p.id} p={p} toast={toast} onChanged={() => { void providers.reload(); onChanged(); }} />
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function McpSection({ revision, toast }: { revision: number; toast: (t: string, b?: string) => void }) {
  const mcp = useLive(api.mcp, revision, (d) => (d?.servers.some((s) => s.state === "starting") ? 1500 : 30_000));
  const [reloading, setReloading] = useState(false);
  const tone = { connected: "seafoam", error: "coral", disabled: "mist", starting: "glacier" } as const;
  return (
    <section>
      <PanelTitle title="MCP servers" hint="External tools via the Model Context Protocol, from backend/mcp_servers.json."
        action={<JiggleButton tone="ghost" className="px-2 py-1" aria-label="Reload MCP servers" disabled={reloading}
          onClick={async () => {
            setReloading(true);
            try { await api.reloadMcp(); await mcp.reload(); toast("MCP servers reloaded"); }
            catch (e) { toast(e instanceof ApiError ? e.message : "Reload failed"); }
            finally { setReloading(false); }
          }}>
          <RefreshCw className={reloading ? "size-3.5 animate-spin" : "size-3.5"} />
        </JiggleButton>} />
      <div className="glass-inset space-y-2 rounded-2xl p-3">
        {mcp.data && mcp.data.servers.length === 0 && (
          <p className="text-xs leading-relaxed text-mist">
            None yet. Copy <code className="font-mono text-glacier">mcp_servers.example.json</code> to{" "}
            <code className="font-mono text-glacier">mcp_servers.json</code> in the backend folder (same format as
            Claude Desktop), then reload.
          </p>
        )}
        {mcp.data?.servers.map((s) => (
          <div key={s.name} className="flex items-start gap-2.5">
            <Plug className="mt-0.5 size-4 shrink-0 text-glacier" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-sm text-frost">{s.name}</span>
                <Chip tone={tone[s.state]}>{s.state}</Chip>
                {s.state === "connected" && <Chip>{s.tools} tools</Chip>}
                <Chip tone={s.approval === "never" ? "amber" : "mist"}>approval {s.approval}</Chip>
              </div>
              {s.error && <p className="mt-1 break-words text-[11px] text-coral">{s.error}</p>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

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
        <PanelTitle title="Brain" hint="Any Ollama model that supports tool calling, or a cloud brain you've connected below."
          action={<JiggleButton tone="ghost" className="px-2 py-1" aria-label="Refresh" onClick={() => { void models.reload(); onHealthChange(); }}>
            <RefreshCw className="size-3.5" />
          </JiggleButton>} />
        <div className="glass-inset space-y-3 rounded-2xl p-3">
          <div className="flex items-center gap-2">
            <Cpu className="size-4 text-glacier" />
            <select disabled={switching || !models.data} value={models.data?.current ?? ""}
              onChange={(e) => void choose(e.target.value)} className={inputClass} aria-label="Model">
              <optgroup label="On this Mac">
                {models.data?.models.filter((m) => !/embed|bge-|minilm|rerank/i.test(m)).map((m) => <option key={m} value={m}>{m}</option>)}
              </optgroup>
              {models.data?.hosts?.filter((h) => h.online).map((h) => (
                <optgroup key={h.name} label={`On ${h.name} (your network)`}>
                  {h.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </optgroup>
              ))}
              {models.data?.cloud?.filter((p) => p.configured).map((p) => (
                <optgroup key={p.id} label={`${p.label} (cloud)`}>
                  {p.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </optgroup>
              ))}
            </select>
          </div>
          {models.error && <ErrorNote message={models.error.message} hint={models.error.hint} />}
          {err && <ErrorNote message={err.message} hint={err.hint} />}
          {health?.model_note && <p className="text-xs text-amber">{health.model_note}</p>}
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            <Chip tone={health?.ollama ? "seafoam" : "coral"}>Ollama {health?.ollama ? "online" : "offline"}</Chip>
            {health?.cloud && <Chip tone="amber"><Cloud className="size-3" />cloud · {health.provider}</Chip>}
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

      <HardwareSection revision={revision} />

      <HostsSection revision={revision} toast={toast} onChanged={() => { void models.reload(); onHealthChange(); }} />

      <CloudSection revision={revision} toast={toast} onChanged={() => { void models.reload(); onHealthChange(); }} />

      <McpSection revision={revision} toast={toast} />

      <section>
        <PanelTitle title="Feel" />
        <div className="glass-inset rounded-2xl p-1">
          <Toggle checked={prefs.calm} onChange={(v) => setPref("calm", v)} label="Calm mode"
            hint="Reduced motion: no drifting light, parallax or wobble." />
        </div>
      </section>

      <NeuralVoiceSection />

      <section>
        <PanelTitle title="Talking to AETHER" hint={voiceOk
          ? "Speech recognition is provided by your browser. Chrome uses Google's servers; Safari can work on-device."
          : "Your browser doesn't support voice input. Try Chrome, Edge or Safari."} />
        <div className="glass-inset rounded-2xl p-1">
          <Toggle checked={prefs.autoSendVoice} onChange={(v) => setPref("autoSendVoice", v)} label="Send when I stop talking" />
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
