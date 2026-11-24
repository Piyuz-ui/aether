"use client";

/** Knowledge Vault: drag-and-drop PDFs and notes, watch them index live. */
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { FileText, RotateCw, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { jelly, popIn } from "@/lib/motion";
import type { KnowledgeDoc } from "@/lib/types";
import { useLive } from "@/lib/useLive";

import { Chip } from "../ui";
import { ConfirmDelete, Empty, ErrorNote, PanelTitle } from "./shared";

const ACCEPT = ".pdf,.docx,.pptx,.md,.markdown,.txt,.xlsx,.csv,.tsv,.ipynb,.py,.js,.ts,.tsx,.java,.c,.cpp,.go,.rs,.sql,.json,.yaml,.yml,.html,.css,image/*";
const busy = (d: KnowledgeDoc) => ["queued", "reading", "embedding"].includes(d.status);

export function VaultPanel({ revision, toast }: { revision: number; toast: (t: string, b?: string) => void }) {
  const docs = useLive(api.documents, revision, (d) => (d?.some(busy) ? 800 : 15000));
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function upload(files: File[]) {
    if (files.some((f) => /\.ppt$/i.test(f.name))) {
      setErr(new ApiError(422, "Old .ppt files can't be read. Save them as .pptx first."));
    }
    const ok = files.filter((f) => /\.(pdf|pptx|md|markdown|txt)$/i.test(f.name));
    if (!ok.length) {
      if (!files.some((f) => /\.ppt$/i.test(f.name))) {
        setErr(new ApiError(422, "Only PDF, PowerPoint (.pptx), Markdown and TXT files can be added."));
      }
      return;
    }
    setUploading(true);
    setErr(null);
    try {
      const added = await api.upload(ok);
      const dup = added.filter((d) => d.duplicate).length;
      toast(`Added ${added.length - dup} document${added.length - dup === 1 ? "" : "s"}`,
        dup ? `${dup} already in the vault` : "Indexing now…");
      await docs.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <PanelTitle title="Knowledge Vault"
        hint="PDFs, slides and notes AETHER can search and cite. Everything stays on this computer." />
      <motion.div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); void upload(Array.from(e.dataTransfer.files)); }}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="Upload documents"
        animate={{ scale: over ? 1.03 : 1 }}
        whileTap={{ scale: 0.97 }}
        transition={jelly}
        className={clsx("cursor-pointer rounded-3xl border border-dashed px-4 py-6 text-center transition-colors",
          over ? "border-glacier/70 bg-glacier/[0.08]" : "border-white/15 bg-white/[0.02] hover:border-glacier/40")}
      >
        <motion.div animate={uploading ? { y: [0, -5, 0] } : { y: 0 }} transition={{ duration: 0.9, repeat: uploading ? Infinity : 0 }}>
          <UploadCloud className="mx-auto size-7 text-glacier" />
        </motion.div>
        <div className="mt-2 text-sm text-frost">{uploading ? "Uploading…" : "Drop documents, sheets, code or images here"}</div>
        <div className="text-xs text-mist">or click to choose · PDF, Word, PowerPoint, Excel/CSV, code, images</div>
        <input ref={input} type="file" multiple accept={ACCEPT} className="hidden"
          onChange={(e) => { void upload(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      </motion.div>

      <div className="mt-3 space-y-2">
        {err && <ErrorNote message={err.message} hint={err.hint} />}
        {docs.error && <ErrorNote message={docs.error.message} hint={docs.error.hint} />}
        {docs.data?.length === 0 && <Empty>No documents yet. Add your lecture notes, PowerPoint slides or syllabus.</Empty>}
        <AnimatePresence initial={false}>
          {docs.data?.map((d) => (
            <motion.div key={d.id} layout variants={popIn} initial="hidden" animate="show" exit="exit"
              className="glass-inset rounded-2xl p-3">
              <div className="flex items-start gap-2.5">
                <FileText className="mt-0.5 size-4 shrink-0 text-periwinkle" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-frost" title={d.filename}>{d.filename}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-mist">
                    {d.status === "ready" && <Chip tone="seafoam">ready</Chip>}
                    {busy(d) && <Chip tone="glacier">{d.status}</Chip>}
                    {d.status === "error" && <Chip tone="coral">error</Chip>}
                    {d.pages > 0 && <span>{d.pages} {/\.pptx$/i.test(d.filename) ? "slides" : "pages"}</span>}
                    {d.chunks > 0 && <span>· {d.chunks} passages</span>}
                  </div>
                </div>
                {d.status === "error" && (
                  <button type="button" aria-label="Retry indexing" title="Retry"
                    onClick={async () => { await api.reindex(d.id); await docs.reload(); }}
                    className="grid size-7 place-items-center rounded-lg text-mist hover:bg-white/10 hover:text-frost">
                    <RotateCw className="size-3.5" />
                  </button>
                )}
                <ConfirmDelete onConfirm={async () => { await api.deleteDocument(d.id); await docs.reload(); }} />
              </div>
              {busy(d) && (
                <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                  <motion.div className="h-full rounded-full bg-gradient-to-r from-sky to-seafoam"
                    animate={{ width: `${Math.max(4, d.progress * 100)}%` }} transition={jelly} />
                </div>
              )}
              {d.error && <div className="mt-2 text-[11px] leading-relaxed text-amber/90">{d.error}</div>}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
