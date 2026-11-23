"use client";

/** Frosted slide-over that previews a note (rendered Markdown) or a PDF, with one-click exports. */
import { AnimatePresence, motion } from "framer-motion";
import { CalendarDays, Download, FileDown, FileText, X } from "lucide-react";
import { useEffect, useState } from "react";

import { api, API_BASE, ApiError } from "@/lib/api";
import { settle } from "@/lib/motion";

import { Markdown } from "./MessageView";
import { JiggleButton, Spinner } from "./ui";

export function ArtifactPreview({ path, onClose, onOpen, toast }: {
  path: string | null;
  onClose: () => void;
  onOpen: (path: string) => void;
  toast: (title: string, body?: string) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const isPdf = !!path?.toLowerCase().endsWith(".pdf");
  const name = path?.split("/").pop() ?? "";

  useEffect(() => {
    setText(null);
    setError(null);
    if (!path || isPdf) return;
    let alive = true;
    fetch(api.fileUrl(path))
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "This file no longer exists." : `Couldn't load it (${r.status}).`);
        return r.text();
      })
      .then((t) => alive && setText(t))
      .catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [path, isPdf]);

  useEffect(() => {
    if (!path) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [path, onClose]);

  async function exportPdf() {
    if (!path) return;
    setExporting(true);
    try {
      const res = await fetch(`${API_BASE}/api/files/pdf`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }),
      });
      const body = await res.json();
      if (!res.ok) throw new ApiError(res.status, body?.detail?.message ?? "Export failed.");
      toast("PDF ready", body.path);
      onOpen(body.path);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Export failed.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <AnimatePresence>
      {path && (
        <>
          <motion.div className="fixed inset-0 z-[45] bg-abyss-950/45 backdrop-blur-[2px]" onClick={onClose}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
          <motion.aside
            key="artifact"
            role="dialog"
            aria-label={`Preview of ${name}`}
            initial={{ x: 80, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 80, opacity: 0 }}
            transition={settle}
            className="glass-strong fixed inset-y-3 right-3 z-50 flex w-[min(760px,calc(100vw-24px))] flex-col overflow-hidden rounded-[28px]"
          >
            <header className="flex items-center gap-3 border-b border-white/[0.07] px-5 py-3.5">
              <FileText className={isPdf ? "size-5 text-coral" : "size-5 text-periwinkle"} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-frost">{name}</div>
                <div className="truncate font-mono text-[11px] text-mist">workspace/{path}</div>
              </div>
              <button type="button" onClick={onClose} aria-label="Close preview"
                className="grid size-9 place-items-center rounded-xl text-mist hover:bg-white/[0.07] hover:text-frost">
                <X className="size-4" />
              </button>
            </header>

            <div className="soft-scroll min-h-0 flex-1 overflow-y-auto">
              {isPdf ? (
                <iframe title={name} src={api.fileUrl(path)} className="size-full min-h-[60vh] bg-white" />
              ) : error ? (
                <div className="p-6 text-sm text-coral">{error}</div>
              ) : text === null ? (
                <div className="flex items-center gap-2 p-6 text-sm text-mist"><Spinner /> Loading…</div>
              ) : (
                <div className="px-6 py-5"><Markdown text={text} /></div>
              )}
            </div>

            <footer className="flex flex-wrap items-center gap-2 border-t border-white/[0.07] px-4 py-3">
              <a href={api.fileUrl(path, true)} download={name}>
                <JiggleButton tone="primary" className="pointer-events-none">
                  <Download className="size-4" /> Download {isPdf ? ".pdf" : ".md"}
                </JiggleButton>
              </a>
              {!isPdf && (
                <JiggleButton onClick={() => void exportPdf()} disabled={exporting}>
                  {exporting ? <Spinner /> : <FileDown className="size-4" />} Export to PDF
                </JiggleButton>
              )}
              <a href={api.calendarUrl()} download="aether-deadlines.ics" className="ml-auto"
                title="Your deadlines (with alarms) and weekly classes, for Apple / Google Calendar">
                <JiggleButton tone="ghost" className="pointer-events-none">
                  <CalendarDays className="size-4" /> Export to Calendar (.ics)
                </JiggleButton>
              </a>
            </footer>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
