"""One model backend for the engine: this machine's Ollama, other computers' Ollama on your
network (e.g. a gaming PC's GPU), and optional cloud providers.

Model names choose the backend:
    "qwen2.5:7b"               Ollama on this machine
    "qwen3:14b@pc"             Ollama on the network host named "pc" (still private: your network)
    "gemini:gemini-2.5-flash"  a cloud provider (any prefix in cloud.PROVIDERS)
The router has the same methods as OllamaClient, so nothing else in AETHER needs to know the
difference. Embeddings (the knowledge vault and memory) always stay on this machine.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .cloud import (PROVIDERS, ClaudeProvider, OpenAICompatProvider, split_model)
from .ollama import ChatDelta, OllamaClient, OllamaError

HOST_NAME = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
ROUTE_TTL_S = 15.0  # how long a working route is trusted before it's checked again


def address_kind(url: str) -> str:
    """How an address reaches a computer: "home" (your local network), "tailscale" (your private
    Tailscale network, works from anywhere) or "internet"."""
    import ipaddress
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(".ts.net"):
        return "tailscale"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "home" if host.endswith(".local") or "." not in host else "internet"
    if ip in ipaddress.ip_network("100.64.0.0/10"):
        return "tailscale"
    return "home" if ip.is_private or ip.is_loopback else "internet"


def _as_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else [v for v in str(value).split(",")]
    return [u.strip().rstrip("/") for u in items if u and u.strip()]


class ModelRouter:
    def __init__(self, ollama: OllamaClient, *, keys: dict[str, str] | None = None,
                 context_length: int = 16384, cloud_max_output_tokens: int = 8192,
                 claude_max_output_tokens: int = 16000, timeout_s: float = 300.0,
                 transport: Any = None, claude_client: Any = None,
                 hosts: dict[str, str | list[str]] | None = None, host_transport: Any = None):
        self.ollama = ollama
        self.context_length = context_length
        self._limits = (cloud_max_output_tokens, claude_max_output_tokens, timeout_s)
        self._transport = transport          # tests inject a mock HTTP transport
        self._claude_client = claude_client  # tests inject a fake SDK client
        self.providers: dict[str, Any] = {}
        for pid, key in (keys or {}).items():
            if key:
                self.providers[pid] = self._make(pid, key)
        self._host_transport = host_transport  # tests inject a mock Ollama
        # Each computer can have several addresses, in order of preference: usually its home
        # address first, then its Tailscale address for when you're away. The router uses the
        # first one that answers and switches automatically when you change networks.
        self.host_addrs: dict[str, list[str]] = {}
        self._clients: dict[str, OllamaClient] = {}        # one client per address
        self.hosts: dict[str, OllamaClient] = {}           # name → client of the address in use
        self.host_urls: dict[str, str] = {}                # name → address in use
        self._route_ok: dict[str, tuple[float, bool]] = {}  # name → (checked at, worked)
        for name, value in (hosts or {}).items():
            addrs = _as_list(value)
            if HOST_NAME.match(name) and addrs:
                self.host_addrs[name] = addrs
                self._use(name, addrs[0])

    def _make_host(self, url: str) -> OllamaClient:
        return OllamaClient(url, timeout_s=self._limits[2], transport=self._host_transport)

    def _client_for(self, url: str) -> OllamaClient:
        if url not in self._clients:
            self._clients[url] = self._make_host(url)
        return self._clients[url]

    def _use(self, name: str, url: str) -> None:
        self.hosts[name] = self._client_for(url)
        self.host_urls[name] = url

    async def _answers(self, url: str, timeout: float = 2.0) -> bool:
        try:
            r = await self._client_for(url)._client.get("/api/version", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    async def ensure_route(self, name: str, *, force: bool = False) -> bool:
        """Point `name` at the first of its addresses that answers right now (home before
        Tailscale). Cheap when nothing changed: a working route is trusted for 15 s."""
        addrs = self.host_addrs.get(name) or []
        if not addrs:
            return False
        checked_at, worked = self._route_ok.get(name, (0.0, False))
        if not force and worked and time.monotonic() - checked_at < ROUTE_TTL_S:
            return True
        results = await asyncio.gather(*(self._answers(u) for u in addrs))
        chosen = next((u for u, ok in zip(addrs, results) if ok), None)
        if chosen is not None:
            self._use(name, chosen)
        self._route_ok[name] = (time.monotonic(), chosen is not None)
        return chosen is not None

    async def ensure_routes(self) -> None:
        await asyncio.gather(*(self.ensure_route(n) for n in list(self.host_addrs)))

    async def set_host(self, name: str, url: str | list[str] | None) -> dict[str, bool]:
        """Add or replace a network computer with one or more addresses (in order of preference),
        or remove it. At least one address must answer now. Returns which ones did."""
        if not HOST_NAME.match(name):
            raise OllamaError("Host names use lowercase letters, digits and dashes (e.g. pc).")
        addrs = _as_list(url)
        if not addrs:
            self.host_addrs.pop(name, None)
            self.hosts.pop(name, None)
            self.host_urls.pop(name, None)
            self._route_ok.pop(name, None)
            return {}
        reach = dict(zip(addrs, await asyncio.gather(*(self._answers(u, 4.0) for u in addrs))))
        if not any(reach.values()):
            tried = " or ".join(addrs)
            raise OllamaError(f"No Ollama answered at {tried}.",
                              hint="On that computer: open Ollama, set OLLAMA_HOST=0.0.0.0 and allow "
                                   "port 11434 through the firewall (see pc/setup-ollama-pc.ps1). For a "
                                   "Tailscale address, make sure Tailscale is on here and on the PC.")
        self.host_addrs[name] = addrs
        self._use(name, next(u for u in addrs if reach[u]))
        self._route_ok[name] = (time.monotonic(), True)
        return reach

    def _make(self, pid: str, key: str) -> Any:
        out_cap, claude_cap, timeout_s = self._limits
        if pid == "claude":
            return ClaudeProvider(key, max_output_tokens=claude_cap, timeout_s=timeout_s,
                                  client=self._claude_client)
        return OpenAICompatProvider(pid, PROVIDERS[pid].base_url, key, max_output_tokens=out_cap,
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

    @staticmethod
    def split_host(model: str) -> tuple[str | None, str]:
        """("pc", "qwen3:14b") for "qwen3:14b@pc"; (None, model) otherwise."""
        bare, sep, host = model.rpartition("@")
        if sep and bare and HOST_NAME.match(host):
            return host, bare
        return None, model

    def is_remote(self, model: str | None) -> bool:
        """A model on another computer on your network (private, but not this machine)."""
        return bool(model) and not self.is_cloud(model) and self.split_host(model or "")[0] is not None

    def _ollama_for(self, model: str) -> tuple[OllamaClient, str]:
        """The Ollama that serves `model` (this machine's, or a network host's) and its bare name."""
        if self.is_cloud(model):
            raise OllamaError(f"'{model}' is a cloud model.")
        host, bare = self.split_host(model)
        if host is None:
            return self.ollama, model
        client = self.hosts.get(host)
        if client is None:
            raise OllamaError(f"There's no computer named '{host}' set up.",
                              hint="Add it in Settings → Computers on your network.")
        return client, bare

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
        if self.is_remote(preferred):
            host, bare = self.split_host(preferred)
            await self.ensure_route(host or "")
            client = self.hosts.get(host or "")
            try:
                if client is not None and bare in await client.list_models():
                    return preferred, None
                why = (f"'{bare}' isn't installed on the computer '{host}'" if client
                       else f"There's no computer named '{host}' set up")
            except OllamaError:
                why = f"The computer '{host}' isn't reachable right now"
            local, _note = await self.ollama.resolve_model(*(fallbacks[:1] or ["qwen2.5:7b"]), fallbacks[1:])
            return local, f"{why}, so using {local} on this Mac for now."
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
        client, bare = self._ollama_for(model)
        return await client.capabilities(bare)

    async def find_model_with(self, *needs: str, prefer: str = "") -> str | None:
        """A model with these capabilities: the preferred one, else this Mac's, else a network host's."""
        if prefer and (self.is_cloud(prefer) or self.is_remote(prefer)):
            try:
                if set(needs) <= await self.capabilities(prefer):
                    return prefer
            except OllamaError:
                pass
            prefer = ""
        try:
            found = await self.ollama.find_model_with(*needs, prefer=prefer)
        except OllamaError:
            found = None
        if found:
            return found
        await self.ensure_routes()
        for name, client in self.hosts.items():
            try:
                hit = await client.find_model_with(*needs)
            except OllamaError:
                continue
            if hit:
                return f"{hit}@{name}"
        return None

    def chat_stream(self, *, model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None,
                    options: dict[str, Any] | None = None,
                    think: bool = False) -> AsyncIterator[ChatDelta]:
        # Returns the backend's own stream (not a wrapper), so closing it early really stops it.
        if self.is_cloud(model):
            provider, bare = self._cloud(model)
            return provider.chat_stream(model=bare, messages=messages, tools=tools,
                                        options=options, think=think)
        host, _ = self.split_host(model)
        if host is not None and len(self.host_addrs.get(host, [])) > 1:
            return self._routed_stream(host, model, messages, tools, options, think)
        client, bare = self._ollama_for(model)
        return client.chat_stream(model=bare, messages=messages, tools=tools,
                                  options=options, think=think)

    async def _routed_stream(self, host: str, model: str, messages: list[dict[str, Any]],
                             tools: list[dict[str, Any]] | None, options: dict[str, Any] | None,
                             think: bool) -> AsyncIterator[ChatDelta]:
        """Pick the working address (home or Tailscale) first, then stream from it. Closing this
        stream closes the backend's too, so stopping early still stops the model."""
        await self.ensure_route(host)
        client, bare = self._ollama_for(model)
        async with contextlib.aclosing(client.chat_stream(model=bare, messages=messages, tools=tools,
                                                          options=options, think=think)) as inner:
            async for d in inner:
                yield d

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return await self.ollama.embed(texts, model)

    async def aclose(self) -> None:
        for p in self.providers.values():
            await p.aclose()
        for c in self._clients.values():
            await c.aclose()
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


    async def hosts_catalog(self) -> list[dict[str, Any]]:
        """Every network Ollama host: its address, whether it answers, and its chat models."""
        await self.ensure_routes()
        out = []
        for name, client in self.hosts.items():
            active = self.host_urls[name]
            entry: dict[str, Any] = {"name": name, "url": active, "online": False,
                                     "route": address_kind(active),
                                     "addresses": [{"url": u, "kind": address_kind(u), "active": u == active}
                                                   for u in self.host_addrs.get(name, [active])],
                                     "models": [], "error": None}
            try:
                names = await client.list_models()
                entry["online"] = True
            except OllamaError as e:
                names, entry["error"] = [], {"message": e.message, "hint": e.hint}
            for n in names:
                caps = await client.capabilities(n)
                if caps and "completion" not in caps:
                    continue  # embedding-only models
                entry["models"].append({"id": f"{n}@{name}", "name": n, "vision": "vision" in caps,
                                        "tools": "tools" in caps})
            out.append(entry)
        return out


def router_from_settings(settings: Any) -> ModelRouter:
    """Local Ollama, network Ollama hosts, and every cloud provider whose API key is set."""
    return ModelRouter(OllamaClient(settings.ollama_url, timeout_s=settings.request_timeout_s),
                       keys=settings.cloud_keys(), context_length=settings.context_length,
                       cloud_max_output_tokens=settings.cloud_max_output_tokens,
                       claude_max_output_tokens=settings.claude_max_output_tokens,
                       timeout_s=settings.request_timeout_s,
                       hosts=dict(getattr(settings, "ollama_hosts", {}) or {}))  # str or [home, remote]


def save_env_value(env_file: Path, name: str, value: str | None) -> None:
    """Set (or with None, remove) NAME=value in a .env file, keeping every other line."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    kept = [ln for ln in lines if not ln.split("=", 1)[0].strip() == name]
    if value:
        kept.append(f"{name}={value}")
    env_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(env_file, 0o600)  # API keys: readable by you only
