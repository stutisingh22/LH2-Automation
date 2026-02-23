"""Timeline page — actions timeline for selected company/contact."""

import streamlit as st

from db_ops import get_company, get_contact, get_timeline_actions
from config import ACTION_STATUS_COLORS
from ui_helpers import sh, tl_item


def render():
    cid = st.session_state.sel_company
    if not cid:
        st.markdown(
            "<div style='text-align:center;padding:60px 0;color:var(--text-3);'>Select a company first.</div>",
            unsafe_allow_html=True,
        )
        return
    comp = get_company(cid) or {}
    ctid = st.session_state.sel_contact
    fl = f"TIMELINE — {comp.get('domain','')}"
    if ctid:
        ct = get_contact(ctid) or {}
        fl += f" / {ct.get('name','')}"
    st.markdown(sh(fl), unsafe_allow_html=True)
    tl_actions = get_timeline_actions(company_id=cid, contact_id=ctid, limit=50)
    for a in tl_actions:
        date = (a.get("created_at") or "")[:10]
        desc = f"{a['action_type']} via {a['channel']}"
        if a.get("contact_name"):
            desc += f" → {a['contact_name']}"
        desc += f": {a['description'][:150]}"
        sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
        st.markdown(tl_item(date, desc, a["status"], sc), unsafe_allow_html=True)
    if not tl_actions:
        st.markdown(
            "<div style='text-align:center;padding:40px;color:var(--text-3);'>No timeline entries yet.</div>",
            unsafe_allow_html=True,
        )
