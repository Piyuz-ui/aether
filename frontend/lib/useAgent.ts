"use client";

/**
 * Chat state for one conversation. Sends a goal, reads the agent's SSE event stream, and
 * turns each event into UI state: streaming text, thoughts, tool pills, approval requests,
 * the final answer, stats and learned memories.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";

import { API_BASE, api, ApiError } from "./api";
import { readSSE } from "./sse";
import type {
  AgentEvent,
  AgentState,
  Approval,
  AssistantMessage,
  Fact,
  Message,
  RunStats,
  Step,
  ToolStep,
  TranscriptMessage,
} from "./types";

type State = {
  messages: Message[];
  sessionId: string | null;
  agentState: AgentState;
  approval: Approval | null;
  busy: boolean;
  /** bumps whenever a run finishes, so side panels can refresh */
  revision: number;
};

type Action =
  | { type: "start"; user: string; id: string; userId?: string; images?: string[] }
  | { type: "session"; id: string }
  | { type: "event"; event: AgentEvent }
  | { type: "end"; stopped?: boolean; error?: { message: string; hint?: string } }
  | { type: "load"; id: string; messages: Message[] }
  | { type: "reset" };

const initial: State = { messages: [], sessionId: null, agentState: "idle", approval: null, busy: false, revision: 0 };

const uid = () => Math.random().toString(36).slice(2, 10);

function updateLast(messages: Message[], fn: (m: AssistantMessage) => AssistantMessage): Message[] {
  const i = messages.length - 1;
  const last = messages[i];
  if (!last || last.role !== "assistant") return messages;
  const next = messages.slice();
  next[i] = fn(last);
  return next;
}

function updateTool(steps: Step[], callId: string, patch: Partial<ToolStep>): Step[] {
  return steps.map((s) => (s.kind === "tool" && s.callId === callId ? { ...s, ...patch } : s));
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "start":
      return {
        ...state,
        busy: true,
        agentState: "thinking",
        messages: [
          ...state.messages,
          { id: action.userId ?? uid(), role: "user", content: action.user, images: action.images },
          { id: action.id, role: "assistant", steps: [], draft: "", thinking: "", content: "", status: "streaming" },
        ],
      };
    case "session":
      return { ...state, sessionId: action.id };
    case "load":
      return { ...initial, revision: state.revision, sessionId: action.id, messages: action.messages };
    case "reset":
      return { ...initial, revision: state.revision + 1 };
    case "end": {
      const messages = updateLast(state.messages, (m) =>
        m.status !== "streaming"
          ? m
          : {
              ...m,
              status: action.error ? "error" : action.stopped ? "stopped" : "done",
              error: action.error ?? m.error,
              content: m.content || (action.stopped ? m.draft : m.content),
              draft: "",
              steps: m.steps.map((s) => (s.kind === "tool" && (s.status === "running" || s.status === "awaiting")
                ? { ...s, status: "error", output: s.output ?? "Stopped." } : s)),
            });
      return { ...state, messages, busy: false, agentState: "idle", approval: null, revision: state.revision + 1 };
    }
    case "event":
      return applyEvent(state, action.event);
  }
}

function applyEvent(state: State, e: AgentEvent): State {
  const d = e.data as Record<string, never>;
  switch (e.type) {
    case "status":
      return { ...state, agentState: d.state as AgentState };
    case "token":
      return {
        ...state,
        messages: updateLast(state.messages, (m) =>
          d.channel === "thinking" ? { ...m, thinking: m.thinking + d.text } : { ...m, draft: m.draft + d.text }),
      };
    case "thought":
      // The streamed text was the agent's reasoning before a tool call, not the answer.
      return {
        ...state,
        messages: updateLast(state.messages, (m) => ({
          ...m,
          draft: "",
          thinking: "",
          steps: [...m.steps, { kind: "thought", text: String(d.text ?? ""), retry: d.kind === "retry" }],
        })),
      };
    case "tool_start":
      return {
        ...state,
        messages: updateLast(state.messages, (m) => ({
          ...m,
          draft: "",
          steps: [...m.steps, {
            kind: "tool", callId: d.call_id, name: d.name, args: (d.args ?? {}) as Record<string, unknown>,
            risk: d.risk ?? "safe", icon: d.icon ?? "wrench", status: "running",
          }],
        })),
      };
    case "approval_required":
      return {
        ...state,
        agentState: "awaiting_approval",
        approval: { approvalId: d.approval_id, callId: d.call_id, name: d.name, args: d.args ?? {}, summary: d.summary ?? "" },
        messages: updateLast(state.messages, (m) => ({ ...m, steps: updateTool(m.steps, d.call_id, { status: "awaiting" }) })),
      };
    case "approval_resolved":
      return {
        ...state,
        approval: null,
        messages: updateLast(state.messages, (m) => ({
          ...m, steps: updateTool(m.steps, d.call_id, { status: d.approved ? "running" : "denied" }),
        })),
      };
    case "tool_result":
      return {
        ...state,
        messages: updateLast(state.messages, (m) => ({
          ...m,
          steps: updateTool(m.steps, d.call_id, {
            status: d.denied ? "denied" : d.ok ? "ok" : "error",
            output: String(d.output ?? ""),
            duration: Number(d.duration_s ?? 0),
          }),
        })),
      };
    case "done":
      return {
        ...state,
        agentState: "idle",
        messages: updateLast(state.messages, (m) => ({
          ...m, status: "done", content: String(d.answer ?? ""), draft: "", thinking: "",
          stats: d.stats as unknown as RunStats,
        })),
      };
    case "memory":
      return {
        ...state,
        messages: updateLast(state.messages, (m) => ({ ...m, learned: (d.saved ?? []) as unknown as Fact[] })),
      };
    case "error":
      return {
        ...state,
        agentState: "idle",
        messages: updateLast(state.messages, (m) => ({
          ...m, status: "error", draft: "", error: { message: String(d.message ?? "Error"), hint: d.hint },
        })),
      };
    default:
      return state;
  }
}

