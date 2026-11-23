"""Importing this package registers every built-in tool."""
from . import calculator, notes, system, web  # noqa: F401  (import = register)
from .registry import REGISTRY, Risk, ToolError, ToolRegistry, ToolResult, ToolSpec, tool

__all__ = ["REGISTRY", "Risk", "ToolError", "ToolRegistry", "ToolResult", "ToolSpec", "tool"]
