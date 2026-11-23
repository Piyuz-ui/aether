"use client";

/**
 * Files dropped on the chat (or picked with the paperclip): upload them to the Knowledge Vault,
 * track indexing live, and hand their names to the next message.
 */
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";
import type { KnowledgeDoc } from "./types";

export const ACCEPTED = /\.(pdf|pptx|md|markdown|txt)$/i;
export const ACCEPT_ATTR = ".pdf,.pptx,.md,.markdown,.txt";

export type Attachment = Pick<KnowledgeDoc, "id" | "filename" | "status" | "progress" | "error">;

export function useAttachments(onError: (title: string, body?: string) => void) {
  const [items, setItems] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);

  const add = useCallback(async (files: File[]) => {
    const rejected = files.filter((f) => !ACCEPTED.test(f.name));
    const ok = files.filter((f) => ACCEPTED.test(f.name));
    if (rejected.length) {
      onError("Some files were skipped",
        rejected.some((f) => /\.ppt$/i.test(f.name))
          ? "Old .ppt files: save them as .pptx first."
          : `Supported: PDF, PowerPoint (.pptx), Markdown, TXT. Skipped: ${rejected.map((f) => f.name).join(", ")}`);
    }
    if (!ok.length) return;
    setUploading(true);
    try {
      const docs = await api.upload(ok);
      setItems((prev) => {
        const next = prev.filter((p) => !docs.some((d) => d.id === p.id));
        return [...next, ...docs.map(({ id, filename, status, progress, error }) => ({ id, filename, status, progress, error }))];
      });
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, String(e));
      onError(err.message, err.hint);
    } finally {
      setUploading(false);
    }
  }, [onError]);

  const indexing = items.some((a) => ["queued", "reading", "embedding"].includes(a.status));

  // Poll the vault while anything is still being indexed.
  useEffect(() => {
    if (!indexing) return;
    const t = window.setInterval(async () => {
      try {
        const docs = await api.documents();
        setItems((prev) => prev.map((a) => {
          const d = docs.find((x) => x.id === a.id);
          return d ? { ...a, status: d.status, progress: d.progress, error: d.error } : a;
        }));
      } catch {
        /* backend briefly unreachable; try again next tick */
      }
    }, 700);
    return () => window.clearInterval(t);
  }, [indexing]);

  const remove = useCallback((id: number) => setItems((prev) => prev.filter((a) => a.id !== id)), []);
  const clear = useCallback(() => setItems([]), []);

  return { items, uploading, indexing, add, remove, clear };
}
