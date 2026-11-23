"use client";

/**
 * Settings → Computers on your network: GPU PCs whose Ollama AETHER can use. Each computer can
 * have a home address and a remote (Tailscale) address; AETHER uses whichever answers, so the
 * same PC works at home and from anywhere else.
 */
import clsx from "clsx";
import { Globe, House, MonitorSmartphone, Plug, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { AddressEye, useHideAddresses } from "@/lib/privacy";
import type { NetworkHost } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Chip, JiggleButton } from "../ui";
import { ErrorNote, inputClass, PanelTitle } from "./shared";

const RAW = "https://raw.githubusercontent.com/Piyuz-ui/aether/main/pc";
const dl = (file: string) =>
  `Invoke-WebRequest ${RAW}/${file} -OutFile "$env:USERPROFILE\\Downloads\\${file}"`;
const run = (file: string) => `powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\\Downloads\\${file}"`;

const ROUTE = {
  home: { label: "Home network", icon: <House className="size-3" /> },
  tailscale: { label: "Tailscale", icon: <Globe className="size-3" /> },
  internet: { label: "Internet", icon: <Globe className="size-3" /> },
} as const;

function Code({ children }: { children: string }) {
  return <code className="mt-1 block break-all rounded-lg bg-black/30 px-2 py-1.5 font-mono text-[10.5px] text-glacier">{children}</code>;
}

function HostCard({ h, onRemove }: { h: NetworkHost; onRemove: () => void }) {
  const route = ROUTE[h.route ?? "home"];
  const { show } = useHideAddresses();
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] px-3 py-2.5">
      <div className="flex items-center gap-3">
        <div className={clsx("grid size-9 shrink-0 place-items-center rounded-lg",
          h.online ? "bg-seafoam/[0.12] text-seafoam" : "bg-white/[0.05] text-mist")}>
          <MonitorSmartphone className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-frost">{h.name}</span>
            <Chip tone={h.online ? "seafoam" : "coral"}>{h.online ? "online" : "offline"}</Chip>
            {h.online && <Chip>{h.models.length} models</Chip>}
          </div>
          {h.online
            ? <div className="flex items-center gap-1 text-[11px] text-mist">{route.icon} via {route.label}</div>
            : <p className="text-[11px] leading-snug text-mist">Not answering on any address. Is it on, awake and running Ollama?</p>}
        </div>
        <JiggleButton tone="ghost" className="size-8 shrink-0 rounded-lg p-0!" aria-label={`Remove ${h.name}`} onClick={onRemove}>
          <Trash2 className="size-3.5 shrink-0" />
        </JiggleButton>
      </div>
      <div className="mt-2 space-y-1 border-t border-white/[0.05] pt-2">
        {(h.addresses ?? [{ url: h.url, kind: "home" as const, active: true }]).map((a) => (
          <div key={a.url} className="flex items-center gap-2 text-[11px]">
            <span className={clsx("inline-flex w-[5.5rem] shrink-0 items-center gap-1", a.active && h.online ? "text-seafoam" : "text-mist")}>
              {ROUTE[a.kind].icon}{a.kind === "tailscale" ? "Remote" : ROUTE[a.kind].label.replace(" network", "")}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-frost/80">{show(a.url.replace(/^https?:\/\//, ""))}</span>
            {a.active && h.online && <span className="text-[10px] uppercase tracking-wider text-seafoam">in use</span>}
          </div>
        ))}
        {h.addresses && !h.addresses.some((a) => a.kind === "tailscale") && (
          <p className="pt-0.5 text-[10.5px] text-mist/80">No remote address yet: it only works on your home network.</p>
        )}
      </div>
    </div>
  );
}

