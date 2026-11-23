"use client";

/**
 * Settings → MCP: tools from Model Context Protocol servers.
 *   · your servers: add, edit, switch off or remove (local commands or remote URLs)
 *   · Claude ⇄ AETHER: let Claude Desktop / Claude Code use AETHER, or import Claude's servers
 */
import clsx from "clsx";
import { Check, Globe, Pencil, Plus, Power, RefreshCw, Sparkles, Terminal } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { ClaudeStatus, McpServerConfig, McpServerEntry } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Chip, JiggleButton, Spinner } from "../ui";
import { ConfirmDelete, ErrorNote, inputClass, PanelTitle } from "./shared";

type Toast = (t: string, b?: string) => void;
const MASK = "••••••";
const card = "rounded-xl border border-white/[0.07] bg-white/[0.025] p-3";
const label = "mb-1.5 block text-[11px] font-medium text-mist";
const APPROVAL = [
  { id: "auto", label: "Ask before changes", hint: "Read-only tools run freely; anything else asks you first." },
  { id: "always", label: "Always ask", hint: "You approve every call." },
  { id: "never", label: "Never ask", hint: "Only for servers you fully trust." },
] as const;

const toLines = (o?: Record<string, string>, sep = "=") => Object.entries(o ?? {}).map(([k, v]) => `${k}${sep}${v}`).join("\n");
function fromLines(text: string, sep: "=" | ":"): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf(sep);
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

