"""Importing this package registers every built-in tool.

The registry names are bound first, so modules imported below (which pull in memory →
engine → tools) always find them, even while this package is still initialising.
"""
from .registry import REGISTRY, Risk, ToolError, ToolRegistry, ToolResult, ToolSpec, tool

from . import calculator, documents, knowledge, memory_tools, notes, planner, study, system, web  # noqa: E402,F401,I001

__all__ = ["REGISTRY", "Risk", "ToolError", "ToolRegistry", "ToolResult", "ToolSpec", "tool"]
