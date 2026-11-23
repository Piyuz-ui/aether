"""High-risk tools. Every one of them is REQUIRES_APPROVAL: the agent pauses and shows
the exact action and a plain-English risk summary, and nothing runs until the user approves.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from typing import Any

from .context import workspace
from .registry import Risk, ToolError, tool

COMMAND_TIMEOUT_S = 30
OUTPUT_LIMIT = 8000

# Patterns that deserve a stronger warning in the approval card.
DANGEROUS = [
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "recursively force-deletes files"),
    (r"\bsudo\b", "runs with administrator rights"),
    (r"(curl|wget)[^|]*\|\s*(ba|z)?sh", "downloads and runs a script from the internet"),
    (r"\bmkfs|\bdd\s+if=", "can wipe a disk"),
    (r">\s*/dev/|\bchmod\s+-R|\bchown\s+-R", "changes system files or permissions"),
    (r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-z]*f)", "changes or discards git history"),
    (r"\bpip\s+install|\bnpm\s+(i|install)\b|\bbrew\s+install", "installs software"),
]


def _danger_notes(command: str) -> list[str]:
    return [why for pattern, why in DANGEROUS if re.search(pattern, command, re.IGNORECASE)]


def _clip(text: str) -> str:
    return text if len(text) <= OUTPUT_LIMIT else (
        text[:2000] + "\n…[middle truncated]…\n" + text[-(OUTPUT_LIMIT - 2000):])


async def _run(argv: list[str], *, stdin: bytes | None = None) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TERM", "USER"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=workspace().root, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), timeout=COMMAND_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolError(f"Stopped after {COMMAND_TIMEOUT_S} s (time limit).") from None
    return proc.returncode or 0, out.decode(errors="replace")


# ----------------------------------------------------------------- run_python
def _python_summary(args: dict[str, Any]) -> str:
    code = str(args.get("code", ""))
    lines = code.count("\n") + 1
    extra = ""
    if re.search(r"\b(os|shutil|subprocess|pathlib)\b|open\(", code):
        extra = " The code uses file or system access, so read it carefully."
    return (f"Run a {lines}-line Python script on your computer (inside the workspace folder, "
            f"{COMMAND_TIMEOUT_S} s limit). Python code can do anything your user account can.{extra}")


@tool(description=(
    "Run a Python 3 script and return its printed output. For data analysis, simulations, "
    "or anything too complex for python_calculator. Runs in the workspace folder with a "
    "30-second limit. The user must approve each run."),
    params={"code": "Complete Python script. Use print() to show results."},
    risk=Risk.REQUIRES_APPROVAL, category="system", icon="square-terminal",
    risk_summary=_python_summary, timeout_s=COMMAND_TIMEOUT_S + 5)
async def run_python(code: str) -> str:
    # -I: isolated mode (ignores env vars and user site-packages, keeps cwd off sys.path).
    code_rc, out = await _run([sys.executable, "-I", "-"], stdin=code.encode())
    return f"exit code {code_rc}\n{_clip(out.strip()) or '(no output)'}"


# ----------------------------------------------------------- run_shell_command
def _shell_summary(args: dict[str, Any]) -> str:
    command = str(args.get("command", ""))
    notes = _danger_notes(command)
    base = (f"Run a shell command in the workspace folder with your user's permissions "
            f"({COMMAND_TIMEOUT_S} s limit). Shell commands can read, change or delete any of "
            "your files.")
    return base + (f" ⚠ This command {', and '.join(notes)}." if notes else "")


@tool(description=(
    "Run a shell command (zsh/bash) in the workspace folder and return its output. "
    "HIGH-RISK: the user must approve every command. Prefer the dedicated tools when one fits."),
    params={"command": "The exact shell command to run"},
    risk=Risk.REQUIRES_APPROVAL, category="system", icon="terminal",
    risk_summary=_shell_summary, timeout_s=COMMAND_TIMEOUT_S + 5)
async def run_shell_command(command: str) -> str:
    shell = shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
    code, out = await _run([shell, "-c", command])
    return f"exit code {code}\n{_clip(out.strip()) or '(no output)'}"


# ----------------------------------------------------------------- delete_file
def _delete_summary(args: dict[str, Any]) -> str:
    path = str(args.get("path", ""))
    try:
        target = workspace().resolve(path)
    except Exception as e:
        return f"Delete '{path}'. ({e})"
    if target.is_dir():
        count = sum(1 for _ in target.rglob("*"))
        return (f"Permanently delete the folder '{workspace().relative(target)}' and the "
                f"{count:,} item(s) inside it. This can't be undone.")
    size = target.stat().st_size if target.exists() else 0
    return (f"Permanently delete '{workspace().relative(target)}' ({size:,} bytes). "
            "It won't go to the Trash and can't be undone.")


def _delete_precheck(args: dict[str, Any]) -> None:
    path = str(args.get("path", ""))
    target = workspace().resolve(path)
    if target == workspace().root:
        raise ToolError("Refusing to delete the whole workspace.")
    if not target.exists():
        folder = target.parent if target.parent.is_dir() else workspace().root
        names = sorted(p.name for p in folder.iterdir() if not p.name.startswith("."))[:30]
        listing = ", ".join(names) if names else "(empty)"
        raise ToolError(f"'{path}' doesn't exist. Files in "
                        f"{workspace().relative(folder)}/: {listing}")


@tool(description="Permanently delete a file or folder inside the workspace. HIGH-RISK: "
                  "the user must approve.",
      params={"path": "Path inside the workspace, e.g. 'notes/old.md'"},
      risk=Risk.REQUIRES_APPROVAL, category="system", icon="trash-2",
      risk_summary=_delete_summary, precheck=_delete_precheck)
def delete_file(path: str) -> str:
    target = workspace().resolve(path, must_exist=True)
    if target == workspace().root:
        raise ToolError("Refusing to delete the whole workspace.")
    rel = workspace().relative(target)
    if target.is_dir():
        shutil.rmtree(target)
        return f"Deleted folder {rel}."
    target.unlink()
    return f"Deleted {rel}."
