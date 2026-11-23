"""Tool registry. Decorate a typed Python function with @tool and it becomes something
the model can call: the JSON schema is built from the signature, arguments are validated
and coerced before execution, and each tool declares its risk level.

    @tool(description="Add two numbers.", params={"a": "first", "b": "second"})
    def add(a: int, b: int) -> str:
        return str(a + b)
"""
from __future__ import annotations

import asyncio
import inspect
import time
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints


class Risk(StrEnum):
    SAFE = "safe"
    REQUIRES_APPROVAL = "requires_approval"


_JSON = {str: "string", int: "integer", float: "number", bool: "boolean",
         list: "array", dict: "object"}


def _schema_for(hint: Any) -> dict[str, Any]:
    origin = get_origin(hint)
    if origin is Literal:
        values = list(get_args(hint))
        return {"type": _JSON.get(type(values[0]), "string"), "enum": values}
    if origin in (Union, types.UnionType):  # Optional[X] → X
        inner = [a for a in get_args(hint) if a is not type(None)]
        return _schema_for(inner[0]) if inner else {"type": "string"}
    if origin is list:
        (item,) = get_args(hint) or (str,)
        return {"type": "array", "items": _schema_for(item)}
    if origin is dict:
        return {"type": "object"}
    return {"type": _JSON.get(hint, "string")}


@dataclass(slots=True)
class ToolResult:
    ok: bool
    output: str
    duration_s: float = 0.0
    denied: bool = False  # the user refused approval (not a mistake the model should fix)


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: dict[str, Any]
    risk: Risk = Risk.SAFE
    category: str = "general"
    icon: str = "wrench"  # lucide icon name, used by the web UI
    risk_check: Callable[[dict[str, Any]], Risk] | None = None
    risk_summary: Callable[[dict[str, Any]], str] | None = None
    timeout_s: float | None = None
    # Runs before the approval prompt: raise ToolError to reject a call that can't work
    # (e.g. deleting a file that doesn't exist) without bothering the user.
    precheck: Callable[[dict[str, Any]], None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}

    def risk_for(self, args: dict[str, Any]) -> Risk:
        """Some tools are only risky sometimes (e.g. writing a note is safe, overwriting is not)."""
        if self.risk_check is not None:
            return self.risk_check(args)
        return self.risk

    def summarize_risk(self, args: dict[str, Any]) -> str:
        if self.risk_summary is not None:
            return self.risk_summary(args)
        return f"{self.name} can change things on your computer."


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------- register
    def tool(self, *, description: str, params: dict[str, str] | None = None,
             name: str | None = None, risk: Risk = Risk.SAFE, category: str = "general",
             icon: str = "wrench", risk_check: Callable[[dict[str, Any]], Risk] | None = None,
             risk_summary: Callable[[dict[str, Any]], str] | None = None,
             timeout_s: float | None = None,
             precheck: Callable[[dict[str, Any]], None] | None = None,
             schemas: dict[str, dict[str, Any]] | None = None,
             ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        params = params or {}

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            hints = get_type_hints(fn)
            props: dict[str, Any] = {}
            required: list[str] = []
            for pname, p in inspect.signature(fn).parameters.items():
                prop = dict((schemas or {}).get(pname) or _schema_for(hints.get(pname, str)))
                desc = params.get(pname, "")
                if p.default is inspect.Parameter.empty:
                    required.append(pname)
                else:
                    prop["default"] = p.default
                if desc:
                    prop["description"] = desc
                props[pname] = prop
            spec = ToolSpec(
                name=name or fn.__name__, description=description, handler=fn,
                parameters={"type": "object", "properties": props, "required": required},
                risk=risk, category=category, icon=icon, risk_check=risk_check,
                risk_summary=risk_summary, timeout_s=timeout_s, precheck=precheck)
            if spec.name in self._tools:
                raise ValueError(f"Tool '{spec.name}' is registered twice.")
            self._tools[spec.name] = spec
            return fn

        return decorator

    def register_spec(self, spec: ToolSpec) -> None:
        """Register a tool built at runtime (e.g. discovered from an MCP server)."""
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is registered twice.")
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ---------------------------------------------------------------- query
    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    # ------------------------------------------------------------- validate
    @staticmethod
    def validate(spec: ToolSpec, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Coerce argument types and report problems in words the model can act on."""
        props = spec.parameters["properties"]
        clean: dict[str, Any] = {}
        errors: list[str] = []
        for key, value in args.items():
            if key not in props:
                continue  # small models invent extra keys; ignore them
            schema = props[key]
            try:
                value = _coerce(value, schema)
            except (TypeError, ValueError):
                errors.append(f"'{key}' should be a {schema['type']}, got {value!r}.")
                continue
            if "enum" in schema and value not in schema["enum"]:
                errors.append(f"'{key}' must be one of {schema['enum']}, got {value!r}.")
                continue
            clean[key] = value
        for req in spec.parameters["required"]:
            if req not in clean and not any(req in e for e in errors):
                errors.append(f"Missing required argument '{req}'.")
        return clean, errors

    # -------------------------------------------------------------- execute
    async def execute(self, spec: ToolSpec, args: dict[str, Any], *, timeout_s: float,
                      max_output_chars: int) -> ToolResult:
        start = time.perf_counter()
        try:
            if inspect.iscoroutinefunction(spec.handler):
                coro = spec.handler(**args)
            else:
                coro = asyncio.to_thread(spec.handler, **args)
            out = await asyncio.wait_for(coro, timeout=spec.timeout_s or timeout_s)
            ok, text = True, "" if out is None else str(out)
        except asyncio.TimeoutError:
            ok, text = False, f"{spec.name} timed out after {spec.timeout_s or timeout_s:.0f} s."
        except ToolError as e:
            ok, text = False, str(e)
        except Exception as e:  # a broken tool must never crash the agent
            ok, text = False, f"{spec.name} failed: {type(e).__name__}: {e}"
        if len(text) > max_output_chars:
            text = text[:max_output_chars] + f"\n…[output truncated at {max_output_chars} characters]"
        return ToolResult(ok=ok, output=text or "(no output)",
                          duration_s=time.perf_counter() - start)


class ToolError(Exception):
    """Raise inside a tool for an expected failure; the message goes back to the model."""


def _coerce(value: Any, schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "integer":
        if isinstance(value, bool):
            raise TypeError
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value if isinstance(value, int) else int(str(value).strip())
    if kind == "number":
        if isinstance(value, bool):
            raise TypeError
        return value if isinstance(value, (int, float)) else float(str(value).strip())
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in {"true", "1", "yes", "y"}:
            return True
        if s in {"false", "0", "no", "n"}:
            return False
        raise ValueError
    if kind == "string":
        return value if isinstance(value, str) else (
            str(value) if isinstance(value, (int, float)) else _json_text(value))
    if kind == "array":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text[:1] in "[{":  # a JSON list (or object) sent as a string
                from ..llm.parsing import ToolCallParseError, loads_lenient
                try:
                    parsed = loads_lenient(text)
                    return parsed if isinstance(parsed, list) else [parsed]
                except ToolCallParseError as e:
                    raise ValueError from e
            return [v.strip() for v in text.split(",") if v.strip()]
        if isinstance(value, dict):
            return [value]
        raise TypeError
    return value


def _json_text(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


REGISTRY = ToolRegistry()
tool = REGISTRY.tool
