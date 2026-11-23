"""The AETHER engine: an explicit, inspectable ReAct / tool-calling loop.

    goal → recall memory → think → tool call → (safety gate) → execute → observe → … → answer

`run()` is an async generator of AgentEvents. The CLI prints them, and the API streams them
as Server-Sent Events. Nothing about the loop is hidden inside a framework.
"""
from __future__ import annotations

import asyncio
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
from .prompts import FORMAT_RETRY_MESSAGE, STEP_LIMIT_MESSAGE, build_system_prompt
from .safety import ApprovalBroker
from .session import Session

MemoryProvider = Callable[[str], Awaitable[str]]


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
                 memory_provider: MemoryProvider | None = None):
        self.settings = settings or get_settings()
        self.llm: ChatModel = llm or OllamaClient(self.settings.ollama_url,
                                                  timeout_s=self.settings.request_timeout_s)
        self.registry = registry
        self.broker = broker or ApprovalBroker(self.settings.approval_timeout_s)
        self.memory_provider = memory_provider
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
        return {"num_ctx": self.settings.context_length, "temperature": self.settings.temperature}

    # ------------------------------------------------------------------ loop
    async def run(self, goal: str, session: Session, *,
                  run_id: str | None = None) -> AsyncIterator[AgentEvent]:
        run_id = run_id or uuid.uuid4().hex[:12]
        s = self.settings
        step = 0
        started = time.perf_counter()

        def ev(kind: EventType, /, **data: Any) -> AgentEvent:
            return AgentEvent(kind, data, step)

        try:
            model = await self.ensure_model()
            stats = RunStats(model=model, context_limit=s.context_length)
            yield ev(EventType.STATUS, state=AgentState.THINKING, run_id=run_id, model=model)

            memory_context = await self.memory_provider(goal) if self.memory_provider else ""
            turn: list[dict[str, Any]] = [{"role": "user", "content": goal}]
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": build_system_prompt(
                    agent_name=s.agent_name, user_name=s.user_name, memory_context=memory_context)},
                *session.history(s.history_chars), *turn]
            tools = self.registry.schemas()
            known = self.registry.names
            format_retries = 0
            answer: str | None = None

            while step < s.max_steps:
                step += 1
                content_parts: list[str] = []
                thinking_parts: list[str] = []
                calls: list[dict[str, Any]] = []
                async for d in self.llm.chat_stream(model=model, messages=messages, tools=tools,
                                                    options=self._options, think=s.think):
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

                if not calls:
                    if looks_like_failed_tool_call(content, known) and \
                            format_retries < s.max_format_retries:
                        format_retries += 1
                        yield ev(EventType.THOUGHT, kind="retry",
                                 text="That tool call was malformed, so I'm asking the model to retry it.")
                        messages += [{"role": "assistant", "content": content},
                                     {"role": "user", "content": FORMAT_RETRY_MESSAGE}]
                        continue
                    answer = content
                    break

                # ---- a tool step ----
                if content or thinking:
                    yield ev(EventType.THOUGHT, text=content or thinking)
                normalized = [self._normalize_call(c) for c in calls]
                assistant_msg = {"role": "assistant", "content": content,
                                 "tool_calls": [{"function": {"name": n, "arguments": a}}
                                                for n, a, _ in normalized]}
                messages.append(assistant_msg)
                turn.append(assistant_msg)

                for name, args, parse_error in normalized:
                    result_holder: list[ToolResult] = []
                    async for e in self._execute(name, args, parse_error, run_id, step,
                                                 result_holder):
                        yield e
                    result = result_holder[0]
                    stats.tool_seconds.append((name, result.duration_s))
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
            turn.append({"role": "assistant", "content": answer})
            session.add_turn(turn)
            stats.wall_s = time.perf_counter() - started
            yield ev(EventType.STATUS, state=AgentState.IDLE)
            yield ev(EventType.DONE, answer=answer, run_id=run_id, stats=stats.to_dict())

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
                                           "Don't try it again; suggest another way.")
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
