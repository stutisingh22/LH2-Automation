"""
app_helpers.py — Shared helpers for app shell and pages.
Used by apps.py and pages/*. No database logic; only orchestration and formatting.
"""

import re
from datetime import datetime
import uuid
import streamlit as st

from config import NOTION_DATABASES
from db_ops import (
    get_company,
    get_contact,
    get_or_create_session,
    load_messages,
    get_all_users,
    update_action,
    delete_action,
)
from ai_engine import refine_email_draft
from config import ACTION_TYPES, ACTION_CHANNELS, ACTION_STATUSES, ACTION_STATUS_COLORS
from ui_helpers import bdg, display_full_name
from gmail_service import create_gmail_draft, parse_email_content
from services.deck_links import inject_deck_links_into_email_body


def get_current_notion_database_id():
    """Return the currently selected Notion database ID (for sync, lookup, create page)."""
    return st.session_state.get("notion_database_id") or (NOTION_DATABASES[0][1] if NOTION_DATABASES else None)


def select_contact(comp_id, ct_id):
    """Set session to company+contact, create or get session, switch to INTELLIGENCE."""
    user = st.session_state.get("user")
    if not user:
        return
    comp = get_company(comp_id)
    ct = get_contact(ct_id)
    if not comp or not ct:
        return
    st.session_state.sel_company = comp_id
    st.session_state.sel_contact = ct_id
    sid = get_or_create_session(comp_id, ct_id, user["id"], 0)
    st.session_state.session_id = sid
    st.session_state.chat_messages = load_messages(sid)
    st.session_state.auto_brief_text = None
    st.session_state.pending_plan = None
    st.session_state.current_tab = "INTELLIGENCE"


