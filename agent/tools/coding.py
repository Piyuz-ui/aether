"""Coding tools: explore, read, write and run code. Relative paths resolve inside
workspace/. Writing files and running commands always ask the user first."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..config import WORKSPACE_DIR
from .base import tool

MAX_CHARS = 12000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist", "build"}


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (WORKSPACE_DIR / p).resolve()


@tool("List files and folders in a directory (relative paths are inside the workspace).",
      params={"path": "Directory path"}, category="coding")
def list_directory(path: str = ".") -> str:
    d = _resolve(path)
    if not d.is_dir():
        return f"Not a directory: {d}"
    entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = [f"{d}:"]
    for p in entries[:200]:
        if p.name in SKIP_DIRS:
            continue
        lines.append(f"  {p.name}/" if p.is_dir() else f"  {p.name}  ({p.stat().st_size} B)")
    return "\n".join(lines)


@tool("Read a text/code file with line numbers.",
      params={"path": "File path", "start_line": "First line (1-based)",
              "end_line": "Last line; 0 = to the end"},
      category="coding")
def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    f = _resolve(path)
    if not f.is_file():
        return f"File not found: {f}"
    lines = f.read_text(errors="ignore").splitlines()
    end = len(lines) if end_line <= 0 else min(end_line, len(lines))
    body = "\n".join(f"{i:>5} {lines[i - 1]}" for i in range(max(1, start_line), end + 1))
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS] + "\n[truncated — read a smaller line range]"
    return f"{f} ({len(lines)} lines)\n{body}"


@tool("Create or overwrite a file with the given content.",
      params={"path": "File path", "content": "Full new file content"},
      category="coding", dangerous=True)
def write_file(path: str, content: str) -> str:
    f = _resolve(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    return f"Wrote {len(content)} characters to {f}"


@tool("Search files under a directory for a regex pattern (like grep).",
      params={"pattern": "Regular expression", "path": "Directory to search"},
      category="coding")
def search_files(pattern: str, path: str = ".") -> str:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"Bad regex: {e}"
    root = _resolve(path)
    hits = []
    for f in root.rglob("*"):
        if any(part in SKIP_DIRS for part in f.parts) or not f.is_file():
            continue
        if f.stat().st_size > 1_000_000:
            continue
        try:
            for n, line in enumerate(f.read_text(errors="strict").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= 100:
                        return "\n".join(hits) + "\n[stopped at 100 matches]"
        except (UnicodeDecodeError, OSError):
            continue
    return "\n".join(hits) if hits else "No matches."


@tool("Run a shell command (e.g. run a script or tests) and return its output. "
      "Times out after 120 seconds.",
      params={"command": "The shell command", "cwd": "Working directory"},
      category="coding", dangerous=True)
def run_command(command: str, cwd: str = ".") -> str:
    d = _resolve(cwd)
    try:
        r = subprocess.run(command, shell=True, cwd=d, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "Command timed out after 120 s."
    out = (r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr else "")).strip()
    if len(out) > MAX_CHARS:
        out = out[:3000] + "\n…[middle truncated]…\n" + out[-6000:]
    return f"exit code {r.returncode}\n{out or '(no output)'}"
