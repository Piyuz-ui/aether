"use client";

/** Lets any file chip or tool pill open the artifact preview pane. */
import { createContext, useContext } from "react";

export const ArtifactContext = createContext<(path: string) => void>(() => undefined);
export const useOpenArtifact = () => useContext(ArtifactContext);

/** Workspace files mentioned in a tool result, e.g. "Created notes/plan.md (…)". */
export const FILE_RE = /\b((?:exports|notes)\/[^\s'"`),;]+?\.(?:pdf|md))\b/g;

export function filesIn(text: string | undefined): string[] {
  if (!text) return [];
  return [...new Set([...text.matchAll(FILE_RE)].map((m) => m[1]))];
}
