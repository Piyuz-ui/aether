"""Tool router, the Python sandbox (real runs), analyze_csv, and chart artifacts end to end."""
from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api
from aether.engine import AetherAgent
from aether.engine.agent import artifacts_in
from aether.engine.session import Session
from aether.events import EventType
from aether.tools import REGISTRY, ToolError
from aether.tools import code_data
from aether.tools.context import workspace
from aether.tools.registry import ToolRegistry
from tool_router import CATALOG, Level, meta_for, route

from .conftest import FakeLLM, call, say

ALL = REGISTRY.all()


# ------------------------------------------------------------------ router
@pytest.mark.parametrize("prompt,must,not_in", [
    ("plot a bar chart of my marks in maths 78 physics 85 chemistry 91", {"python_sandbox"}, {"run_shell_command"}),
    ("analyse uploads/sales.csv and tell me the trends", {"analyze_csv", "python_sandbox"}, set()),
    ("I have a DBMS exam on friday, add it", {"add_task"}, {"python_sandbox"}),
    ("what does my lecture pdf say about normalisation?", {"search_knowledge_base"}, set()),
    ("read https://example.com/article and summarise it", {"read_webpage"}, set()),
    ("quiz me on operating systems", {"make_quiz"}, set()),
])
def test_router_picks_the_right_tools(prompt: str, must: set[str], not_in: set[str]) -> None:
    picked = route(prompt, ALL)
    assert 6 <= len(picked) <= 8
    assert must <= set(picked), picked
    assert not (not_in & set(picked)), picked


def test_router_small_talk_gets_sensible_defaults_and_follow_ups_keep_tools() -> None:
    picked = route("hi, how are you?", ALL)
    assert {"search_knowledge_base", "recall_memory", "web_search"} <= set(picked) and len(picked) == 6
    follow_up = route("now make it a line chart instead", ALL, recent={"python_sandbox"})
    assert follow_up[0] == "python_sandbox"


def test_every_builtin_tool_is_catalogued_with_a_level() -> None:
    for spec in ALL:
        if spec.category == "mcp":
            continue
        assert spec.name in CATALOG, spec.name
        m = meta_for(spec.name, spec)
        assert m.pack in {"core", "research", "code_data"} and m.tags
        if spec.risk.value == "requires_approval" and spec.risk_check is None:
            assert m.level is Level.HIGH_RISK or m.level is Level.WRITE, spec.name
    assert CATALOG["python_sandbox"].level is Level.HIGH_RISK and CATALOG["analyze_csv"].level is Level.SAFE


async def test_local_models_get_routed_tools_cloud_models_get_all() -> None:
    llm = FakeLLM([say("hi")])
    agent = AetherAgent(llm=llm)
    events = [e async for e in agent.run("plot a chart of 1 2 3", Session())]
    offered = next(e.data["tools"] for e in events if e.type is EventType.STATUS and "tools" in e.data)
    assert "python_sandbox" in offered and len(offered) <= 8
    assert len(llm.requests[0]["tools"]) == len(offered)

    class Cloud(FakeLLM):
        def is_cloud(self, model: str) -> bool:
            return True
    cloud = Cloud([say("hi")])
    [e async for e in AetherAgent(llm=cloud).run("plot a chart", Session())]
    assert len(cloud.requests[0]["tools"]) == len(REGISTRY.names)


# ----------------------------------------------------------------- sandbox
async def test_sandbox_runs_numpy_pandas_and_saves_unsaved_charts() -> None:
    code = ("import numpy as np, pandas as pd, matplotlib.pyplot as plt\n"
            "df = pd.DataFrame({'x': np.arange(5), 'y': np.arange(5) ** 2})\n"
            "print(df['y'].sum())\n"
            "plt.plot(df.x, df.y)\n")                      # drawn but never saved
    out = await code_data.python_sandbox.__wrapped__(code) if hasattr(code_data.python_sandbox, "__wrapped__") \
        else await code_data.python_sandbox(code)
    assert out.startswith("30")
    arts = artifacts_in(out)
    assert len(arts) == 1 and arts[0]["url"].startswith("/artifacts/chart-") and arts[0]["url"].endswith(".png")
    png = workspace().root / arts[0]["path"]
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


