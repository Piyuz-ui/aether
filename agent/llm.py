"""Minimal Ollama client: chat with tool calling, and embeddings."""
from __future__ import annotations

import re
from typing import Any

import requests

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaError(RuntimeError):
    pass


def strip_thinking(text: str) -> str:
    """Remove <think>…</think> blocks some reasoning models put in their reply."""
    return THINK_RE.sub("", text or "").strip()


class OllamaClient:
    def __init__(self, base_url: str, model: str, *, context_length: int = 16384,
                 temperature: float = 0.3, think: bool = False, timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_length = context_length
        self.temperature = temperature
        self.think = think
        self.timeout = timeout
        self._send_think = True  # turned off if the model rejects the parameter

    # ------------------------------------------------------------------ health
    def is_up(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/api/version", timeout=3).ok
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        r = requests.get(f"{self.base_url}/api/tags", timeout=10)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))

    # -------------------------------------------------------------------- chat
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.context_length, "temperature": self.temperature},
        }
        if tools:
            payload["tools"] = tools
        if self._send_think:
            payload["think"] = self.think

        try:
            r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        except requests.ConnectionError as e:
            raise OllamaError(
                f"Can't reach Ollama at {self.base_url}. Is the Ollama app running?") from e

        if r.status_code != 200:
            err = r.text
            # Older models/servers don't understand "think" — retry once without it.
            if "think" in err.lower() and self._send_think:
                self._send_think = False
                return self.chat(messages, tools)
            if "does not support tools" in err:
                raise OllamaError(
                    f"Model '{self.model}' doesn't support tool calling. "
                    "Try qwen3, qwen2.5, llama3.1 or mistral-nemo.")
            raise OllamaError(f"Ollama error {r.status_code}: {err}")
        return r.json()["message"]

    # -------------------------------------------------------------- embeddings
    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        r = requests.post(f"{self.base_url}/api/embed",
                          json={"model": model, "input": texts}, timeout=120)
        if r.status_code != 200:
            raise OllamaError(r.text)
        return r.json()["embeddings"]
