"use client";

/**
 * Files dropped on the chat (or picked with the paperclip): upload them to the Knowledge Vault,
 * track indexing live, and hand their names to the next message.
 */
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";
import type { KnowledgeDoc } from "./types";

export const ACCEPTED = /\.(pdf|pptx|md|markdown|txt)$/i;
export const DATA = /\.(csv|tsv|xlsx|xlsm|json)$/i;   // tables for analyze_csv / python_sandbox
export const IMAGE = /\.(png|jpe?g|webp|gif|heic|heif)$/i;
export const ACCEPT_ATTR = ".pdf,.pptx,.md,.markdown,.txt,.csv,.tsv,.xlsx,.json,image/*";
const MAX_SIDE = 1600;

export type ImageAttachment = { id: string; name: string; dataUrl: string };

/** Downscale big photos/screenshots in the browser: faster upload, fewer vision tokens. */
async function loadImage(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file).catch(() => {
    throw new Error(`${file.name}: this browser can't read that image format. Try PNG or JPEG.`);
  });
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return canvas.toDataURL("image/jpeg", 0.88);
}

export type Attachment = Pick<KnowledgeDoc, "id" | "filename" | "status" | "progress" | "error"> & {
  /** Data files: where they were saved in the workspace (e.g. uploads/sales.csv). */
  path?: string;
};

export function useAttachments(onError: (title: string, body?: string) => void) {
  const [items, setItems] = useState<Attachment[]>([]);
  const [images, setImages] = useState<ImageAttachment[]>([]);
  const [uploading, setUploading] = useState(false);

  const add = useCallback(async (files: File[]) => {
    const pics = files.filter((f) => IMAGE.test(f.name) || f.type.startsWith("image/"));
    if (pics.length) {
      const room = Math.max(0, 4 - images.length);
      if (pics.length > room) onError("Up to 4 images per message", `Skipped ${pics.length - room}.`);
      for (const f of pics.slice(0, room)) {
        try {
          const dataUrl = await loadImage(f);
          setImages((prev) => [...prev, { id: `${f.name}-${Date.now()}-${Math.random()}`, name: f.name, dataUrl }]);
        } catch (e) {
          onError(e instanceof Error ? e.message : "Couldn't read that image.");
        }
      }
    }
    files = files.filter((f) => !pics.includes(f));
    const data = files.filter((f) => DATA.test(f.name));
    if (data.length) {
      setUploading(true);
      try {
        const saved = await api.uploadData(data);
        setItems((prev) => [...prev, ...saved.map((d, i) => ({
          id: -(Date.now() + i), filename: d.filename, status: "ready" as const, progress: 1, error: null, path: d.path }))]);
      } catch (e) {
        const err = e instanceof ApiError ? e : new ApiError(0, String(e));
        onError(err.message, err.hint);
      } finally {
        setUploading(false);
      }
    }
    files = files.filter((f) => !data.includes(f));
    if (!files.length) return;
    const rejected = files.filter((f) => !ACCEPTED.test(f.name));
    const ok = files.filter((f) => ACCEPTED.test(f.name));
    if (rejected.length) {
      onError("Some files were skipped",
        rejected.some((f) => /\.ppt$/i.test(f.name))
          ? "Old .ppt files: save them as .pptx first."
          : `Supported: PDF, PowerPoint (.pptx), Markdown, TXT, and data files (CSV, Excel, JSON). Skipped: ${rejected.map((f) => f.name).join(", ")}`);
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
  }, [onError, images.length]);

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
  const removeImage = useCallback((id: string) => setImages((prev) => prev.filter((a) => a.id !== id)), []);
  const clear = useCallback(() => { setItems([]); setImages([]); }, []);

  return { items, images, uploading, indexing, add, remove, removeImage, clear };
}