async def test_sandbox_keeps_charts_you_save_yourself_and_has_no_secrets() -> None:
    code = ("import os, matplotlib.pyplot as plt\n"
            "plt.bar(['a', 'b'], [1, 2]); plt.savefig(f'{ARTIFACTS}/marks.png')\n"
            "print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))\n")
    out = await code_data.python_sandbox(code)
    assert "[]" in out                                         # no API keys or tokens inside
    assert [a["name"] for a in artifacts_in(out)] == ["marks.png"]


async def test_sandbox_errors_and_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ToolError, match="exit 1"):
        await code_data.python_sandbox("raise ValueError('boom')")
    with pytest.raises(ToolError, match="numpy, pandas and matplotlib"):
        await code_data.python_sandbox("import sklearn")
    monkeypatch.setattr(code_data, "SANDBOX_TIMEOUT_S", 2)
    started = time.monotonic()
    with pytest.raises(ToolError, match="longer than 2 s"):
        await code_data.python_sandbox("while True: pass")
    assert time.monotonic() - started < 8


# ------------------------------------------------------------- analyze_csv
async def test_analyze_csv_and_excel() -> None:
    import pandas as pd
    up = workspace().root / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"subject": ["Maths", "Physics", "Maths", None], "marks": [78, 85, 91, 60]})
    df.to_csv(up / "marks.csv", index=False)
    out = await code_data.analyze_csv("uploads/marks.csv")
    assert "4 rows × 2 columns" in out and "| marks | int64 | 4 | 0 | 4 |" in out
    assert "Top values · subject" in out and "Maths (2)" in out and "78.5" in out   # median
    df.to_excel(up / "marks.xlsx", index=False, sheet_name="Term 1")
    assert "4 rows × 2 columns" in await code_data.analyze_csv("uploads/marks.xlsx")
    with pytest.raises(Exception):
        await code_data.analyze_csv("uploads/nope.csv")
    (up / "x.xls").write_bytes(b"old")
    with pytest.raises(ToolError, match="xlsx"):
        await code_data.analyze_csv("uploads/x.xls")


# --------------------------------------------- artifacts through the stack
def test_artifacts_parse_only_safe_paths() -> None:
    out = "done\n[artifact] artifacts/a.png\n[artifact] ../secret.png\n[artifact] artifacts/../x.png"
    assert artifacts_in(out) == [{"path": "artifacts/a.png", "name": "a.png", "url": "/artifacts/a.png",
                                  "download": "/api/files/artifacts/a.png?download=true"}]


async def test_tool_result_event_carries_chart_urls() -> None:
    reg = ToolRegistry()

    @reg.tool(description="Draw a test chart.")
    async def draw() -> str:
        return "ok\n[artifact] artifacts/test.png"

    agent = AetherAgent(llm=FakeLLM([call("draw"), say("Here it is.")]), registry=reg)
    events = [e async for e in agent.run("draw", Session())]
    result = next(e for e in events if e.type is EventType.TOOL_RESULT)
    assert result.data["artifacts"][0]["url"] == "/artifacts/test.png"


def test_api_serves_artifacts_and_takes_data_uploads(settings: Any, tmp_path: Any) -> None:
    s = settings.model_copy(update={"data_dir": tmp_path / "data", "auto_memory": False})
    (workspace().root / "artifacts").mkdir(parents=True, exist_ok=True)
    (workspace().root / "artifacts" / "served.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    with TestClient(api.create_app(s, llm=FakeLLM([]), db_path=":memory:")) as c:
        r = c.get("/artifacts/served.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert c.get("/artifacts/../aether.db").status_code == 404
        d = c.get("/api/files/artifacts/served.png?download=true")
        assert "attachment" in d.headers["content-disposition"]
        up = c.post("/api/workspace/upload", files=[("files", ("my sales.csv", b"a,b\n1,2\n", "text/csv"))])
        assert up.status_code == 201 and up.json()[0]["path"] == "uploads/my_sales.csv"
        again = c.post("/api/workspace/upload", files=[("files", ("my sales.csv", b"a,b\n", "text/csv"))])
        assert again.json()[0]["path"] == "uploads/my_sales-1.csv"
        assert c.post("/api/workspace/upload", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))]).status_code == 422
        routed = c.get("/api/tools/route", params={"q": "chart my csv"}).json()["tools"]
        assert "python_sandbox" in routed
        tools = {t["name"]: t for t in c.get("/api/tools").json()}
        assert tools["python_sandbox"]["level"] == "HIGH_RISK" and tools["analyze_csv"]["pack"] == "code_data"


_ = json
