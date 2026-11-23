"""Async Ollama client: streaming chat with tool calling, model discovery, embeddings.

Every failure becomes an OllamaError with a `hint`: the exact thing the user should do.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..metrics import CallStats


class OllamaError(RuntimeError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(slots=True)
class ChatDelta:
    """One streamed piece of a reply."""
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    stats: CallStats | None = None


def _same_model(a: str, b: str) -> bool:
    norm = lambda m: m if ":" in m else f"{m}:latest"  # noqa: E731
    return norm(a) == norm(b)


class OllamaClient:
    def __init__(self, base_url: str, *, timeout_s: float = 300.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport,
            timeout=httpx.Timeout(timeout_s, connect=5.0))
        self._think_supported: dict[str, bool] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- discovery
    async def health(self) -> bool:
        try:
            return (await self._client.get("/api/version")).is_success
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[str]:
        try:
            r = await self._client.get("/api/tags")
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise self._offline_error() from e
        except httpx.HTTPError as e:
            raise OllamaError(f"Couldn't list Ollama models: {e}") from e
        return sorted(m["name"] for m in r.json().get("models", []))

    async def resolve_model(self, preferred: str, fallbacks: list[str]) -> tuple[str, str | None]:
        """Return (model to use, note). The note explains a fallback, if one was needed."""
        installed = await self.list_models()
        if not installed:
            raise OllamaError("No Ollama models are installed.",
                              hint=f"Run: ollama pull {preferred}")
        for name in [preferred, *fallbacks]:
            match = next((m for m in installed if _same_model(m, name)), None)
            if match:
                note = None if match == preferred or _same_model(match, preferred) else (
                    f"'{preferred}' isn't installed, so using '{match}'. "
                    f"For the default, run: ollama pull {preferred}")
                return match, note
        raise OllamaError(
            f"None of the supported tool-calling models are installed "
            f"(installed: {', '.join(installed)}).",
            hint=f"Run: ollama pull {preferred}")

    # ------------------------------------------------------------------ chat
    async def chat_stream(self, *, model: str, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]] | None = None,
                          options: dict[str, Any] | None = None,
                          think: bool = False) -> AsyncIterator[ChatDelta]:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True,
                                   "options": options or {}}
        if tools:
            payload["tools"] = tools
        if self._think_supported.get(model, True):
            payload["think"] = think

        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    if "think" in body.lower() and "think" in payload:
                        # This model doesn't know the `think` option. Remember that and retry.
                        self._think_supported[model] = False
                        async for d in self.chat_stream(model=model, messages=messages,
                                                        tools=tools, options=options):
                            yield d
                        return
                    raise self._http_error(r.status_code, body, model)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a partial or garbled line; the stream continues
                    if "error" in chunk:
                        raise self._http_error(500, chunk["error"], model)
                    msg = chunk.get("message") or {}
                    delta = ChatDelta(content=msg.get("content") or "",
                                      thinking=msg.get("thinking") or "",
                                      tool_calls=list(msg.get("tool_calls") or []),
                                      done=bool(chunk.get("done")))
                    if delta.done:
                        delta.stats = CallStats.from_ollama(chunk)
                    yield delta
        except httpx.ConnectError as e:
            raise self._offline_error() from e
        except httpx.ReadTimeout as e:
            raise OllamaError("The model took too long to respond.",
                              hint="Try a smaller model, or raise AETHER_REQUEST_TIMEOUT_S.") from e
        except httpx.RemoteProtocolError as e:
            raise OllamaError("Ollama closed the connection mid-reply.",
                              hint="Ollama may have crashed or run out of memory. "
                                   "Restart it and try a smaller model.") from e

    # ------------------------------------------------------------ embeddings
    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        try:
            r = await self._client.post("/api/embed", json={"model": model, "input": texts})
        except httpx.ConnectError as e:
            raise self._offline_error() from e
        if r.status_code != 200:
            raise self._http_error(r.status_code, r.text, model)
        return r.json()["embeddings"]

    # ---------------------------------------------------------------- errors
    def _offline_error(self) -> OllamaError:
        return OllamaError(f"Can't reach Ollama at {self.base_url}.",
                           hint="Open the Ollama app, or run `ollama serve` in a terminal.")

    @staticmethod
    def _http_error(status: int, body: str, model: str) -> OllamaError:
        low = body.lower()
        if "not found" in low and "model" in low:
            return OllamaError(f"Model '{model}' isn't installed.", hint=f"Run: ollama pull {model}")
        if "does not support tools" in low:
            return OllamaError(f"Model '{model}' can't call tools.",
                               hint="Use a tool-calling model such as qwen2.5:7b, "
                                    "qwen3:8b or llama3.1:8b.")
        if "memory" in low or "out of memory" in low:
            return OllamaError("Not enough memory to run this model.",
                               hint="Close other apps or pick a smaller model.")
        return OllamaError(f"Ollama error {status}: {body.strip()[:300]}")
