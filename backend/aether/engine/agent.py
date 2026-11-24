"""The AETHER engine: an explicit, inspectable ReAct / tool-calling loop.

    goal → recall memory → think → tool call → (safety gate) → execute → observe → … → answer

`run()` is an async generator of AgentEvents. The CLI prints them, and the API streams them
as Server-Sent Events. Nothing about the loop is hidden inside a framework.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from ..config import Settings, get_settings
from ..events import AgentEvent, AgentState, EventType
from ..llm.ollama import ChatDelta, OllamaClient, OllamaError
from ..llm.parsing import (ToolCallParseError, extract_tool_calls, looks_like_failed_tool_call,
                           parse_arguments, strip_thinking)
from ..metrics import RunStats
from ..tools import REGISTRY, Risk, ToolRegistry, ToolResult
from .prompts import (CUT_OFF_MESSAGE, FORMAT_RETRY_MESSAGE, SALVAGE_MESSAGE, STEP_LIMIT_MESSAGE, THINKING_RAN_OUT,
                      build_system_prompt)
from .safety import ApprovalBroker
from .session import Session

MemoryProvider = Callable[[str], Awaitable[str]]
# Requests that only make sense with a specific tool. If the model answers in plain text
# instead, it's sent back once to use the tool (e.g. quizzes must be interactive cards).
INTENT_TOOLS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(quiz|test) me\b|\b(make|create|give|generate|build)\s+(me\s+)?(an?\s+)?(quick\s+|short\s+)?"
                r"(quiz|test|mcqs?|flash ?cards?)\b|\bflash ?cards?\b|\bmcqs?\b", re.IGNORECASE), "make_quiz",
     "The user asked for a quiz. Call make_quiz now with 4–6 questions (multiple choice, 4 options "
     "each) built from what you found, so it appears as interactive cards. Don't write the "
     "questions as text."),
    (re.compile(r"\b(as|in|into|to|make|create|export|save|generate)\b[^?!\n]{0,30}?\.?pdf\b", re.IGNORECASE),
     "create_pdf", "The user asked for a PDF. Call create_pdf now (with `source` or `content`)."),
]
# Output room (tokens, thinking included) per reasoning level. Without thinking: max_output_tokens.
REASONING_ROOM = {"low": 2048, "medium": 6144, "high": 12288}
MAX_ROOM = 16384          # a retry doubles the room, up to this
REASONING_CTX = 16384     # context floor when reasoning, so thinking plus the answer fit
# After thinking used up all the room: hand its reasoning back and ask again one level lighter.
LIGHTER = {"high": "medium", "medium": "low", "low": "low"}
CUT_SHORT_NOTE = "\n\n_(This reply hit its length limit, so it may be cut short.)_"
# "I'll use manage_timetable…", "let me call web_search" — a promise of a tool call.
PROMISE_RE = re.compile(r"\b(i'll|i will|i'm going to|let me|let's|now i|next,? i)\b", re.IGNORECASE)
# after_turn(goal, answer, model) → facts learned (shown to the user as a `memory` event)
AfterTurn = Callable[[str, str, str], Awaitable[list[dict[str, Any]]]]


def artifacts_in(output: str) -> list[dict[str, str]]:
    """Images a tool made (its `[artifact] artifacts/x.png` lines) → URLs the chat can show.
    `url` is served by /artifacts (static files); `download` by /api/files (as an attachment)."""
    out = []
    for line in output.splitlines():
        if line.startswith("[artifact] "):
            path = line.removeprefix("[artifact] ").strip()
            if path.startswith("artifacts/") and ".." not in path:
                out.append({"path": path, "name": path.rsplit("/", 1)[-1], "url": f"/{path}",
                            "download": f"/api/files/{path}?download=true"})
    return out


def _with_skill(goal: str, playbook: str | None) -> str:
    """A skill's playbook + what you typed after the /command."""
    if not playbook:
        return goal
    if "{{input}}" in playbook:
        return playbook.replace("{{input}}", goal).strip()
    return f"{playbook}\n\nWhat the user added: {goal}" if goal else playbook