export function HostsSection({ revision, onChanged, toast }: {
  revision: number;
  onChanged: () => void;
  toast: (t: string, b?: string) => void;
}) {
  const hosts = useLive(api.hosts, revision, () => 20_000);
  const [name, setName] = useState("pc");
  const [url, setUrl] = useState("");
  const [remote, setRemote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [guide, setGuide] = useState<"home" | "away" | null>(null);

  const existing = hosts.data?.find((h) => h.name === name.trim().toLowerCase());
  const { hidden, show } = useHideAddresses();
  // Editing an existing computer: start from its saved addresses (e.g. to add the remote one).
  useEffect(() => {
    if (!existing) return;
    const addrs = existing.addresses ?? [];
    const strip = (u?: string) => (u ?? "").replace(/^https?:\/\//, "").replace(/:11434$/, "");
    setUrl((v) => v || strip(addrs.find((a) => a.kind !== "tailscale")?.url ?? existing.url));
    setRemote((v) => v || strip(addrs.find((a) => a.kind === "tailscale")?.url));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing?.name, existing?.addresses?.length]);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const h = await api.setHost(name.trim().toLowerCase(), url.trim(), remote.trim());
      const missed = h.unreachable ?? [];
      toast(`${h.name} saved`, missed.length
        ? `Using ${ROUTE[h.route ?? "home"].label}. ${missed.map((u) => show(u.replace(/^https?:\/\//, ""))).join(", ")} didn't answer right now (is Tailscale on on both?), but it's saved for later.`
        : `${h.models.length} model${h.models.length === 1 ? "" : "s"} on it are in the model menu.`);
      setUrl("");
      setRemote("");
      void hosts.reload();
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setBusy(false);
    }
  }

  async function remove(n: string) {
    try {
      await api.removeHost(n);
      toast(`${n} removed`);
      void hosts.reload();
      onChanged();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Couldn't remove it");
    }
  }

  return (
    <section>
      <PanelTitle title="Computers on your network" action={<AddressEye />}
        hint="Run models on another computer's GPU, at home or from anywhere via Tailscale. Private: only your own devices can reach it." />
      <div className="glass-inset space-y-3 rounded-2xl p-3">
        {hosts.data?.map((h) => <HostCard key={h.name} h={h} onRemove={() => void remove(h.name)} />)}

        <form className="space-y-3 rounded-xl border border-white/[0.07] bg-white/[0.025] p-3"
          onSubmit={(e) => { e.preventDefault(); if ((url.trim() || remote.trim()) && name.trim()) void save(); }}>
          <div className="text-xs font-medium text-frost">
            {existing ? `Edit ${existing.name}` : hosts.data?.length ? "Add another computer" : "Add a computer"}
          </div>
          <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,5fr)] gap-2.5">
            <label className="block min-w-0">
              <span className="mb-1.5 block text-[11px] font-medium text-mist">Name</span>
              <input value={name} onChange={(e) => { setName(e.target.value); setUrl(""); setRemote(""); }}
                spellCheck={false} autoComplete="off" className={clsx(inputClass, "h-10")} placeholder="pc" />
            </label>
            <label className="block min-w-0">
              <span className="mb-1.5 flex items-center gap-1 text-[11px] font-medium text-mist"><House className="size-3" />Home address</span>
              <input value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false} autoComplete="off" inputMode="url"
                type={hidden ? "password" : "text"}
              onFocus={(e) => e.currentTarget.select()} className={clsx(inputClass, "h-10 font-mono")} placeholder="192.168.1.9" />
            </label>
          </div>
          <label className="block">
            <span className="mb-1.5 flex items-center gap-1 text-[11px] font-medium text-mist">
              <Globe className="size-3" />Remote address <span className="font-normal text-mist/70">· Tailscale, optional</span>
            </span>
            <input value={remote} onChange={(e) => setRemote(e.target.value)} spellCheck={false} autoComplete="off" inputMode="url"
              type={hidden ? "password" : "text"}
              onFocus={(e) => e.currentTarget.select()} className={clsx(inputClass, "h-10 font-mono")} placeholder="100.x.y.z or pc-name.ts.net" />
          </label>
          <p className="text-[11px] leading-snug text-mist">
            AETHER uses the home address when it answers and the remote one everywhere else, switching by itself.
            Models appear as <span className="font-mono text-frost/80">model@{name.trim().toLowerCase() || "pc"}</span>.
          </p>
          <JiggleButton type="submit" tone="primary" className="h-10 w-full rounded-xl"
            disabled={busy || !name.trim() || !(url.trim() || remote.trim())}>
            {busy ? <><RefreshCw className="size-3.5 animate-spin" /> Checking…</> : <><Plug className="size-3.5" /> {existing ? "Save" : "Connect"}</>}
          </JiggleButton>
          {err && <ErrorNote message={err.message} hint={err.hint} />}
        </form>

        {(["home", "away"] as const).map((g) => (
          <div key={g}>
            <button type="button" onClick={() => setGuide((v) => (v === g ? null : g))} aria-expanded={guide === g}
              className="flex w-full items-center justify-between rounded-lg px-1 text-[11.5px] text-glacier hover:text-frost">
              {g === "home" ? "Set up a Windows PC (RTX GPU) at home" : "Use it away from home (college, anywhere)"}
              <span aria-hidden>{guide === g ? "−" : "+"}</span>
            </button>
            {guide === g && g === "home" && (
              <ol className="mt-2 list-decimal space-y-2 pl-5 text-[11px] leading-relaxed text-frost/85">
                <li>On the PC, install the latest Ollama from <span className="font-mono text-glacier">ollama.com</span>.</li>
                <li>In PowerShell (<i>Run as administrator</i>), download and run the setup script:
                  <Code>{dl("setup-ollama-pc.ps1")}</Code><Code>{run("setup-ollama-pc.ps1")}</Code></li>
                <li>Enter the address it prints as the <b>Home address</b> above.</li>
              </ol>
            )}
            {guide === g && g === "away" && (
              <ol className="mt-2 list-decimal space-y-2 pl-5 text-[11px] leading-relaxed text-frost/85">
                <li>Uses <span className="font-mono text-glacier">Tailscale</span>: a free private network for only your own devices.
                  Nothing is opened to the internet.</li>
                <li>On the PC, in PowerShell (<i>Run as administrator</i>):
                  <Code>{dl("setup-remote-access.ps1")}</Code><Code>{run("setup-remote-access.ps1")}</Code>
                  It installs Tailscale (log in when the browser opens), lets only your devices in, keeps the PC awake while plugged in, and prints its remote address.</li>
                <li>On this Mac, install Tailscale from <span className="font-mono text-glacier">tailscale.com/download</span> and log in with the <b>same account</b>.</li>
                <li>Type the printed address as the <b>Remote address</b> above (name <span className="font-mono">pc</span>) and press Save.</li>
                <li>Before you leave: PC plugged in and logged in, and Windows Update set not to restart it during the day.</li>
              </ol>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
