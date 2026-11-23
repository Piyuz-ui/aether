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


async def fast_link(host: str, port: int) -> dict[str, Any]:
    return {**hardware.rate_link([4.8, 4.9, 5.1, 4.7], 4), "samples": 4}


async def dead_link(host: str, port: int) -> dict[str, Any]:
    return {**hardware.rate_link([], 4), "samples": 4}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never probe real ports or read the real Wi-Fi in tests."""
    monkeypatch.setattr(hardware, "mac_network", lambda: {"type": "Wi-Fi", "signal_dbm": -62, "quality": "good"})

    async def closed(host: str, port: int, timeout: float = 1.0) -> bool:
        return False
    monkeypatch.setattr(hardware, "port_open", closed)


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
    mac, pc = await hardware.snapshot(router, monitor_transport=monitor(PC_STATS), prober=fast_link)
    assert mac["label"] == "This Mac" and mac["system"]["cpu"] == "Apple M5 Pro" and mac["loaded"] == []
    assert pc["id"] == "pc" and pc["online"] and pc["monitor"] == "ok"
    assert pc["system"]["gpus"][0]["name"] == "NVIDIA GeForce RTX 5080"
    assert pc["loaded"] == [
        {"name": "qwen3:14b", "size_gb": 10.0, "vram_gb": 10.0, "gpu_pct": 100, "context": 16384},
        {"name": "gpt-oss:20b", "size_gb": 16.0, "vram_gb": 12.0, "gpu_pct": 75, "context": None}]


async def test_a_pc_without_the_monitor_or_switched_off_still_gets_a_card() -> None:
    router = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                         hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama([]))
    _, pc = await hardware.snapshot(router, monitor_transport=monitor(None), prober=fast_link)
    assert pc["online"] and pc["monitor"] == "missing" and pc["system"] is None
    assert pc["availability"]["state"] == "ready" and pc["link"]["quality"] == "excellent"
    off = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                      hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama([], online=False))
    _, pc = await hardware.snapshot(off, monitor_transport=monitor(None), prober=dead_link)
    assert not pc["online"] and pc["loaded"] == []
    assert pc["availability"]["state"] == "offline" and pc["link"]["quality"] == "unreachable"


def test_api_hardware(tmp_path: Any) -> None:
    from fastapi.testclient import TestClient

    import api
    from aether.config import Settings

    router = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                         hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama([]))
    app = api.create_app(Settings(), llm=router, db_path=":memory:")
    app.state.monitor_transport = monitor(PC_STATS)
    app.state.link_prober = fast_link
    with TestClient(app) as client:
        cards = client.get("/api/hardware").json()
    assert [c["id"] for c in cards] == ["local", "pc"]
    assert json.dumps(cards).count("RTX 5080") == 1


@pytest.mark.parametrize("rtts,attempts,quality", [
    ([4.8, 5.0, 4.9, 5.2, 4.7, 5.1], 6, "excellent"),
    ([18, 22, 25, 20, 19, 24], 6, "good"),
    ([45, 70, 50, 60], 5, "fair"),      # slower, and 1 of 5 attempts lost
    ([150, 300, 120], 6, "poor"),
    ([], 6, "unreachable"),
])
def test_link_ratings(rtts: list[float], attempts: int, quality: str) -> None:
    r = hardware.rate_link(rtts, attempts)
    assert r["quality"] == quality and r["verdict"]
    if rtts:
        assert r["latency_ms"] == pytest.approx(sorted(rtts)[len(rtts) // 2], abs=15)


def test_availability_explains_what_is_wrong() -> None:
    up = hardware.rate_link([5.0], 1)
    down = hardware.rate_link([], 1)
    assert hardware.availability(True, up, False)["state"] == "ready"
    assert hardware.availability(False, up, False)["state"] == "ollama_error"
    assert hardware.availability(False, down, True)["state"] == "no_ollama"
    assert hardware.availability(False, down, False)["state"] == "offline"


def test_wifi_signal_from_system_profiler() -> None:
    data = {"SPAirPortDataType": [{"spairport_airport_interfaces": [
        {"_name": "en0", "spairport_current_network_information": {
            "_name": "HomeNet", "spairport_network_channel": "157 (5GHz, 80MHz)", "spairport_network_rate": 260,
            "spairport_network_phymode": "802.11ac", "spairport_signal_noise": "-62 dBm / -94 dBm"}},
        {"_name": "awdl0", "spairport_current_network_information": {}}]}]}
    w = hardware.parse_airport(data)
    assert w == {"type": "Wi-Fi", "signal_dbm": -62, "noise_dbm": -94, "snr_db": 32, "quality": "good",
                 "band": "5GHz", "channel": "157", "link_mbps": 260, "standard": "802.11ac"}
    assert "HomeNet" not in json.dumps(w)  # the network's name is never sent to the UI
    assert hardware.parse_airport({"SPAirPortDataType": [{}]}) is None


def test_pc_network_from_the_monitor() -> None:
    n = hardware.normalize_monitor({**PC_STATS, "network": {"type": "Wi-Fi", "link_mbps": 866,
                                                            "wifi_signal_pct": 72, "adapter": "Intel Wi-Fi 7"}})
    assert n["network"] == {"type": "Wi-Fi", "link_mbps": 866.0, "adapter": "Intel Wi-Fi 7",
                            "wifi_signal_pct": 72.0, "quality": "good"}
    assert hardware.normalize_monitor(PC_STATS)["network"] is None


def test_live_gpu_endpoint_follows_the_answering_model() -> None:
    from fastapi.testclient import TestClient

    import api
    from aether.config import Settings

    router = ModelRouter(OllamaClient("http://mac.test", transport=ollama([])),
                         hosts={"pc": "http://192.168.1.9:11434"}, host_transport=ollama([]))
    app = api.create_app(Settings(), llm=router, db_path=":memory:")
    app.state.monitor_transport = monitor(PC_STATS)
    with TestClient(app) as client:
        live = client.get("/api/gpu", params={"model": "qwen3:14b@pc"}).json()
        assert live == {"available": True, "host": "pc", "name": "NVIDIA GeForce RTX 5080", "util_pct": 64.0,
                        "vram_used_gb": 9.6, "vram_total_gb": 15.9, "temp_c": 58.0, "power_w": 212.4}
        assert client.get("/api/gpu", params={"model": "qwen2.5:7b"}).json()["reason"] == "local"
        assert client.get("/api/gpu", params={"model": "gemini:gemini-2.5-flash"}).json()["reason"] == "cloud"
    app.state.monitor_transport = monitor(None)
    with TestClient(app) as client:
        assert client.get("/api/gpu", params={"model": "qwen3:14b@pc"}).json() == {
            "available": False, "reason": "no_monitor", "host": "pc"}
