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
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from aether import __version__
from aether.config import Settings, get_settings
from aether.engine import AetherAgent
from aether.config import ENV_FILE
from aether.llm.cloud import PROVIDERS, split_model
from aether.llm.ollama import OllamaError
from aether.llm.router import router_from_settings, save_env_value
from aether.memory import MemorySystem
from aether.memory.knowledge import KnowledgeError
from aether.mcp import MCPManager
from tts import TTSError, TTSService
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
    mcp: MCPManager
    tts: TTSService
    busy_sessions: set[str] = field(default_factory=set)
    background: set[asyncio.Task[Any]] = field(default_factory=set)

    def spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)


def build_context(settings: Settings, llm: Any = None, db_path: str | None = None) -> AppContext:
    llm = llm or router_from_settings(settings)
    memory = MemorySystem(settings, llm, db_path=db_path)
    set_memory(memory)

    async def learn(goal: str, answer: str, model: str) -> list[dict[str, Any]]:
        if not settings.auto_memory:
            return []
        return await memory.extract_facts(llm, model, goal, answer)

    agent = AetherAgent(settings, llm=llm, memory_provider=memory.build_context, after_turn=learn)
    return AppContext(settings=settings, llm=llm, memory=memory, agent=agent,
                      mcp=MCPManager(settings.mcp_config), tts=TTSService(settings.data_dir / "tts"))


# ------------------------------------------------------------------- schemas
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    session_id: str | None = None
    # base64 images (no "data:" prefix); up to 4, about 8 MB each
    images: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("images")
    @classmethod
    def _valid_images(cls, v: list[str]) -> list[str]:
        import base64
        import binascii
        out = []
        for img in v:
            img = img.split(",", 1)[1] if img.startswith("data:") else img
            if len(img) > 11_500_000:
                raise ValueError("Each image must be under about 8 MB.")
            try:
                base64.b64decode(img[:4096] + "=" * (-len(img[:4096]) % 4), validate=True)
            except (binascii.Error, ValueError) as e:
                raise ValueError("Images must be base64-encoded.") from e
            out.append(img)
        return out


class ApprovalRequestBody(BaseModel):
    approved: bool
    reason: str = ""


class ModelChoice(BaseModel):
    model: str


class HostIn(BaseModel):
    # "192.168.1.50", "my-pc.local:11434" or "http://100.64.0.7:11434"
    url: str = Field(min_length=3, max_length=200, pattern=r"^(https?://)?[A-Za-z0-9.\-\[\]:]+/?$")


class KeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=400, pattern=r"^[\x21-\x7e]+$")  # no spaces/newlines


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


