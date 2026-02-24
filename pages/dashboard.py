"""Dashboard page — Notion database selector, overview, status breakdown, alerts, recent activity."""

import streamlit as st

from config import CONTACT_STATUSES, CONTACT_STATUS_LABELS_STRIP, ACTION_STATUS_COLORS, NOTION_DATABASES
from db_ops import (
    get_pipeline_stats,
    get_all_companies,
    get_overdue_followups,
    get_todays_scheduled,
    get_recent_activity,
)
from ui_helpers import lh2_section_header, lh2_kpi_cell, lh2_status_cell, bdg


def render():
    # Notion database selector: same functionality for whichever database is selected
    if NOTION_DATABASES:
        current_id = st.session_state.get("notion_database_id") or (NOTION_DATABASES[0][1] if NOTION_DATABASES else None)
        options = [label for label, _ in NOTION_DATABASES]
        ids = [db_id for _, db_id in NOTION_DATABASES]
        default_idx = ids.index(current_id) if current_id in ids else 0
        selected_label = st.selectbox(
            "Notion database",
            options=options,
            index=default_idx,
            key="notion_database_select",
            help="Choose which Notion database to sync and use. All pages use this selection.",
        )
        if selected_label:
            new_id = NOTION_DATABASES[options.index(selected_label)][1]
            if st.session_state.get("notion_database_id") != new_id:
                st.session_state.notion_database_id = new_id
                st.rerun()
        st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)

    st.markdown(lh2_section_header("OVERVIEW"), unsafe_allow_html=True)
    stats = get_pipeline_stats()
    kpi_items = [
        (len(get_all_companies()), "Companies"),
        (stats.get("outreach_sent", 0), "Active Outreach"),
        (stats.get("meeting_booked", 0), "Meetings"),
        (stats.get("partnership_active", 0), "Partnerships"),
    ]
    st.markdown(
        "<div class='lh2-kpi-strip'>" + "".join(lh2_kpi_cell(n, l) for n, l in kpi_items) + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(lh2_section_header("STATUS BREAKDOWN"), unsafe_allow_html=True)
    status_items = [(stats.get(s, 0), CONTACT_STATUS_LABELS_STRIP.get(s, s)) for s in CONTACT_STATUSES]
    st.markdown(
        "<div class='lh2-status-grid'>" + "".join(lh2_status_cell(v, lbl) for v, lbl in status_items) + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(lh2_section_header("ALERTS"), unsafe_allow_html=True)
    overdue = get_overdue_followups(3)
    todays = get_todays_scheduled()
    for a in overdue[:5]:
        desc = a.get("description", "")
        contact = a.get("contact_name", "?")
        domain = a.get("company_domain", "?")
        preview = f"Overdue: {contact} at {domain}"
        with st.expander(preview, expanded=False):
            st.markdown(f"**Action:** {desc}")
            st.caption(f"Contact: {contact} | Company: {domain}")
    if not overdue:
        st.caption("No overdue follow-ups.")
    for a in todays[:5]:
        desc = a.get("description", "")
        contact = a.get("contact_name", "?")
        preview = f"Today: {contact}"
        with st.expander(preview, expanded=False):
            st.markdown(f"**Action:** {desc}")
            if a.get("company_domain"):
                st.caption(f"Contact: {contact} | Company: {a.get('company_domain')}")
    if not todays:
        st.caption("No tasks due today.")
    st.markdown("<div style='margin-bottom:16px;'></div>", unsafe_allow_html=True)

    st.markdown(lh2_section_header("RECENT ACTIVITY"), unsafe_allow_html=True)
    recent = get_recent_activity(10)
    for a in recent:
        sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
        st.markdown(
            f"<div class='c' style='padding:10px 16px;'>"
            f"<div style='display:flex;justify-content:space-between;'>"
            f"<span class='c-l'>{a.get('company_domain','?')} · {a.get('contact_name','?')} · {a['action_type']}</span>"
            f"{bdg(a['status'], sc)}</div>"
            f"<div style='font-size:12px;margin-top:4px;'>{a['description'][:200]}</div></div>",
            unsafe_allow_html=True,
        )
    if not recent:
        st.markdown(
            "<div style='text-align:center;padding:40px;color:var(--text-3);'>No activity yet.</div>",
            unsafe_allow_html=True,
        )
