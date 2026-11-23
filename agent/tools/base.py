"""Tool registry. Decorate a plain Python function with @tool and it becomes something
the LLM can call: the JSON schema is generated from the function's signature."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean",
               list: "array", dict: "object"}


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict
    category: str = "general"
    dangerous: bool = False  # needs the user's OK before it runs

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


@dataclass
class ToolContext:
    """Shared objects tools may need (set by the Agent at startup)."""
    memory: Any = None
    settings: Any = None


CONTEXT = ToolContext()
REGISTRY: dict[str, Tool] = {}


def tool(description: str, *, params: dict[str, str] | None = None,
         category: str = "general", dangerous: bool = False):
    """Register a function as a tool.

    params maps argument name -> human description shown to the model.
    """
    params = params or {}

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        hints = get_type_hints(func)
        properties: dict[str, dict] = {}
        required: list[str] = []
        for name, p in sig.parameters.items():
            py_type = hints.get(name, str)
            prop: dict[str, Any] = {"type": _JSON_TYPES.get(py_type, "string")}
            if name in params:
                prop["description"] = params[name]
            if p.default is inspect.Parameter.empty:
                required.append(name)
            else:
                prop["description"] = (prop.get("description", "") +
                                       f" (default: {p.default!r})").strip()
            properties[name] = prop
        REGISTRY[func.__name__] = Tool(
            name=func.__name__, description=description, func=func,
            parameters={"type": "object", "properties": properties, "required": required},
            category=category, dangerous=dangerous)
        return func

    return decorator


def coerce_args(t: Tool, args: dict) -> dict:
    """Small models sometimes send "5" for an integer — fix types and drop unknown keys."""
    out = {}
    props = t.parameters["properties"]
    for key, value in (args or {}).items():
        if key not in props:
            continue
        kind = props[key]["type"]
        try:
            if kind == "integer" and not isinstance(value, int):
                value = int(value)
            elif kind == "number" and not isinstance(value, (int, float)):
                value = float(value)
            elif kind == "boolean" and isinstance(value, str):
                value = value.strip().lower() in {"true", "1", "yes"}
        except (TypeError, ValueError):
            pass
        out[key] = value
    return out