function ServerForm({ initial, onDone, onCancel }: {
  initial?: McpServerEntry;
  onDone: (name: string) => void;
  onCancel: () => void;
}) {
  const c = initial?.config ?? {};
  const [name, setName] = useState(initial?.name ?? "");
  const [kind, setKind] = useState<"local" | "remote">(c.url ? "remote" : "local");
  const [command, setCommand] = useState(c.command ?? "");
  const [args, setArgs] = useState((c.args ?? []).join("\n"));
  const [env, setEnv] = useState(toLines(c.env));
  const [url, setUrl] = useState(c.url ?? "");
  const [headers, setHeaders] = useState(toLines(c.headers, ": "));
  const [approval, setApproval] = useState<NonNullable<McpServerConfig["approval"]>>(c.approval ?? "auto");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);

  async function save() {
    setBusy(true);
    setErr(null);
    const cfg: McpServerConfig = kind === "local"
      ? { command: command.trim(), args: args.split("\n").map((a) => a.trim()).filter(Boolean), env: fromLines(env, "="), approval,
          disabled: c.disabled }
      : { url: url.trim(), headers: fromLines(headers, ":"), approval, disabled: c.disabled };
    try {
      await api.saveMcpServer(name.trim(), cfg);
      onDone(name.trim());
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={clsx(card, "space-y-3")} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <div className="text-xs font-medium text-frost">{initial ? `Edit ${initial.name}` : "Add an MCP server"}</div>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2.5">
        <label className="block min-w-0">
          <span className={label}>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value.replace(/[^A-Za-z0-9_-]/g, "-"))} disabled={!!initial}
            className={clsx(inputClass, "h-9")} placeholder="filesystem" spellCheck={false} />
        </label>
        <div className="flex h-9 rounded-xl bg-abyss-950/40 p-0.5" role="radiogroup" aria-label="Server type">
          {(["local", "remote"] as const).map((k) => (
            <button key={k} type="button" role="radio" aria-checked={kind === k} onClick={() => setKind(k)}
              className={clsx("inline-flex items-center gap-1 rounded-[10px] px-2.5 text-[11.5px] transition-colors",
                kind === k ? "bg-sky/20 text-frost" : "text-mist hover:text-frost")}>
              {k === "local" ? <Terminal className="size-3" /> : <Globe className="size-3" />}{k === "local" ? "Command" : "URL"}
            </button>
          ))}
        </div>
      </div>
      {kind === "local" ? (
        <>
          <label className="block">
            <span className={label}>Command</span>
            <input value={command} onChange={(e) => setCommand(e.target.value)} className={clsx(inputClass, "h-9 font-mono text-xs")}
              placeholder="npx" spellCheck={false} />
          </label>
          <label className="block">
            <span className={label}>Arguments <span className="font-normal text-mist/70">· one per line</span></span>
            <textarea value={args} onChange={(e) => setArgs(e.target.value)} rows={3} spellCheck={false}
              className={clsx(inputClass, "resize-y font-mono text-xs")} placeholder={"-y\n@modelcontextprotocol/server-filesystem\n./workspace"} />
          </label>
          <label className="block">
            <span className={label}>Environment <span className="font-normal text-mist/70">· KEY=value per line, e.g. tokens</span></span>
            <textarea value={env} onChange={(e) => setEnv(e.target.value)} rows={2} spellCheck={false}
              className={clsx(inputClass, "resize-y font-mono text-xs")} placeholder="GITHUB_TOKEN=…" />
          </label>
        </>
      ) : (
        <>
          <label className="block">
            <span className={label}>Server URL</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} className={clsx(inputClass, "h-9 font-mono text-xs")}
              placeholder="https://example.com/mcp" spellCheck={false} inputMode="url" />
          </label>
          <label className="block">
            <span className={label}>Headers <span className="font-normal text-mist/70">· Name: value per line, e.g. a token</span></span>
            <textarea value={headers} onChange={(e) => setHeaders(e.target.value)} rows={2} spellCheck={false}
              className={clsx(inputClass, "resize-y font-mono text-xs")} placeholder="Authorization: Bearer …" />
          </label>
          <p className="text-[10.5px] text-mist/80">Servers that need you to sign in through a browser (OAuth) aren&apos;t supported yet; ones that take a token work.</p>
        </>
      )}
      {(env.includes(MASK) || headers.includes(MASK)) && (
        <p className="text-[10.5px] text-mist/80">{MASK} = a saved secret, kept unless you replace it.</p>
      )}
      <div>
        <span className={label}>Approval</span>
        <div className="grid grid-cols-3 gap-1.5" role="radiogroup" aria-label="Approval">
          {APPROVAL.map((a) => (
            <button key={a.id} type="button" role="radio" aria-checked={approval === a.id} onClick={() => setApproval(a.id)} title={a.hint}
              className={clsx("rounded-lg border px-1.5 py-1.5 text-[11px] transition-colors",
                approval === a.id ? "border-glacier/40 bg-sky/[0.12] text-frost" : "border-white/[0.07] text-mist hover:text-frost")}>
              {a.label}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[10.5px] text-mist/80">{APPROVAL.find((a) => a.id === approval)?.hint}</p>
      </div>
      <div className="flex gap-2">
        <JiggleButton type="submit" tone="primary" className="h-9 flex-1 rounded-xl text-sm"
          disabled={busy || !name.trim() || (kind === "local" ? !command.trim() : !url.trim())}>
          {busy ? <><RefreshCw className="size-3.5 animate-spin" /> Starting…</> : <><Check className="size-3.5" /> Save &amp; start</>}
        </JiggleButton>
        <JiggleButton tone="ghost" className="h-9 rounded-xl px-3 text-sm" onClick={onCancel}>Cancel</JiggleButton>
      </div>
      {err && <ErrorNote message={err.message} hint={err.hint} />}
    </form>
  );
}

function ServerCard({ s, onEdit, onChanged, toast }: { s: McpServerEntry; onEdit: () => void; onChanged: () => void; toast: Toast }) {
  const st = s.status;
  const remote = !!s.config.url;
  const tone = { connected: "seafoam", error: "coral", disabled: "mist", starting: "glacier" } as const;
  async function toggle() {
    try {
      await api.saveMcpServer(s.name, { ...s.config, disabled: !s.config.disabled });
      onChanged();
    } catch (e) { toast(e instanceof ApiError ? e.message : "Couldn't change it"); }
  }
  return (
    <div className={card}>
      <div className="flex items-center gap-2.5">
        <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-white/[0.05] text-glacier">
          {remote ? <Globe className="size-4" /> : <Terminal className="size-4" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm text-frost">{s.name}</span>
            <Chip tone={tone[st?.state ?? "disabled"]}>{st?.state ?? "not started"}</Chip>
            {st?.state === "connected" && <Chip>{st.tools} tools</Chip>}
          </div>
          <div className="truncate font-mono text-[10.5px] text-mist">{s.config.url ?? [s.config.command, ...(s.config.args ?? [])].join(" ")}</div>
        </div>
        <button type="button" onClick={() => void toggle()} title={s.config.disabled ? "Turn on" : "Turn off"}
          aria-label={s.config.disabled ? `Turn ${s.name} on` : `Turn ${s.name} off`}
          className={clsx("grid size-7 place-items-center rounded-lg hover:bg-white/[0.08]", s.config.disabled ? "text-mist" : "text-seafoam")}>
          <Power className="size-3.5" />
        </button>
        <button type="button" onClick={onEdit} aria-label={`Edit ${s.name}`} className="grid size-7 place-items-center rounded-lg text-mist hover:bg-white/[0.08] hover:text-frost">
          <Pencil className="size-3.5" />
        </button>
        <ConfirmDelete onConfirm={async () => { await api.deleteMcpServer(s.name); onChanged(); }} />
      </div>
      {st?.error && <p className="mt-1.5 break-words text-[11px] text-coral">{st.error}</p>}
    </div>
  );
}

function ClaudeBridge({ c, onChanged, toast }: { c: ClaudeStatus; onChanged: () => void; toast: Toast }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const key = (s: { source: string; name: string }) => `${s.source}|${s.name}`;

  async function link(target: "desktop" | "code", on: boolean) {
    setBusy(target);
    try {
      const r = await api.connectClaude(target, on);
      toast(on ? `AETHER added to Claude ${target === "desktop" ? "Desktop" : "Code"}` : "Removed from Claude", r.next);
      onChanged();
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      toast(err?.message ?? "Couldn't change Claude's settings", err?.hint);
    } finally {
      setBusy(null);
    }
  }

  async function importPicked() {
    setBusy("import");
    try {
      const items = c.found.filter((f) => picked.has(key(f))).map(({ source, name }) => ({ source, name }));
      const r = await api.importClaude(items);
      toast(`Imported ${r.added.length} server${r.added.length === 1 ? "" : "s"}`, "They ask before every call until you change that.");
      setPicked(new Set());
      onChanged();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Couldn't import");
    } finally {
      setBusy(null);
    }
  }

  const rows = [
    { id: "desktop" as const, name: "Claude Desktop", ...c.aether.desktop },
    { id: "code" as const, name: "Claude Code", ...c.aether.code },
  ];
  return (
    <div className="space-y-2.5">
      <div className={card}>
        <div className="flex items-center gap-1.5 text-xs font-medium text-frost"><Sparkles className="size-3.5 text-periwinkle" />Let Claude use AETHER</div>
        <p className="mt-1 text-[11px] leading-snug text-mist">
          Claude gets AETHER&apos;s tools for your documents, memory, tasks, timetable and notes, as an MCP server that runs on this
          Mac. Claude asks you before using them. Nothing that runs code or deletes files is included.
        </p>
        <div className="mt-2.5 space-y-1.5">
          {rows.map((r) => (
            <div key={r.id} className="flex items-center gap-2">
              <span className="flex-1 text-[12.5px] text-frost">{r.name}</span>
              {r.connected ? (
                <>
                  <Chip tone="seafoam"><Check className="size-3" />connected</Chip>
                  <JiggleButton tone="ghost" className="h-7 rounded-lg px-2 text-[11px]" disabled={!!busy} onClick={() => void link(r.id, false)}>Remove</JiggleButton>
                </>
              ) : (
                <JiggleButton tone="primary" className="h-7 rounded-lg px-3 text-[11px]" disabled={!!busy || !r.installed}
                  onClick={() => void link(r.id, true)}>
                  {busy === r.id ? <Spinner /> : r.installed ? "Connect" : "Not installed"}
                </JiggleButton>
              )}
            </div>
          ))}
        </div>
        <details className="mt-2 text-[10.5px] text-mist">
          <summary className="cursor-pointer text-glacier">Set it up by hand</summary>
          <p className="mt-1">Claude Code, in a terminal:</p>
          <code className="mt-1 block break-all rounded-md bg-black/30 px-2 py-1.5 font-mono text-glacier">{c.aether.code_command}</code>
          <p className="mt-1">Any MCP client: run <span className="font-mono">{c.aether.command}</span> over stdio.</p>
        </details>
      </div>

      <div className={card}>
        <div className="text-xs font-medium text-frost">Import from Claude</div>
        {c.found.length === 0 ? (
          <p className="mt-1 text-[11px] text-mist">No MCP servers are set up in Claude Desktop or Claude Code on this Mac yet. Ones you add there show up here.</p>
        ) : (
          <>
            <p className="mt-1 text-[11px] text-mist">Use the same servers in AETHER. Imported ones ask before every call.</p>
            <div className="mt-2 space-y-1">
              {c.found.map((f) => (
                <label key={key(f)} className={clsx("flex items-start gap-2 rounded-lg px-1.5 py-1", f.supported && !f.added && "cursor-pointer hover:bg-white/[0.04]")}>
                  <input type="checkbox" className="mt-0.5 accent-sky" disabled={!f.supported || f.added} checked={picked.has(key(f))}
                    onChange={(e) => setPicked((p) => { const n = new Set(p); if (e.target.checked) n.add(key(f)); else n.delete(key(f)); return n; })} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 text-[12px] text-frost">
                      {f.kind === "remote" ? <Globe className="size-3 text-mist" /> : <Terminal className="size-3 text-mist" />}{f.name}
                      {f.added && <Chip tone="seafoam">added</Chip>}
                    </span>
                    <span className="block truncate font-mono text-[10px] text-mist">{f.source} · {f.command}</span>
                    {f.note && <span className="block text-[10.5px] text-amber">{f.note}</span>}
                  </span>
                </label>
              ))}
            </div>
            <JiggleButton tone="primary" className="mt-2 h-8 w-full rounded-lg text-xs" disabled={!picked.size || !!busy} onClick={() => void importPicked()}>
              {busy === "import" ? <Spinner /> : `Import ${picked.size || ""} selected`.replace("  ", " ")}
            </JiggleButton>
          </>
        )}
      </div>
    </div>
  );
}

export function McpSection({ revision, toast }: { revision: number; toast: Toast }) {
  const cfg = useLive(api.mcpConfig, revision, (d) => (d?.servers.some((s) => s.status?.state === "starting") ? 1500 : 20_000));
  const claude = useLive(api.mcpClaude, revision);
  const [editing, setEditing] = useState<McpServerEntry | "new" | null>(null);
  const [reloading, setReloading] = useState(false);
  const changed = () => { void cfg.reload(); void claude.reload(); };

  return (
    <section>
      <PanelTitle title="MCP servers"
        hint="Extra tools from Model Context Protocol servers: local commands or remote URLs. Same format as Claude."
        action={<JiggleButton tone="ghost" className="px-2 py-1" aria-label="Restart MCP servers" disabled={reloading}
          onClick={async () => {
            setReloading(true);
            try { await api.reloadMcp(); changed(); toast("MCP servers restarted"); }
            catch (e) { toast(e instanceof ApiError ? e.message : "Restart failed"); }
            finally { setReloading(false); }
          }}>
          <RefreshCw className={reloading ? "size-3.5 animate-spin" : "size-3.5"} />
        </JiggleButton>} />
      <div className="glass-inset space-y-2.5 rounded-2xl p-3">
        {cfg.error && <ErrorNote message={cfg.error.message} hint={cfg.error.hint} />}
        {cfg.data?.servers.map((s) => (editing !== "new" && editing?.name === s.name
          ? <ServerForm key={s.name} initial={s} onCancel={() => setEditing(null)}
              onDone={(n) => { setEditing(null); changed(); toast(`${n} saved`); }} />
          : <ServerCard key={s.name} s={s} onEdit={() => setEditing(s)} onChanged={changed} toast={toast} />))}
        {cfg.data && cfg.data.servers.length === 0 && editing !== "new" && (
          <p className="text-[11px] text-mist">No servers yet. Add one, or import what you use in Claude.</p>
        )}
        {editing === "new" ? (
          <ServerForm onCancel={() => setEditing(null)} onDone={(n) => { setEditing(null); changed(); toast(`${n} added`, "Its tools are now available to AETHER."); }} />
        ) : (
          <JiggleButton tone="ghost" className="h-9 w-full rounded-xl border border-dashed border-white/[0.12] text-sm" onClick={() => setEditing("new")}>
            <Plus className="size-3.5" />Add a server
          </JiggleButton>
        )}
        {claude.data && <ClaudeBridge c={claude.data} onChanged={changed} toast={toast} />}
      </div>
    </section>
  );
}
