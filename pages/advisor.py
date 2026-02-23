"""Advisor page — chat for company suggestions, outreach plans, advice."""

from datetime import datetime
import streamlit as st

from db_ops import get_advisor_conversations, load_advisor_conversation, save_advisor_conversation
from ai_engine import advisor_chat
from ui_helpers import sh


def render():
    user = st.session_state.get("user") or {}
    uid = user.get("id")
    if not uid:
        return
    h1, h2 = st.columns([3, 1])
    with h1:
        st.markdown(sh("ADVISOR"), unsafe_allow_html=True)
        st.caption("Ask for company suggestions, outreach plans, or advice. I remember our conversation and your pipeline.")
    with h2:
        past_convs = get_advisor_conversations(uid, limit=20)
        if past_convs:

            def format_conv_option(conv_id):
                if conv_id is None:
                    return "Current Chat"
                conv = next((c for c in past_convs if c["id"] == conv_id), None)
                if not conv:
                    return ""
                updated = datetime.fromisoformat(conv["updated_at"])
                now = datetime.utcnow()
                diff = now - updated
                if diff.days == 0:
                    time_str = f"{diff.seconds // 60}m ago" if diff.seconds < 3600 else f"{diff.seconds // 3600}h ago"
                elif diff.days == 1:
                    time_str = "Yesterday"
                elif diff.days < 7:
                    time_str = f"{diff.days}d ago"
                else:
                    time_str = updated.strftime("%b %d")
                return f"{conv['title']} · {time_str} · {conv['msg_count']} msgs"

            def on_history_select():
                selected = st.session_state.advisor_history_select
                if selected:
                    loaded = load_advisor_conversation(selected)
                    if loaded:
                        st.session_state.advisor_messages = loaded["messages"]
                        st.session_state.advisor_loaded_conv_id = selected

            selected_conv = st.selectbox(
                "History",
                options=[None] + [c["id"] for c in past_convs],
                format_func=format_conv_option,
                key="advisor_history_select",
                on_change=on_history_select,
                index=0,
            )

    if "advisor_messages" not in st.session_state:
        st.session_state.advisor_messages = []

    for msg in st.session_state.advisor_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Ask for companies to reach out to, plans, or advice...")
    if prompt:
        st.session_state.advisor_messages.append({"role": "user", "content": prompt})
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                reply = advisor_chat(
                    uid,
                    prompt,
                    [{"role": m["role"], "content": m["content"]} for m in st.session_state.advisor_messages[:-1]],
                )
            st.markdown(reply)
        st.session_state.advisor_messages.append({"role": "assistant", "content": reply})
        save_advisor_conversation(uid, st.session_state.advisor_messages)
        st.rerun()
