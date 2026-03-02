"""Advisor page — chat for company suggestions, outreach plans, advice."""

from datetime import datetime
import streamlit as st

from db_ops import get_all_advisor_conversations, load_advisor_conversation, save_advisor_conversation
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
        past_convs = get_all_advisor_conversations(limit=20)
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
    
    # ── Sender selection for outreach tone/voice ──
    st.markdown("---")
    from sender_profiles import get_all_sender_profiles, format_sender_display_name
    sender_profiles = get_all_sender_profiles()
    sender_emails = [None] + [p["email"] for p in sender_profiles]
    
    if "selected_sender" not in st.session_state:
        st.session_state.selected_sender = None
    
    sender_col1, sender_col2 = st.columns([2, 1])
    with sender_col1:
        selected_sender_email = st.selectbox(
            "🎭 Outreach Voice (for AI-generated drafts)",
            options=sender_emails,
            format_func=lambda x: "Select sender for outreach..." if x is None else format_sender_display_name(
                next((p for p in sender_profiles if p["email"] == x), None)
            ),
            index=sender_emails.index(st.session_state.selected_sender) if st.session_state.selected_sender in sender_emails else 0,
            key="sender_voice_select_advisor",
            help="Choose who is sending the outreach. This affects the tone, voice, and signature in AI-generated drafts."
        )
        st.session_state.selected_sender = selected_sender_email
    
    with sender_col2:
        if st.session_state.selected_sender:
            selected_profile = next((p for p in sender_profiles if p["email"] == st.session_state.selected_sender), None)
            if selected_profile:
                st.markdown(
                    f"<div style='padding:12px 0;'>"
                    f"<div style='font-size:11px;color:var(--text-3);'>{selected_profile.get('voice_type', 'professional').replace('_', ' ').title()}</div>"
                    f"<div style='font-size:10px;color:var(--text-3);margin-top:4px;'>Source: {selected_profile.get('source', 'unknown')}</div></div>",
                    unsafe_allow_html=True)
        else:
            st.markdown(
                f"<div style='padding:12px 0;'>"
                f"<div style='font-size:11px;color:var(--text-warning);'>⚠️ No sender selected</div>"
                f"<div style='font-size:10px;color:var(--text-3);margin-top:4px;'>Select a sender for outreach drafts</div></div>",
                unsafe_allow_html=True)
    st.markdown("---")

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
                    sender_email=st.session_state.selected_sender,
                )
            st.markdown(reply)
        st.session_state.advisor_messages.append({"role": "assistant", "content": reply})
        save_advisor_conversation(uid, st.session_state.advisor_messages)
        st.rerun()
