"use client";

/**
 * Usage: what cloud models cost and how hard they're used. This month and today, a monthly
 * budget, the last 14 days, each model's tokens / context / speed / cost, and what free tiers
 * say is left. Prices: Claude built in, free tiers $0, others entered once by you.
 */
import clsx from "clsx";
import { motion } from "framer-motion";
import { Pencil, Wallet } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { jelly } from "@/lib/motion";
import type { ModelUsage, UsageSummary } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Chip, JiggleButton } from "../ui";
import { inputClass, PanelTitle } from "./shared";

export const money = (n: number | null | undefined) =>
  n == null ? "–" : n === 0 ? "$0" : n < 0.01 ? `$${n.toFixed(4)}` : n < 100 ? `$${n.toFixed(2)}` : `$${Math.round(n).toLocaleString()}`;
const tokens = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : `${n}`);

function Bar({ pct, tone }: { pct: number; tone?: "ok" | "near" | "over" }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
      <motion.div className={clsx("h-full rounded-full",
        tone === "over" ? "bg-coral" : tone === "near" ? "bg-amber" : "bg-gradient-to-r from-sky to-seafoam")}
        initial={false} animate={{ width: `${Math.min(100, Math.max(pct, pct > 0 ? 2 : 0))}%` }} transition={jelly} />
    </div>
  );
}

function Budget({ u, onSaved }: { u: UsageSummary; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(u.budget.monthly_usd?.toString() ?? "");
  const b = u.budget;
  async function save(v: number | null) {
    await api.setBudget(v);
    setEditing(false);
    onSaved();
  }
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-frost"><Wallet className="size-3.5 text-mist" />Monthly budget</span>
        {!editing && (
          <button type="button" onClick={() => setEditing(true)} className="inline-flex items-center gap-1 text-[11px] text-glacier hover:text-frost">
            <Pencil className="size-3" />{b.monthly_usd ? "Change" : "Set a budget"}
          </button>
        )}
      </div>
      {editing ? (
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void save(value.trim() ? Number(value) : null); }}>
          <div className="relative flex-1">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-mist">$</span>
            <input autoFocus value={value} onChange={(e) => setValue(e.target.value.replace(/[^0-9.]/g, ""))} inputMode="decimal"
              className={clsx(inputClass, "h-9 pl-6 font-mono")} placeholder="10" aria-label="Monthly budget in US dollars" />
          </div>
          <JiggleButton type="submit" tone="primary" className="h-9 rounded-xl px-3 text-xs">Save</JiggleButton>
          {b.monthly_usd && <JiggleButton tone="ghost" className="h-9 rounded-xl px-2 text-xs" onClick={() => void save(null)}>Remove</JiggleButton>}
        </form>
      ) : b.monthly_usd ? (
        <div className="mt-2 space-y-1.5">
          <div className="flex items-baseline justify-between font-mono text-[12px]">
            <span className="text-frost">{money(u.month.cost_usd)} <span className="text-mist">of {money(b.monthly_usd)}</span></span>
            <span className={clsx(b.state === "over" ? "text-coral" : b.state === "near" ? "text-amber" : "text-mist")}>{b.used_pct}%</span>
          </div>
          <Bar pct={b.used_pct ?? 0} tone={b.state ?? "ok"} />
          {b.state === "near" && <p className="text-[11px] text-amber">Over 80% of this month&apos;s budget.</p>}
          {b.state === "over" && <p className="text-[11px] text-coral">Budget reached. Local and free-tier models cost nothing.</p>}
        </div>
      ) : (
        <p className="mt-1 text-[11px] text-mist">Get a warning here when paid models near a monthly limit.</p>
      )}
    </div>
  );
}

