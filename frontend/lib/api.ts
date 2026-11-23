/** Typed client for the AETHER backend. */
export type McpServer = {
  name: string; state: "starting" | "connected" | "error" | "disabled"; tools: number;
  error: string | null; approval: string; command: string; server?: string;
};

import type {
  ClassSlot,
  CloudProvider,
  ModelList,
  NetworkHost,
  Fact,
  Health,
  KnowledgeDoc,
  SessionSummary,
  Task,
  ToolInfo,
  TranscriptMessage,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_AETHER_API ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string, public hint?: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(0, "Can't reach the AETHER backend.",
      "Start it with: cd backend && source .venv/bin/activate && python api.py");
  }
  if (!res.ok) {
    let message = `Request failed (${res.status}).`;
    let hint: string | undefined;
    try {
      const body = await res.json();
      const detail = body?.detail;
      if (typeof detail === "string") message = detail;
      else if (detail?.message) {
        message = detail.message;
        hint = detail.hint;
      } else if (Array.isArray(detail) && detail[0]?.msg) message = detail[0].msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message, hint);
  }
  return (await res.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  health: () => request<Health>("/api/health"),
  tools: () => request<ToolInfo[]>("/api/tools"),
  models: () => request<ModelList>("/api/models"),
  setModel: (model: string) => request<{ current: string; cloud?: boolean }>("/api/models", json("POST", { model })),
  providers: () => request<CloudProvider[]>("/api/providers"),
  hosts: () => request<NetworkHost[]>("/api/hosts"),
  setHost: (name: string, url: string) => request<NetworkHost>(`/api/hosts/${name}`, json("PUT", { url })),
  removeHost: (name: string) => request<{ removed: string }>(`/api/hosts/${name}`, json("DELETE")),
  setProviderKey: (id: string, key: string) =>
    request<CloudProvider>(`/api/providers/${id}/key`, json("PUT", { key })),
  removeProviderKey: (id: string) => request<{ removed: string }>(`/api/providers/${id}/key`, json("DELETE")),

  decide: (approvalId: string, approved: boolean, reason = "") =>
    request(`/api/approvals/${approvalId}`, json("POST", { approved, reason })),

  sessions: () => request<SessionSummary[]>("/api/sessions"),
  session: (id: string) =>
    request<SessionSummary & { messages: TranscriptMessage[] }>(`/api/sessions/${id}`),
  renameSession: (id: string, title: string) => request(`/api/sessions/${id}`, json("PATCH", { title })),
  deleteSession: (id: string) => request(`/api/sessions/${id}`, { method: "DELETE" }),

  facts: () => request<Fact[]>("/api/memory/facts"),
  addFact: (text: string, category: Fact["category"]) =>
    request<Fact & { created: boolean }>("/api/memory/facts", json("POST", { text, category })),
  editFact: (id: number, patch: { text?: string; category?: Fact["category"] }) =>
    request<Fact>(`/api/memory/facts/${id}`, json("PATCH", patch)),
  deleteFact: (id: number) => request(`/api/memory/facts/${id}`, { method: "DELETE" }),

  tasks: (includeDone = false) => request<Task[]>(`/api/tasks?include_done=${includeDone}`),
  addTask: (title: string, due: string, kind: Task["kind"] = "task") =>
    request<Task>("/api/tasks", json("POST", { title, due, kind })),
  editTask: (id: number, patch: Partial<Pick<Task, "title" | "kind" | "notes" | "done">> & { due?: string }) =>
    request<Task>(`/api/tasks/${id}`, json("PATCH", patch)),
  deleteTask: (id: number) => request(`/api/tasks/${id}`, { method: "DELETE" }),

  timetable: () => request<ClassSlot[]>("/api/timetable"),
  addClass: (c: { course: string; day: string; start: string; end: string; location?: string }) =>
    request<ClassSlot>("/api/timetable", json("POST", c)),
  deleteClass: (id: number) => request(`/api/timetable/${id}`, { method: "DELETE" }),

  documents: () => request<KnowledgeDoc[]>("/api/knowledge"),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    return request<KnowledgeDoc[]>("/api/knowledge", { method: "POST", body: form });
  },
  reindex: (id: number) => request(`/api/knowledge/${id}/reindex`, { method: "POST" }),
  deleteDocument: (id: number) => request(`/api/knowledge/${id}`, { method: "DELETE" }),

  quizStats: () => request<{ topic: string; correct: number; wrong: number; accuracy: number; weak: boolean }[]>("/api/quiz-stats"),
  mcp: () => request<{ config: string; exists: boolean; servers: McpServer[] }>("/api/mcp"),
  reloadMcp: () => request<{ servers: McpServer[] }>("/api/mcp/reload", { method: "POST" }),
  calendarUrl: () => `${API_BASE}/api/calendar.ics`,
  fileUrl: (path: string, download = false) =>
    `${API_BASE}/api/files/${path.split("/").map(encodeURIComponent).join("/")}${download ? "?download=true" : ""}`,
};
