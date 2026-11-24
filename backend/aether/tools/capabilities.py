"""The meta-tool: lets the agent see every tool pack and pull one in mid-task.

Small local models are offered only the 6–8 tools a request seems to need. If one realises it
needs something else ("I should check the web too"), it calls list_available_capabilities with
that pack, and the engine adds the pack's tools for the rest of the task.
"""
from __future__ import annotations

from .registry import REGISTRY, tool


@tool(description=(
    "See every tool pack you can use, or load one. Call it with no pack to list the packs; call it "
    "with a pack id (system_mac, knowledge_rag, code_data, research_web, productivity, mcp_bridge) "
    "to make all of that pack's tools available to you from now on in this task."),
    params={"pack": "Optional: the pack to load, e.g. research_web"},
    category="meta", icon="layers")
async def list_available_capabilities(pack: str = "") -> str:
    from tool_router import PACKS, meta_for, packs_summary, state

    specs = REGISTRY.all()
    off = set(state()["disabled"])
    pack = pack.strip().lower()
    if pack:
        if pack not in PACKS:
            return f"There's no pack '{pack}'. Packs: {', '.join(PACKS)}."
        if pack in off:
            return f"The {PACKS[pack].label} pack is switched off by the user (Settings → Tool packs)."
        lines = [f"Loaded the {PACKS[pack].label} pack. These tools are now available to you:"]
        for s in specs:
            if s.name != "list_available_capabilities" and meta_for(s.name, s).pack == pack:
                lines.append(f"- {s.name} ({meta_for(s.name, s).level.value}): {s.description[:160]}")
        return "\n".join(lines) if len(lines) > 1 else f"The {PACKS[pack].label} pack has no tools right now."
    lines = ["Tool packs (call me with a pack id to load one):"]
    for p in packs_summary(specs):
        status = "off" if not p["enabled"] else "pinned" if p["pinned"] else "on"
        names = ", ".join(t["name"] for t in p["tools"]) or "none yet"
        lines.append(f"- {p['id']} · {p['label']} [{status}]: {p['description']} Tools: {names}")
    return "\n".join(lines)
