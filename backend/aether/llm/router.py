"""One model backend for the engine: local Ollama models plus optional cloud providers.

Model names choose the backend. "qwen2.5:7b" runs on Ollama; "gemini:gemini-2.5-flash",
"groq:…", "openrouter:…" and "claude:…" run in the cloud. The router has the same methods as
OllamaClient, so nothing else in AETHER needs to know the difference. Embeddings (the
knowledge vault and memory) always stay on Ollama, on this machine.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .cloud import (PROVIDERS, ClaudeProvider, OpenAICompatProvider, split_model)
from .ollama import ChatDelta, OllamaClient, OllamaError

BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class ModelRouter:
    def __init__(self, ollama: OllamaClient, *, keys: dict[str, str] | None = None,
                 context_length: int = 16384, cloud_max_output_tokens: int = 8192,
                 claude_max_output_tokens: int = 16000, timeout_s: float = 300.0,
                 transport: Any = None, claude_client: Any = None):
        self.ollama = ollama
        self.context_length = context_length
        self._limits = (cloud_max_output_tokens, claude_max_output_tokens, timeout_s)
        self._transport = transport          # tests inject a mock HTTP transport
        self._claude_client = claude_client  # tests inject a fake SDK client
        self.providers: dict[str, Any] = {}
        for pid, key in (keys or {}).items():
            if key:
                self.providers[pid] = self._make(pid, key)

    def _make(self, pid: str, key: str) -> Any:
        out_cap, claude_cap, timeout_s = self._limits
        if pid == "claude":
            return ClaudeProvider(key, max_output_tokens=claude_cap, timeout_s=timeout_s,
                                  client=self._claude_client)
        return OpenAICompatProvider(pid, BASE_URLS[pid], key, max_output_tokens=out_cap,
                                    timeout_s=timeout_s, transport=self._transport)

    async def set_key(self, pid: str, key: str | None, *, verify: bool = True) -> None:
        """Add, replace (and check) or remove a provider's key, live."""
        if pid not in PROVIDERS:
            raise OllamaError(f"Unknown provider '{pid}'.")
        old = self.providers.pop(pid, None)
        if old is not None:
            await old.aclose()
        if not key:
            return
        provider = self._make(pid, key)
        if verify:
            try:
                await provider.verify()
            except Exception:
                await provider.aclose()
                raise
        self.providers[pid] = provider

    # ------------------------------------------------------------- routing
    def is_cloud(self, model: str | None) -> bool:
        return bool(model) and split_model(model)[0] is not None

    def _cloud(self, model: str) -> tuple[Any, str]:
        pid, bare = split_model(model)
        assert pid is not None
        provider = self.providers.get(pid)
        if provider is None:
            info = PROVIDERS[pid]
            raise OllamaError(f"{info.label} isn't set up yet.",
                              hint=f"Paste an API key in Settings → Cloud brains, or set "
                                   f"{info.key_env} in backend/.env (free key: {info.key_url}).")
        return provider, bare

    def context_limit(self, model: str) -> int:
        if self.is_cloud(model):
            pid, bare = split_model(model)
            provider = self.providers.get(pid or "")
            return provider.meta(bare).context if provider else self.context_length
        return self.context_length

    # ------------------------------------------------- OllamaClient surface
    async def health(self) -> bool:
        return await self.ollama.health()

    async def list_models(self) -> list[str]:
        return await self.ollama.list_models()

    async def resolve_model(self, preferred: str, fallbacks: list[str]) -> tuple[str, str | None]:
        if self.is_cloud(preferred):
            pid, _ = split_model(preferred)
            if pid in self.providers:
                return preferred, None
            local, _note = await self.ollama.resolve_model(
                *(fallbacks[:1] or ["qwen2.5:7b"]), fallbacks[1:])
            return local, (f"No {PROVIDERS[pid or ''].label} key is set, so using {local}. "
                           f"Add {PROVIDERS[pid or ''].key_env} to backend/.env to use {preferred}.")
        return await self.ollama.resolve_model(preferred, fallbacks)

    async def capabilities(self, model: str) -> set[str]:
        if self.is_cloud(model):
            pid, bare = split_model(model)
            provider = self.providers.get(pid or "")
            caps = {"completion", "tools"}
            if provider is not None and provider.meta(bare).vision:
                caps.add("vision")
            return caps
        return await self.ollama.capabilities(model)

    async def find_model_with(self, *needs: str, prefer: str = "") -> str | None:
        if prefer and self.is_cloud(prefer) and set(needs) <= await self.capabilities(prefer):
            return prefer
        return await self.ollama.find_model_with(*needs, prefer=prefer)

    def chat_stream(self, *, model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None,
                    options: dict[str, Any] | None = None,
                    think: bool = False) -> AsyncIterator[ChatDelta]:
        # Returns the backend's own stream (not a wrapper), so closing it early really stops it.
        if self.is_cloud(model):
            provider, bare = self._cloud(model)
            return provider.chat_stream(model=bare, messages=messages, tools=tools,
                                        options=options, think=think)
        return self.ollama.chat_stream(model=model, messages=messages, tools=tools,
                                       options=options, think=think)

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return await self.ollama.embed(texts, model)

    async def aclose(self) -> None:
        for p in self.providers.values():
            await p.aclose()
        await self.ollama.aclose()

    # -------------------------------------------------------------- catalog
    async def catalog(self) -> list[dict[str, Any]]:
        """Every cloud provider, whether it's set up, and (if it is) its chat models."""
        out = []
        for pid, info in PROVIDERS.items():
            provider = self.providers.get(pid)
            entry: dict[str, Any] = {"id": pid, "label": info.label, "free": info.free,
                                     "configured": provider is not None, "key_env": info.key_env,
                                     "key_url": info.key_url, "note": info.note, "models": [],
                                     "error": None}
            if provider is not None:
                try:
                    names = await provider.list_models()
                except OllamaError as e:
                    names, entry["error"] = [], {"message": e.message, "hint": e.hint}
                entry["models"] = [{"id": f"{pid}:{n}", "name": n,
                                    "vision": provider.meta(n).vision,
                                    "context": provider.meta(n).context} for n in names]
            out.append(entry)
        return out


def router_from_settings(settings: Any) -> ModelRouter:
    """Local Ollama, plus every cloud provider whose API key is set."""
    return ModelRouter(OllamaClient(settings.ollama_url, timeout_s=settings.request_timeout_s),
                       keys=settings.cloud_keys(), context_length=settings.context_length,
                       cloud_max_output_tokens=settings.cloud_max_output_tokens,
                       claude_max_output_tokens=settings.claude_max_output_tokens,
                       timeout_s=settings.request_timeout_s)


def save_env_value(env_file: Path, name: str, value: str | None) -> None:
    """Set (or with None, remove) NAME=value in a .env file, keeping every other line."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    kept = [ln for ln in lines if not ln.split("=", 1)[0].strip() == name]
    if value:
        kept.append(f"{name}={value}")
    env_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(env_file, 0o600)  # API keys: readable by you only
