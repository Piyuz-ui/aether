/** Cloud model names look like "gemini:gemini-2.5-flash"; local Ollama ones like "qwen2.5:7b". */
export const CLOUD_LABELS: Record<string, string> = {
  gemini: "Gemini", groq: "Groq", openrouter: "OpenRouter", mistral: "Mistral", cerebras: "Cerebras",
  github: "GitHub Models", huggingface: "Hugging Face", claude: "Claude", openai: "OpenAI",
  deepseek: "DeepSeek", xai: "Grok",
};

/** The provider's short label for a cloud model, or null for a local one. */
export function cloudProvider(model?: string | null): string | null {
  if (!model) return null;
  const i = model.indexOf(":");
  if (i <= 0) return null;
  return CLOUD_LABELS[model.slice(0, i)] ?? null;
}

/** The network computer a model runs on ("qwen3:14b@pc" → "pc"), or null. */
export function remoteHost(model?: string | null): string | null {
  if (!model || cloudProvider(model)) return null;
  const m = /@([a-z][a-z0-9-]{0,23})$/.exec(model);
  return m ? m[1] : null;
}

/** How to write a computer's name: short ones like "pc" in capitals ("PC"). */
export const hostLabel = (host: string) => (host.length <= 3 ? host.toUpperCase() : host);

/** The model's own name: "gemini:gemini-2.5-flash" → "gemini-2.5-flash", "qwen3:14b@pc" → "qwen3:14b". */
export function modelName(model?: string | null): string {
  if (!model) return "";
  if (cloudProvider(model)) return model.slice(model.indexOf(":") + 1);
  const host = remoteHost(model);
  return host ? model.slice(0, -(host.length + 1)) : model;
}
