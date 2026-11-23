"""Hardware view: this Mac's memory, a network PC's GPU monitor, and Ollama's loaded models."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from aether import hardware
from aether.llm.ollama import OllamaClient
from aether.llm.router import ModelRouter

GB = 1024 ** 3

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     7415.
Pages active:                                 500000.
Pages inactive:                               521540.
Pages wired down:                             200000.
"Translation faults":                      360831422.
Pages occupied by compressor:                 100000.
"""

PC_STATS = {"hostname": "GAMING-PC", "cpu": "AMD Ryzen 7 9800X3D", "cpu_pct": 7,
            "ram_total_gb": 31.8, "ram_used_gb": 12.4,
            "gpus": [{"name": "NVIDIA GeForce RTX 5080", "vram_total_gb": 15.9, "vram_used_gb": 9.6,
                      "util_pct": 64, "temp_c": 58, "power_w": "212.40", "driver": "591.44"}]}


def ollama(ps: list[dict[str, Any]], *, online: bool = True) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if not online:
            raise httpx.ConnectError("down", request=request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.13.0"})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": ps})
        return httpx.Response(404)
    return httpx.MockTransport(handle)


def monitor(stats: dict[str, Any] | None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.port == 11435 and request.url.path == "/stats"
        if stats is None:
            raise httpx.ConnectError("no monitor", request=request)
        return httpx.Response(200, json=stats)
    return httpx.MockTransport(handle)


@pytest.fixture(autouse=True)
def fake_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "this_machine", lambda: {
        "hostname": "mac", "cpu": "Apple M5 Pro", "ram_total_gb": 24.0, "ram_used_gb": 15.1,
        "gpus": [{"name": "Apple M5 Pro GPU · 16-core"}], "unified_memory": True})


def test_vm_stat_counts_app_wired_and_compressed_memory() -> None:
    assert hardware.parse_vm_stat(VM_STAT) == (500000 + 200000 + 100000) * 16384


def test_monitor_numbers_are_normalized_and_tolerant() -> None:
    n = hardware.normalize_monitor(PC_STATS)
    gpu = n["gpus"][0]
    assert gpu["name"] == "NVIDIA GeForce RTX 5080" and gpu["vram_total_gb"] == 15.9
    assert gpu["power_w"] == 212.4 and n["ram_total_gb"] == 31.8 and not n["unified_memory"]
    sparse = hardware.normalize_monitor({"gpus": [{"name": "GPU", "power_w": "[N/A]"}, "junk"]})
    assert sparse["gpus"] == [{"name": "GPU", "vram_total_gb": None, "vram_used_gb": None, "util_pct": None,
                               "temp_c": None, "power_w": None, "driver": None}]


async def test_snapshot_shows_the_mac_and_the_pc_with_loaded_models() -> None:
    loaded = [{"name": "qwen3:14b", "size": 10 * GB, "size_vram": 10 * GB, "context_length": 16384},
              {"name": "gpt-oss:20b", "size": 16 * GB, "size_vram": 12 * GB}]
    router = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                         hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama(loaded))
    mac, pc = await hardware.snapshot(router, monitor_transport=monitor(PC_STATS))
    assert mac["label"] == "This Mac" and mac["system"]["cpu"] == "Apple M5 Pro" and mac["loaded"] == []
    assert pc["id"] == "pc" and pc["online"] and pc["monitor"] == "ok"
    assert pc["system"]["gpus"][0]["name"] == "NVIDIA GeForce RTX 5080"
    assert pc["loaded"] == [
        {"name": "qwen3:14b", "size_gb": 10.0, "vram_gb": 10.0, "gpu_pct": 100, "context": 16384},
        {"name": "gpt-oss:20b", "size_gb": 16.0, "vram_gb": 12.0, "gpu_pct": 75, "context": None}]


async def test_a_pc_without_the_monitor_or_switched_off_still_gets_a_card() -> None:
    router = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                         hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama([]))
    _, pc = await hardware.snapshot(router, monitor_transport=monitor(None))
    assert pc["online"] and pc["monitor"] == "missing" and pc["system"] is None
    off = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                      hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama([], online=False))
    _, pc = await hardware.snapshot(off, monitor_transport=monitor(None))
    assert not pc["online"] and pc["loaded"] == []


def test_api_hardware(tmp_path: Any) -> None:
    from fastapi.testclient import TestClient

    import api
    from aether.config import Settings

    router = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                         hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama([]))
    app = api.create_app(Settings(), llm=router, db_path=":memory:")
    app.state.monitor_transport = monitor(PC_STATS)
    with TestClient(app) as client:
        cards = client.get("/api/hardware").json()
    assert [c["id"] for c in cards] == ["local", "pc"]
    assert json.dumps(cards).count("RTX 5080") == 1
