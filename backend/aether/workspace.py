"""Path jail. Every file tool goes through Workspace.resolve(), so the agent can't
touch anything outside ./workspace: no `../`, no absolute paths elsewhere, no symlink escapes."""
from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    """Raised when a path is invalid or points outside the workspace."""


class Workspace:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise WorkspaceError("Path is empty.")
        if "\x00" in path:
            raise WorkspaceError("Path contains a null byte.")
        raw = Path(path.strip()).expanduser()
        candidate = raw if raw.is_absolute() else self.root / raw
        # resolve() follows symlinks, so a link pointing outside is caught here too.
        resolved = candidate.resolve()
        if resolved != self.root and not resolved.is_relative_to(self.root):
            raise WorkspaceError(f"'{path}' is outside the workspace. Only files inside "
                                 f"{self.root} can be used.")
        if must_exist and not resolved.exists():
            raise WorkspaceError(f"'{self.relative(resolved)}' doesn't exist.")
        return resolved

    def relative(self, path: Path) -> str:
        try:
            rel = path.resolve().relative_to(self.root)
        except ValueError:
            return str(path)
        return str(rel) if str(rel) != "." else "."