function Chart({ u }: { u: UsageSummary }) {
  const useCost = u.daily.some((d) => d.cost_usd > 0);
  const values = u.daily.map((d) => (useCost ? d.cost_usd : d.tokens));
  const max = Math.max(...values, 0);
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
      <div className="mb-2 flex items-baseline justify-between text-[11px]">
        <span className="font-medium text-frost">Last 14 days</span>
        <span className="text-mist">{useCost ? "spend per day" : "tokens per day"}</span>
      </div>
      <div className="flex h-16 items-end gap-1" role="img" aria-label={`Daily ${useCost ? "spend" : "tokens"} for the last 14 days`}>
        {u.daily.map((d, i) => {
          const v = values[i];
          return (
            <div key={d.date} className="flex h-full flex-1 flex-col justify-end"
              title={`${new Date(d.date + "T12:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}: ${useCost ? money(d.cost_usd) : `${tokens(d.tokens)} tokens`} · ${d.requests} requests`}>
              <motion.div className={clsx("rounded-sm", v > 0 ? "bg-gradient-to-t from-sky/60 to-seafoam/80" : "bg-white/[0.06]")}
                initial={false} animate={{ height: v > 0 && max ? `${Math.max(6, (100 * v) / max)}%` : "2px" }} transition={jelly} />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[9.5px] text-mist/70">
        <span>{new Date(u.daily[0].date + "T12:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
        <span>today</span>
      </div>
    </div>
  );
}

function PriceForm({ m, onSaved }: { m: ModelUsage; onSaved: () => void }) {
  const [inp, setInp] = useState(m.price?.input_per_m?.toString() ?? "");
  const [out, setOut] = useState(m.price?.output_per_m?.toString() ?? "");
  const [err, setErr] = useState<string | null>(null);
  return (
    <form className="mt-2 space-y-2 rounded-lg bg-black/20 p-2.5" onSubmit={async (e) => {
      e.preventDefault();
      try { await api.setPrice(m.model, Number(inp), Number(out)); onSaved(); }
      catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't save the price"); }
    }}>
      <p className="text-[10.5px] text-mist">Price per 1 million tokens, from {m.provider_label}&apos;s pricing page:</p>
      <div className="grid grid-cols-2 gap-2">
        {([["Input", inp, setInp], ["Output", out, setOut]] as const).map(([label, v, set]) => (
          <label key={label} className="block">
            <span className="mb-1 block text-[10.5px] text-mist">{label} $/1M</span>
            <input value={v} onChange={(e) => set(e.target.value.replace(/[^0-9.]/g, ""))} inputMode="decimal"
              className={clsx(inputClass, "h-8 font-mono text-xs")} placeholder="0.00" />
          </label>
        ))}
      </div>
      <JiggleButton type="submit" tone="primary" className="h-8 w-full rounded-lg text-xs" disabled={!inp || !out}>Save price</JiggleButton>
      {err && <p className="text-[11px] text-coral">{err}</p>}
    </form>
  );
}

function ModelRow({ m, onSaved }: { m: ModelUsage; onSaved: () => void }) {
  const [pricing, setPricing] = useState(false);
  const priceTone = m.price?.source === "free tier" ? "seafoam" : m.price?.source === "yours" ? "periwinkle" : "glacier";
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[12.5px] text-frost">{m.name}</div>
          <div className="text-[10.5px] text-mist">{m.provider_label} · {m.requests} request{m.requests === 1 ? "" : "s"} · {m.avg_seconds}s avg</div>
        </div>
        <div className="text-right">
          {m.cost_usd == null
            ? <button type="button" onClick={() => setPricing((p) => !p)} className="text-[11px] font-medium text-amber hover:text-frost">Set price</button>
            : <div className="font-mono text-[13px] text-frost">{money(m.cost_usd)}</div>}
          {m.price && (
            <button type="button" onClick={() => setPricing((p) => !p)} className="block w-full text-right">
              <Chip tone={priceTone}>{m.price.source}</Chip>
            </button>
          )}
        </div>
      </div>
      <div className="mt-2.5 grid grid-cols-3 gap-1.5 text-center">
        {[["in", tokens(m.prompt_tokens)], ["out", tokens(m.output_tokens)], ["cached", tokens(m.cached_tokens)]].map(([k, v]) => (
          <div key={k} className="rounded-lg bg-white/[0.03] py-1">
            <div className="font-mono text-[12px] text-frost">{v}</div>
            <div className="text-[9.5px] uppercase tracking-wider text-mist/80">tokens {k}</div>
          </div>
        ))}
      </div>
      <div className="mt-2.5">
        <div className="mb-1 flex justify-between text-[10.5px] text-mist">
          <span>Context used</span>
          <span className="font-mono">avg {m.avg_context_pct}% · peak {m.max_context_pct}%</span>
        </div>
        <Bar pct={m.max_context_pct} tone={m.max_context_pct > 85 ? "over" : m.max_context_pct > 60 ? "near" : "ok"} />
      </div>
      {pricing && <PriceForm m={m} onSaved={() => { setPricing(false); onSaved(); }} />}
    </div>
  );
}

function Limits({ u }: { u: UsageSummary }) {
  if (!u.limits.length) return null;
  // Only what the provider names: "requests-day" → "requests per day"; plain "requests" stays as is
  // (its period differs by provider; the reset time shows it).
  const nice = (w: string) => w.replace("-", " per ");
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
      <div className="mb-2 text-[11px] font-medium text-frost">Free-tier allowance left</div>
      <div className="space-y-2.5">
        {u.limits.map((p) => (
          <div key={p.provider}>
            <div className="mb-1 text-[11px] text-mist">{p.label}</div>
            {Object.entries(p.windows).map(([w, v]) => {
              const pct = v.limit ? (100 * (v.remaining ?? 0)) / v.limit : 0;
              return (
                <div key={w} className="mb-1.5">
                  <div className="mb-0.5 flex justify-between font-mono text-[10.5px]">
                    <span className="text-mist">{nice(w)}</span>
                    <span className="text-frost/85">{(v.remaining ?? 0).toLocaleString()}{v.limit ? ` / ${v.limit.toLocaleString()}` : ""}{v.reset ? ` · resets ${v.reset}` : ""}</span>
                  </div>
                  {v.limit ? <Bar pct={pct} tone={pct < 10 ? "over" : pct < 25 ? "near" : "ok"} /> : null}
                </div>
              );
            })}
          </div>
        ))}
        <p className="text-[10px] text-mist/70">As reported by each provider on its last reply.</p>
      </div>
    </div>
  );
}

export function UsagePanel({ revision }: { revision: number }) {
  const usage = useLive(api.usage, revision, () => 15_000);
  const u = usage.data;
  const reload = () => void usage.reload();
  return (
    <div className="space-y-5">
      <section>
        <PanelTitle title="Usage" hint="Cloud models only: what they cost and how hard they work. Local and network-PC models are free." />
        {!u && !usage.error && <p className="text-xs text-mist">Loading…</p>}
        {usage.error && <p className="text-xs text-coral">{usage.error.message}</p>}
        {u && (
          <div className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
                <div className="text-[10.5px] uppercase tracking-[0.14em] text-mist">{u.month.label}</div>
                <div className="mt-1 font-mono text-xl text-frost">{money(u.month.cost_usd)}</div>
                <div className="text-[10.5px] text-mist">{u.month.requests} requests · {tokens(u.month.prompt_tokens + u.month.output_tokens)} tokens</div>
              </div>
              <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
                <div className="text-[10.5px] uppercase tracking-[0.14em] text-mist">Today</div>
                <div className="mt-1 font-mono text-xl text-frost">{money(u.today.cost_usd)}</div>
                <div className="text-[10.5px] text-mist">{u.today.requests} requests · {tokens(u.today.tokens)} tokens</div>
              </div>
            </div>
            {u.month.unpriced_requests > 0 && (
              <p className="rounded-lg border border-amber/20 bg-amber/[0.06] px-2.5 py-1.5 text-[11px] text-amber">
                {u.month.unpriced_requests} request{u.month.unpriced_requests === 1 ? " isn't" : "s aren't"} counted in the total yet: set the model&apos;s price below.
              </p>
            )}
            <Budget u={u} onSaved={reload} />
            <Chart u={u} />
            <Limits u={u} />
          </div>
        )}
      </section>
      {u && (
        <section>
          <PanelTitle title="By model" hint="This month. Context shows how full each request's context window got." />
          <div className="space-y-2">
            {u.by_model.length === 0
              ? <p className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3 text-xs text-mist">No cloud usage this month yet. Pick a cloud model in the model menu and it shows up here.</p>
              : u.by_model.map((m) => <ModelRow key={m.model} m={m} onSaved={reload} />)}
          </div>
        </section>
      )}
    </div>
  );
}
