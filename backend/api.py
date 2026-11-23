"""AETHER web API (FastAPI).

    python api.py                     → http://127.0.0.1:8000 (interactive docs at /docs)

Chat streams every agent event over Server-Sent Events. When a risky tool needs approval,
the stream sends `approval_required` and the loop waits until the UI calls
POST /api/approvals/{approval_id}.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from aether import __version__
from aether.config import Settings, get_settings
from aether.engine import AetherAgent
from aether.llm.ollama import OllamaClient, OllamaError
from aether.memory import MemorySystem
from aether.memory.knowledge import KnowledgeError
from aether.memory.taskbook import TaskError
from aether.tools import REGISTRY
from aether.tools.context import set_memory, workspace
from aether.workspace import WorkspaceError


# ----------------------------------------------------------------- app state
@dataclass
class AppContext:
    settings: Settings
    llm: Any
    memory: MemorySystem
    agent: AetherAgent
    busy_sessions: set[str] = field(default_factory=set)
    background: set[asyncio.Task[Any]] = field(default_factory=set)

    def spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)


def build_context(settings: Settings, llm: Any = None, db_path: str | None = None) -> AppContext:
    llm = llm or OllamaClient(settings.ollama_url, timeout_s=settings.request_timeout_s)
    memory = MemorySystem(settings, llm, db_path=db_path)
    set_memory(memory)

    async def learn(goal: str, answer: str, model: str) -> list[dict[str, Any]]:
        if not settings.auto_memory:
            return []
        return await memory.extract_facts(llm, model, goal, answer)

    agent = AetherAgent(settings, llm=llm, memory_provider=memory.build_context, after_turn=learn)
    return AppContext(settings=settings, llm=llm, memory=memory, agent=agent)


# ------------------------------------------------------------------- schemas
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    session_id: str | None = None


class ApprovalRequestBody(BaseModel):
    approved: bool
    reason: str = ""


class ModelChoice(BaseModel):
    model: str


class SessionPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class FactIn(BaseModel):
    text: str = Field(min_length=3, max_length=500)
    category: Literal["profile", "preference", "goal", "academic", "general"] = "general"


class FactPatch(BaseModel):
    text: str | None = Field(default=None, min_length=3, max_length=500)
    category: Literal["profile", "preference", "goal", "academic", "general"] | None = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["task", "deadline", "exam", "reminder"] = "task"
    due: str = ""
    notes: str = ""


class TaskPatch(BaseModel):
    title: str | None = None
    kind: Literal["task", "deadline", "exam", "reminder"] | None = None
    due: str | None = None
    notes: str | None = None
    done: bool | None = None


class ClassIn(BaseModel):
    course: str = Field(min_length=1, max_length=200)
    day: str
    start: str
    end: str
    location: str = ""


# ---------------------------------------------------------------------- app
def create_app(settings: Settings | None = None, *, llm: Any = None,
               db_path: str | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = build_context(settings, llm, db_path)
        app.state.ctx = ctx
        # Resume indexing of any documents interrupted by a previous shutdown.
        for d in ctx.memory.knowledge.documents():
            if d["status"] in {"queued", "reading", "embedding"}:
                ctx.spawn(ctx.memory.knowledge.index(d["id"]))
        yield
        for task in list(ctx.background):
            task.cancel()
        with contextlib.suppress(Exception):
            await ctx.llm.aclose()
        ctx.memory.close()
        set_memory(None)

    app = FastAPI(title="AETHER API", version=__version__, lifespan=lifespan,
                  description="Local-first autonomous agent: chat (SSE), approvals, memory, "
                              "tasks, timetable and knowledge vault.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    def ctx_of(request: Request) -> AppContext:
        return request.app.state.ctx

    # ------------------------------------------------------------ system
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """People often open the API address in a browser; point them to the right places."""
        return {"name": "AETHER API", "status": "running",
                "app": "Open the web app at http://localhost:3000",
                "docs": f"Interactive API docs at http://{settings.api_host}:{settings.api_port}/docs"}

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        ollama_up = await ctx.llm.health() if hasattr(ctx.llm, "health") else True
        model, note, error = ctx.agent.model, ctx.agent.model_note, None
        if ollama_up and model is None:
            try:
                model = await ctx.agent.ensure_model()
                note = ctx.agent.model_note
            except OllamaError as e:
                error = {"message": e.message, "hint": e.hint}
        if not ollama_up:
            error = {"message": f"Can't reach Ollama at {ctx.settings.ollama_url}.",
                     "hint": "Open the Ollama app, or run `ollama serve`."}
        emb = ctx.memory.embedder
        return {"ok": ollama_up and error is None, "version": __version__, "ollama": ollama_up,
                "model": model, "model_note": note, "error": error,
                "embed_model": ctx.settings.embed_model, "embeddings_available": emb.available,
                "embeddings_hint": emb.last_error.hint if emb.last_error else None,
                "tools": len(REGISTRY.all()), "context_length": ctx.settings.context_length}

    @app.get("/api/models")
    async def models(request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        try:
            installed = await ctx.llm.list_models()
        except OllamaError as e:
            raise HTTPException(503, detail={"message": e.message, "hint": e.hint}) from e
        return {"models": installed, "current": ctx.agent.model or ctx.settings.model}

    @app.post("/api/models")
    async def choose_model(body: ModelChoice, request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        installed = await ctx.llm.list_models()
        if body.model not in installed:
            raise HTTPException(404, detail={"message": f"'{body.model}' isn't installed.",
                                             "hint": f"Run: ollama pull {body.model}"})
        ctx.agent.set_model(body.model)
        return {"current": body.model}

    @app.get("/api/tools")
    async def tools() -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "category": t.category,
                 "icon": t.icon, "risk": "conditional" if t.risk_check else t.risk.value}
                for t in REGISTRY.all()]

    # -------------------------------------------------------------- chat
    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request) -> EventSourceResponse:
        ctx = ctx_of(request)
        session = ctx.memory.session(body.session_id)
        if session.id in ctx.busy_sessions:
            raise HTTPException(409, detail={"message": "This chat is still answering.",
                                             "hint": "Wait for it to finish, or open a new chat."})
        ctx.busy_sessions.add(session.id)

        async def stream() -> AsyncIterator[dict[str, str]]:
            seq = 0
            try:
                yield {"event": "session", "id": "0",
                       "data": json.dumps({"session_id": session.id})}
                async with contextlib.aclosing(ctx.agent.run(body.message, session)) as events:
                    async for event in events:
                        seq += 1
                        yield {"event": event.type.value, "id": str(seq), "data": event.to_json()}
            finally:
                # Also runs when the browser disconnects: the agent run is closed and any
                # pending approval is denied by the engine's own cleanup.
                ctx.busy_sessions.discard(session.id)

        return EventSourceResponse(stream(), ping=15)

    @app.get("/api/approvals")
    async def pending_approvals(request: Request) -> list[dict[str, Any]]:
        return [r.to_dict() for r in ctx_of(request).agent.broker.pending()]

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: str, body: ApprovalRequestBody, request: Request) -> dict[str, Any]:
        if not ctx_of(request).agent.broker.resolve(approval_id, body.approved, body.reason):
            raise HTTPException(404, detail={"message": "That approval request has expired or "
                                                        "was already answered."})
        return {"approval_id": approval_id, "approved": body.approved}

    # ---------------------------------------------------------- sessions
    @app.get("/api/sessions")
    async def sessions(request: Request) -> list[dict[str, Any]]:
        return ctx_of(request).memory.store.list_sessions()

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str, request: Request) -> dict[str, Any]:
        store = ctx_of(request).memory.store
        meta = store.one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not meta:
            raise HTTPException(404, detail={"message": "No such chat."})
        return {**meta, "messages": transcript(store.load_turns(session_id))}

    @app.patch("/api/sessions/{session_id}")
    async def rename(session_id: str, body: SessionPatch, request: Request) -> dict[str, Any]:
        if not ctx_of(request).memory.store.rename_session(session_id, body.title):
            raise HTTPException(404, detail={"message": "No such chat."})
        return {"id": session_id, "title": body.title}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
        if not ctx_of(request).memory.store.delete_session(session_id):
            raise HTTPException(404, detail={"message": "No such chat."})
        return {"deleted": session_id}

    # ------------------------------------------------- memory inspector
    @app.get("/api/memory/facts")
    async def facts(request: Request) -> list[dict[str, Any]]:
        return ctx_of(request).memory.facts.all()

    @app.post("/api/memory/facts", status_code=201)
    async def add_fact(body: FactIn, request: Request) -> dict[str, Any]:
        fact, created = await ctx_of(request).memory.facts.add(body.text, body.category, "user")
        return {**fact, "created": created}

    @app.patch("/api/memory/facts/{fact_id}")
    async def edit_fact(fact_id: int, body: FactPatch, request: Request) -> dict[str, Any]:
        fact = await ctx_of(request).memory.facts.update(fact_id, body.text, body.category)
        if not fact:
            raise HTTPException(404, detail={"message": "No such memory."})
        return fact

    @app.delete("/api/memory/facts/{fact_id}")
    async def delete_fact(fact_id: int, request: Request) -> dict[str, Any]:
        if not ctx_of(request).memory.facts.delete(fact_id):
            raise HTTPException(404, detail={"message": "No such memory."})
        return {"deleted": fact_id}

    # ------------------------------------------------------------- tasks
    @app.get("/api/tasks")
    async def tasks(request: Request, include_done: bool = False) -> list[dict[str, Any]]:
        return ctx_of(request).memory.tasks.list(include_done=include_done)

    @app.post("/api/tasks", status_code=201)
    async def add_task(body: TaskIn, request: Request) -> dict[str, Any]:
        try:
            return ctx_of(request).memory.tasks.add(body.title, body.kind, body.due, body.notes)
        except TaskError as e:
            raise HTTPException(422, detail={"message": str(e)}) from e

    @app.patch("/api/tasks/{task_id}")
    async def edit_task(task_id: int, body: TaskPatch, request: Request) -> dict[str, Any]:
        try:
            task = ctx_of(request).memory.tasks.update(task_id, **body.model_dump(exclude_unset=True))
        except TaskError as e:
            raise HTTPException(422, detail={"message": str(e)}) from e
        if not task:
            raise HTTPException(404, detail={"message": "No such task."})
        return task

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: int, request: Request) -> dict[str, Any]:
        if not ctx_of(request).memory.tasks.delete(task_id):
            raise HTTPException(404, detail={"message": "No such task."})
        return {"deleted": task_id}

    @app.get("/api/timetable")
    async def timetable(request: Request) -> list[dict[str, Any]]:
        return ctx_of(request).memory.tasks.classes()

    @app.post("/api/timetable", status_code=201)
    async def add_class(body: ClassIn, request: Request) -> dict[str, Any]:
        try:
            return ctx_of(request).memory.tasks.add_class(body.course, body.day, body.start,
                                                          body.end, body.location)
        except TaskError as e:
            raise HTTPException(422, detail={"message": str(e)}) from e

    @app.delete("/api/timetable/{class_id}")
    async def delete_class(class_id: int, request: Request) -> dict[str, Any]:
        if not ctx_of(request).memory.tasks.delete_class(class_id):
            raise HTTPException(404, detail={"message": "No such class."})
        return {"deleted": class_id}

    @app.get("/api/files/{path:path}")
    async def workspace_file(path: str, download: bool = False) -> FileResponse:
        """Serve a file the agent created (PDF, note…). Only inside the workspace."""
        try:
            target = workspace().resolve(path, must_exist=True)
        except WorkspaceError as e:
            raise HTTPException(404, detail={"message": str(e)}) from e
        if not target.is_file():
            raise HTTPException(404, detail={"message": "That's a folder, not a file."})
        return FileResponse(target, filename=target.name,
                            content_disposition_type="attachment" if download else "inline")

    @app.get("/api/calendar.ics")
    async def calendar(request: Request) -> Response:
        """Deadlines and weekly classes as an iCalendar file (Apple/Google Calendar import)."""
        mem = ctx_of(request).memory
        body = to_ics(mem.tasks.list(), mem.tasks.classes())
        return Response(body, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="aether-deadlines.ics"'})

    # ---------------------------------------------------- knowledge vault
    @app.get("/api/knowledge")
    async def knowledge(request: Request) -> list[dict[str, Any]]:
        return ctx_of(request).memory.knowledge.documents()

    @app.post("/api/knowledge", status_code=202)
    async def upload(request: Request, files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
        """Store the files and index them in the background. Poll GET /api/knowledge for progress."""
        ctx = ctx_of(request)
        limit = ctx.settings.max_upload_mb * 1024 * 1024
        out = []
        tmp_dir = ctx.settings.data_dir / "uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            name = Path(f.filename or "upload").name
            data = await f.read(limit + 1)
            if len(data) > limit:
                raise HTTPException(413, detail={"message": f"{name} is over {ctx.settings.max_upload_mb} MB."})
            with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False, suffix=Path(name).suffix) as tmp:
                tmp.write(data)
            try:
                doc, is_new = ctx.memory.knowledge.register(Path(tmp.name), filename=name)
            except KnowledgeError as e:
                raise HTTPException(422, detail={"message": str(e)}) from e
            finally:
                Path(tmp.name).unlink(missing_ok=True)
            if is_new or doc["status"] == "error":
                ctx.spawn(ctx.memory.knowledge.index(doc["id"]))
            out.append({**doc, "duplicate": not is_new})
        return out

    @app.post("/api/knowledge/{doc_id}/reindex", status_code=202)
    async def reindex(doc_id: int, request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        if not ctx.memory.knowledge.document(doc_id):
            raise HTTPException(404, detail={"message": "No such document."})
        ctx.memory.embedder.reset()
        ctx.spawn(ctx.memory.knowledge.index(doc_id))
        return {"reindexing": doc_id}

    @app.delete("/api/knowledge/{doc_id}")
    async def delete_doc(doc_id: int, request: Request) -> dict[str, Any]:
        if not ctx_of(request).memory.knowledge.delete(doc_id):
            raise HTTPException(404, detail={"message": "No such document."})
        return {"deleted": doc_id}

    @app.get("/api/knowledge/search")
    async def search(request: Request, q: str, k: int = 5, document: str = "") -> list[dict[str, Any]]:
        try:
            hits = await ctx_of(request).memory.knowledge.search(q, k=max(1, min(k, 20)),
                                                                 document=document)
        except KnowledgeError as e:
            raise HTTPException(404, detail={"message": str(e)}) from e
        return [{"filename": h.filename, "page": h.page, "text": h.text, "score": h.score,
                 "semantic": h.semantic, "doc_id": h.doc_id} for h in hits]

    return app


def _ics_text(value: str) -> str:
    return (value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\n", "\\n"))


def to_ics(tasks: list[dict[str, Any]], classes: list[dict[str, Any]]) -> str:
    """Build an RFC 5545 calendar: dated tasks become events with alarms, and classes
    become weekly repeating events starting from their next occurrence."""
    from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

    stamp = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AETHER//Local agent//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:AETHER"]
    for t in tasks:
        if not t.get("due"):
            continue
        summary = f"[{t['kind'].capitalize()}] {t['title']}"
        lines += ["BEGIN:VEVENT", f"UID:aether-task-{t['id']}@aether.local", f"DTSTAMP:{stamp}",
                  f"SUMMARY:{_ics_text(summary)}"]
        if "T" in t["due"]:
            start = _dt.fromisoformat(t["due"])
            lines += [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{start + _td(hours=1):%Y%m%dT%H%M%S}"]
            trigger = "-PT1H"
        else:
            day = _date.fromisoformat(t["due"])
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + _td(days=1):%Y%m%d}"]
            trigger = "-P1D"
        if t.get("notes"):
            lines.append(f"DESCRIPTION:{_ics_text(t['notes'])}")
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_text(summary)}",
                  f"TRIGGER:{trigger}", "END:VALARM", "END:VEVENT"]
    byday = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    today = _date.today()
    for c in classes:
        first = today + _td(days=(c["weekday"] - today.weekday()) % 7)
        sh, sm = map(int, c["start"].split(":"))
        eh, em = map(int, c["end"].split(":"))
        lines += ["BEGIN:VEVENT", f"UID:aether-class-{c['id']}@aether.local", f"DTSTAMP:{stamp}",
                  f"SUMMARY:{_ics_text(c['course'])}",
                  f"DTSTART:{first:%Y%m%d}T{sh:02d}{sm:02d}00", f"DTEND:{first:%Y%m%d}T{eh:02d}{em:02d}00",
                  f"RRULE:FREQ=WEEKLY;BYDAY={byday[c['weekday']]}"]
        if c.get("location"):
            lines.append(f"LOCATION:{_ics_text(c['location'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def transcript(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Turn stored messages into what a chat UI shows: user bubbles, and assistant bubbles
    with the tool steps that led to them."""
    out: list[dict[str, Any]] = []
    for turn in turns:
        steps: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for m in turn:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                if m["content"]:
                    steps.append({"type": "thought", "text": m["content"]})
                for c in m["tool_calls"]:
                    fn = c.get("function", {})
                    step = {"type": "tool", "name": fn.get("name"), "args": fn.get("arguments", {}),
                            "output": None, "ok": None}
                    steps.append(step)
                    pending.append(step)
            elif m["role"] == "tool":
                step = pending.pop(0) if pending else {"type": "tool", "name": m.get("tool_name")}
                content = m["content"]
                step["ok"] = not content.startswith("ERROR: ")
                step["output"] = content.removeprefix("ERROR: ")
            elif m["role"] == "assistant":
                out.append({"role": "assistant", "content": m["content"], "steps": steps})
                steps = []
    return out


app = create_app()

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("api:app", host=s.api_host, port=s.api_port, reload=False)
