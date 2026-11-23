/** Cloud model names look like "gemini:gemini-2.5-flash"; local Ollama ones like "qwen2.5:7b". */
export const CLOUD_LABELS: Record<string, string> = {
  gemini: "Gemini", groq: "Groq", openrouter: "OpenRouter", claude: "Claude",
};

/** The provider's short label for a cloud model, or null for a local one. */
export function cloudProvider(model?: string | null): string | null {
  if (!model) return null;
  const i = model.indexOf(":");
  if (i <= 0) return null;
  return CLOUD_LABELS[model.slice(0, i)] ?? null;
}

/** "gemini:gemini-2.5-flash" → "gemini-2.5-flash"; local names are unchanged. */
export function modelName(model?: string | null): string {
  if (!model) return "";
  return cloudProvider(model) ? model.slice(model.indexOf(":") + 1) : model;
}
