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
  /** Cloud models with a known price: what this answer cost. */
  cost_usd?: number | null;
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
  /** Charts / images this tool made (from the tool_result event). */
  artifacts?: ChartArtifact[];
};

export type ChartArtifact = { path: string; name: string; url: string; download: string };

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
  /** The model answering (may differ from the selected one, e.g. a vision model for images). */
  model?: string;
  stats?: RunStats;
  error?: { message: string; hint?: string };
  learned?: Fact[];
  /** Your 👍/👎 on this reply. */
  feedback?: Feedback;
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
export type AddressKind = "home" | "tailscale" | "internet";
export type NetworkHost = {
  name: string;
  /** The address in use right now. */
  url: string;
  route?: AddressKind;
  /** All saved addresses, in order of preference (home first, then remote). */
  addresses?: { url: string; kind: AddressKind; active: boolean }[];
  /** After saving: addresses that didn't answer at that moment. */
  unreachable?: string[];
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

export type SessionSummary = {
  id: string; title: string; created: number; updated: number; turns: number;
  /** Where the title came from: the first message, the model ("ai") or you ("user"). */
  title_source?: "first" | "ai" | "user";
  good?: number | null; bad?: number | null;
};
export type Feedback = { rating: "good" | "bad"; reason?: string };

export type TranscriptMessage =
  | { role: "user"; content: string; images?: number }
  | {
      role: "assistant";
      content: string;
      stats?: RunStats | null;
      feedback?: Feedback;
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
  network?: NetworkLink | null;
};
export type SignalQuality = "excellent" | "good" | "fair" | "weak";
/** How a machine is connected. Mac: Wi-Fi in dBm. PC (from its monitor): cable or Wi-Fi in %. */
export type NetworkLink = {
  type: string;
  quality?: SignalQuality | null;
  signal_dbm?: number;
  noise_dbm?: number;
  snr_db?: number;
  band?: string | null;
  channel?: string | null;
  link_mbps?: number | null;
  standard?: string | null;
  adapter?: string | null;
  wifi_signal_pct?: number | null;
};
export type LinkQuality = {
  latency_ms: number | null;
  jitter_ms: number | null;
  loss_pct: number;
  quality: "excellent" | "good" | "fair" | "poor" | "unreachable";
  bars: number;
  verdict: string;
  samples: number;
};
export type Availability = {
  state: "ready" | "ollama_error" | "no_ollama" | "offline";
  label: string;
  detail: string;
};
export type LoadedModel = { name: string; size_gb: number; vram_gb: number; gpu_pct: number; context: number | null };
export type HardwareCard = {
  id: string;
  label: string;
  url?: string;
  online: boolean;
  monitor: "built-in" | "ok" | "missing";
  link?: LinkQuality;
  route?: AddressKind;
  availability?: Availability;
  system: MachineSystem | null;
  loaded: LoadedModel[];
};

export type GpuLive =
  | { available: true; host: string; name: string; util_pct: number | null; vram_used_gb: number | null;
      vram_total_gb: number | null; temp_c: number | null; power_w: number | null }
  | { available: false; reason: "local" | "cloud" | "no_monitor"; host?: string };

export type ModelPrice = { input_per_m: number; output_per_m: number; cached_per_m: number | null; source: "official" | "yours" | "free tier" };
export type ModelUsage = {
  model: string;
  name: string;
  provider: string;
  provider_label: string;
  requests: number;
  prompt_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  /** null when the model has no price yet. */
  cost_usd: number | null;
  priced: boolean;
  avg_context_pct: number;
  max_context_pct: number;
  avg_seconds: number;
  last_used: number;
  price: ModelPrice | null;
};
export type RateWindow = { remaining?: number; limit?: number; reset?: string };
export type UsageSummary = {
  month: { label: string; cost_usd: number; requests: number; prompt_tokens: number; output_tokens: number; unpriced_requests: number };
  today: { cost_usd: number; requests: number; tokens: number };
  budget: { monthly_usd: number | null; used_pct: number | null; state: "ok" | "near" | "over" | null };
  by_model: ModelUsage[];
  daily: { date: string; cost_usd: number; tokens: number; requests: number }[];
  limits: { provider: string; label: string; at: number; windows: Record<string, RateWindow> }[];
};

export type McpServerConfig = {
  command?: string; args?: string[]; env?: Record<string, string>;
  url?: string; headers?: Record<string, string>;
  approval?: "auto" | "always" | "never"; include?: string[]; disabled?: boolean;
};
export type McpServerEntry = {
  name: string;
  config: McpServerConfig;
  status: { state: "starting" | "connected" | "error" | "disabled"; tools: number; error: string | null; server?: string; kind?: "local" | "remote" } | null;
};
export type ClaudeServer = { name: string; source: string; kind: "local" | "remote"; command: string; supported: boolean; note: string | null; added: boolean };
export type ClaudeStatus = {
  found: ClaudeServer[];
  aether: {
    desktop: { connected: boolean; installed: boolean; path: string };
    code: { connected: boolean; installed: boolean };
    command: string;
    code_command: string;
  };
};
