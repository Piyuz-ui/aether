"""Web interface (Streamlit). Start with:  python main.py web"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agent import Agent, Settings  # noqa: E402
from agent.config import DOCS_DIR  # noqa: E402
from agent.metrics import fmt_seconds  # noqa: E402
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

    if st.button("🧹 New conversation", width="stretch"):
        agent.reset()
        state.messages = []
        st.rerun()

    if agent.active_docs:
        with st.container(border=True):
            st.markdown(":material/description: **Chatting about**  \n" +
                        "  \n".join(agent.active_docs))
            if st.button("Stop focusing on this document", width="stretch"):
                agent.active_docs = []
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
                               key=f"dl-{n['id']}", width="stretch")

    st.subheader(":material/monitoring: Monitoring")
    session = agent.monitor.session()
    last = agent.last_stats
    if not session["turns"]:
        st.caption("Stats appear after your first message.")
    else:
        with st.container(horizontal=True):
            st.metric("Last reply", fmt_seconds(last.wall_s) if last else "–", border=True)
            st.metric("Avg reply", fmt_seconds(session["avg_s"]), border=True)
        with st.container(horizontal=True):
            st.metric("Session tokens", f"{session['total_tokens']:,}", border=True)
            st.metric("Speed", f"{last.tokens_per_s:.0f} tok/s" if last else "–", border=True)
        if last:
            pct = min(last.context_pct, 100.0)
            st.progress(pct / 100, text=f"Context: {last.context_used:,} / "
                                        f"{last.context_limit:,} tokens ({pct:.0f}%)")
            if pct > 85:
                st.warning("Context is nearly full. Older messages may be forgotten; "
                           "start a new conversation if answers get worse.",
                           icon=":material/warning:")
        with st.expander("Details"):
            st.caption(f"{session['turns']} repl{'y' if session['turns'] == 1 else 'ies'} · {session['prompt_tokens']:,} tokens in · "
                       f"{session['output_tokens']:,} out · {session['tool_calls']} tool calls · "
                       f"total wait {fmt_seconds(session['total_s'])}")
            rows = [{"Reply": i + 1, "Seconds": round(t.wall_s, 1)}
                    for i, t in enumerate(agent.monitor.turns)]
            st.bar_chart(pd.DataFrame(rows), x="Reply", y="Seconds", height=160)
            if last and last.tools:
                st.caption("Last reply's tools: " +
                           ", ".join(f"{n} {fmt_seconds(s)}" for n, s in last.tools))
            all_time = agent.monitor.history()
            if all_time:
                st.caption(f"All time: {len(all_time)} replies, "
                           f"{sum(r['prompt_tokens'] + r['output_tokens'] for r in all_time):,} "
                           f"tokens (logged in data/metrics.jsonl)")

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


def save_upload(f) -> Path:
    """Store a file attached in the chat in documents/ (replacing an older copy)."""
    target = DOCS_DIR / Path(f.name).name
    target.write_bytes(f.getbuffer())
    return target


def render_files(names: list[str]) -> None:
    if names:
        st.caption("  ".join(f":material/attach_file: {n}" for n in names))


if not state.messages:
    st.markdown(f"### Hi! I'm {settings.agent_name}.\n"
                "I can **study** with your PDFs, track **tasks & deadlines**, "
                "do **web research**, and help with **code**, all running locally.\n\n"
                "Attach a PDF with the :material/attach_file: button in the message box, "
                "then ask anything about it.")

for msg in state.messages:
    with st.chat_message(msg["role"]):
        render_files(msg.get("files", []))
        render_tools(msg.get("tools", []))
        st.markdown(msg["content"])
        render_notes(msg.get("notes", []), msg["content"])
        if msg.get("stats"):
            st.caption(f":material/speed: {msg['stats']}")

submission = st.chat_input(f"Message {settings.agent_name}, or attach a document…", key="chat_box",
                           accept_file="multiple", file_type=["pdf", "txt", "md"])
if submission:
    attachments = [save_upload(f) for f in submission.files]
    names = [p.name for p in attachments]
    prompt = submission.text.strip() or (
        "I've attached a document. Give me a short summary of what it covers."
        if attachments else "")
    state.messages.append({"role": "user", "content": prompt, "files": names})
    with st.chat_message("user"):
        render_files(names)
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
            reply = agent.chat(prompt, attachments=attachments or None)
            status.update(label="Done", state="complete")
        render_tools(tool_log)
        st.markdown(reply)
        render_notes(agent.last_notes, reply)
        stats = agent.last_stats.summary() if agent.last_stats and agent.last_stats.calls else ""
        if stats:
            st.caption(f":material/speed: {stats}")
    state.messages.append({"role": "assistant", "content": reply, "tools": tool_log,
                           "notes": agent.last_notes, "stats": stats})
    st.rerun()
