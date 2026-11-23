"""Shared objects that tools need, set up once and replaceable in tests."""
from __future__ import annotations

from ..config import get_settings
from ..workspace import Workspace

_workspace: Workspace | None = None


def workspace() -> Workspace:
    global _workspace
    if _workspace is None:
        _workspace = Workspace(get_settings().workspace_dir)
    return _workspace


def set_workspace(ws: Workspace) -> None:
    global _workspace
    _workspace = ws
