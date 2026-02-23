"""Calendar page — upcoming scheduled actions."""

from datetime import datetime, timedelta
import streamlit as st

from db_ops import get_timeline_actions
from ui_helpers import sh
from app_helpers import render_calendar_event


def render():
    st.markdown(sh("UPCOMING ACTIONS"), unsafe_allow_html=True)
    tl = get_timeline_actions(limit=2000)
    now = datetime.utcnow()
    horizon = now + timedelta(days=90)
    upcoming = []
    for a in tl:
        sa = a.get("scheduled_at")
        if not sa:
            continue
        try:
            dt = datetime.fromisoformat(str(sa).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt < now or dt > horizon:
            continue
        if a.get("status") in ("completed", "deal_closed", "deal_lost"):
            continue
        upcoming.append((dt, a))
    upcoming.sort(key=lambda x: x[0])

    if not upcoming:
        st.info(
            "No scheduled actions in the next 90 days. Generate an outreach plan in STRATEGIST and add it, or schedule actions manually."
        )
        return
    day = None
    batch = []
    for dt, a in upcoming:
        dkey = dt.date().isoformat()
        if day is None:
            day = dkey
        if dkey != day:
            st.markdown(sh(day), unsafe_allow_html=True)
            for _, ax in batch:
                render_calendar_event(ax)
            batch = []
            day = dkey
        batch.append((dt, a))
    if batch:
        st.markdown(sh(day), unsafe_allow_html=True)
        for _, ax in batch:
            render_calendar_event(ax)
