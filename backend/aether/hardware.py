"""What the hardware running your models looks like right now: this Mac and network GPU PCs.

    This Mac      chip, GPU cores and unified memory (sysctl / vm_stat / system_profiler)
    A network PC  GPU name, VRAM, utilisation, temperature, RAM and CPU, from AETHER's small
                  hardware monitor on that PC (pc/install-hardware-monitor.ps1, port 11435)
    Both          which models Ollama has loaded, and how much of each sits in GPU memory

Everything is read-only, and every source is optional: a missing one just leaves blanks.
"""
from __future__ import annotations

import asyncio
import json
import platform
import re
import socket
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .llm.ollama import OllamaClient

GB = 1024 ** 3
MONITOR_PORT = 11435
_static_cache: dict[str, Any] = {}


def _run(*cmd: str, timeout: float = 4.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_vm_stat(text: str) -> int:
    """Bytes in use (app + wired + compressed), like Activity Monitor's "Memory Used"."""
    page = int(m.group(1)) if (m := re.search(r"page size of (\d+) bytes", text)) else 16384
    pages: dict[str, int] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            if (v := value.strip().rstrip(".")).isdigit():
                pages[key.strip().strip('"')] = int(v)
    used = (pages.get("Pages active", 0) + pages.get("Pages wired down", 0)
            + pages.get("Pages occupied by compressor", 0))
    return used * page


def _mac_static() -> dict[str, Any]:
    if "mac" not in _static_cache:
        chip = _run("sysctl", "-n", "machdep.cpu.brand_string").strip() or platform.processor()
        total = int(_run("sysctl", "-n", "hw.memsize").strip() or 0)
        gpu, cores = chip, None
        try:
            d = json.loads(_run("system_profiler", "SPDisplaysDataType", "-json", timeout=8))
            first = (d.get("SPDisplaysDataType") or [{}])[0]
            gpu, cores = first.get("sppci_model") or chip, first.get("sppci_cores")
        except (ValueError, TypeError):
            pass
        _static_cache["mac"] = {"chip": chip, "ram_total": total, "gpu": gpu,
                                "gpu_cores": int(cores) if str(cores or "").isdigit() else None}
    return _static_cache["mac"]


def this_machine() -> dict[str, Any]:
    """Stats for the computer AETHER runs on (macOS in full; elsewhere best effort)."""
    host = socket.gethostname().removesuffix(".local")
    if platform.system() != "Darwin":
        return {"hostname": host, "cpu": platform.processor() or platform.machine(),
                "ram_total_gb": None, "ram_used_gb": None, "gpus": [], "unified_memory": False}
    s = _mac_static()
    used = parse_vm_stat(_run("vm_stat"))
    gpu_name = f"{s['gpu']} GPU" + (f" · {s['gpu_cores']}-core" if s["gpu_cores"] else "")
    return {"hostname": host, "cpu": s["chip"],
            "ram_total_gb": round(s["ram_total"] / GB, 1), "ram_used_gb": round(used / GB, 1),
            # Apple silicon: the GPU shares the same (unified) memory, so there's no separate VRAM.
            "gpus": [{"name": gpu_name, "vram_total_gb": None, "vram_used_gb": None,
                      "util_pct": None, "temp_c": None, "power_w": None, "driver": None}],
            "unified_memory": True}


def normalize_monitor(raw: dict[str, Any]) -> dict[str, Any]:
    """The PC monitor's JSON → the same shape as this_machine(). Tolerates missing fields."""
    def num(v: Any) -> float | None:
        try:
            return round(float(v), 1)
        except (TypeError, ValueError):
            return None

    gpus = [{"name": str(g.get("name") or "GPU"),
             "vram_total_gb": num(g.get("vram_total_gb")), "vram_used_gb": num(g.get("vram_used_gb")),
             "util_pct": num(g.get("util_pct")), "temp_c": num(g.get("temp_c")),
             "power_w": num(g.get("power_w")), "driver": g.get("driver")}
            for g in (raw.get("gpus") or []) if isinstance(g, dict)]
    return {"hostname": raw.get("hostname"), "cpu": raw.get("cpu"), "cpu_pct": num(raw.get("cpu_pct")),
            "ram_total_gb": num(raw.get("ram_total_gb")), "ram_used_gb": num(raw.get("ram_used_gb")),
            "gpus": gpus, "unified_memory": False}


async def loaded_models(client: OllamaClient) -> list[dict[str, Any]]:
    """Models Ollama has in memory right now, and how much of each is on the GPU."""
    try:
        r = await client._client.get("/api/ps", timeout=3.0)
        r.raise_for_status()
        models = r.json().get("models") or []
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for m in models:
        size, vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
        out.append({"name": m.get("name"), "size_gb": round(size / GB, 1),
                    "vram_gb": round(vram / GB, 1),
                    "gpu_pct": round(100 * vram / size) if size else 0,
                    "context": m.get("context_length")})
    return out


async def monitor_stats(url: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any] | None:
    """Ask the hardware monitor on a network PC (same host as its Ollama, port 11435)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        async with httpx.AsyncClient(timeout=2.5, transport=transport) as c:
            r = await c.get(f"http://{host}:{MONITOR_PORT}/stats")
            r.raise_for_status()
            return normalize_monitor(r.json())
    except (httpx.HTTPError, ValueError):
        return None


async def snapshot(router: Any, *, monitor_transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    """One card per machine: this computer first, then every network host."""
    started = time.perf_counter()
    local_ollama = getattr(router, "ollama", router)
    hosts: dict[str, OllamaClient] = getattr(router, "hosts", {})
    urls: dict[str, str] = getattr(router, "host_urls", {})

    async def local() -> dict[str, Any]:
        system = await asyncio.to_thread(this_machine)
        return {"id": "local", "label": "This Mac" if system.get("unified_memory") else "This computer",
                "online": await local_ollama.health(), "monitor": "built-in", "system": system,
                "loaded": await loaded_models(local_ollama)}

    async def remote(name: str, client: OllamaClient) -> dict[str, Any]:
        online, system = await asyncio.gather(client.health(), monitor_stats(urls[name], monitor_transport))
        return {"id": name, "label": name, "url": urls[name], "online": online,
                "monitor": "ok" if system else "missing", "system": system,
                "loaded": await loaded_models(client) if online else []}

    cards = await asyncio.gather(local(), *(remote(n, c) for n, c in hosts.items()))
    for c in cards:
        c["took_ms"] = round((time.perf_counter() - started) * 1000)
    return list(cards)