class ChatModel(Protocol):
    """What the engine needs from a model backend (OllamaClient, or a fake in tests)."""

    def chat_stream(self, *, model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None,
                    options: dict[str, Any] | None = None,
                    think: bool = False) -> AsyncIterator[ChatDelta]: ...

    async def resolve_model(self, preferred: str, fallbacks: list[str]) -> tuple[str, str | None]: ...


class AetherAgent:
    def __init__(self, settings: Settings | None = None, *, llm: ChatModel | None = None,
                 registry: ToolRegistry = REGISTRY, broker: ApprovalBroker | None = None,
                 memory_provider: MemoryProvider | None = None,
                 after_turn: AfterTurn | None = None, tool_index: Any = None):
        self.settings = settings or get_settings()
        self.llm: ChatModel = llm or OllamaClient(self.settings.ollama_url,
                                                  timeout_s=self.settings.request_timeout_s)
        self.registry = registry
        self.broker = broker or ApprovalBroker(self.settings.approval_timeout_s)
        self.memory_provider = memory_provider
        self.after_turn = after_turn
        self.tool_index = tool_index  # tool_router.ToolIndex: match tools by meaning (optional)
        self.model: str | None = None
        self.model_note: str | None = None  # explains a fallback model choice, if any

    # ----------------------------------------------------------------- model
    async def ensure_model(self) -> str:
        if self.model is None:
            self.model, self.model_note = await self.llm.resolve_model(
                self.settings.model, self.settings.fallback_models)
        return self.model

    def set_model(self, name: str) -> None:
        self.model, self.model_note = name, None

    @property
    def _options(self) -> dict[str, Any]:
        return self._options_for(False)

    def _options_for(self, think: bool | str, room: int | None = None) -> dict[str, Any]:
        """Thinking counts toward the output cap, so models that reason get more room: the higher
        the level, the more. `room` overrides it (a retry after running out). The context grows
        to hold the prompt plus that much output."""
        cap, ctx = self.settings.max_output_tokens, self.settings.context_length
        if think:
            level = think if isinstance(think, str) else "medium"
            cap = max(cap, room or REASONING_ROOM.get(level, REASONING_ROOM["medium"]))
            ctx = max(ctx, REASONING_CTX, cap + 4096)
        return {"num_ctx": ctx, "temperature": self.settings.temperature, "num_predict": cap}

    # ------------------------------------------------------------------ loop
    async def run(self, goal: str, session: Session, *, run_id: str | None = None,
                  images: list[str] | None = None, reasoning: str | None = None,
                  skill: str | None = None, skill_prompt: str | None = None,
                  force_tools: list[str] | None = None) -> AsyncIterator[AgentEvent]:
        """Work on `goal`. `images` are base64-encoded pictures sent with it (vision).
        `reasoning`: "low" | "medium" | "high" for models that think (gpt-oss takes the level;
        other thinking models think for medium/high). `skill_prompt`: a skill's playbook, sent to
        the model with the goal (the chat keeps just what you typed); `force_tools` always offered."""
        run_id = run_id or uuid.uuid4().hex[:12]
        s = self.settings
        step = 0
        started = time.perf_counter()

        def ev(kind: EventType, /, **data: Any) -> AgentEvent:
            return AgentEvent(kind, data, step)

        try:
            model = await self.ensure_model()
            # Your PC is off or unreachable: answer on this Mac instead of failing.
            failover = getattr(self.llm, "failover", None)
            failover_note = None
            if failover is not None:
                model, failover_note = await failover(model, [s.model, *s.fallback_models])
            switched_for_vision = False
            if images:
                vision_model = await self._vision_model(model)
                if vision_model is None:
                    raise OllamaError(
                        "None of your installed models can look at images.",
                        hint="Pull a vision model, e.g.: ollama pull qwen3.5:4b (or gemma3, llava).")
                switched_for_vision = vision_model != model
                model = vision_model
            limit_fn = getattr(self.llm, "context_limit", None)
            stats = RunStats(model=model, context_limit=limit_fn(model) if limit_fn else s.context_length)
            think = await self._think_value(model, reasoning)
            yield ev(EventType.STATUS, state=AgentState.THINKING, run_id=run_id, model=model,
                     reasoning=think if isinstance(think, str) else ("on" if think else None))
            if failover_note:
                yield ev(EventType.THOUGHT, kind="info", text=failover_note)
            if switched_for_vision:
                yield ev(EventType.THOUGHT, kind="info",
                         text=f"Looking at your image{'s' * (len(images or []) > 1)} with {model} "
                              f"(it can see images; {self.model} can't).")

            memory_context = await self.memory_provider(goal) if self.memory_provider else ""
            # What's saved to history: the text, plus a note of how many images there were
            # (images themselves are big and only needed for this turn).
            user_saved: dict[str, Any] = {"role": "user", "content": goal}
            meta: dict[str, Any] = {}
            if images:
                meta["images"] = len(images)
            if skill:
                meta["skill"] = skill
            if meta:
                user_saved["meta"] = meta
            turn: list[dict[str, Any]] = [user_saved]
            typed = goal[len(skill) + 1:].strip() if skill and goal.lower().startswith(f"/{skill}") else goal
            user_for_model: dict[str, Any] = {"role": "user",
                                              "content": _with_skill(typed, skill_prompt) if skill_prompt else goal}
            if images:
                user_for_model["images"] = images
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": build_system_prompt(
                    agent_name=s.agent_name, user_name=s.user_name, memory_context=memory_context)},
                *session.history(s.history_chars), user_for_model]
            offered = await self._route_tools(goal, session, model, bool(images), force_tools)
            tools = [self.registry.get(n).schema() for n in offered if self.registry.get(n)]  # type: ignore[union-attr]
            if len(offered) < len(self.registry.names):
                yield ev(EventType.STATUS, state=AgentState.THINKING, tools=offered)
            known = self.registry.names
            format_retries = 0
            thought_chars = 0  # how much it reasoned this run (for an honest message if it never answers)
            room: int | None = None  # output room after a retry; None = the level's default
            nudges = 0
            unresolved: dict[str, str] = {}  # tool → error of a failed call not yet fixed
            used_ok: set[str] = set()        # tools that succeeded this turn
            answer: str | None = None

            while step < s.max_steps:
                step += 1
                content_parts: list[str] = []
                thinking_parts: list[str] = []
                calls: list[dict[str, Any]] = []
                cut_off = False
                stream = self.llm.chat_stream(model=model, messages=messages, tools=tools,
                                              options=self._options_for(think, room), think=think)
                # Cloud providers report usage (what you're billed) only at the very end of a stream,
                # so their streams are never cut short; the early stop is for small local models.
                cloud = getattr(self.llm, "is_cloud", lambda _m: False)(model)
                async for d in self._until_settled(stream, lambda: bool(calls) and not cloud):
                    if d.done and d.done_reason == "length":
                        cut_off = True
                    if d.thinking:
                        thinking_parts.append(d.thinking)
                        yield ev(EventType.TOKEN, text=d.thinking, channel="thinking")
                    if d.content:
                        content_parts.append(d.content)
                        yield ev(EventType.TOKEN, text=d.content, channel="content")
                    if d.tool_calls:
                        calls.extend(d.tool_calls)
                    if d.done and d.stats:
                        stats.calls.append(d.stats)

                content = strip_thinking("".join(content_parts))
                thinking = "".join(thinking_parts).strip()

                # Fallback: the model wrote the tool call as text instead of using the channel.
                if not calls:
                    recovered, cleaned = extract_tool_calls(content, known)
                    if recovered:
                        calls, content = recovered, cleaned

                thought_chars += len(thinking)
                if not calls and cut_off and content.strip():
                    # Cut off mid-answer: what it wrote is still the answer. Keep it, say it's cut short.
                    answer = content + CUT_SHORT_NOTE
                    break

                if not calls and cut_off and thinking and format_retries < s.max_format_retries:
                    # It reasoned until the room ran out and never answered. Keep that work: hand the
                    # reasoning back and ask for the answer now, one level lighter, with twice the room.
                    format_retries += 1
                    used = self._options_for(think, room)["num_predict"]
                    room = min(used * 2, MAX_ROOM)
                    think = LIGHTER.get(think, "low") if isinstance(think, str) else think
                    level = {"high": "High", "medium": "Med", "low": "Low"}.get(think, "") if isinstance(think, str) else ""
                    yield ev(EventType.THOUGHT, kind="retry",
                             text=f"It used all its room thinking before answering, so I'm handing its reasoning back "
                                  f"and asking for the answer{f' at {level}' if level else ''} with more room "
                                  f"(attempt {format_retries + 1} of {s.max_format_retries + 1}).")
                    messages.append({"role": "user", "content": SALVAGE_MESSAGE.format(reasoning=thinking[-6000:])})
                    continue

                if not calls and cut_off and format_retries < s.max_format_retries:
                    # Hit the output cap with nothing usable (often a runaway loop).
                    format_retries += 1
                    yield ev(EventType.THOUGHT, kind="retry",
                             text="The model's reply ran far too long, so I'm asking it to be concise.")
                    messages += [{"role": "assistant", "content": content[:2000]},
                                 {"role": "user", "content": CUT_OFF_MESSAGE}]
                    continue

                if not calls:
                    if looks_like_failed_tool_call(content, known) and \
                            format_retries < s.max_format_retries:
                        format_retries += 1
                        yield ev(EventType.THOUGHT, kind="retry",
                                 text="That tool call was malformed, so I'm asking the model to retry it.")
                        messages += [{"role": "assistant", "content": content},
                                     {"role": "user", "content": FORMAT_RETRY_MESSAGE}]
                        continue
                    nudge = self._nudge(content, unresolved, known, goal, used_ok)
                    if nudge and nudges < s.max_nudges:
                        # The model tried to finish with a broken tool call it never fixed,
                        # or promised a tool it never called. Send it back once, precisely.
                        nudges += 1
                        unresolved.clear()
                        yield ev(EventType.THOUGHT, kind="retry", text=nudge[0])
                        messages += [{"role": "assistant", "content": content},
                                     {"role": "user", "content": nudge[1]}]
                        continue
                    answer = content
                    break

                # ---- a tool step ----
                if content:  # what it said before the tool call (its thinking streams separately)
                    yield ev(EventType.THOUGHT, text=content)
                normalized = [self._normalize_call(c) for c in calls]
                # Keep the backend's call ids: cloud APIs pair each result with its call by id.
                assistant_msg = {"role": "assistant", "content": content,
                                 "tool_calls": [{**({"id": c["id"]} if c.get("id") else {}),
                                                 "function": {"name": n, "arguments": a}}
                                                for c, (n, a, _) in zip(calls, normalized)]}
                messages.append(assistant_msg)
                turn.append(assistant_msg)

                for name, args, parse_error in normalized:
                    result_holder: list[ToolResult] = []
                    async for e in self._execute(name, args, parse_error, run_id, step,
                                                 result_holder):
                        yield e
                    result = result_holder[0]
                    stats.tool_seconds.append((name, result.duration_s))
                    if result.ok:
                        used_ok.add(name)
                    if result.ok or result.denied:
                        unresolved.pop(name, None)
                    else:
                        unresolved[name] = result.output
                    tool_msg = {"role": "tool", "tool_name": name,
                                "content": result.output if result.ok else f"ERROR: {result.output}"}
                    messages.append(tool_msg)
                    turn.append(tool_msg)
                    # The agent loaded a pack mid-task: its tools are offered from the next step.
                    if name == "list_available_capabilities" and result.ok and args.get("pack"):
                        more = self._pack_tools(str(args["pack"]))
                        if any(n not in offered for n in more):
                            offered = list(dict.fromkeys([*offered, *more]))
                            tools = [self.registry.get(n).schema() for n in offered if self.registry.get(n)]  # type: ignore[union-attr]
                            yield ev(EventType.STATUS, state=AgentState.THINKING, tools=offered)
                yield ev(EventType.STATUS, state=AgentState.THINKING)

            if answer is None:
                # Step limit reached: one last call, without tools, for the best answer so far.
                messages.append({"role": "user", "content": STEP_LIMIT_MESSAGE})
                parts: list[str] = []
                async for d in self.llm.chat_stream(model=model, messages=messages, tools=None,
                                                    options=self._options, think=False):
                    if d.content:
                        parts.append(d.content)
                        yield ev(EventType.TOKEN, text=d.content, channel="content")
                    if d.done and d.stats:
                        stats.calls.append(d.stats)
                answer = strip_thinking("".join(parts))

            if not answer:
                answer = THINKING_RAN_OUT if thought_chars > 500 else \
                    "I couldn't come up with an answer for that. Could you rephrase it?"
            stats.wall_s = time.perf_counter() - started
            # `meta` is saved with the conversation (telemetry survives reloads) but never
            # sent back to the model: Session.history() strips it.
            turn.append({"role": "assistant", "content": answer, "meta": {"stats": stats.to_dict()}})
            session.add_turn(turn)
            yield ev(EventType.STATUS, state=AgentState.IDLE)
            yield ev(EventType.DONE, answer=answer, run_id=run_id, stats=stats.to_dict())

            if self.after_turn is not None:
                # Runs after `done`, so the answer is never delayed by memory upkeep.
                try:
                    learned = await self.after_turn(goal, answer, model)
                except Exception:
                    learned = []
                if learned:
                    yield ev(EventType.MEMORY, saved=[{"id": f.get("id"), "text": f.get("text"),
                                                       "category": f.get("category")}
                                                      for f in learned])

        except OllamaError as e:
            yield ev(EventType.STATUS, state=AgentState.IDLE)
            yield ev(EventType.ERROR, message=e.message, hint=e.hint)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:  # last line of defence: report, don't crash the interface
            yield ev(EventType.STATUS, state=AgentState.IDLE)
            yield ev(EventType.ERROR, message=f"Unexpected error: {type(e).__name__}: {e}",
                     hint="This is a bug in AETHER. The details are in the server log.")
        finally:
            self.broker.cancel_run(run_id)

    # --------------------------------------------------------------- helpers
    async def _think_value(self, model: str, reasoning: str | None) -> bool | str:
        """What to send as Ollama's `think`: a level for gpt-oss, on/off for other thinking models."""
        if reasoning not in ("low", "medium", "high"):
            return self.settings.think
        caps_fn = getattr(self.llm, "capabilities", None)
        caps = await caps_fn(model) if caps_fn else set()
        if "thinking" not in caps:
            return False
        if "gpt-oss" in model:
            return reasoning
        return reasoning in ("medium", "high")

    async def _route_tools(self, goal: str, session: Session, model: str, has_images: bool,
                           force_tools: list[str] | None = None) -> list[str]:
        """All enabled tools for cloud models; the 6–8 most relevant (+ the meta-tool) for local ones."""
        try:
            from tool_router import enabled, route
        except ImportError:  # backend/ not on the path (embedded use): no routing
            return [t.name for t in self.registry.all()]
        everything = [t.name for t in self.registry.all() if enabled(t.name, t)]
        if not self.settings.tool_router or getattr(self.llm, "is_cloud", lambda _m: False)(model):
            return everything
        similarity: dict[str, float] = {}
        if self.tool_index is not None:
            try:
                similarity = await asyncio.wait_for(self.tool_index.similarities(goal, self.registry.all()), 4)
            except Exception:  # the embedding model is optional: keywords still work
                similarity = {}
        recent = {c.get("function", {}).get("name") for turn in session.turns[-3:] for m in turn
                  for c in (m.get("tool_calls") or [])}
        names = route(goal, self.registry.all(), max_tools=self.settings.router_max_tools,
                      recent={n for n in recent if n}, has_images=has_images, similarity=similarity)
        for pattern, tool_name, _ in INTENT_TOOLS:  # a quiz or PDF request must get its tool
            if pattern.search(goal) and tool_name in everything and tool_name not in names:
                names = [tool_name, *names][: self.settings.router_max_tools]
        forced = [t for t in (force_tools or []) if t in everything]  # a skill's own tools come first
        if forced:
            names = list(dict.fromkeys([*forced, *names]))[: max(self.settings.router_max_tools, len(forced))]
        return names

    def _pack_tools(self, pack: str) -> list[str]:
        try:
            from tool_router import pack_tools
        except ImportError:
            return []
        return pack_tools(pack.strip().lower(), self.registry.all())

    async def _vision_model(self, current: str) -> str | None:
        """The current model if it can see images, else an installed one that can."""
        caps_fn = getattr(self.llm, "capabilities", None)
        if caps_fn is None:
            return current  # a backend without capability info: just try
        if "vision" in await caps_fn(current):
            return current
        finder = getattr(self.llm, "find_model_with", None)
        if finder is None:
            return None
        return (await finder("vision", "tools", prefer=self.settings.vision_model)
                or await finder("vision", prefer=self.settings.vision_model))

    async def _until_settled(self, stream: AsyncIterator[ChatDelta],
                             have_calls: Callable[[], bool]) -> AsyncIterator[ChatDelta]:
        """Relay a model stream, but once tool calls have arrived, give up waiting after
        `tool_call_grace_s` of silence. Closing the stream also makes Ollama stop generating."""
        it = stream.__aiter__()
        try:
            while True:
                try:
                    if have_calls():
                        d = await asyncio.wait_for(it.__anext__(), self.settings.tool_call_grace_s)
                    else:
                        d = await it.__anext__()
                except (StopAsyncIteration, asyncio.TimeoutError):
                    return
                yield d
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    @staticmethod
    def _nudge(answer: str, unresolved: dict[str, str], known: set[str], goal: str = "",
               used_ok: set[str] | None = None) -> tuple[str, str] | None:
        """(note for the user, message for the model) when a final answer shouldn't stand yet."""
        for pattern, tool, instruction in INTENT_TOOLS:
            if tool in known and tool not in (used_ok or set()) and tool not in unresolved \
                    and pattern.search(goal):
                return (f"You asked for something {tool} makes, so I'm asking the model to use it.",
                        instruction)
        if unresolved:
            name, error = next(iter(unresolved.items()))
            return (f"{name} failed and wasn't fixed, so I'm asking the model to correct it.",
                    f"Your {name} call failed: {error[:400]} Fix the arguments and call {name} "
                    "again now. Only if it truly can't work, tell the user plainly that it failed. "
                    "Never say it succeeded.")
        promised = [t for t in known if t in answer]
        if promised and PROMISE_RE.search(answer):
            tool = promised[0]
            return (f"The model announced {tool} without calling it, so I'm asking it to call it.",
                    f"You said you would use {tool} but didn't call it. Call it now. Otherwise "
                    "give your final answer without promising future actions.")
        return None

    @staticmethod
    def _normalize_call(call: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
        fn = call.get("function") or {}
        name = str(fn.get("name") or "").strip()
        try:
            return name, parse_arguments(fn.get("arguments")), None
        except ToolCallParseError as e:
            return name, {}, str(e)

    async def _execute(self, name: str, args: dict[str, Any], parse_error: str | None,
                       run_id: str, step: int,
                       out: list[ToolResult]) -> AsyncIterator[AgentEvent]:
        """Validate → (approve) → run one tool call. Appends its ToolResult to `out`."""
        call_id = uuid.uuid4().hex[:10]

        def ev(kind: EventType, /, **data: Any) -> AgentEvent:
            return AgentEvent(kind, data, step)

        spec = self.registry.get(name)
        try:
            from tool_router import PACKS, enabled, meta_for
            if spec is not None and not enabled(name, spec):
                pack = PACKS[meta_for(name, spec).pack].label
                yield ev(EventType.TOOL_START, call_id=call_id, name=name, args=args, risk="safe", icon=spec.icon)
                result = ToolResult(False, f"The {pack} pack is switched off by the user, so {name} can't run. "
                                           "Use another tool or tell the user.")
                yield ev(EventType.TOOL_RESULT, call_id=call_id, name=name, ok=False, output=result.output, duration_s=0.0)
                out.append(result)
                return
        except ImportError:
            pass
        if spec is None:
            yield ev(EventType.TOOL_START, call_id=call_id, name=name or "?", args=args,
                     risk="unknown", icon="circle-help")
            result = ToolResult(False, f"There's no tool named '{name}'. Available tools: "
                                       f"{', '.join(sorted(self.registry.names))}.")
            yield ev(EventType.TOOL_RESULT, call_id=call_id, name=name, ok=False,
                     output=result.output, duration_s=0.0)
            out.append(result)
            return

        clean, errors = self.registry.validate(spec, args)
        if parse_error:
            errors.insert(0, parse_error)
        if not errors and spec.precheck is not None:
            try:
                spec.precheck(clean)
            except Exception as e:  # ToolError, WorkspaceError, …
                errors.append(str(e))
        risk = spec.risk_for(clean) if not errors else spec.risk
        yield ev(EventType.TOOL_START, call_id=call_id, name=name, args=clean or args,
                 risk=risk.value, icon=spec.icon, category=spec.category)

        if errors:
            result = ToolResult(False, "This call can't run: " + " ".join(errors) +
                                " Fix the arguments and call the tool again.")
            yield ev(EventType.TOOL_RESULT, call_id=call_id, name=name, ok=False,
                     output=result.output, duration_s=0.0)
            out.append(result)
            return

        if risk is Risk.REQUIRES_APPROVAL:
            req = self.broker.open(run_id=run_id, tool=name, args=clean,
                                   summary=spec.summarize_risk(clean))
            yield ev(EventType.STATUS, state=AgentState.AWAITING_APPROVAL)
            yield ev(EventType.APPROVAL_REQUIRED, call_id=call_id, **req.to_dict())
            decision = await self.broker.wait(req.id)
            yield ev(EventType.APPROVAL_RESOLVED, approval_id=req.id, call_id=call_id,
                     approved=decision.approved, reason=decision.reason)
            if not decision.approved:
                why = f" Reason: {decision.reason}" if decision.reason else ""
                result = ToolResult(False, f"The user DENIED running {name}.{why} "
                                           "Don't try it again; suggest another way.", denied=True)
                yield ev(EventType.TOOL_RESULT, call_id=call_id, name=name, ok=False,
                         output=result.output, duration_s=0.0, denied=True)
                out.append(result)
                return

        yield ev(EventType.STATUS, state=AgentState.USING_TOOL, tool=name)
        result = await self.registry.execute(spec, clean, timeout_s=self.settings.tool_timeout_s,
                                             max_output_chars=self.settings.max_tool_output_chars)
        yield ev(EventType.TOOL_RESULT, call_id=call_id, name=name, ok=result.ok,
                 output=result.output, duration_s=round(result.duration_s, 3),
                 artifacts=artifacts_in(result.output) if result.ok else [])
        out.append(result)
