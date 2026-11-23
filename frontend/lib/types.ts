/** Types mirroring the AETHER backend (backend/api.py and aether/events.py). */

export type AgentState = "idle" | "thinking" | "using_tool" | "awaiting_approval";

export type EventType =
  | "session"
  | "status"
  | "token"
  | "thought"
  | "tool_start"
  | "approval_required"
  | "approval_resolved"
  | "tool_result"
  | "done"
  | "memory"
  | "error";

export type AgentEvent = {
  type: EventType;
  step: number;
  ts: number;
  data: Record<string, unknown>;
};

export type RunStats = {
  model: string;
  wall_s: number;
  prompt_tokens: number;
  output_tokens: number;
  context_used: number;
  context_limit: number;
  context_pct: number;
  tokens_per_s: number;
  model_calls: number;
  tools: { name: string; seconds: number }[];
  summary: string;
};

export type ToolStatus = "running" | "awaiting" | "ok" | "error" | "denied";

export type ToolStep = {
  kind: "tool";
  callId: string;
  name: string;
  args: Record<string, unknown>;
  risk: string;
  icon: string;
  status: ToolStatus;
  output?: string;
  duration?: number;
};

export type ThoughtStep = { kind: "thought"; text: string; retry?: boolean };

export type Step = ToolStep | ThoughtStep;

export type Fact = {
  id: number;
  text: string;
  category: "profile" | "preference" | "goal" | "academic" | "general";
  source?: string;
  created?: number;
  updated?: number;
};

export type UserMessage = {
  id: string;
  role: "user";
  content: string;
  images?: string[]; // data URLs shown in the bubble (this session only)
  imageCount?: number; // from saved history: images aren't stored, only how many
};

export type AssistantMessage = {
  id: string;
  role: "assistant";
  steps: Step[];
  draft: string; // streaming text of the current step (becomes a thought or the answer)
  thinking: string; // live reasoning tokens from thinking models
  content: string; // the final answer
  status: "streaming" | "done" | "error" | "stopped";
  stats?: RunStats;
  error?: { message: string; hint?: string };
  learned?: Fact[];
};

export type Message = UserMessage | AssistantMessage;

export type Approval = {
  approvalId: string;
  callId: string;
  name: string;
  args: Record<string, unknown>;
  summary: string;
};

export type Health = {
  ok: boolean;
  version: string;
  ollama: boolean;
  model: string | null;
  model_note: string | null;
  error: { message: string; hint?: string } | null;
  embed_model: string;
  embeddings_available: boolean | null;
  embeddings_hint: string | null;
  tools: number;
  context_length: number;
  cloud?: boolean;
  provider?: string | null;
  /** Set when the model runs on another computer on your network (e.g. "pc"). */
  host?: string | null;
};

export type HostModel = { id: string; name: string; vision: boolean; tools: boolean };
export type NetworkHost = {
  name: string;
  url: string;
  online: boolean;
  models: HostModel[];
  error: { message: string; hint?: string } | null;
};

export type CloudModel = { id: string; name: string; vision: boolean; context: number };
export type CloudProvider = {
  id: string;
  label: string;
  free: boolean;
  configured: boolean;
  key_env: string;
  key_url: string;
  note: string;
  models: CloudModel[];
  error: { message: string; hint?: string } | null;
};

export type ModelList = {
  models: string[];
  current: string;
  local_error?: { message: string; hint?: string } | null;
  cloud?: CloudProvider[];
  hosts?: NetworkHost[];
};

export type Task = {
  id: number;
  title: string;
  kind: "task" | "deadline" | "exam" | "reminder";
  due: string | null;
  notes: string;
  done: boolean;
  when: string;
  overdue: boolean;
};

export type ClassSlot = {
  id: number;
  course: string;
  weekday: number;
  day: string;
  start: string;
  end: string;
  location: string;
};

export type KnowledgeDoc = {
  id: number;
  filename: string;
  pages: number;
  chunks: number;
  status: "queued" | "reading" | "embedding" | "ready" | "error";
  progress: number;
  error: string | null;
  created: number;
  duplicate?: boolean;
};

export type SessionSummary = { id: string; title: string; created: number; updated: number; turns: number };

export type TranscriptMessage =
  | { role: "user"; content: string; images?: number }
  | {
      role: "assistant";
      content: string;
      stats?: RunStats | null;
      steps: (
        | { type: "thought"; text: string }
        | { type: "tool"; name: string; args: Record<string, unknown>; output: string | null; ok: boolean | null }
      )[];
    };

export type ToolInfo = { name: string; description: string; category: string; icon: string; risk: string };

export type GpuInfo = {
  name: string;
  vram_total_gb: number | null;
  vram_used_gb: number | null;
  util_pct: number | null;
  temp_c: number | null;
  power_w: number | null;
  driver: string | null;
};
export type MachineSystem = {
  hostname: string | null;
  cpu: string | null;
  cpu_pct?: number | null;
  ram_total_gb: number | null;
  ram_used_gb: number | null;
  gpus: GpuInfo[];
  /** Apple silicon: the GPU shares system memory, so there's no separate VRAM. */
  unified_memory: boolean;
};
export type LoadedModel = { name: string; size_gb: number; vram_gb: number; gpu_pct: number; context: number | null };
export type HardwareCard = {
  id: string;
  label: string;
  url?: string;
  online: boolean;
  monitor: "built-in" | "ok" | "missing";
  system: MachineSystem | null;
  loaded: LoadedModel[];
};
