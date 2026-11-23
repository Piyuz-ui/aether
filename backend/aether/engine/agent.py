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
from .prompts import (CUT_OFF_MESSAGE, FORMAT_RETRY_MESSAGE, STEP_LIMIT_MESSAGE,
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
# "I'll use manage_timetable…", "let me call web_search" — a promise of a tool call.
PROMISE_RE = re.compile(r"\b(i'll|i will|i'm going to|let me|let's|now i|next,? i)\b", re.IGNORECASE)
# after_turn(goal, answer, model) → facts learned (shown to the user as a `memory` event)
AfterTurn = Callable[[str, str, str], Awaitable[list[dict[str, Any]]]]


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
                 after_turn: AfterTurn | None = None):
        self.settings = settings or get_settings()
        self.llm: ChatModel = llm or OllamaClient(self.settings.ollama_url,
                                                  timeout_s=self.settings.request_timeout_s)
        self.registry = registry
        self.broker = broker or ApprovalBroker(self.settings.approval_timeout_s)
        self.memory_provider = memory_provider
        self.after_turn = after_turn
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
        return {"num_ctx": self.settings.context_length, "temperature": self.settings.temperature,
                "num_predict": self.settings.max_output_tokens}

    # ------------------------------------------------------------------ loop
    async def run(self, goal: str, session: Session, *, run_id: str | None = None,
                  images: list[str] | None = None) -> AsyncIterator[AgentEvent]:
        """Work on `goal`. `images` are base64-encoded pictures sent with it (vision)."""
        run_id = run_id or uuid.uuid4().hex[:12]
        s = self.settings
        step = 0
        started = time.perf_counter()

        def ev(kind: EventType, /, **data: Any) -> AgentEvent:
            return AgentEvent(kind, data, step)

        try:
            model = await self.ensure_model()
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
            yield ev(EventType.STATUS, state=AgentState.THINKING, run_id=run_id, model=model)
            if switched_for_vision:
                yield ev(EventType.THOUGHT, kind="info",
                         text=f"Looking at your image{'s' * (len(images or []) > 1)} with {model} "
                              f"(it can see images; {self.model} can't).")

            memory_context = await self.memory_provider(goal) if self.memory_provider else ""
            # What's saved to history: the text, plus a note of how many images there were
            # (images themselves are big and only needed for this turn).
            user_saved: dict[str, Any] = {"role": "user", "content": goal}
            if images:
                user_saved["meta"] = {"images": len(images)}
            turn: list[dict[str, Any]] = [user_saved]
            user_for_model: dict[str, Any] = {"role": "user", "content": goal}
            if images:
                user_for_model["images"] = images
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": build_system_prompt(
                    agent_name=s.agent_name, user_name=s.user_name, memory_context=memory_context)},
                *session.history(s.history_chars), user_for_model]
            tools = self.registry.schemas()
            known = self.registry.names
            format_retries = 0
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
                                              options=self._options, think=s.think)
                async for d in self._until_settled(stream, lambda: bool(calls)):
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
                if content or thinking:
                    yield ev(EventType.THOUGHT, text=content or thinking)
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
                answer = "I couldn't come up with an answer for that. Could you rephrase it?"
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
                 output=result.output, duration_s=round(result.duration_s, 3))
        out.append(result)
