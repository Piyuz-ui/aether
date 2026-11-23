"""FastAPI backend: SSE chat, approvals over HTTP, sessions, memory, tasks, knowledge."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import api
from aether.tools.context import workspace

from .conftest import FakeLLM, call, make_pdf, say


@pytest.fixture
def make_client(settings, tmp_path):
    clients = []

    def factory(replies=None):
        s = settings.model_copy(update={"data_dir": tmp_path / "data", "auto_memory": False})
        app = api.create_app(s, llm=FakeLLM(replies or []), db_path=":memory:")
        client = TestClient(app)
        client.__enter__()  # runs the lifespan (startup)
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def sse_events(response):
    """Parse a text/event-stream into (event, data) pairs."""
    event, data = None, []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
        elif line == "" and event:
            yield event, json.loads("\n".join(data))
            event, data = None, []


def test_health_models_tools(make_client):
    c = make_client()
    h = c.get("/api/health").json()
    assert h["ok"] and h["model"] == "fake-model" and h["tools"] >= 16
    assert c.get("/api/models").json()["models"] == ["fake-model", "other-model"]
    assert c.post("/api/models", json={"model": "other-model"}).json()["current"] == "other-model"
    assert c.post("/api/models", json={"model": "missing"}).status_code == 404
    tools = {t["name"]: t for t in c.get("/api/tools").json()}
    assert tools["delete_file"]["risk"] == "requires_approval"
    assert tools["write_note"]["risk"] == "conditional"


def test_chat_streams_events_and_persists_session(make_client):
    c = make_client([call("python_calculator", expression="6*7"), say("It's **42**.")])
    with c.stream("POST", "/api/chat", json={"message": "what is 6 times 7?"}) as r:
        assert r.status_code == 200
        events = list(sse_events(r))
    names = [e for e, _ in events]
    assert names[0] == "session"
    assert {"status", "tool_start", "tool_result", "token", "done"} <= set(names)
    done = next(d for e, d in events if e == "done")["data"]
    assert done["answer"] == "It's **42**." and done["stats"]["model_calls"] == 2
    session_id = events[0][1]["session_id"]

    detail = c.get(f"/api/sessions/{session_id}").json()
    assert detail["title"] == "what is 6 times 7?"
    user, assistant = detail["messages"]
    assert user == {"role": "user", "content": "what is 6 times 7?"}
    assert assistant["steps"][0]["name"] == "python_calculator"
    assert "42" in assistant["steps"][0]["output"] and assistant["steps"][0]["ok"]

    assert c.patch(f"/api/sessions/{session_id}", json={"title": "Maths"}).status_code == 200
    assert c.get("/api/sessions").json()[0]["title"] == "Maths"
    assert c.delete(f"/api/sessions/{session_id}").status_code == 200
    assert c.get(f"/api/sessions/{session_id}").status_code == 404


@pytest.mark.parametrize("approve", [True, False])
def test_approval_over_http_pauses_and_resumes(make_client, approve):
    target = workspace().resolve("notes/draft.md")
    target.write_text("draft")
    c = make_client([call("delete_file", path="notes/draft.md"),
                     say("Deleted it." if approve else "Left it alone.")])
    seen = []
    with c.stream("POST", "/api/chat", json={"message": "delete notes/draft.md"}) as r:
        for event, data in sse_events(r):
            seen.append(event)
            if event == "approval_required":
                approval_id = data["data"]["approval_id"]
                assert c.get("/api/approvals").json()[0]["approval_id"] == approval_id
                resp = c.post(f"/api/approvals/{approval_id}", json={"approved": approve})
                assert resp.status_code == 200
    assert seen.index("approval_required") < seen.index("approval_resolved") < seen.index("done")
    assert target.exists() is not approve
    assert c.post("/api/approvals/nope", json={"approved": True}).status_code == 404


def test_memory_inspector_crud(make_client):
    c = make_client()
    created = c.post("/api/memory/facts", json={"text": "The user loves chess.",
                                                "category": "preference"}).json()
    assert created["created"]
    fid = created["id"]
    assert c.patch(f"/api/memory/facts/{fid}", json={"text": "The user loves chess and Go."}
                   ).json()["text"] == "The user loves chess and Go."
    assert [f["id"] for f in c.get("/api/memory/facts").json()] == [fid]
    assert c.delete(f"/api/memory/facts/{fid}").status_code == 200
    assert c.get("/api/memory/facts").json() == []
    assert c.delete(f"/api/memory/facts/{fid}").status_code == 404


def test_tasks_and_timetable_endpoints(make_client):
    c = make_client()
    due = (date.today() + timedelta(days=3)).isoformat()
    t = c.post("/api/tasks", json={"title": "Physics lab", "kind": "deadline", "due": due}).json()
    assert t["when"].startswith("due")
    assert c.post("/api/tasks", json={"title": "x", "due": "someday"}).status_code == 422
    assert c.patch(f"/api/tasks/{t['id']}", json={"done": True}).json()["done"] is True
    assert c.get("/api/tasks").json() == []
    assert len(c.get("/api/tasks", params={"include_done": True}).json()) == 1
    cls = c.post("/api/timetable", json={"course": "DBMS", "day": "Mon", "start": "9am",
                                         "end": "10am", "location": "204"}).json()
    assert cls["day"] == "Monday" and cls["start"] == "09:00"
    assert c.delete(f"/api/timetable/{cls['id']}").status_code == 200


def test_knowledge_upload_indexes_in_background(make_client, tmp_path):
    c = make_client()
    pdf = make_pdf(tmp_path / "ai.pdf", ["Gradient descent minimises a loss function step by step."])
    with pdf.open("rb") as fh:
        resp = c.post("/api/knowledge", files=[("files", ("ai.pdf", fh, "application/pdf"))])
    assert resp.status_code == 202
    doc_id = resp.json()[0]["id"]
    for _ in range(100):  # poll like the UI does
        doc = next(d for d in c.get("/api/knowledge").json() if d["id"] == doc_id)
        if doc["status"] in {"ready", "error"}:
            break
        time.sleep(0.02)
    assert doc["status"] == "ready" and doc["chunks"] == 1
    hits = c.get("/api/knowledge/search", params={"q": "what does gradient descent do"}).json()
    assert hits[0]["filename"] == "ai.pdf" and hits[0]["page"] == 1

    with pdf.open("rb") as fh:
        dup = c.post("/api/knowledge", files=[("files", ("ai.pdf", fh, "application/pdf"))]).json()
    assert dup[0]["duplicate"]
    bad = c.post("/api/knowledge", files=[("files", ("virus.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 422
    assert c.delete(f"/api/knowledge/{doc_id}").status_code == 200
    assert c.get("/api/knowledge").json() == []


def test_ollama_errors_reach_the_stream_with_hints(make_client):
    from aether.llm.ollama import OllamaError
    c = make_client([OllamaError("Ollama closed the connection mid-reply.", hint="Restart it.")])
    with c.stream("POST", "/api/chat", json={"message": "hi"}) as r:
        events = list(sse_events(r))
    err = next(d for e, d in events if e == "error")["data"]
    assert err["hint"] == "Restart it."