export function transcriptToMessages(transcript: TranscriptMessage[]): Message[] {
  return transcript.map((t) =>
    t.role === "user"
      ? { id: uid(), role: "user", content: t.content, imageCount: t.images }
      : {
          id: uid(), role: "assistant", content: t.content, draft: "", thinking: "", status: "done",
          stats: t.stats ?? undefined,
          steps: t.steps.map((s): Step => s.type === "thought"
            ? { kind: "thought", text: s.text }
            : { kind: "tool", callId: uid(), name: s.name, args: s.args ?? {}, risk: "safe", icon: "",
                status: s.ok === false ? "error" : "ok", output: s.output ?? undefined }),
        });
}

const SESSION_KEY = "aether.session";

export function useAgent(options?: { onAnswer?: (text: string) => void; onLearned?: (facts: Fact[]) => void }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const controller = useRef<AbortController | null>(null);
  const optsRef = useRef(options);
  optsRef.current = options;

  const loadSession = useCallback(async (id: string) => {
    try {
      const s = await api.session(id);
      dispatch({ type: "load", id, messages: transcriptToMessages(s.messages) });
      window.localStorage.setItem(SESSION_KEY, id);
    } catch {
      window.localStorage.removeItem(SESSION_KEY);
    }
  }, []);

  // Pick up where you left off.
  useEffect(() => {
    const last = typeof window !== "undefined" ? window.localStorage.getItem(SESSION_KEY) : null;
    if (last) void loadSession(last);
  }, [loadSession]);

  const send = useCallback(async (text: string, images?: string[], opts?: { userId?: string }) => {
    const goal = text.trim() || (images?.length ? "What's in this image?" : "");
    if (!goal || controller.current) return;
    const ac = new AbortController();
    controller.current = ac;
    dispatch({ type: "start", user: goal, id: uid(), userId: opts?.userId, images });
    let error: { message: string; hint?: string } | undefined;
    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ message: goal, session_id: stateRef.current.sessionId,
          images: (images ?? []).map((d) => d.slice(d.indexOf(",") + 1)) }),
        signal: ac.signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new ApiError(res.status, body?.detail?.message ?? `Request failed (${res.status})`, body?.detail?.hint);
      }
      for await (const msg of readSSE(res, ac.signal)) {
        const parsed = JSON.parse(msg.data);
        if (msg.event === "session") {
          dispatch({ type: "session", id: parsed.session_id });
          window.localStorage.setItem(SESSION_KEY, parsed.session_id);
          continue;
        }
        const event = parsed as AgentEvent;
        dispatch({ type: "event", event });
        if (event.type === "done") optsRef.current?.onAnswer?.(String(event.data.answer ?? ""));
        if (event.type === "memory") optsRef.current?.onLearned?.((event.data.saved ?? []) as Fact[]);
      }
    } catch (e) {
      if (ac.signal.aborted) {
        dispatch({ type: "end", stopped: true });
        controller.current = null;
        return;
      }
      error = e instanceof ApiError
        ? { message: e.message, hint: e.hint }
        : { message: "Lost connection to AETHER.", hint: "Is the backend still running? (python api.py)" };
    }
    controller.current = null;
    dispatch({ type: "end", error });
  }, []);

  // `send` is stable, so it reads the live session id through a ref.
  const stateRef = useRef(state);
  stateRef.current = state;

  const stop = useCallback(() => controller.current?.abort(), []);

  const decide = useCallback(async (approved: boolean) => {
    const a = stateRef.current.approval;
    if (!a) return;
    try {
      await api.decide(a.approvalId, approved, approved ? "" : "Denied in the web UI.");
    } catch {
      /* the run already ended; the stream will report it */
    }
  }, []);

  const newChat = useCallback(() => {
    controller.current?.abort();
    window.localStorage.removeItem(SESSION_KEY);
    dispatch({ type: "reset" });
  }, []);

  return { ...state, send, stop, decide, newChat, loadSession };
}
