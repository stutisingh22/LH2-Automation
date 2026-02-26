"""History page — global insights, analytics, all sessions."""

import streamlit as st

from db_ops import (
    get_all_global_insights,
    get_all_actions,
    get_all_sessions,
    get_message_count,
    get_session_actions,
    load_messages,
    delete_session,
)
from ui_helpers import lh2_section_header, bdg, lh2_kpi_cell
from ui_helpers import has_perm


def render():
    user = st.session_state.get("user") or {}
    uid = user.get("id")
    if not uid:
        return
    ginsights = get_all_global_insights(limit=8)
    if ginsights:
        st.markdown(lh2_section_header("GLOBAL PATTERNS"), unsafe_allow_html=True)
        for g in ginsights:
            cc2 = "green" if g["confidence_score"] >= 0.7 else "yellow" if g["confidence_score"] >= 0.4 else "gray"
            conf_lbl = f"conf: {g['confidence_score']:.1f}"
            st.markdown(
                f"<div class='c' style='padding:12px 16px;'><div style='display:flex;justify-content:space-between;'>"
                f"<span style='font-size:13px;'>{g['insight']}</span>"
                f"{bdg(conf_lbl, cc2)}</div></div>",
                unsafe_allow_html=True,
            )

    all_ua = get_all_actions(limit=200)
    if all_ua:
        st.markdown(lh2_section_header("ANALYTICS"), unsafe_allow_html=True)
        total = len(all_ua)
        sent = sum(
            1
            for a in all_ua
            if a["status"]
            in ("sent", "completed", "replied_positive", "replied_negative", "meeting_booked", "deal_closed")
        )
        pos = sum(1 for a in all_ua if a["status"] in ("replied_positive", "meeting_booked", "deal_closed"))
        mtg = sum(1 for a in all_ua if a["status"] == "meeting_booked")
        nr = sum(1 for a in all_ua if a["status"] == "no_response")
        rate = f"{(pos / sent * 100):.0f}%" if sent else "—"
        analytics_items = [(total, "Total"), (sent, "Sent"), (rate, "Positive Rate"), (mtg, "Meetings"), (nr, "No Response")]
        st.markdown(
            "<div class='lh2-kpi-strip'>" + "".join(lh2_kpi_cell(n, l) for n, l in analytics_items) + "</div>",
            unsafe_allow_html=True,
        )

    st.markdown(lh2_section_header("ALL SESSIONS"), unsafe_allow_html=True)
    sessions = get_all_sessions()
    for sx in sessions:
        sh1, sh2, sh3 = st.columns([2, 1.5, 1])
        with sh1:
            mt = " · Memory" if sx.get("strategic_summary") else ""
            st.markdown(
                f"<div class='c'><div class='c-l'>{sx.get('company_domain','?')}</div>"
                f"<div class='c-v'>{sx.get('contact_name','?')}{mt}</div></div>",
                unsafe_allow_html=True,
            )
        with sh2:
            mc2 = get_message_count(sx["id"])
            ac2 = len(get_session_actions(sx["id"]))
            st.markdown(
                f"<div class='c'><div class='c-l'>Activity</div><div class='c-v'>{mc2} msgs · {ac2} actions</div></div>",
                unsafe_allow_html=True,
            )
        with sh3:
            b1, b2 = st.columns(2)
            with b1:
                if st.button("Open", key=f"ho_{sx['id']}"):
                    st.session_state.sel_company = sx["company_id"]
                    st.session_state.sel_contact = sx["contact_id"]
                    st.session_state.session_id = sx["id"]
                    st.session_state.chat_messages = load_messages(sx["id"])
                    st.session_state.auto_brief_text = None
                    st.session_state.current_tab = "INTELLIGENCE"
                    st.rerun()
            with b2:
                if has_perm("can_delete_sessions"):
                    if st.button("Del", key=f"hd_{sx['id']}"):
                        delete_session(sx["id"])
                        if st.session_state.session_id == sx["id"]:
                            st.session_state.session_id = None
                            st.session_state.chat_messages = []
                        st.rerun()
    if not sessions:
        st.markdown(
            "<div style='text-align:center;padding:40px;color:var(--text-3);'>No sessions.</div>",
            unsafe_allow_html=True,
        )
