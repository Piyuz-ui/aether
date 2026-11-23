"""What the hardware running your models looks like right now: this Mac and network GPU PCs.

    This Mac      chip, GPU cores and unified memory (sysctl / vm_stat / system_profiler)
    A network PC  GPU name, VRAM, utilisation, temperature, RAM and CPU, from AETHER's small
                  hardware monitor on that PC (pc/install-hardware-monitor.ps1, port 11435)
    Both          which models Ollama has loaded, and how much of each sits in GPU memory
    The link      how fast and steady the connection to each PC is, and whether it's available

Everything is read-only, and every source is optional: a missing one just leaves blanks.
"""
from __future__ import annotations

import asyncio
import json
import platform
import re
import socket
import statistics
import subprocess
import threading
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


# ------------------------------------------------------------------ network
def wifi_quality(dbm: int | None) -> str | None:
    """Signal strength in plain words (dBm: closer to 0 is stronger)."""
    if dbm is None:
        return None
    return "excellent" if dbm >= -60 else "good" if dbm >= -67 else "fair" if dbm >= -75 else "weak"


def parse_airport(data: dict[str, Any]) -> dict[str, Any] | None:
    """system_profiler SPAirPortDataType → this Mac's Wi-Fi link (no network name: not needed)."""
    for item in data.get("SPAirPortDataType") or []:
        for iface in item.get("spairport_airport_interfaces") or []:
            cur = iface.get("spairport_current_network_information") or {}
            sn = cur.get("spairport_signal_noise") or ""
            m = re.match(r"\s*(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", sn)
            if not m:
                continue
            signal, noise = int(m.group(1)), int(m.group(2))
            channel = str(cur.get("spairport_network_channel") or "")
            band = b.group(1) if (b := re.search(r"(\d(?:\.\d)?GHz)", channel)) else None
            return {"type": "Wi-Fi", "signal_dbm": signal, "noise_dbm": noise, "snr_db": signal - noise,
                    "quality": wifi_quality(signal), "band": band,
                    "channel": channel.split(" ")[0] or None,
                    "link_mbps": cur.get("spairport_network_rate"),
                    "standard": cur.get("spairport_network_phymode")}
    return None


_wifi: dict[str, Any] = {"value": None, "at": 0.0, "busy": False}


def mac_network() -> dict[str, Any] | None:
    """This Mac's Wi-Fi link. Reading it takes seconds, so it's always refreshed in the background
    (at most once a minute) and the last reading is returned straight away: None at first."""
    if platform.system() != "Darwin":
        return None

    def refresh() -> None:
        try:
            data = json.loads(_run("system_profiler", "SPAirPortDataType", "-json", timeout=15) or "{}")
            _wifi["value"] = parse_airport(data) or {"type": "Wired or offline"}
        except ValueError:
            pass
        finally:
            _wifi["at"], _wifi["busy"] = time.monotonic(), False

    if time.monotonic() - _wifi["at"] > 60 and not _wifi["busy"]:
        _wifi["busy"] = True
        threading.Thread(target=refresh, daemon=True).start()  # never makes the page wait
    return _wifi["value"]


def rate_link(rtts: list[float], attempts: int) -> dict[str, Any]:
    """Median delay, jitter and loss → a rating and a plain-words verdict for AETHER use."""
    loss = round(100 * (attempts - len(rtts)) / attempts) if attempts else 100
    if not rtts:
        return {"latency_ms": None, "jitter_ms": None, "loss_pct": 100, "quality": "unreachable", "bars": 0,
                "verdict": "Can't reach it: it may be off, asleep, or on a different network."}
    latency = statistics.median(rtts)
    jitter = statistics.mean(abs(a - b) for a, b in zip(rtts, rtts[1:])) if len(rtts) > 1 else 0.0
    if latency <= 10 and jitter <= 5 and loss == 0:
        quality, bars = "excellent", 4
        verdict = f"Perfect for AETHER. The network adds about {latency:.0f} ms to a reply, which you won't notice."
    elif latency <= 30 and jitter <= 15 and loss <= 10:
        quality, bars = "good", 3
        verdict = "Great for AETHER. Replies stream smoothly."
    elif latency <= 80 and loss <= 25:
        quality, bars = "fair", 2
        verdict = "Usable, but replies may start a little late or stutter. A cable or a closer router helps."
    else:
        quality, bars = "poor", 1
        verdict = "Slow or unstable. Expect delays and dropped replies; use a cable, or this Mac's models."
    return {"latency_ms": round(latency, 1), "jitter_ms": round(jitter, 1), "loss_pct": loss,
            "quality": quality, "bars": bars, "verdict": verdict}


