"""Shared objects that tools need, set up once by the CLI/API and replaceable in tests."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import get_settings
from ..workspace import Workspace
from .registry import ToolError

if TYPE_CHECKING:
    from ..memory import MemorySystem

_workspace: Workspace | None = None
_memory: MemorySystem | None = None


def workspace() -> Workspace:
    global _workspace
    if _workspace is None:
        _workspace = Workspace(get_settings().workspace_dir)
    return _workspace


def set_workspace(ws: Workspace) -> None:
    global _workspace
    _workspace = ws


def memory() -> MemorySystem:
    if _memory is None:
        raise ToolError("Memory isn't available in this session.")
    return _memory


def set_memory(system: MemorySystem | None) -> None:
    global _memory
    _memory = system
