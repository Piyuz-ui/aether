"""Skills: slash commands written as plain Markdown playbooks in backend/skills/.

    backend/skills/brief.md   →  /brief

A skill file is a short front matter block, then the instructions AETHER follows:

    ---
    description: Morning briefing from your classes, deadlines and memories
    usage: /brief
    tools: manage_timetable, manage_tasks, recall_memory   # always offered to the model
    reasoning: high                                        # optional: low | medium | high
    ---
    Instructions… Use {{input}} where the text typed after the command should go.

Add or edit files there; they're read fresh every time, no restart needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    prompt: str
    usage: str = ""
    tools: list[str] = field(default_factory=list)
    reasoning: str | None = None

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "usage": self.usage or f"/{self.name}",
                "tools": self.tools, "reasoning": self.reasoning}


def parse_skill(name: str, text: str) -> Skill | None:
    meta: dict[str, str] = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.S)
    if m:
        for line in m.group(1).splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        body = m.group(2)
    body = body.strip()
    if not body:
        return None
    reasoning = meta.get("reasoning", "").lower() or None
    return Skill(
        name=name,
        description=meta.get("description") or body.splitlines()[0][:100],
        prompt=body,
        usage=meta.get("usage", ""),
        tools=[t.strip() for t in meta.get("tools", "").strip("[]").split(",") if t.strip()],
        reasoning=reasoning if reasoning in ("low", "medium", "high") else None,
    )


def list_skills(folder: Path = SKILLS_DIR) -> list[Skill]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.md")):
        name = path.stem.lower()
        if not NAME.match(name):
            continue
        try:
            skill = parse_skill(name, path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if skill:
            out.append(skill)
    return out


def load_skill(name: str, folder: Path = SKILLS_DIR) -> Skill | None:
    name = name.lower().lstrip("/")
    return next((s for s in list_skills(folder) if s.name == name), None) if NAME.match(name) else None