async def probe_link(host: str, port: int = 11434, samples: int = 6, timeout: float = 1.0) -> dict[str, Any]:
    """Time real connections to the PC's Ollama port (Windows blocks ping by default, and this is
    exactly the path AETHER uses). Gives up after two misses in a row, so an offline PC is quick."""
    rtts: list[float] = []
    misses = 0
    for i in range(samples):
        if misses >= 2 and not rtts:
            samples = i  # count only the attempts made
            break
        t = time.perf_counter()
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
            rtts.append((time.perf_counter() - t) * 1000)
            w.close()
            misses = 0
        except (OSError, asyncio.TimeoutError):
            misses += 1
        if i < samples - 1:
            await asyncio.sleep(0.03)
    return {**rate_link(rtts, samples), "samples": samples}


async def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def availability(ollama_up: bool, link: dict[str, Any], monitor_up: bool) -> dict[str, str]:
    """Can AETHER use this computer right now, and if not, what's wrong?"""
    if ollama_up:
        return {"state": "ready", "label": "Ready to use", "detail": "Ollama is answering. Its models are in the model menu."}
    if link.get("latency_ms") is not None:
        return {"state": "ollama_error", "label": "Ollama not responding",
                "detail": "The computer answers, but Ollama didn't reply. Restart the Ollama app on it."}
    if monitor_up:
        return {"state": "no_ollama", "label": "On, but Ollama is off",
                "detail": "The computer is on. Open the Ollama app on it (or log in, if it just restarted)."}
    return {"state": "offline", "label": "Not available",
            "detail": "No answer. It may be off or asleep, or on a different Wi-Fi. Wake it and check it's on the same network."}


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
    net = raw.get("network") if isinstance(raw.get("network"), dict) else None
    network = None
    if net:
        sig = num(net.get("wifi_signal_pct"))
        network = {"type": str(net.get("type") or "Network"), "link_mbps": num(net.get("link_mbps")),
                   "adapter": net.get("adapter"), "wifi_signal_pct": sig,
                   # Windows reports Wi-Fi signal as a percentage
                   "quality": None if sig is None else
                   "excellent" if sig >= 80 else "good" if sig >= 60 else "fair" if sig >= 40 else "weak"}
    return {"hostname": raw.get("hostname"), "cpu": raw.get("cpu"), "cpu_pct": num(raw.get("cpu_pct")),
            "ram_total_gb": num(raw.get("ram_total_gb")), "ram_used_gb": num(raw.get("ram_used_gb")),
            "gpus": gpus, "unified_memory": False, "network": network}


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


async def snapshot(router: Any, *, monitor_transport: httpx.AsyncBaseTransport | None = None,
                   prober: Any = None) -> list[dict[str, Any]]:
    """One card per machine: this computer first, then every network host."""
    started = time.perf_counter()
    prober = prober or probe_link  # tests pass a fake, so they never touch the network
    local_ollama = getattr(router, "ollama", router)
    hosts: dict[str, OllamaClient] = getattr(router, "hosts", {})
    urls: dict[str, str] = getattr(router, "host_urls", {})

    async def local() -> dict[str, Any]:
        system = await asyncio.to_thread(this_machine)
        system["network"] = await asyncio.to_thread(mac_network)
        return {"id": "local", "label": "This Mac" if system.get("unified_memory") else "This computer",
                "online": await local_ollama.health(), "monitor": "built-in", "system": system,
                "loaded": await loaded_models(local_ollama)}

    async def remote(name: str, client: OllamaClient) -> dict[str, Any]:
        parts = urlsplit(urls[name])
        host, port = parts.hostname or "", parts.port or 11434
        link = await prober(host, port)
        if link.get("latency_ms") is None:  # nothing answers on Ollama's port: skip the slow checks
            online, system = False, None
            monitor_up = await port_open(host, MONITOR_PORT)
        else:
            online, system = await asyncio.gather(client.health(), monitor_stats(urls[name], monitor_transport))
            monitor_up = system is not None
        return {"id": name, "label": name, "url": urls[name], "online": online,
                "monitor": "ok" if system else "missing", "system": system, "link": link,
                "availability": availability(online, link, monitor_up),
                "loaded": await loaded_models(client) if online else []}

    cards = await asyncio.gather(local(), *(remote(n, c) for n, c in hosts.items()))
    for c in cards:
        c["took_ms"] = round((time.perf_counter() - started) * 1000)
    return list(cards)
