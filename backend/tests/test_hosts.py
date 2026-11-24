"""Computers on your network: a GPU PC running Ollama, reached as "<model>@<name>".

A mock Ollama plays the PC, so these tests need no network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from aether.config import Settings
from aether.llm.ollama import OllamaClient, OllamaError
from aether.llm.router import ModelRouter


class FakePC:
    """A minimal Ollama: version, tags, show and a streamed chat. Records chat requests."""

    def __init__(self, models: dict[str, list[str]], *, online: bool = True):
        self.models, self.online = models, online
        self.chats: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("unreachable", request=request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.13.0"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.models]})
        if path == "/api/show":
            name = json.loads(request.content)["model"]
            return httpx.Response(200, json={"capabilities": self.models.get(name, [])})
        if path == "/api/chat":
            self.chats.append(json.loads(request.content))
            lines = [{"message": {"content": "Hi from the GPU."}, "done": False},
                     {"message": {"content": ""}, "done": True, "done_reason": "stop",
                      "eval_count": 5, "prompt_eval_count": 20}]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        return httpx.Response(404)


PC_MODELS = {"qwen3:14b": ["completion", "tools"], "qwen3.5:4b": ["completion", "tools", "vision"],
             "nomic-embed-text:latest": ["embedding"]}


def make_router(pc: FakePC, mac_models: dict[str, list[str]] | None = None, **hosts: str) -> ModelRouter:
    mac = FakePC(mac_models if mac_models is not None else {"qwen2.5:7b": ["completion", "tools"]})
    return ModelRouter(OllamaClient("http://mac.test", transport=httpx.MockTransport(mac)),
                       hosts=hosts or {"pc": "http://192.168.1.50:11434"},
                       host_transport=httpx.MockTransport(pc))


def test_host_names_parse_without_clashing_with_ollama_or_cloud_names() -> None:
    split = ModelRouter.split_host
    assert split("qwen3:14b@pc") == ("pc", "qwen3:14b")
    assert split("hf.co/org/model:Q4_K_M@gaming-pc") == ("gaming-pc", "hf.co/org/model:Q4_K_M")
    assert split("qwen2.5:7b") == (None, "qwen2.5:7b")
    assert split("model@sha256:abc") == (None, "model@sha256:abc")  # a digest, not a host
    r = make_router(FakePC(PC_MODELS))
    assert r.is_remote("qwen3:14b@pc") and not r.is_cloud("qwen3:14b@pc")
    assert not r.is_remote("gemini:gemini-2.5-flash")


async def test_chat_runs_on_the_pc_with_the_bare_model_name() -> None:
    pc = FakePC(PC_MODELS)
    r = make_router(pc)
    out = [d async for d in r.chat_stream(model="qwen3:14b@pc", messages=[{"role": "user", "content": "hi"}],
                                          options={"num_ctx": 16384})]
    assert out[0].content == "Hi from the GPU." and out[-1].done
    assert pc.chats[0]["model"] == "qwen3:14b" and pc.chats[0]["options"]["num_ctx"] == 16384


async def test_unknown_host_explains_where_to_add_it() -> None:
    with pytest.raises(OllamaError) as err:
        make_router(FakePC(PC_MODELS)).chat_stream(model="qwen3:14b@laptop", messages=[])
    assert "laptop" in err.value.message and "Settings" in err.value.hint


async def test_catalog_lists_chat_models_with_capabilities_and_hides_embedders() -> None:
    hosts = await make_router(FakePC(PC_MODELS)).hosts_catalog()
    assert hosts[0]["online"] and hosts[0]["url"] == "http://192.168.1.50:11434"
    assert {m["id"]: m["vision"] for m in hosts[0]["models"]} == {
        "qwen3:14b@pc": False, "qwen3.5:4b@pc": True}
    offline = await make_router(FakePC(PC_MODELS, online=False)).hosts_catalog()
    assert not offline[0]["online"] and offline[0]["error"]


async def test_vision_falls_back_to_the_pc_when_the_mac_has_no_vision_model() -> None:
    r = make_router(FakePC(PC_MODELS))
    assert await r.find_model_with("vision", "tools") == "qwen3.5:4b@pc"
    assert "vision" in await r.capabilities("qwen3.5:4b@pc")


async def test_pc_default_stays_selected_while_the_pc_is_off() -> None:
    """An offline PC stays your brain (each answer fails over on its own), so it's used again the
    moment it's back."""
    r = make_router(FakePC(PC_MODELS, online=False))
    model, note = await r.resolve_model("qwen3:14b@pc", ["qwen2.5:7b"])
    assert model == "qwen3:14b@pc" and "isn't reachable right now" in (note or "")
    assert (await r.failover(model, ["qwen2.5:7b"]))[0] == "qwen2.5:7b"
    on = make_router(FakePC(PC_MODELS))
    assert await on.resolve_model("qwen3:14b@pc", ["qwen2.5:7b"]) == ("qwen3:14b@pc", None)


