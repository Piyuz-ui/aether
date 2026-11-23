"""Optional cloud "brains": Google Gemini, Groq, OpenRouter (all with free tiers) and Claude.

Each provider streams `ChatDelta`s exactly like OllamaClient, so the engine can't tell them
apart. The internal message format is Ollama's ({"role", "content", "images", "tool_calls",
"tool_name"}); each provider converts it to its own wire format here.

Cloud models are opt-in: a provider is only offered once its API key is set, and the UI marks
these models as sending your messages off this machine. Embeddings always stay local.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..metrics import CallStats
from .ollama import ChatDelta, OllamaError


# ------------------------------------------------------------------- catalog
@dataclass(frozen=True, slots=True)
class ProviderInfo:
    id: str            # the prefix in model names, e.g. "gemini" → "gemini:gemini-2.5-flash"
    label: str
    free: bool         # has a free tier
    key_env: str       # where the key goes in backend/.env
    key_url: str       # where to get a key
    note: str


PROVIDERS: dict[str, ProviderInfo] = {p.id: p for p in [
    ProviderInfo("gemini", "Google Gemini", True, "GEMINI_API_KEY",
                 "https://aistudio.google.com/apikey",
                 "Free tier with daily limits. Sees images. Very long context."),
    ProviderInfo("groq", "Groq", True, "GROQ_API_KEY", "https://console.groq.com/keys",
                 "Free tier with rate limits. Extremely fast open models (Llama, Qwen, GPT-OSS)."),
    ProviderInfo("openrouter", "OpenRouter", True, "OPENROUTER_API_KEY",
                 "https://openrouter.ai/keys",
                 "One key, many models. Only the free (:free) tool-calling models are listed."),
    ProviderInfo("claude", "Anthropic Claude", False, "ANTHROPIC_API_KEY",
                 "https://platform.claude.com/settings/keys",
                 "Paid API (no free tier). The strongest tool use and reasoning."),
]}

# Shown when a provider's live model list can't be fetched. Live lists win when available.
DEFAULT_MODELS: dict[str, list[str]] = {
    "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
    "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "qwen/qwen3-32b",
             "meta-llama/llama-4-scout-17b-16e-instruct"],
    "openrouter": [],
    "claude": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
}

# Claude models that support the server-side refusal fallback (a declined request is retried
# on a fallback model inside the same call).
CLAUDE_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5",
                          "claude-opus-5", "claude-sonnet-5-5"}


def split_model(name: str) -> tuple[str | None, str]:
    """("gemini", "gemini-2.5-flash") for a cloud name; (None, name) for a local Ollama one."""
    prefix, sep, rest = name.partition(":")
    if sep and prefix in PROVIDERS and rest:
        return prefix, rest
    return None, name


def _guess_mime(b64: str) -> str:
    head = b64[:12]
    if head.startswith("iVBOR"):
        return "image/png"
    if head.startswith("R0lGOD"):
        return "image/gif"
    if head.startswith("UklGR"):
        return "image/webp"
    return "image/jpeg"


def _args_dict(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


class _Echo:
    """Provider-specific pieces of an assistant turn that must be sent back verbatim on the next
    request (Claude's thinking blocks, Gemini's thought signatures), keyed by tool-call id."""

    def __init__(self, limit: int = 500):
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._limit = limit

    def put(self, key: str, value: Any) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def get(self, key: str | None) -> Any:
        return self._items.get(key) if key else None


def _status_error(provider: str, status: int, body: str, model: str) -> OllamaError:
    info = PROVIDERS[provider]
    low = body.lower()
    if status in (401, 403) or "api key" in low or "api_key" in low:
        return OllamaError(f"{info.label} rejected the API key.",
                           hint=f"Check {info.key_env} in backend/.env (get one at {info.key_url}), "
                                "or paste a new key in Settings → Cloud brains.")
    if status == 429 or "quota" in low or "rate limit" in low:
        return OllamaError(f"{info.label}: rate limit or free quota reached.",
                           hint="Wait a minute and try again, or switch to another model.")
    if status == 404 or ("model" in low and "not found" in low):
        return OllamaError(f"{info.label} doesn't offer '{model}' (or not to this key).",
                           hint="Pick another model from the model menu.")
    if status == 413 or "too long" in low or "context" in low and "length" in low:
        return OllamaError(f"The conversation is too long for {model}.",
                           hint="Start a new chat, or pick a model with a longer context.")
    return OllamaError(f"{info.label} error {status}: {body.strip()[:300]}")


# ------------------------------------------------------- OpenAI-compatible
@dataclass
class _ModelMeta:
    context: int = 131_072
    vision: bool = False


@dataclass
class OpenAICompatProvider:
    """Gemini, Groq and OpenRouter all speak the OpenAI Chat Completions protocol."""
    provider: str
    base_url: str
    api_key: str
    max_output_tokens: int = 8192
    timeout_s: float = 300.0
    transport: httpx.AsyncBaseTransport | None = None
    _meta: dict[str, _ModelMeta] = field(default_factory=dict)
    _listed_at: float = 0.0
    _models: list[str] = field(default_factory=list)
    _echo: _Echo = field(default_factory=_Echo)

    def __post_init__(self) -> None:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider == "openrouter":
            headers |= {"X-Title": "AETHER"}
        self._client = httpx.AsyncClient(base_url=self.base_url.rstrip("/"), headers=headers,
                                         transport=self.transport,
                                         timeout=httpx.Timeout(self.timeout_s, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------ discovery
    async def list_models(self, *, force: bool = False) -> list[str]:
        if self._models and not force and time.monotonic() - self._listed_at < 600:
            return self._models
        try:
            r = await self._client.get("/models")
            if r.status_code != 200:
                raise _status_error(self.provider, r.status_code, r.text, "")
            models = self._parse_listing(r.json().get("data") or [])
        except httpx.HTTPError:
            models = []
        if not models:
            models = list(DEFAULT_MODELS[self.provider])
        self._models, self._listed_at = models, time.monotonic()
        return models

    async def verify(self) -> None:
        """Raise OllamaError (with a hint) unless the key works."""
        try:
            r = await self._client.get("/models")
        except httpx.HTTPError as e:
            raise OllamaError(f"Can't reach {PROVIDERS[self.provider].label}.",
                              hint="Check your internet connection.") from e
        if r.status_code != 200:
            raise _status_error(self.provider, r.status_code, r.text, "")
        if self.provider == "openrouter":
            # OpenRouter lists models without a key, so check the key itself.
            k = await self._client.get("/key")
            if k.status_code != 200:
                raise _status_error(self.provider, k.status_code, k.text, "")
        self._models = self._parse_listing(r.json().get("data") or []) or \
            list(DEFAULT_MODELS[self.provider])
        self._listed_at = time.monotonic()

    def _parse_listing(self, data: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for m in data:
            mid = str(m.get("id") or "").removeprefix("models/")
            low = mid.lower()
            if self.provider == "gemini":
                if "gemini" not in low or any(x in low for x in (
                        "embed", "tts", "image", "live", "audio", "aqa", "vision", "robotics",
                        "computer-use", "native")):
                    continue
                self._meta[mid] = _ModelMeta(context=1_048_576, vision=True)
            elif self.provider == "groq":
                if any(x in low for x in ("whisper", "tts", "guard", "playai", "orpheus",
                                          "distil", "allam", "compound")):
                    continue
                self._meta[mid] = _ModelMeta(context=int(m.get("context_window") or 131_072),
                                             vision="llama-4" in low or "vision" in low)
            elif self.provider == "openrouter":
                params = m.get("supported_parameters") or []
                if not mid.endswith(":free") or "tools" not in params:
                    continue
                modalities = (m.get("architecture") or {}).get("input_modalities") or []
                self._meta[mid] = _ModelMeta(context=int(m.get("context_length") or 131_072),
                                             vision="image" in modalities)
            out.append(mid)
        return sorted(out)

    def meta(self, model: str) -> _ModelMeta:
        if model in self._meta:
            return self._meta[model]
        if self.provider == "gemini":
            return _ModelMeta(context=1_048_576, vision=True)
        return _ModelMeta(vision="llama-4" in model or "vision" in model)

    # ---------------------------------------------------------- conversion
    def to_wire(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending_ids: list[str] = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                call_id = pending_ids.pop(0) if pending_ids else f"call_{uuid.uuid4().hex[:12]}"
                out.append({"role": "tool", "tool_call_id": call_id,
                            "name": m.get("tool_name") or "", "content": str(m.get("content") or "")})
                continue
            pending_ids = []
            if role == "assistant" and m.get("tool_calls"):
                calls = []
                for c in m["tool_calls"]:
                    cid = c.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                    raw = self._echo.get(cid)
                    if raw is None:
                        fn = c.get("function") or {}
                        raw = {"id": cid, "type": "function",
                               "function": {"name": fn.get("name") or "",
                                            "arguments": json.dumps(_args_dict(fn.get("arguments")))}}
                    calls.append(raw)
                    pending_ids.append(cid)
                out.append({"role": "assistant", "content": m.get("content") or None,
                            "tool_calls": calls})
            elif role == "user" and m.get("images"):
                parts: list[dict[str, Any]] = [{"type": "text", "text": m.get("content") or ""}]
                parts += [{"type": "image_url",
                           "image_url": {"url": f"data:{_guess_mime(b)};base64,{b}"}}
                          for b in m["images"]]
                out.append({"role": "user", "content": parts})
            else:
                out.append({"role": role, "content": str(m.get("content") or "")})
        return out

    # ---------------------------------------------------------------- chat
    async def chat_stream(self, *, model: str, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]] | None = None,
                          options: dict[str, Any] | None = None,
                          think: bool = False) -> AsyncIterator[ChatDelta]:
        options = options or {}
        payload: dict[str, Any] = {
            "model": model, "messages": self.to_wire(messages), "stream": True,
            "max_tokens": self.max_output_tokens,
            "temperature": options.get("temperature", 0.2),
            "stream_options": {"include_usage": True}}
        if tools:
            payload["tools"] = tools
        started = time.perf_counter()
        first_token: float | None = None
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish = ""
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    raise _status_error(self.provider, r.status_code, body, model)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        err = chunk["error"]
                        raise _status_error(self.provider, int(err.get("code") or 500)
                                            if str(err.get("code") or "").isdigit() else 500,
                                            str(err.get("message") or err), model)
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        text = delta.get("content") or ""
                        thought = delta.get("reasoning") or delta.get("reasoning_content") or ""
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(int(tc.get("index") or 0), {
                                "id": None, "type": "function",
                                "function": {"name": "", "arguments": ""}})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            slot["function"]["name"] += fn.get("name") or ""
                            slot["function"]["arguments"] += fn.get("arguments") or ""
                            if tc.get("extra_content"):  # Gemini's thought signature
                                slot["extra_content"] = tc["extra_content"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
                        if text or (thought and think):
                            first_token = first_token or time.perf_counter()
                            yield ChatDelta(content=text, thinking=thought if think else "")
        except httpx.ConnectError as e:
            raise OllamaError(f"Can't reach {PROVIDERS[self.provider].label}.",
                              hint="Check your internet connection.") from e
        except httpx.ReadTimeout as e:
            raise OllamaError(f"{PROVIDERS[self.provider].label} took too long to respond.",
                              hint="Try again, or pick a faster model.") from e

        tool_calls: list[dict[str, Any]] = []
        for _, raw in sorted(calls.items()):
            raw["id"] = raw["id"] or f"call_{uuid.uuid4().hex[:12]}"
            self._echo.put(raw["id"], raw)
            tool_calls.append({"id": raw["id"],
                               "function": {"name": raw["function"]["name"],
                                            "arguments": _args_dict(raw["function"]["arguments"])}})
        if finish == "length" and tool_calls:
            tool_calls = []  # a truncated tool call can't be trusted
        end = time.perf_counter()
        stats = CallStats(prompt_tokens=int(usage.get("prompt_tokens") or 0),
                          cached_tokens=int((usage.get("prompt_tokens_details") or {})
                                            .get("cached_tokens") or 0),
                          output_tokens=int(usage.get("completion_tokens") or 0),
                          total_s=end - started,
                          prompt_s=(first_token or end) - started,
                          gen_s=end - (first_token or started))
        yield ChatDelta(tool_calls=tool_calls, done=True,
                        done_reason="length" if finish == "length" else "stop", stats=stats)


# --------------------------------------------------------------- Anthropic
class ClaudeProvider:
    """Claude through the official Anthropic SDK (Messages API, streamed)."""
    provider = "claude"

    def __init__(self, api_key: str, *, max_output_tokens: int = 16000, timeout_s: float = 300.0,
                 client: Any = None):
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - depends on the environment
                raise OllamaError("The Anthropic SDK isn't installed.",
                                  hint="Run: pip install -r requirements.txt") from e
            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=2)
        self._client = client
        self.max_output_tokens = max_output_tokens
        self._echo = _Echo()

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    async def list_models(self, *, force: bool = False) -> list[str]:
        return list(DEFAULT_MODELS["claude"])

    async def verify(self) -> None:
        import anthropic
        try:
            await self._client.models.retrieve(DEFAULT_MODELS["claude"][0])
        except anthropic.APIConnectionError as e:
            raise OllamaError("Can't reach the Claude API.",
                              hint="Check your internet connection.") from e
        except anthropic.APIStatusError as e:
            raise _status_error("claude", e.status_code, str(e.message), "") from e

    def meta(self, model: str) -> _ModelMeta:
        return _ModelMeta(context=200_000 if model.startswith("claude-haiku-4") else 1_000_000,
                          vision=True)

    # ---------------------------------------------------------- conversion
    @staticmethod
    def to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for t in tools:
            fn = t.get("function") or t
            out.append({"name": fn["name"], "description": fn.get("description") or "",
                        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                        # stream large tool inputs (e.g. a PDF's content) as they're written;
                        # the engine validates every call against its schema before running it
                        "eager_input_streaming": True})
        return out

    def to_wire(self, messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system: list[str] = []
        out: list[dict[str, Any]] = []
        pending_ids: list[str] = []

        def push(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if out and out[-1]["role"] == role:
                prev = out[-1]["content"]
                if isinstance(blocks[0], dict) and blocks[0].get("type") == "tool_result":
                    # tool results must lead their user message
                    n = sum(1 for b in prev if isinstance(b, dict) and b.get("type") == "tool_result")
                    prev[n:n] = blocks
                else:
                    prev.extend(blocks)
            else:
                out.append({"role": role, "content": list(blocks)})

        for m in messages:
            role, text = m.get("role"), str(m.get("content") or "")
            if role == "system":
                system.append(text)
                continue
            if role == "tool":
                call_id = pending_ids.pop(0) if pending_ids else None
                if call_id is None:  # orphan result: keep the information as plain text
                    push("user", [{"type": "text", "text": f"[{m.get('tool_name')} result]\n{text}"}])
                    continue
                push("user", [{"type": "tool_result", "tool_use_id": call_id, "content": text,
                               **({"is_error": True} if text.startswith("ERROR: ") else {})}])
                continue
            pending_ids = []
            if role == "assistant":
                calls = m.get("tool_calls") or []
                ids = [c.get("id") or f"toolu_{uuid.uuid4().hex[:20]}" for c in calls]
                cached = self._echo.get(ids[0]) if ids else None
                if cached is not None:
                    blocks = cached  # the exact blocks Claude sent, thinking included
                else:
                    blocks = [{"type": "text", "text": text}] if text.strip() else []
                    for cid, c in zip(ids, calls):
                        fn = c.get("function") or {}
                        blocks.append({"type": "tool_use", "id": cid, "name": fn.get("name") or "",
                                       "input": _args_dict(fn.get("arguments"))})
                if not blocks:
                    blocks = [{"type": "text", "text": "(no reply)"}]
                pending_ids = ids
                push("assistant", blocks)
            else:
                blocks = [{"type": "image", "source": {"type": "base64",
                                                        "media_type": _guess_mime(b), "data": b}}
                          for b in m.get("images") or []]
                if text.strip() or not blocks:
                    blocks.append({"type": "text", "text": text or "(empty)"})
                push("user", blocks)
        if out and out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continuing)"}]})
        return "\n\n".join(system), out

    # ---------------------------------------------------------------- chat
    async def chat_stream(self, *, model: str, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]] | None = None,
                          options: dict[str, Any] | None = None,
                          think: bool = False) -> AsyncIterator[ChatDelta]:
        import anthropic

        system, wire = self.to_wire(messages)
        kwargs: dict[str, Any] = {"model": model, "max_tokens": self.max_output_tokens,
                                  "messages": wire}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self.to_tools(tools)
        if think:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if model in CLAUDE_FALLBACK_MODELS:
            # On a policy decline, the API re-runs the request on a fallback model in the same call.
            kwargs |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        started = time.perf_counter()
        first_token: float | None = None
        try:
            async with self._client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    if event.delta.type == "text_delta" and event.delta.text:
                        first_token = first_token or time.perf_counter()
                        yield ChatDelta(content=event.delta.text)
                    elif event.delta.type == "thinking_delta" and event.delta.thinking:
                        first_token = first_token or time.perf_counter()
                        yield ChatDelta(thinking=event.delta.thinking)
                final = await stream.get_final_message()
        except ValueError as e:
            # Tool-call JSON the SDK couldn't parse at all; the engine asks the model to retry.
            raise OllamaError("Claude sent a tool call that couldn't be read.",
                              hint="Send the message again.") from e
        except anthropic.APIConnectionError as e:
            raise OllamaError("Can't reach the Claude API.",
                              hint="Check your internet connection.") from e
        except anthropic.APIStatusError as e:
            raise _status_error("claude", e.status_code, str(e.message), model) from e

        end = time.perf_counter()
        blocks = list(final.content)  # SDK objects; sent back as-is on the next request
        tool_calls = [{"id": b.id, "function": {"name": b.name,
                                                "arguments": b.input if isinstance(b.input, dict) else {}}}
                      for b in blocks if b.type == "tool_use"]
        stop = final.stop_reason or ""
        if stop in ("max_tokens", "refusal"):
            tool_calls = []  # a cut-off or declined tool call must not run
        if tool_calls:
            self._echo.put(tool_calls[0]["id"], blocks)
        if stop == "refusal" and not any(b.type == "text" for b in blocks):
            yield ChatDelta(content="I can't help with that request.")
        u = final.usage
        prompt = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + \
            (getattr(u, "cache_creation_input_tokens", 0) or 0)
        yield ChatDelta(tool_calls=tool_calls, done=True,
                        done_reason="length" if stop == "max_tokens" else "stop",
                        stats=CallStats(prompt_tokens=prompt,
                                        cached_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                                        output_tokens=u.output_tokens or 0,
                                        total_s=end - started,
                                        prompt_s=(first_token or end) - started,
                                        gen_s=end - (first_token or started)))