def format_timeline_ts(iso_str):
    """Format ISO timestamp for timeline display."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return str(iso_str)[:16] if iso_str else "—"


def build_ics(actions, title="LH2 Outreach Plan"):
    """
    Create a minimal .ics calendar file for a list of actions/steps.
    Expects each item to have: channel, description, scheduled_at (isoformat).
    """
    def _dt(v):
        if not v:
            return None
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return d
        except Exception:
            return None

    def _ics_escape(s):
        return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LH2//Command Center//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_ics_escape(title)}",
    ]

    for a in actions or []:
        d = _dt(a.get("scheduled_at"))
        if not d:
            continue
        dtstart = d.astimezone().strftime("%Y%m%dT%H%M%S")
        dtend = (d.astimezone() + __import__("datetime").timedelta(minutes=30)).strftime("%Y%m%dT%H%M%S")
        ch = a.get("channel") or a.get("action_type") or "Action"
        desc = a.get("description") or ""
        summary = f"{ch}: {desc[:80]}".strip()
        uid = f"{uuid.uuid4()}@lh2"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def display_original_from_content_sent(content_sent):
    """If content_sent was saved as combined (v1 + '---' + v2), return only first part for display."""
    raw = (content_sent or "").strip()
    if "\n\n---\n\n" in raw:
        return raw.split("\n\n---\n\n", 1)[0].strip()
    return raw


def render_calendar_event(ax):
    """Render one calendar event card with Edit and Delete."""
    aid = ax["id"]
    sc = ACTION_STATUS_COLORS.get(ax["status"], "gray")
    st.markdown(
        f"<div class='c' style='padding:10px 14px;'>"
        f"<div style='display:flex;justify-content:space-between;'>"
        f"<span class='c-l'>{ax.get('action_type','Action')} · {ax.get('channel','')}</span>"
        f"{bdg(ax.get('status','planned'), sc)}</div>"
        f"<div style='font-size:12px;margin-top:4px;'>{(ax.get('description') or '')[:240]}</div>"
        f"<div style='font-size:10px;color:var(--text-3);margin-top:4px;'>"
        f"{(ax.get('company_domain') or '')} {('· ' + ax.get('contact_name','')) if ax.get('contact_name') else ''}"
        f"</div></div>",
        unsafe_allow_html=True,
    )
    row1, row2 = st.columns([1, 3])
    with row1:
        if st.button("Delete", key=f"cal_del_{aid}", type="secondary"):
            delete_action(aid)
            st.rerun()
    with row2:
        with st.expander("Edit event", expanded=False):
            with st.form(f"cal_edit_form_{aid}"):
                desc = st.text_area("Description", value=ax.get("description") or "", height=80, key=f"cal_desc_{aid}")
                atype = st.selectbox("Type", options=ACTION_TYPES, index=ACTION_TYPES.index(ax.get("action_type") or "Outreach") if (ax.get("action_type") or "Outreach") in ACTION_TYPES else 0, key=f"cal_type_{aid}")
                ch = st.selectbox("Channel", options=ACTION_CHANNELS, index=ACTION_CHANNELS.index(ax.get("channel") or "Email") if (ax.get("channel") or "Email") in ACTION_CHANNELS else 0, key=f"cal_ch_{aid}")
                stat = st.selectbox("Status", options=ACTION_STATUSES, index=ACTION_STATUSES.index(ax.get("status") or "planned") if (ax.get("status") or "planned") in ACTION_STATUSES else 0, key=f"cal_stat_{aid}")
                sa = ax.get("scheduled_at")
                try:
                    dt_val = datetime.fromisoformat(str(sa).replace("Z", "+00:00")) if sa else datetime.utcnow()
                except Exception:
                    dt_val = datetime.utcnow()
                d = st.date_input("Date", value=dt_val.date(), key=f"cal_date_{aid}")
                t = st.time_input("Time", value=dt_val.time(), key=f"cal_time_{aid}")
                scheduled_str = datetime.combine(d, t).isoformat()
                if st.form_submit_button("Save"):
                    update_action(aid, description=desc, action_type=atype, channel=ch, status=stat, scheduled_at=scheduled_str)
                    st.rerun()


def render_save_to_gmail(to_email, subject, body, draft_id, product_key=None, step_index=None, lead_brand=None):
    """Render Save to Gmail section: account dropdown + Save to Gmail Drafts button.
    If product_key, step_index, or lead_brand are provided, deck phrases in body are turned into hyperlinks."""
    all_users = get_all_users()
    if not all_users:
        return
    options = []
    for u in all_users:
        key = (u.get("email") or "").strip() or (u.get("username") or str(u["id"]))
        label = (u.get("email") or display_full_name(u.get("username")) or f"User {u['id']}")
        options.append((key, label))
    if not options:
        return
    st.markdown("<div class='sequence-step-divider' style='margin-top:0;'></div>", unsafe_allow_html=True)
    st.markdown("**Save to Gmail**", unsafe_allow_html=True)
    gcol1, gcol2 = st.columns([2, 1])
    with gcol1:
        gmail_sel = st.selectbox(
            "Gmail account",
            options=[o[0] for o in options],
            format_func=lambda x: next((o[1] for o in options if o[0] == x), x),
            index=0,
            key=f"gmail_account_{draft_id}",
            label_visibility="collapsed",
        )
    with gcol2:
        has_to_email = bool((to_email or "").strip())
        disabled = not (subject or body)
        if not has_to_email:
            st.caption("Recipient can be added in Gmail if needed.")
        if st.button("Save to Gmail Drafts", key=f"gmail_save_{draft_id}", type="primary", use_container_width=True, disabled=disabled):
            body_html = None
            try:
                body_html = inject_deck_links_into_email_body(
                    body or "", product_key=product_key, step_index=step_index, lead_brand=lead_brand
                )
                if not (body_html or "").strip():
                    body_html = None
            except Exception:
                body_html = None
            out = create_gmail_draft(
                gmail_sel, to_email or "", subject or "", body or "", body_html=body_html
            )
            if out.get("success"):
                st.success("Draft saved to Gmail.")
            else:
                st.error(out.get("error") or "Authentication required.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_ai_refinement(step, seq_id, channel="email"):
    """Render AI draft refinement panel for one step (email or LinkedIn)."""
    step_id = step["id"]
    content_sent_raw = (step.get("content_sent") or "").strip()
    original_from_db = (step.get("draft_original") or "").strip() or display_original_from_content_sent(step.get("content_sent"))
    rev_list_key = f"draft_revisions_list_{step_id}"
    orig_stored_key = f"draft_original_{step_id}"
    revisions_list = st.session_state.get(rev_list_key) or []
    if not isinstance(revisions_list, list):
        revisions_list = [revisions_list] if revisions_list else []
    if not revisions_list and step.get("draft_revisions"):
        revs = step["draft_revisions"]
        revisions_list = revs if isinstance(revs, list) else list(revs) if revs else []
        st.session_state[rev_list_key] = revisions_list
    if orig_stored_key not in st.session_state and (original_from_db or content_sent_raw):
        st.session_state[orig_stored_key] = original_from_db or content_sent_raw
    original = st.session_state.get(orig_stored_key) or original_from_db
    current_text = revisions_list[-1] if revisions_list else original
    is_linkedin = "linkedin" in (channel or "").lower()
    draft_label = "LinkedIn draft" if is_linkedin else "email draft"
    refine_placeholder = "e.g. Make it shorter (under 300 chars)…" if is_linkedin else "e.g. Rewrite to be shorter…"
    latest_revision = revisions_list[-1] if revisions_list else None

    st.markdown("<div class='sequence-step-divider'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--text-2);"
        "margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid #e2e8f0;'>AI Refinement</div>",
        unsafe_allow_html=True,
    )
    version_options = ["Version 1 (Original)"]
    for i in range(len(revisions_list)):
        version_options.append(f"Version {i + 2} (AI Revised)")
    default_index = min(1, len(version_options) - 1) if revisions_list else 0
    version_sel = st.selectbox(
        "Select version",
        options=version_options,
        index=default_index,
        key=f"refine_version_sel_{step_id}",
        label_visibility="collapsed",
    )
    if version_sel == "Version 1 (Original)":
        selected_text = original
    else:
        try:
            m = re.search(r"Version\s+(\d+)", version_sel)
            v = int(m.group(1)) if m else 2
            idx = v - 2
            raw = revisions_list[idx] if 0 <= idx < len(revisions_list) else (revisions_list[-1] if revisions_list else original)
            selected_text = (raw if isinstance(raw, str) else str(raw)) if raw else original
        except Exception:
            raw = revisions_list[-1] if revisions_list else original
            selected_text = (raw if isinstance(raw, str) else str(raw)) if raw else original
    preview_text = selected_text or current_text or original
    st.markdown(
        f"<div style='font-size:11px;color:var(--text-3);margin-bottom:4px;'>"
        f"{'Version %d (AI Revised)' % (len(revisions_list) + 1) if revisions_list else 'Version 1 (Original)'}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='font-size:12px;background:#f8fafc;padding:10px 12px;border-radius:4px;border:1px solid #e2e8f0;"
        f"white-space:pre-wrap;max-height:120px;overflow-y:auto;margin-bottom:10px;'>{(preview_text or '')[:400]}{'...' if len(preview_text or '') > 400 else ''}</div>",
        unsafe_allow_html=True,
    )
    instruction = st.text_input(
        "Instruction",
        placeholder=refine_placeholder,
        key=f"refine_instruction_{step_id}",
        label_visibility="collapsed",
    )
    if st.button(f"Refine {draft_label}", key=f"refine_btn_{step_id}", type="primary", use_container_width=True):
        if (instruction or "").strip():
            with st.spinner("Refining…"):
                revised = refine_email_draft(current_text, instruction.strip(), draft_type="linkedin" if is_linkedin else "email")
            if revised:
                revisions_list = st.session_state.get(rev_list_key) or []
                if not isinstance(revisions_list, list):
                    revisions_list = [revisions_list] if revisions_list else []
                is_first_revision = len(revisions_list) == 0
                revisions_list.append(revised)
                st.session_state[rev_list_key] = revisions_list
                update_action(step_id, draft_revisions=revisions_list, draft_original=content_sent_raw if is_first_revision else None)
                st.rerun()
            else:
                st.error("Could not refine draft. Try again.")
        else:
            st.warning("Enter an instruction.")

    if latest_revision:
        st.markdown("<div style='font-size:11px;color:var(--text-2);margin:10px 0 6px 0;'>Latest revised draft</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:12px;background:#f0fdf4;padding:10px 12px;border-radius:4px;border:1px solid #bbf7d0;"
            f"white-space:pre-wrap;margin-bottom:10px;'>{(latest_revision)}</div>",
            unsafe_allow_html=True,
        )
    r1, r2 = st.columns(2)
    with r1:
        use_disabled = not revisions_list and version_sel == "Version 1 (Original)"
        if st.button("Use selected version", key=f"use_version_btn_{step_id}", type="secondary", use_container_width=True, disabled=use_disabled):
            if selected_text is not None:
                text_to_save = (selected_text if isinstance(selected_text, str) else str(selected_text)).strip()
                if text_to_save:
                    update_action(step_id, content_sent=text_to_save)
            st.rerun()
    with r2:
        if revisions_list and st.button("Keep both versions", key=f"keep_both_btn_{step_id}", use_container_width=True):
            update_action(step_id, content_sent=latest_revision)
            st.rerun()