class QuizAnswer(BaseModel):
    index: int = Field(ge=0)
    correct: bool


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str = "af_heart"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class PdfRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)


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
        # External MCP servers start in the background so a slow one never delays AETHER.
        ctx.spawn(ctx.mcp.start_all())
        # Resume indexing of any documents interrupted by a previous shutdown.
        for d in ctx.memory.knowledge.documents():
            if d["status"] in {"queued", "reading", "embedding"}:
                ctx.spawn(ctx.memory.knowledge.index(d["id"]))
        yield
        for task in list(ctx.background):
            task.cancel()
        await ctx.mcp.stop_all()
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
        is_cloud = getattr(ctx.llm, "is_cloud", lambda _m: False)
        is_remote = getattr(ctx.llm, "is_remote", lambda _m: False)
        model, note, error = ctx.agent.model, ctx.agent.model_note, None
        wanted = model or ctx.settings.model
        # A cloud brain works without Ollama (only document search and memory need it).
        if model is None and (ollama_up or is_cloud(wanted) or is_remote(wanted)):
            try:
                model = await ctx.agent.ensure_model()
                note = ctx.agent.model_note
            except OllamaError as e:
                error = {"message": e.message, "hint": e.hint}
        host = ctx.llm.split_host(model)[0] if model and is_remote(model) else None
        if host is not None:
            client = ctx.llm.hosts.get(host)
            if client is None or not await client.health():
                error = {"message": f"Can't reach the computer '{host}'.",
                         "hint": "Make sure it's on, Ollama is open there, and both are on the same network."}
        elif not ollama_up and not is_cloud(model):
            error = {"message": f"Can't reach Ollama at {ctx.settings.ollama_url}.",
                     "hint": "Open the Ollama app, or run `ollama serve`."}
        emb = ctx.memory.embedder
        provider = split_model(model)[0] if model else None
        limit_fn = getattr(ctx.llm, "context_limit", None)
        return {"ok": error is None, "version": __version__, "ollama": ollama_up,
                "model": model, "model_note": note, "error": error,
                "cloud": provider is not None, "host": host,
                "provider": PROVIDERS[provider].label if provider else None,
                "embed_model": ctx.settings.embed_model, "embeddings_available": emb.available,
                "embeddings_hint": emb.last_error.hint if emb.last_error else None,
                "tools": len(REGISTRY.all()),
                "context_length": limit_fn(model) if limit_fn and model else ctx.settings.context_length}

    @app.get("/api/models")
    async def models(request: Request) -> dict[str, Any]:
        """Local Ollama models, plus the models of every cloud provider that's set up."""
        ctx = ctx_of(request)
        installed, local_error = [], None
        try:
            installed = await ctx.llm.list_models()
        except OllamaError as e:
            local_error = {"message": e.message, "hint": e.hint}
        catalog = await ctx.llm.catalog() if hasattr(ctx.llm, "catalog") else []
        hosts = await ctx.llm.hosts_catalog() if hasattr(ctx.llm, "hosts_catalog") else []
        if local_error and not any(p["configured"] for p in catalog) and not any(h["online"] for h in hosts):
            raise HTTPException(503, detail=local_error)
        return {"models": installed, "current": ctx.agent.model or ctx.settings.model,
                "local_error": local_error, "cloud": catalog, "hosts": hosts}

    @app.post("/api/models")
    async def choose_model(body: ModelChoice, request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        pid, _bare = split_model(body.model)
        if pid is not None:
            if pid not in getattr(ctx.llm, "providers", {}):
                info = PROVIDERS[pid]
                raise HTTPException(409, detail={"message": f"{info.label} isn't set up yet.",
                                                 "hint": f"Add a key in Settings → Cloud brains "
                                                         f"(get one at {info.key_url})."})
        elif getattr(ctx.llm, "is_remote", lambda _m: False)(body.model):
            host, bare = ctx.llm.split_host(body.model)
            client = ctx.llm.hosts.get(host)
            if client is None:
                raise HTTPException(404, detail={"message": f"There's no computer named '{host}'.",
                                                 "hint": "Add it in Settings → Computers on your network."})
            try:
                installed = await client.list_models()
            except OllamaError as e:
                raise HTTPException(503, detail={"message": f"Can't reach '{host}'.", "hint": e.hint}) from e
            if bare not in installed:
                raise HTTPException(404, detail={"message": f"'{bare}' isn't installed on {host}.",
                                                 "hint": f"On {host}, run: ollama pull {bare}"})
        else:
            try:
                installed = await ctx.llm.list_models()
            except OllamaError as e:
                raise HTTPException(503, detail={"message": e.message, "hint": e.hint}) from e
            if body.model not in installed:
                raise HTTPException(404, detail={"message": f"'{body.model}' isn't installed.",
                                                 "hint": f"Run: ollama pull {body.model}"})
        ctx.agent.set_model(body.model)
        return {"current": body.model, "cloud": pid is not None}

    # ---------------------------------------------------------- hardware
    @app.get("/api/hardware")
    async def hardware(request: Request) -> list[dict[str, Any]]:
        """This computer and every network GPU PC: GPU, VRAM, RAM and the models loaded on them."""
        from aether.hardware import snapshot
        ctx = ctx_of(request)
        return await snapshot(ctx.llm, monitor_transport=getattr(request.app.state, "monitor_transport", None))

    # ------------------------------------------- computers on your network
    @app.get("/api/hosts")
    async def hosts(request: Request) -> list[dict[str, Any]]:
        ctx = ctx_of(request)
        return await ctx.llm.hosts_catalog() if hasattr(ctx.llm, "hosts_catalog") else []

    @app.put("/api/hosts/{name}")
    async def set_host(name: str, body: HostIn, request: Request) -> dict[str, Any]:
        """Check that Ollama answers at the address, then save it to backend/.env."""
        ctx = ctx_of(request)
        if not hasattr(ctx.llm, "set_host"):
            raise HTTPException(404, detail={"message": "Network computers aren't supported here."})
        url = normalize_host_url(body.url)
        if url is None:
            raise HTTPException(422, detail={
                "message": f"'{body.url.strip()[:60]}' isn't a valid address.",
                "hint": "Type just the PC's address once, e.g. 192.168.1.9 (run ipconfig on the PC to see it)."})
        try:
            await ctx.llm.set_host(name, url)
        except OllamaError as e:
            raise HTTPException(422, detail={"message": e.message, "hint": e.hint}) from e
        save_env_value(getattr(request.app.state, "env_file", ENV_FILE), "AETHER_OLLAMA_HOSTS",
                       json.dumps(ctx.llm.host_urls))
        return next(h for h in await ctx.llm.hosts_catalog() if h["name"] == name)

    @app.delete("/api/hosts/{name}")
    async def remove_host(name: str, request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        if name not in getattr(ctx.llm, "hosts", {}):
            raise HTTPException(404, detail={"message": f"There's no computer named '{name}'."})
        await ctx.llm.set_host(name, None)
        save_env_value(getattr(request.app.state, "env_file", ENV_FILE), "AETHER_OLLAMA_HOSTS",
                       json.dumps(ctx.llm.host_urls) if ctx.llm.host_urls else None)
        if ctx.agent.model and ctx.llm.split_host(ctx.agent.model)[0] == name:
            ctx.agent.model, ctx.agent.model_note = None, None  # back to this Mac's default
        return {"removed": name}

    # ------------------------------------------------------ cloud brains
    @app.get("/api/providers")
    async def providers(request: Request) -> list[dict[str, Any]]:
        ctx = ctx_of(request)
        return await ctx.llm.catalog() if hasattr(ctx.llm, "catalog") else []

    @app.put("/api/providers/{provider_id}/key")
    async def set_provider_key(provider_id: str, body: KeyIn, request: Request) -> dict[str, Any]:
        """Check a key with the provider, then save it to backend/.env (gitignored, owner-only)."""
        ctx = ctx_of(request)
        if provider_id not in PROVIDERS or not hasattr(ctx.llm, "set_key"):
            raise HTTPException(404, detail={"message": "No such provider."})
        try:
            await ctx.llm.set_key(provider_id, body.key)
        except OllamaError as e:
            raise HTTPException(422, detail={"message": e.message, "hint": e.hint}) from e
        env_file = getattr(request.app.state, "env_file", ENV_FILE)
        save_env_value(env_file, PROVIDERS[provider_id].key_env, body.key)
        return next(p for p in await ctx.llm.catalog() if p["id"] == provider_id)

    @app.delete("/api/providers/{provider_id}/key")
    async def remove_provider_key(provider_id: str, request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        if provider_id not in PROVIDERS or not hasattr(ctx.llm, "set_key"):
            raise HTTPException(404, detail={"message": "No such provider."})
        await ctx.llm.set_key(provider_id, None)
        save_env_value(getattr(request.app.state, "env_file", ENV_FILE),
                       PROVIDERS[provider_id].key_env, None)
        if ctx.agent.model and split_model(ctx.agent.model)[0] == provider_id:
            ctx.agent.model, ctx.agent.model_note = None, None  # fall back to the local default
        return {"removed": provider_id}

    # ------------------------------------------------------------- voice
    @app.get("/api/tts/status")
    async def tts_status(request: Request) -> dict[str, Any]:
        return ctx_of(request).tts.status()

    @app.post("/api/tts/warmup")
    async def tts_warmup(request: Request) -> dict[str, Any]:
        """Load the voice model now, so the first sentence plays instantly."""
        ctx = ctx_of(request)
        try:
            return await ctx.tts.warmup()
        except TTSError as e:
            raise HTTPException(503, detail={"message": e.message, "hint": e.hint}) from e

    @app.post("/api/tts/speak")
    async def tts_speak(body: SpeakRequest, request: Request) -> Response:
        """Synthesise one chunk (usually a sentence) and return it as WAV audio."""
        try:
            clip = await ctx_of(request).tts.speak(body.text, body.voice, body.speed)
        except TTSError as e:
            raise HTTPException(503 if "installed" in e.message or "download" in e.message else 422,
                                detail={"message": e.message, "hint": e.hint}) from e
        return Response(clip.wav, media_type="audio/wav", headers={
            "X-Audio-Seconds": f"{clip.seconds:.3f}", "X-Synth-Seconds": f"{clip.synth_seconds:.3f}",
            "Cache-Control": "no-store", "Access-Control-Expose-Headers": "X-Audio-Seconds, X-Synth-Seconds"})

    @app.get("/api/mcp")
    async def mcp_status(request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        return {"config": str(ctx.settings.mcp_config), "exists": ctx.settings.mcp_config.exists(),
                "servers": ctx.mcp.summary()}

    @app.post("/api/mcp/reload")
    async def mcp_reload(request: Request) -> dict[str, Any]:
        ctx = ctx_of(request)
        await ctx.mcp.reload()
        return {"servers": ctx.mcp.summary()}

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
                async with contextlib.aclosing(ctx.agent.run(body.message, session,
                                                             images=body.images or None)) as events:
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

    @app.post("/api/files/pdf")
    async def note_to_pdf(body: PdfRequest) -> dict[str, Any]:
        """One-click export: turn a Markdown note into a PDF in exports/."""
        from aether.tools.documents import markdown_to_pdf
        try:
            src = workspace().resolve(body.path, must_exist=True)
        except WorkspaceError as e:
            raise HTTPException(404, detail={"message": str(e)}) from e
        if src.suffix.lower() not in {".md", ".markdown", ".txt"}:
            raise HTTPException(422, detail={"message": "Only Markdown or text notes can be exported."})
        out = workspace().resolve(f"exports/{src.stem}.pdf")
        title = src.stem.replace("-", " ").strip().capitalize() or "Note"
        pages = await asyncio.to_thread(markdown_to_pdf, src.read_text(encoding="utf-8", errors="replace"),
                                        title, out)
        return {"path": workspace().relative(out), "pages": pages}

    @app.get("/api/quizzes/{quiz_id}")
    async def quiz(quiz_id: int, request: Request) -> dict[str, Any]:
        q = ctx_of(request).memory.quizzes.get(quiz_id)
        if not q:
            raise HTTPException(404, detail={"message": "No such quiz."})
        return q

    @app.post("/api/quizzes/{quiz_id}/answer")
    async def quiz_answer(quiz_id: int, body: QuizAnswer, request: Request) -> dict[str, Any]:
        from aether.memory.quizzes import QuizError
        try:
            return await ctx_of(request).memory.quizzes.record(quiz_id, body.index, body.correct)
        except QuizError as e:
            raise HTTPException(404, detail={"message": str(e)}) from e

    @app.get("/api/quiz-stats")
    async def quiz_stats(request: Request) -> list[dict[str, Any]]:
        return ctx_of(request).memory.quizzes.stats()

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


def normalize_host_url(raw: str) -> str | None:
    """'192.168.1.9' → 'http://192.168.1.9:11434'. None if it isn't a real IP address or hostname."""
    import ipaddress
    import re
    from urllib.parse import urlsplit

    raw = raw.strip().rstrip("/")
    try:
        parts = urlsplit(raw if "://" in raw else f"http://{raw}")
        host, port = parts.hostname, parts.port or 11434
    except ValueError:  # e.g. a port that isn't a number
        return None
    if not host or parts.scheme not in ("http", "https") or parts.path not in ("", "/"):
        return None
    labels = host.split(".")
    if all(label.isdigit() for label in labels):  # looks numeric: must be a real IPv4 address
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return None
    elif ":" in host:  # IPv6
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        host = f"[{host}]"
    elif not all(re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
        return None
    return f"{parts.scheme}://{host}:{port}"


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
                msg = {"role": "user", "content": m["content"]}
                if (m.get("meta") or {}).get("images"):
                    msg["images"] = m["meta"]["images"]
                out.append(msg)
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
                out.append({"role": "assistant", "content": m["content"], "steps": steps,
                            "stats": (m.get("meta") or {}).get("stats")})
                steps = []
    return out


app = create_app()

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("api:app", host=s.api_host, port=s.api_port, reload=False)
