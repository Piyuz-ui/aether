"""Web interface (Streamlit). Start with:  python main.py web"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from agent import Agent, Settings  # noqa: E402
from agent.config import DOCS_DIR  # noqa: E402
from agent.storage import get_store  # noqa: E402
from agent.tools.personal import note_path  # noqa: E402

settings = Settings()
st.set_page_config(page_title=f"{settings.agent_name} · local AI agent", page_icon="🤖",
                   layout="wide")

state = st.session_state
state.setdefault("messages", [])        # [{"role", "content", "tools": [...]}]
state.setdefault("allow_risky", False)


def confirm(name: str, args: dict) -> bool:
    # Streamlit can't pause mid-run for a dialog, so risky tools follow the sidebar switch.
    return bool(state.allow_risky)


if "agent" not in state:
    state.agent = Agent(settings, confirm=confirm)
agent: Agent = state.agent

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title(f"🤖 {settings.agent_name}")
    up = agent.llm.is_up()
    st.caption(("🟢 Ollama online" if up else "🔴 Ollama not running") + " · fully local")

    if up:
        models = agent.llm.list_models()
        idx = models.index(agent.model) if agent.model in models else 0
        agent.model = st.selectbox("Model", models, index=idx)

    state.allow_risky = st.toggle(
        "Allow file writes, commands & deletes", value=state.allow_risky,
        help="When off, the agent can only read. Turn on only when you trust the request.")

    if st.button("🧹 New conversation", use_container_width=True):
        agent.reset()
        state.messages = []
        st.rerun()

    st.subheader("📚 Documents")
    uploads = st.file_uploader("Add PDFs or notes", type=["pdf", "txt", "md"],
                               accept_multiple_files=True)
    for f in uploads or []:
        target = DOCS_DIR / Path(f.name).name
        if not target.exists():
            target.write_bytes(f.getbuffer())
            st.success(f"Added {f.name}")
    docs = sorted(p.name for p in DOCS_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    st.caption("\n".join(f"• {d}" for d in docs) or "No documents yet.")

    st.subheader("✅ Tasks")
    store = get_store()
    for t in store.list_tasks():
        label = t["title"] + (f"  ·  {t['due']}" if t["due"] else "")
        if st.checkbox(label, key=f"task-{t['id']}"):
            store.set_task_done(t["id"])
            st.rerun()

    st.subheader("📝 Notes")
    notes = store.recent_notes(50)
    if not notes:
        st.caption("No notes yet. Try: “Make notes from my PDF”.")
    for n in notes:
        with st.expander(f"#{n['id']} {n['title']}"):
            st.caption(n["created"])
            st.markdown(n["body"])
            st.download_button("⬇️ Download .md", f"# {n['title']}\n\n{n['body']}\n",
                               file_name=note_path(n["id"], n["title"]).name,
                               key=f"dl-{n['id']}", use_container_width=True)

    with st.expander("🧠 Memory"):
        for m in agent.memory.all():
            st.write(f"#{m['id']} {m['text']}")


# --------------------------------------------------------------------- chat
def render_tools(tool_log: list[dict]) -> None:
    if not tool_log:
        return
    with st.expander(f"🔧 Used {len(tool_log)} tool call(s)"):
        for item in tool_log:
            st.markdown(f"**{item['name']}** `{json.dumps(item['args'], ensure_ascii=False)[:300]}`")
            st.code(item.get("result", "")[:1500], language=None)


def render_notes(notes: list[dict], reply: str) -> None:
    for n in notes:
        if f"Saved as note #{n['id']}" in reply:
            continue  # the reply already contains the full note text
        with st.container(border=True):
            st.markdown(f"#### 📝 {n['title']}")
            st.markdown(n["body"])
            st.download_button("⬇️ Download .md", f"# {n['title']}\n\n{n['body']}\n",
                               file_name=note_path(n["id"], n["title"]).name,
                               key=f"chat-dl-{n['id']}-{len(state.messages)}")


if not state.messages:
    st.markdown(f"### Hi! I'm {settings.agent_name}.\n"
                "I can **study** with your PDFs, track **tasks & deadlines**, "
                "do **web research**, and help with **code** — all running locally.")

for msg in state.messages:
    with st.chat_message(msg["role"]):
        render_tools(msg.get("tools", []))
        st.markdown(msg["content"])
        render_notes(msg.get("notes", []), msg["content"])

if prompt := st.chat_input(f"Message {settings.agent_name}…"):
    state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        tool_log: list[dict] = []
        with st.status("Thinking…", expanded=False) as status:
            def on_event(kind: str, payload: dict) -> None:
                if kind == "tool_call":
                    tool_log.append({"name": payload["name"], "args": payload["args"]})
                    status.update(label=f"Using {payload['name']}…")
                elif kind == "tool_result" and tool_log:
                    tool_log[-1]["result"] = payload["result"]

            agent.on_event = on_event
            reply = agent.chat(prompt)
            status.update(label="Done", state="complete")
        render_tools(tool_log)
        st.markdown(reply)
        render_notes(agent.last_notes, reply)
    state.messages.append({"role": "assistant", "content": reply, "tools": tool_log,
                           "notes": agent.last_notes})
    st.rerun()