async def test_adding_a_host_checks_that_ollama_answers() -> None:
    r = make_router(FakePC(PC_MODELS, online=False))
    with pytest.raises(OllamaError) as err:
        await r.set_host("desk", "http://192.168.1.60:11434")
    assert "No Ollama answered" in err.value.message and "OLLAMA_HOST" in err.value.hint
    assert "desk" not in r.hosts
    with pytest.raises(OllamaError):
        await r.set_host("Bad Name", "http://x:11434")


def test_settings_read_hosts_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_OLLAMA_HOSTS", '{"pc": "http://192.168.1.50:11434"}')
    assert Settings(_env_file=None).ollama_hosts == {"pc": "http://192.168.1.50:11434"}


def test_api_adds_switches_to_and_removes_a_pc(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import api

    pc = FakePC(PC_MODELS)
    router = make_router(pc)
    router.hosts.clear(), router.host_urls.clear()  # start with no computers set up
    app = api.create_app(Settings(), llm=router, db_path=":memory:")
    env = tmp_path / ".env"
    app.state.env_file = env
    with TestClient(app) as client:
        assert client.put("/api/hosts/pc", json={"url": "rm -rf /"}).status_code == 422
        added = client.put("/api/hosts/pc", json={"url": "192.168.1.50"})
        assert added.status_code == 200 and added.json()["online"]
        assert added.json()["url"] == "http://192.168.1.50:11434"  # scheme and port filled in
        assert json.loads(env.read_text().split("=", 1)[1]) == {"pc": "http://192.168.1.50:11434"}
        models = client.get("/api/models").json()
        assert [m["id"] for m in models["hosts"][0]["models"]] == ["qwen3.5:4b@pc", "qwen3:14b@pc"]
        assert client.post("/api/models", json={"model": "llama3:70b@pc"}).status_code == 404
        assert client.post("/api/models", json={"model": "qwen3:14b@pc"}).status_code == 200
        health = client.get("/api/health").json()
        assert health["ok"] and health["host"] == "pc" and not health["cloud"]
        assert client.delete("/api/hosts/pc").status_code == 200
        assert "AETHER_OLLAMA_HOSTS" not in env.read_text()
        assert client.get("/api/health").json()["model"] != "qwen3:14b@pc"


@pytest.mark.parametrize("raw,expected", [
    ("192.168.1.9", "http://192.168.1.9:11434"),
    (" http://192.168.1.9:11434/ ", "http://192.168.1.9:11434"),
    ("my-pc.local", "http://my-pc.local:11434"),
    ("100.64.0.7:8080", "http://100.64.0.7:8080"),
    ("192.168.1.9192.168.1.9192.168.1.9", None),   # pasted three times
    ("192.168.1.300", None),
    ("192.168.1.9 192.168.1.9", None),
    ("http://pc:abc", None),
    ("ftp://192.168.1.9", None),
])
def test_host_addresses_are_validated(raw: str, expected: str | None) -> None:
    import api
    assert api.normalize_host_url(raw) == expected


# ------------------------------------------------- away from home: Tailscale
class Network:
    """Several fake computers by address, e.g. the PC's home IP and its Tailscale IP."""

    def __init__(self, **machines: FakePC):
        self.machines = {k.replace("_", "."): v for k, v in machines.items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        pc = self.machines.get(request.url.host)
        if pc is None:
            raise httpx.ConnectError("no route", request=request)
        return pc(request)


HOME, TS = "http://192.168.1.9:11434", "http://100.101.102.103:11434"


def two_address_router(home: FakePC, remote: FakePC) -> ModelRouter:
    net = Network(**{"192_168_1_9": home, "100_101_102_103": remote})
    mac = FakePC({"qwen2.5:7b": ["completion", "tools"]})
    return ModelRouter(OllamaClient("http://mac.test", transport=httpx.MockTransport(mac)),
                       hosts={"pc": [HOME, TS]}, host_transport=httpx.MockTransport(net))


@pytest.mark.parametrize("url,kind", [
    ("http://192.168.1.9:11434", "home"), ("http://10.0.0.5:11434", "home"), ("http://my-pc.local:11434", "home"),
    ("http://100.101.102.103:11434", "tailscale"), ("http://desktop.tail1234.ts.net:11434", "tailscale"),
    ("http://8.8.8.8:11434", "internet"),
])
def test_address_kinds(url: str, kind: str) -> None:
    from aether.llm.router import address_kind
    assert address_kind(url) == kind


async def test_away_from_home_it_uses_tailscale_and_switches_back_at_home() -> None:
    home, remote = FakePC(PC_MODELS, online=False), FakePC(PC_MODELS)   # at college
    r = two_address_router(home, remote)
    out = [d async for d in r.chat_stream(model="qwen3:14b@pc", messages=[{"role": "user", "content": "hi"}])]
    assert out[0].content == "Hi from the GPU." and len(remote.chats) == 1 and not home.chats
    assert r.host_urls["pc"] == TS
    cat = (await r.hosts_catalog())[0]
    assert cat["route"] == "tailscale" and [a["active"] for a in cat["addresses"]] == [False, True]

    home.online = True                                                   # back home
    assert await r.ensure_route("pc", force=True)
    assert r.host_urls["pc"] == HOME                                     # home is preferred
    [d async for d in r.chat_stream(model="qwen3:14b@pc", messages=[{"role": "user", "content": "hi"}])]
    assert len(home.chats) == 1


async def test_nothing_answers_falls_back_to_the_mac_with_a_reason() -> None:
    r = two_address_router(FakePC(PC_MODELS, online=False), FakePC(PC_MODELS, online=False))
    model, note = await r.resolve_model("qwen3:14b@pc", ["qwen2.5:7b"])
    assert model == "qwen3:14b@pc" and "isn't reachable" in (note or "")      # stays your brain…
    local, why = await r.failover(model, ["qwen2.5:7b"])
    assert local == "qwen2.5:7b" and "this Mac" in (why or "")                  # …each answer falls back


async def test_a_working_route_is_trusted_for_a_while() -> None:
    home, remote = FakePC(PC_MODELS), FakePC(PC_MODELS)
    r = two_address_router(home, remote)
    assert await r.ensure_route("pc")
    home.online = False                      # changes within the trust window aren't re-checked...
    assert await r.ensure_route("pc") and r.host_urls["pc"] == HOME
    assert await r.ensure_route("pc", force=True) and r.host_urls["pc"] == TS  # ...but forced ones are


def test_settings_read_a_list_of_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_OLLAMA_HOSTS", json.dumps({"pc": [HOME, TS]}))
    assert Settings(_env_file=None).ollama_hosts == {"pc": [HOME, TS]}


def test_api_saves_a_home_and_a_remote_address(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import api

    home, remote = FakePC(PC_MODELS), FakePC(PC_MODELS, online=False)   # Tailscale off on the Mac for now
    r = two_address_router(home, remote)
    r.host_addrs.clear(), r.hosts.clear(), r.host_urls.clear()
    app = api.create_app(Settings(), llm=r, db_path=":memory:")
    env = tmp_path / ".env"
    app.state.env_file = env
    with TestClient(app) as client:
        bad = client.put("/api/hosts/pc", json={"url": "192.168.1.9", "remote_url": "not an address"})
        assert bad.status_code == 422 and "remote address" in bad.json()["detail"]["message"]
        ok = client.put("/api/hosts/pc", json={"url": "192.168.1.9", "remote_url": "100.101.102.103"})
        body = ok.json()
        assert ok.status_code == 200 and body["route"] == "home"
        assert body["unreachable"] == [TS]                              # saved anyway, but flagged
        assert [a["kind"] for a in body["addresses"]] == ["home", "tailscale"]
        assert json.loads(env.read_text().split("=", 1)[1]) == {"pc": [HOME, TS]}
        single = client.put("/api/hosts/pc", json={"url": "192.168.1.9", "remote_url": ""})
        assert single.status_code == 200
        assert json.loads(env.read_text().split("=", 1)[1]) == {"pc": HOME}
