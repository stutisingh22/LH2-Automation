"""
app.py — LH2 Command Center v3

Features:
  - Sidebar: conversation history
  - From/To selectors: who is sending, who is receiving
  - Sequence builder: one-click full outreach plan
  - Adaptive replanning: AI tweaks plan when responses arrive
  - Streaming chat with proper styling
"""

import inspect
import re
import time
import streamlit as st
from datetime import datetime
import hashlib
import os
import json
from pathlib import Path

st.set_page_config(
    page_title="LH2 Command Center", page_icon="◆",
    layout="wide", initial_sidebar_state="expanded",
)

# Force sidebar to always be visible
if 'sidebar_state' not in st.session_state:
    st.session_state.sidebar_state = 'expanded'

# Session file directory
SESSION_DIR = Path(".sessions")
SESSION_DIR.mkdir(exist_ok=True)

from config import (
    CONTACT_STATUSES, CONTACT_STATUS_LABELS, CONTACT_STATUS_LABELS_STRIP, CONTACT_STATUS_COLORS,
    ACTION_TYPES, ACTION_CHANNELS, ACTION_STATUSES, ACTION_STATUS_COLORS,
    GOOGLE_SERVICE_ACCOUNT_JSON,
)
from db_ops import (
    create_user, authenticate, get_user, get_all_users,
    get_company, get_all_companies, search_companies,
    upsert_company, upsert_contact,
    get_contacts, get_contact, update_contact_status, update_contact,
    get_or_create_session, get_session, get_sessions_for_company,
    get_user_sessions, delete_session,
    save_message, load_messages, get_message_count,
    log_action, bulk_log_actions, get_session_actions, get_user_actions,
    get_timeline_actions, get_active_sequences, get_sequence_actions,
    update_action, delete_action,
    get_overdue_followups, get_todays_scheduled, get_recent_activity,
    get_memory_events, get_global_insights,
    get_session_summary, get_pipeline_stats,
    merge_company_extra_data,
    save_advisor_conversation, get_advisor_conversations, load_advisor_conversation, delete_advisor_conversation,
    get_enrichment_result, get_latest_enrichment_for_user,
)
from notion_sync import sync_notion_to_db, lookup_lead_in_notion, fetch_notes_for_page, create_notion_contact_page
from notion_sync_advisor import sync_notion_advisor_to_db
from db_advisor import get_all_advisor_companies, get_advisor_company, search_advisor_companies, get_advisor_parents_with_children, merge_advisor_company_extra_data
from drive_sync import list_drive_files, create_doc_in_drive
from ai_engine import (
    compute_lead_score, get_local_time,
    get_strategist_response_stream, auto_brief,
    generate_outreach_plan, replan_on_response,
    maybe_update_summary, extract_episodic_memories,
    generate_insight_from_outcome, parse_ai_actions, export_session_csv,
    intelligence_request, research_person_for_company, summarize_research_to_context,
    advisor_chat, refine_email_draft, company_brief_with_power_mapping,
)
from research import fetch_research_from_notion, run_research, write_research_to_notion, research_command_ai, split_research_into_sections
from web_search import search_web_for_person, search_web_for_person_by_name, power_scrape_company_spocs
from contact_timeline import build_contact_timeline
from signalhire_enrichment import (
    get_credits as get_signalhire_credits,
    request_enrichment,
    has_verified_email,
)
from gmail_service import create_gmail_draft, parse_email_content
from ui_helpers import inject_css, bdg, crd, mcard, sh, sh_small, tl_item, alert, fmt_v, has_perm, timeline_item_html, lh2_kpi_cell, lh2_status_cell, lh2_section_header, lh2_pipeline_row, display_full_name, render_brief_card_html, markdown_to_html

inject_css()

def _build_ics(actions, title="LH2 Outreach Plan"):
    """
    Create a minimal .ics calendar file for a list of actions/steps.
    Expects each item to have: channel, description, scheduled_at (isoformat).
    """
    from datetime import datetime
    import uuid

    def _dt(v):
        if not v:
            return None
        try:
            # Accept either date or datetime strings
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
        # Default 30 min block
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


# ══════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════

def get_or_create_session_token():
    """Get or create a persistent session token from query params"""
    query_params = st.query_params
    
    if "session_token" in query_params:
        return query_params["session_token"]
    
    if "session_token" not in st.session_state:
        # Create new token
        token = hashlib.sha256(f"{datetime.now().timestamp()}{os.urandom(16).hex()}".encode()).hexdigest()[:32]
        st.session_state.session_token = token
        st.query_params["session_token"] = token
        return token
    
    return st.session_state.session_token

def save_session_user(user_id):
    """Save user session to file"""
    token = get_or_create_session_token()
    session_file = SESSION_DIR / f"{token}.json"
    with open(session_file, 'w') as f:
        json.dump({"user_id": user_id, "timestamp": datetime.now().isoformat()}, f)

def load_session_user():
    """Load user from session file"""
    token = get_or_create_session_token()
    session_file = SESSION_DIR / f"{token}.json"
    if session_file.exists():
        try:
            with open(session_file, 'r') as f:
                data = json.load(f)
                return data.get("user_id")
        except:
            pass
    return None

def clear_session_user():
    """Clear user session file and token"""
    token = get_or_create_session_token()
    session_file = SESSION_DIR / f"{token}.json"
    if session_file.exists():
        session_file.unlink()
    # Clear query params
    if "session_token" in st.query_params:
        del st.query_params["session_token"]

def render_auth():
    if "user" not in st.session_state:
        st.session_state.user = None
    
    if st.session_state.user:
        return True
    
    # Try to restore from saved session
    saved_user_id = load_session_user()
    if saved_user_id:
        usr = get_user(saved_user_id)
        if usr:
            st.session_state.user = usr
            return True
        else:
            clear_session_user()
    
    if st.session_state.user:
        return True
    _, col, _ = st.columns([1.1, 0.8, 1.1])
    with col:
        st.markdown(
            "<div style='text-align:center;margin-top:14vh;margin-bottom:36px;'>"
            "<div style='font-size:14px;font-weight:700;letter-spacing:5px;color:var(--accent);margin-bottom:6px;'>◆ LH2</div>"
            "<div style='font-size:22px;font-weight:600;'>Command Center</div>"
            "<div style='font-size:13px;color:var(--text-3);margin-top:6px;'>Relationship intelligence platform</div>"
            "</div>", unsafe_allow_html=True)
        t1, t2 = st.tabs(["SIGN IN", "REGISTER"])
        with t1:
            with st.form("login"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                if st.form_submit_button("Sign In"):
                    usr = authenticate(u, p)
                    if usr:
                        st.session_state.user = usr
                        save_session_user(usr["id"])
                        st.session_state._login_redirect = True
                        st.rerun()
                    else:
                        st.error("Invalid credentials")
        with t2:
            with st.form("register"):
                nu = st.text_input("Username", key="r_u")
                ne = st.text_input("Email", key="r_e")
                np_ = st.text_input("Password", type="password", key="r_p")
                if st.form_submit_button("Create Account"):
                    if len(nu) < 3 or len(np_) < 6:
                        st.error("Username 3+, password 6+ chars")
                    elif create_user(nu, np_, ne):
                        st.success("Created. Sign in above.")
                    else:
                        st.error("Username taken")
    return False

if not render_auth():
    st.stop()

# One-run loading screen after login to avoid login + main app overlap
if st.session_state.pop("_login_redirect", None):
    st.markdown(
        "<div style='text-align:center;padding:38vh 0;font-size:15px;color:var(--text-2);'>Loading...</div>",
        unsafe_allow_html=True,
    )
    time.sleep(0.35)
    st.rerun()

# ══════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════

user = st.session_state.user
defaults = {
    "sel_company": None, "sel_contact": None, "session_id": None,
    "sel_advisor_company_id": None,
    "chat_messages": [], "auto_brief_text": None, "research_cache": {},
    "pending_plan": None, "sender_id": user["id"],
    "intel_lookup_lead": None, "intel_person_brief": None,
    "intel_suggestion": None, "intel_suggestion_raw": None,
    "current_tab": "DASHBOARD", "advisor_messages": [],
    "company_arrow_cid": None, "company_brief_cache": {},
    "timeline_contact_id": None, "timeline_company_id": None, "timeline_contact_name": None,
}
for k, d in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = d


def select_contact(comp_id, ct_id):
    comp = get_company(comp_id)
    ct = get_contact(ct_id)
    if not comp or not ct:
        return
    st.session_state.sel_company = comp_id
    st.session_state.sel_contact = ct_id
    score = compute_lead_score(comp, ct)
    sid = get_or_create_session(comp_id, ct_id, user["id"], score)
    st.session_state.session_id = sid
    st.session_state.chat_messages = load_messages(sid)
    st.session_state.auto_brief_text = None
    st.session_state.pending_plan = None
    st.session_state.current_tab = "INTELLIGENCE"


def _format_timeline_ts(iso_str):
    """Format ISO timestamp for timeline display."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return str(iso_str)[:16] if iso_str else "—"


def _render_calendar_event(ax):
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


def _render_save_to_gmail(to_email, subject, body, draft_id):
    """Render 'Save to Gmail' section: account dropdown + Save to Gmail Drafts button."""
    all_users = get_all_users()
    if not all_users:
        return
    # Account key for token: prefer email, else username
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
        disabled = not (subject or body)
        if st.button("Save to Gmail Drafts", key=f"gmail_save_{draft_id}", type="primary", use_container_width=True, disabled=disabled):
            out = create_gmail_draft(gmail_sel, to_email or "", subject or "", body or "")
            if out.get("success"):
                st.success("Draft saved to Gmail.")
            else:
                st.error(out.get("error") or "Authentication required.")
    st.markdown("</div>", unsafe_allow_html=True)


def _display_original_from_content_sent(content_sent):
    """If content_sent was saved as combined (v1 + '---' + v2), return only first part for display."""
    raw = (content_sent or "").strip()
    if "\n\n---\n\n" in raw:
        return raw.split("\n\n---\n\n", 1)[0].strip()
    return raw


def _render_ai_refinement(step, seq_id, channel="email"):
    """Render AI draft refinement panel for one step (email or LinkedIn). Actions tab only. Isolated per-step state."""
    step_id = step["id"]
    content_sent_raw = (step.get("content_sent") or "").strip()
    # Version 1 (Original): use persisted draft_original from DB after refresh, else current content_sent
    original_from_db = (step.get("draft_original") or "").strip() or _display_original_from_content_sent(step.get("content_sent"))
    rev_list_key = f"draft_revisions_list_{step_id}"
    orig_stored_key = f"draft_original_{step_id}"
    revisions_list = st.session_state.get(rev_list_key) or []
    if not isinstance(revisions_list, list):
        revisions_list = [revisions_list] if revisions_list else []
    # After refresh: restore revision list from DB so drafts persist
    if not revisions_list and step.get("draft_revisions"):
        revs = step["draft_revisions"]
        revisions_list = revs if isinstance(revs, list) else list(revs) if revs else []
        st.session_state[rev_list_key] = revisions_list
    # Keep a stable "Version 1 (Original)" so it doesn't change after "Use version 2"
    if orig_stored_key not in st.session_state and (original_from_db or content_sent_raw):
        st.session_state[orig_stored_key] = original_from_db or content_sent_raw
    original = st.session_state.get(orig_stored_key) or original_from_db
    current_text = revisions_list[-1] if revisions_list else original
    is_linkedin = "linkedin" in (channel or "").lower()
    draft_label = "LinkedIn draft" if is_linkedin else "email draft"
    refine_placeholder = "e.g. Make it shorter and more direct (under 300 chars)…" if is_linkedin else "e.g. Rewrite to be shorter and more direct…"
    latest_revision = revisions_list[-1] if revisions_list else None

    st.markdown("<div class='sequence-step-divider'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--text-2);"
        "margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid #e2e8f0;'>AI Refinement</div>",
        unsafe_allow_html=True)
    # Version dropdown and selected text (computed early so preview + expander above can use it)
    st.markdown(
        "<div style='font-size:11px;color:var(--text-2);margin-bottom:4px;'>Select version to use</div>",
        unsafe_allow_html=True)
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
    current_version_num = len(revisions_list) + 1
    st.markdown(
        f"<div style='font-size:11px;color:var(--text-3);margin-bottom:4px;'>"
        f"{'Version %d (AI Revised)' % current_version_num if revisions_list else 'Version 1 (Original)'}</div>",
        unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:12px;background:#f8fafc;padding:10px 12px;border-radius:4px;border:1px solid #e2e8f0;"
        f"white-space:pre-wrap;max-height:120px;overflow-y:auto;margin-bottom:10px;'>{(preview_text or '')[:400]}{'...' if len(preview_text or '') > 400 else ''}</div>",
        unsafe_allow_html=True)
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
                # Persist revisions; on first revision, save current content_sent as draft_original for Version 1 after refresh
                update_action(
                    step_id,
                    draft_revisions=revisions_list,
                    draft_original=content_sent_raw if is_first_revision else None,
                )
                st.rerun()
            else:
                st.error("Could not refine draft. Try again.")
        else:
            st.warning("Enter an instruction.")

    if latest_revision:
        st.markdown(
            "<div style='font-size:11px;color:var(--text-2);margin:10px 0 6px 0;'>Latest revised draft</div>",
            unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:12px;background:#f0fdf4;padding:10px 12px;border-radius:4px;border:1px solid #bbf7d0;"
            f"white-space:pre-wrap;margin-bottom:10px;'>{(latest_revision)}</div>",
            unsafe_allow_html=True)

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
            # Save only the latest as main draft (single draft); keep all versions in dropdown
            update_action(step_id, content_sent=latest_revision)
            st.rerun()


# ══════════════════════════════════════════════════════════════
# SIDEBAR — CONVERSATION HISTORY
# ══════════════════════════════════════════════════════════════

with st.sidebar:
    try:
        _base = Path(__file__).resolve().parent
        _logo_path = None
        for _p in ("assets/lh2_holdings_logo.png", "assets/lh2_holdings_logo.jpeg", "lh2_holdings_logo.jpeg", "lh2_holdings_logo.png"):
            _candidate = _base / _p
            if _candidate.is_file():
                _logo_path = str(_candidate)
                break
        if _logo_path:
            st.markdown("<div class='sidebar-logo'>", unsafe_allow_html=True)
            st.image(_logo_path, width=120)
            st.markdown("</div>", unsafe_allow_html=True)
    except Exception:
        pass
    st.markdown(
        "<div style='font-size:12px;font-weight:700;letter-spacing:3px;"
        "color:var(--accent);padding:4px 0 12px;'>◆ LH2</div>",
        unsafe_allow_html=True)

    _src = st.session_state.get("notion_companies_source", "Independent")
    _aid = st.session_state.get("sel_advisor_company_id")
    if _src == "Media Houses" and _aid:
        _adv = get_advisor_company(_aid)
        if _adv:
            st.markdown(
                f"<div style='background:var(--accent-bg);padding:10px 14px;border-radius:8px;"
                f"margin-bottom:12px;border:1px solid #c7d2fe;'>"
                f"<div style='font-size:13px;font-weight:600;'>{_adv['domain']}</div>"
                f"<div style='font-size:11px;color:var(--text-2);margin-top:2px;'>"
                f"Media House · {_adv.get('parent_name','')}</div></div>",
                unsafe_allow_html=True)
    elif st.session_state.sel_company and st.session_state.sel_contact:
        _c = get_company(st.session_state.sel_company)
        _t = get_contact(st.session_state.sel_contact)
        if _c and _t:
            st.markdown(
                f"<div style='background:var(--accent-bg);padding:10px 14px;border-radius:8px;"
                f"margin-bottom:12px;border:1px solid #c7d2fe;'>"
                f"<div style='font-size:13px;font-weight:600;'>{_c['domain']}</div>"
                f"<div style='font-size:11px;color:var(--text-2);margin-top:2px;'>"
                f"{_t['name']} · {_t.get('role','')}</div></div>",
                unsafe_allow_html=True)

    if st.button("＋ New Session", key="new_sess", use_container_width=True):
        st.session_state.sel_company = None
        st.session_state.sel_contact = None
        st.session_state.session_id = None
        st.session_state.sel_advisor_company_id = None
        st.session_state.chat_messages = []
        st.session_state.auto_brief_text = None
        st.session_state.pending_plan = None
        st.rerun()

    st.markdown(
        "<div style='font-size:11px;font-weight:600;letter-spacing:1.2px;"
        "text-transform:uppercase;color:var(--text-3);margin:12px 0 10px;'>Recent</div>",
        unsafe_allow_html=True)

    sessions = get_user_sessions(user["id"])
    for sx in sessions[:15]:
        is_active = st.session_state.session_id == sx["id"]
        mc2 = get_message_count(sx["id"])
        domain = sx.get("company_domain", "?")
        contact = sx.get("contact_name", "?")
        updated = (sx.get("updated_at") or "")[:10]
        has_memory = sx.get("strategic_summary")
        
        # Status badge based on message count
        if mc2 == 0:
            status_badge = "<span class='session-badge badge-new'>NEW</span>"
        elif mc2 < 5:
            status_badge = "<span class='session-badge badge-active'>ACTIVE</span>"
        else:
            status_badge = "<span class='session-badge badge-ongoing'>ONGOING</span>"
        
        if has_memory:
            status_badge += " <span class='session-badge badge-memory'>🧠</span>"
        
        card_class = "sidebar-card active" if is_active else "sidebar-card"
        
        # Card with info
        st.markdown(
            f"<div class='{card_class}'>"
            f"<div class='card-header'>"
            f"<span class='card-domain'>{domain}</span>"
            f"<div class='card-badges'>{status_badge}</div>"
            f"</div>"
            f"<div class='card-meta'>{contact} · {mc2} msgs</div>"
            f"<div class='card-footer'>"
            f"<span class='card-date'>{updated}</span>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True)
        
        # Full width arrow button
        if not is_active:
            if st.button("→", key=f"sb_{sx['id']}", type="secondary", use_container_width=True):
                st.session_state.sel_company = sx["company_id"]
                st.session_state.sel_contact = sx["contact_id"]
                st.session_state.session_id = sx["id"]
                st.session_state.sel_advisor_company_id = None
                st.session_state.chat_messages = load_messages(sx["id"])
                st.session_state.auto_brief_text = None
                st.session_state.pending_plan = None
                st.session_state.current_tab = "INTELLIGENCE"
                st.rerun()

    st.markdown("---")
    st.markdown(f"<div style='font-size:11px;color:var(--text-3);'>{display_full_name(user['username'])} · {user['role']}</div>", unsafe_allow_html=True)
    if st.button("Sign Out", key="sb_logout", use_container_width=True):
        clear_session_user()
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ══════════════════════════════════════════════════════════════
# HEADER + TOP TAB BAR (buttons)
# ══════════════════════════════════════════════════════════════

TAB_NAMES = ["DASHBOARD", "COMPANIES", "INTELLIGENCE", "STRATEGIST", "ACTIONS", "TIMELINE", "CALENDAR", "PIPELINE", "HISTORY", "ADVISOR"]

# Which Notion DB drives the Companies view: Independent (NOTION_DATABASE_ID) or Media Houses (NOTION_DATABASE_ID_2)
if "notion_companies_source" not in st.session_state:
    st.session_state.notion_companies_source = "Independent"
else:
    v = st.session_state.notion_companies_source
    if v == "Primary":
        st.session_state.notion_companies_source = "Independent"
    elif v == "Advisor":
        st.session_state.notion_companies_source = "Media Houses"
current_tab = st.session_state.get("current_tab", "DASHBOARD")
if current_tab not in TAB_NAMES:
    current_tab = "DASHBOARD"
    st.session_state.current_tab = current_tab

# Clean Admin Header with User Badge
header_cols = st.columns([0.35, 0.3, 0.35])
with header_cols[0]:
    st.markdown(
        "<div class='dashboard-header-left'>"
        "<div class='logo-container'>"
        "<span class='logo-diamond'>◆</span>"
        "<span class='logo-text'>LH2</span>"
        "<span class='logo-subtitle'>Command Center</span>"
        "</div>"
        "</div>",
        unsafe_allow_html=True)
with header_cols[1]:
    # Breadcrumb (company > contact) intentionally not shown — selection stays in list only
    pass
with header_cols[2]:
    st.selectbox(
        "Companies DB",
        options=["Independent", "Media Houses"],
        index=["Independent", "Media Houses"].index(st.session_state.get("notion_companies_source", "Independent")),
        key="notion_companies_source",
        label_visibility="collapsed",
    )
    st.markdown(
        f"<div class='dashboard-header-right'>"
        f"<div class='user-badge'>"
        f"<span class='user-avatar'>{display_full_name(user['username'])[0].upper()}</span>"
        f"<span class='user-info'>"
        f"<span class='user-name' style='font-size:9px!important;'>{display_full_name(user['username'])}</span>"
        f"<span class='user-role' style='font-size:7px!important;'>Admin</span>"
        f"</span>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True)

# Professional Admin Navigation using st.tabs()
tab_labels = [n.title() if n != "ADVISOR" else "Advisor" for n in TAB_NAMES]
tabs = st.tabs(tab_labels)

# ════════════════════ DASHBOARD ════════════════════

with tabs[0]:  # DASHBOARD
    # === UI REFACTOR START ===
    st.markdown(lh2_section_header("OVERVIEW"), unsafe_allow_html=True)
    stats = get_pipeline_stats()
    _companies_count = len(get_all_advisor_companies()) if st.session_state.get("notion_companies_source") == "Media Houses" else len(get_all_companies())
    kpi_items = [
        (_companies_count, "Companies"),
        (stats.get("outreach_sent", 0) + stats.get("awaiting_reply", 0), "Active Outreach"),
        (stats.get("meeting_booked", 0), "Meetings"),
        (stats.get("partnership_active", 0), "Partnerships"),
    ]
    st.markdown(
        "<div class='lh2-kpi-strip'>" + "".join(lh2_kpi_cell(n, l) for n, l in kpi_items) + "</div>",
        unsafe_allow_html=True)

    st.markdown(lh2_section_header("STATUS BREAKDOWN"), unsafe_allow_html=True)
    status_items = [(stats.get(s, 0), CONTACT_STATUS_LABELS_STRIP.get(s, s)) for s in CONTACT_STATUSES]
    st.markdown(
        "<div class='lh2-status-grid'>" + "".join(lh2_status_cell(v, lbl) for v, lbl in status_items) + "</div>",
        unsafe_allow_html=True)
    # === UI REFACTOR END ===

    st.markdown(lh2_section_header("ALERTS"), unsafe_allow_html=True)
    overdue = get_overdue_followups(3)
    todays = get_todays_scheduled()
    for a in overdue[:5]:
        desc = a.get('description', '')
        contact = a.get('contact_name', '?')
        domain = a.get('company_domain', '?')
        preview = f"⚠️ Overdue: {contact} at {domain}"
        with st.expander(preview, expanded=False):
            st.markdown(f"**Action:** {desc}")
            st.caption(f"Contact: {contact} | Company: {domain}")
    if not overdue:
        st.caption("No overdue follow-ups.")
    for a in todays[:5]:
        desc = a.get('description', '')
        contact = a.get('contact_name', '?')
        preview = f"📅 Today: {contact}"
        with st.expander(preview, expanded=False):
            st.markdown(f"**Action:** {desc}")
            if a.get('company_domain'):
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
            f"{bdg(a['status'],sc)}</div>"
            f"<div style='font-size:12px;margin-top:4px;'>{a['description'][:200]}</div></div>",
            unsafe_allow_html=True)
    if not recent:
        st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);'>No activity yet.</div>", unsafe_allow_html=True)


# ════════════════════ COMPANIES ════════════════════

with tabs[1]:  # COMPANIES
    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)
    _companies_source = st.session_state.get("notion_companies_source", "Independent")

    if _companies_source == "Media Houses":
        # ── Media Houses DB (NOTION_DATABASE_ID_2): parent (media) → children (domain) ──
        cc1, cc2, cc3_adv = st.columns([2, 1, 1])
        with cc1:
            sq_adv = st.text_input("Search", placeholder="Search domain or parent...", label_visibility="collapsed", key="csearch_advisor")
        with cc2:
            if st.button("🔄 Sync Notion (Media Houses)", key="nsync_advisor", use_container_width=True):
                with st.spinner("Syncing Media Houses DB..."):
                    r = sync_notion_advisor_to_db()
                    if r.get("error"):
                        st.error(f"Error: {r['error']}")
                    else:
                        st.success(f"Synced {r['companies']} companies from Media Houses DB")
                    st.rerun()
        with cc3_adv:
            if st.button("Reset", key="resetsel_advisor", use_container_width=True):
                st.session_state.sel_advisor_company_id = None
                st.session_state.current_tab = "COMPANIES"
                st.rerun()
        if sq_adv.strip():
            advisor_rows = search_advisor_companies(sq_adv)
            # Flatten for count; display grouped by parent
            by_parent = {}
            for row in advisor_rows:
                p = row["parent_name"] or "(No parent)"
                if p not in by_parent:
                    by_parent[p] = []
                by_parent[p].append(row)
            advisor_groups = [{"parent_name": p, "children": kids} for p, kids in sorted(by_parent.items())]
        else:
            advisor_groups = get_advisor_parents_with_children()
            advisor_rows = get_all_advisor_companies()
        if not advisor_rows:
            st.markdown(
                "<div style='text-align:center;padding:80px 0;color:var(--text-3);'>"
                "<div style='font-size:32px;opacity:.2;margin-bottom:14px;'>◆</div>"
                "<div style='font-size:14px;'>No companies yet — Sync from Notion (Media Houses)</div></div>",
                unsafe_allow_html=True)
        else:
            st.markdown(sh(f"{len(advisor_rows)} COMPANIES (by parent)"), unsafe_allow_html=True)
            for grp in advisor_groups:
                with st.expander(f"**{grp['parent_name']}** — {len(grp['children'])} child(ren)", expanded=False):
                    for ch in grp["children"]:
                        is_sel_adv = st.session_state.get("sel_advisor_company_id") == ch["id"]
                        row_col1, row_col2 = st.columns([4, 0.5])
                        with row_col1:
                            st.markdown(
                                f"<div class='c' style='padding:8px 12px;'>"
                                f"<span style='font-size:13px;'>{ch['domain']}</span>"
                                f"</div>",
                                unsafe_allow_html=True)
                        with row_col2:
                            if st.button("→", key=f"adv_go_{ch['id']}", type="secondary", use_container_width=True):
                                st.session_state.sel_company = None
                                st.session_state.sel_contact = None
                                st.session_state.session_id = None
                                st.session_state.sel_advisor_company_id = ch["id"]
                                st.session_state.chat_messages = []
                                st.session_state.auto_brief_text = None
                                st.session_state.pending_plan = None
                                st.session_state.current_tab = "INTELLIGENCE"
                                st.rerun()
    else:
        # ── Primary DB: existing companies + contacts flow ──
        if st.session_state.get("clear_company_search"):
            st.session_state["csearch"] = ""
            st.session_state["liurl"] = ""
            st.session_state.pop("clear_company_search", None)
        if "linkedin_search_domain" in st.session_state:
            st.session_state["csearch"] = st.session_state.pop("linkedin_search_domain")
        cc1, cc2, cc3 = st.columns([2, 1, 1])
        with cc1:
            sq = st.text_input("Search", placeholder="Search domain or name...", label_visibility="collapsed", key="csearch")
        with cc2:
            if st.button("🔄 Sync Notion", key="nsync", use_container_width=True):
                with st.spinner("Syncing..."):
                    r = sync_notion_to_db()
                    if r.get("error"):
                        st.error(f"Error: {r['error']}")
                    else:
                        st.success(f"Synced {r['companies']} companies, {r['contacts']} contacts")
                    st.rerun()
        with cc3:
            if st.button("Reset", key="resetsel", use_container_width=True):
                st.session_state.sel_company = None
                st.session_state.sel_contact = None
                st.session_state.session_id = None
                st.session_state.sel_advisor_company_id = None
                st.session_state.chat_messages = []
                st.session_state.clear_company_search = True
                st.rerun()

        tcid = st.session_state.get("timeline_contact_id")
        tcoid = st.session_state.get("timeline_company_id")
        tname = st.session_state.get("timeline_contact_name") or "Contact"
        if tcid and tcoid:
            comp_for_timeline = get_company(tcoid)
            timeline_items = build_contact_timeline(
                tcoid, tcid, user["id"],
                company_domain=comp_for_timeline.get("domain") if comp_for_timeline else None,
                company_name=comp_for_timeline.get("name") if comp_for_timeline else None,
            )
            with st.expander(f"📅 Contact timeline — {tname}", expanded=True):
                close_col, _ = st.columns([1, 4])
                with close_col:
                    if st.button("Close timeline", key="close_timeline", use_container_width=True):
                        st.session_state.timeline_contact_id = None
                        st.session_state.timeline_company_id = None
                        st.session_state.timeline_contact_name = None
                        st.rerun()
                st.markdown("<div class='timeline-wrap'>", unsafe_allow_html=True)
                for item in timeline_items:
                    ts_display = _format_timeline_ts(item.get("ts"))
                    st.markdown(
                        timeline_item_html(
                            ts_display,
                            item.get("type"),
                            item.get("channel"),
                            item.get("status"),
                            item.get("summary"),
                            status_colors=ACTION_STATUS_COLORS,
                        ),
                        unsafe_allow_html=True,
                    )
                    full = (item.get("full_content") or "").strip()
                    if full:
                        with st.expander("Full content", expanded=False):
                            st.markdown(full)
                st.markdown("</div>", unsafe_allow_html=True)
                if not timeline_items:
                    st.caption("No timeline items yet for this contact.")

        with st.expander("🔗 Lookup by LinkedIn URL"):
            li_url = st.text_input("LinkedIn URL", placeholder="Paste LinkedIn URL...", key="liurl")
            if st.button("Find", key="lisync"):
                if li_url:
                    with st.spinner("Searching..."):
                        lead = lookup_lead_in_notion(li_url)
                        if lead and lead.get("domain"):
                            cid = upsert_company(domain=lead["domain"], name=lead.get("name") or lead["domain"],
                                publisher_type=lead.get("publisher_type"), content_type=lead.get("content_type"),
                                monthly_visits=lead.get("monthly_visits"), notion_page_id=lead.get("page_id"),
                                research_status=lead.get("research_status"), gmail_thread=lead.get("gmail_thread"),
                                extra_data={"research_notes": lead.get("research_notes", [])})
                            if lead.get("name"):
                                upsert_contact(company_id=cid, name=lead["name"], role="SPOC / Editor",
                                    email=lead.get("email"), linkedin=lead.get("linkedin"),
                                    city=lead.get("city"), state=lead.get("state"), country=lead.get("country"),
                                    notion_page_id=lead.get("page_id"))
                            st.session_state["linkedin_search_domain"] = lead["domain"]
                            spoc_label = f" — {lead['name']}" if lead.get("name") else ""
                            st.success(f"Found: {lead['domain']}{spoc_label}. Select it from the list below.")
                            st.rerun()
                        else:
                            st.error("No match in Notion")

        companies = search_companies(sq) if sq else get_all_companies()

        if sq and companies:
            sel_cid = st.selectbox(
                "Matching companies",
                options=[c["id"] for c in companies],
                format_func=lambda cid: next((f"{c['domain']} — {c.get('publisher_type','')}" for c in companies if c["id"] == cid), str(cid)),
                key="company_search_dropdown",
            )
            if sel_cid and sel_cid != st.session_state.sel_company:
                st.session_state.sel_company = sel_cid
                st.session_state.current_tab = "COMPANIES"
                st.rerun()
        if not companies:
            st.markdown(
                "<div style='text-align:center;padding:80px 0;color:var(--text-3);'>"
                "<div style='font-size:32px;opacity:.2;margin-bottom:14px;'>◆</div>"
                "<div style='font-size:14px;'>No companies yet — Sync from Notion</div></div>",
                unsafe_allow_html=True)
        else:
            st.markdown(sh(f"{len(companies)} COMPANIES"), unsafe_allow_html=True)
            for comp in companies:
                is_sel = st.session_state.sel_company == comp["id"]
                border = "border-left:3px solid var(--accent);" if is_sel else ""
                row_col1, row_col2 = st.columns([4, 0.35])
                with row_col1:
                    st.markdown(
                        f"<div class='c' style='padding:10px 14px;{border}'>"
                        f"<div style='display:flex;justify-content:space-between;'>"
                        f"<div><span style='font-size:14px;font-weight:500;'>{comp['domain']}</span>"
                        f" <span style='font-size:12px;color:var(--text-3);'>{comp.get('publisher_type','')}</span></div>"
                        f"<span style='font-size:12px;color:var(--text-2);'>{fmt_v(comp.get('monthly_visits'))}</span></div></div>",
                        unsafe_allow_html=True)
                with row_col2:
                    if st.button("→", key=f"company_arrow_{comp['id']}", type="secondary", use_container_width=True, help="Brief, research & power mapping"):
                        st.session_state.sel_company = comp["id"]
                        st.session_state.sel_contact = None
                        st.session_state.session_id = None
                        st.session_state.company_arrow_cid = comp["id"]
                        st.session_state.current_tab = "INTELLIGENCE"
                        st.rerun()
                cts = get_contacts(comp["id"])
                if cts:
                    with st.expander(f"👥 {len(cts)} contact(s)", expanded=is_sel):
                        for ct in cts:
                            cols = st.columns([2.5, 1.2, 0.4, 0.4])
                            with cols[0]:
                                scolor = CONTACT_STATUS_COLORS.get(ct.get("status", "not_contacted"), "gray")
                                slabel = CONTACT_STATUS_LABELS.get(ct.get("status", "not_contacted"), "")
                                st.markdown(
                                    f"<b>{ct['name']}</b> <span style='font-size:11px;color:var(--text-3);'>"
                                    f"{ct.get('role','')}</span> {bdg(slabel, scolor)}",
                                    unsafe_allow_html=True)
                            with cols[1]:
                                ns = st.selectbox("s", CONTACT_STATUSES,
                                    index=CONTACT_STATUSES.index(ct.get("status", "not_contacted")) if ct.get("status") in CONTACT_STATUSES else 0,
                                    format_func=lambda x: CONTACT_STATUS_LABELS.get(x, x),
                                    key=f"qs_{ct['id']}", label_visibility="collapsed")
                                if ns != ct.get("status"):
                                    update_contact_status(ct["id"], ns)
                                    st.rerun()
                            with cols[2]:
                                if st.button("→", key=f"go_{ct['id']}", type="secondary", use_container_width=True):
                                    select_contact(comp["id"], ct["id"])
                                    st.rerun()
                            with cols[3]:
                                if st.button("📅", key=f"tl_{ct['id']}", use_container_width=True, help="View contact timeline"):
                                    st.session_state.timeline_contact_id = ct["id"]
                                    st.session_state.timeline_company_id = comp["id"]
                                    st.session_state.timeline_contact_name = ct.get("name", "Contact")
                                    st.rerun()


# ════════════════════ INTELLIGENCE ════════════════════

with tabs[2]:  # INTELLIGENCE
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact
    _src = st.session_state.get("notion_companies_source", "Independent")
    aid = st.session_state.get("sel_advisor_company_id")
    is_advisor = (_src == "Media Houses" and aid is not None)
    if is_advisor:
        comp = get_advisor_company(aid)
        if not comp:
            st.error("Company not found.")
            comp = None
        else:
            adv_extra = comp.get("extra_data") or {}  # from DB (Supabase or SQLite)
            comp = {
                "id": comp["id"],
                "domain": comp.get("domain", "—"),
                "name": comp.get("domain") or comp.get("parent_name") or "—",
                "publisher_type": comp.get("parent_name") or "—",
                "content_type": "—",
                "monthly_visits": None,
                "research_status": "—",
                "notion_page_id": comp.get("notion_page_id"),
                "extra_data": adv_extra,
            }
    elif cid:
        comp = get_company(cid)
    else:
        comp = None
    if not comp:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);font-size:14px;'>Select a company from COMPANIES tab (or a child under Media Houses).</div>", unsafe_allow_html=True)
    else:
        cache_key = f"advisor_{aid}" if is_advisor else cid
        page_id = comp.get("notion_page_id")
        rn = []
        if page_id:
            if cache_key not in st.session_state.research_cache:
                with st.spinner("Loading from Notion..."):
                    rn = fetch_research_from_notion(page_id)
                    st.session_state.research_cache[cache_key] = rn
            else:
                rn = st.session_state.research_cache[cache_key]

            # Company brief & power mapping (Independent only): show when opened via arrow or when cached
            _arrow_cid = st.session_state.get("company_arrow_cid")
            _brief_cache = st.session_state.get("company_brief_cache") or {}
            if not is_advisor and cid and (_arrow_cid == cid or cid in _brief_cache):
                if _arrow_cid == cid and cid not in _brief_cache:
                    with st.spinner("Building company brief & power mapping (3+ SPOCs)…"):
                        spocs_ctx = power_scrape_company_spocs(comp.get("domain"))
                        brief_text = company_brief_with_power_mapping(
                            comp, research_notes=rn, company_spocs_web_context=spocs_ctx,
                        )
                        if brief_text:
                            if "company_brief_cache" not in st.session_state:
                                st.session_state.company_brief_cache = {}
                            st.session_state.company_brief_cache[cid] = {"text": brief_text, "domain": comp.get("domain")}
                        st.session_state.company_arrow_cid = None
                    st.rerun()
                if cid in (st.session_state.get("company_brief_cache") or {}):
                    _cached = st.session_state.company_brief_cache[cid]
                    st.markdown(sh_small("Company brief & power mapping"), unsafe_allow_html=True)
                    st.markdown(render_brief_card_html(_cached["text"]), unsafe_allow_html=True)
                    regen_col, _ = st.columns([1, 4])
                    with regen_col:
                        if st.button("🔄 Regenerate brief & SPOCs", key="regen_company_brief", use_container_width=True):
                            _cache = st.session_state.get("company_brief_cache") or {}
                            _cache.pop(cid, None)
                            st.session_state.company_brief_cache = _cache
                            st.session_state.company_arrow_cid = cid
                            st.rerun()
            if ctid and st.session_state.session_id:
                ct = get_contact(ctid)
                if ct:
                    _contact_label = ct.get("name") or "Unknown"
                    _domain = comp.get("domain") or ""
                    st.markdown(sh_small(f"Brief — {_contact_label}" + (f" · {_domain}" if _domain else "")), unsafe_allow_html=True)
                    st.caption("One entry for the brief: optional focus or instruction, then Improve brief.")
                    brief_entry = st.text_input(
                        "Brief",
                        placeholder="e.g. Focus on power mapping · or leave blank for full brief",
                        key=f"brief_entry_{cache_key}_{ctid}",
                        label_visibility="collapsed",
                    )
                    if st.session_state.auto_brief_text is None:
                        with st.spinner("Generating brief..."):
                            contact_web_ctx = search_web_for_person(
                                company_domain=comp.get("domain"),
                                person_name=ct.get("name"),
                                email=ct.get("email"),
                                linkedin_url=ct.get("linkedin"),
                            )
                            company_spocs_ctx = power_scrape_company_spocs(comp.get("domain"))
                            brief = auto_brief(
                                comp, ct, user["id"], st.session_state.session_id, rn,
                                contact_web_context=contact_web_ctx,
                                company_spocs_web_context=company_spocs_ctx,
                            )
                            st.session_state.auto_brief_text = brief or "Could not generate brief."
                    st.markdown(render_brief_card_html(st.session_state.auto_brief_text or ""), unsafe_allow_html=True)
                    brief_btn_col1, brief_btn_col2 = st.columns(2)
                    with brief_btn_col1:
                        if st.button("✨ Improve brief", key="rb", help="Regenerate the strategic brief using current research"):
                            st.session_state.auto_brief_text = None
                            st.rerun()
                    with brief_btn_col2:
                        if st.button("📁 Save to Drive", key="brief_save_drive", help="Save current brief as a Google Doc in your main Drive folder"):
                            _doc_name = comp.get("domain") or comp.get("name", "Company")
                            fid, link, err = create_doc_in_drive(f"{_doc_name} - Brief", st.session_state.auto_brief_text or "")
                            if fid:
                                st.toast("Brief saved to Google Drive (main folder)")
                                st.rerun()
                            else:
                                st.error(err or "Could not save to Drive.")

            st.markdown(sh_small("Company profile"), unsafe_allow_html=True)
            r1 = st.columns(5)
            for col, (l, v) in zip(r1, [
                ("Domain", comp.get("domain")), ("Publisher", comp.get("publisher_type")),
                ("Content", comp.get("content_type")), ("Visits", fmt_v(comp.get("monthly_visits"))),
                ("Research", comp.get("research_status")),
            ]):
                with col:
                    st.markdown(crd(l, v), unsafe_allow_html=True)

            # ── Research document: from Notion, from DB, or run deep research; always same edit + AI options ──
            st.markdown(sh_small("Research document"), unsafe_allow_html=True)
            if is_advisor:
                st.caption("Same power-scraped SPOCs and deep research flow as Independent — Run deep research and extend use web-sourced C-level contacts.")
            extra = comp.get("extra_data") or {}
            research_full_db = (extra.get("research_full") or "").strip() if not is_advisor else (extra.get("research_full") or "").strip()
            research_source_from_notion = False
            if not research_full_db and rn:
                research_full_display = "\n\n".join(
                    f"{'='*60}\n{note.get('title') or 'Section'}\n{'='*60}\n\n{(note.get('content') or '').strip()}"
                    for note in rn
                ).strip()
                research_source_from_notion = True
            else:
                research_full_display = research_full_db

            if not research_full_display and not rn:
                st.caption("No research yet. Run **deep research** below (same prompt as the Notion research bot). Once you have research, you can edit it manually, use AI to extend it, and save to DB, Drive, or Notion.")
                if has_perm("can_use_strategist") and st.button("🔬 Run deep research", key="run_res_doc", type="primary"):
                    err_msg = None
                    result = None
                    try:
                        pb = st.progress(0, text="Starting…")
                        def _prog(c, e):
                            pb.progress(min(c / 40000, 0.95), text=f"Researching… {c:,} chars")
                        result = run_research(comp.get("domain", ""), progress_callback=_prog)
                    except Exception as e:
                        err_msg = str(e)
                    if result:
                        if is_advisor:
                            merge_advisor_company_extra_data(aid, {"research_full": result, "research_full_updated_at": datetime.utcnow().isoformat()})
                        else:
                            merge_company_extra_data(cid, {"research_full": result, "research_full_updated_at": datetime.utcnow().isoformat()})
                        if page_id:
                            with st.spinner("Writing to Notion…"):
                                write_research_to_notion(page_id, result, comp.get("domain", ""))
                        if cache_key in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cache_key]
                        st.success("Research saved. Edit below and click Save to store everywhere.")
                        st.rerun()
                    else:
                        st.error("Research failed." + (f" {err_msg}" if err_msg else " Check ANTHROPIC_API_KEY in keys.env and that the model is available."))
            else:
                # Same flow whether research came from Notion or DB or deep research: manual edit + AI extend + save options
                _source_label = "Loaded from Notion" if research_source_from_notion and page_id else "Command Center"
                st.caption(f"**{_source_label}** — Edit sections below, use the AI command to generate or extend, then Save.")
                if page_id and research_source_from_notion:
                    if st.button("🔄 Refresh from Notion", key=f"refresh_notion_{cache_key}", use_container_width=False, help="Re-pull latest research from Notion"):
                        if cache_key in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cache_key]
                        st.rerun()
                sections = split_research_into_sections(research_full_display)
                section_bodies = []
                for i, (sec_title, body) in enumerate(sections):
                    with st.expander(sec_title, expanded=(i == 0)):
                        edited = st.text_area(
                            "Content",
                            value=body,
                            height=min(180, 120 + body.count("\n") * 18),
                            key=f"research_sec_{cache_key}_{i}",
                            label_visibility="collapsed",
                            placeholder="(No content for this section.)",
                        )
                        section_bodies.append(edited)
                # Current full document = concatenation of section edits (for AI command and Save)
                current_research_text = "\n\n".join(section_bodies).strip() if section_bodies else research_full_display
                save_col, delete_col, _ = st.columns([1, 1, 4])
                with save_col:
                    if st.button("💾 Save", key=f"save_research_full_{cache_key}", use_container_width=True, help="Save research to DB, Drive, and Notion"):
                        text = (current_research_text or "").strip()
                        if is_advisor:
                            merge_advisor_company_extra_data(aid, {"research_full": text, "research_full_updated_at": datetime.utcnow().isoformat()})
                        else:
                            merge_company_extra_data(cid, {"research_full": text, "research_full_updated_at": datetime.utcnow().isoformat()})
                        st.session_state.auto_brief_text = None
                        ok_drive = True
                        if text:
                            _doc_name = comp.get("domain") or comp.get("name", "Company")
                            fid, _, err = create_doc_in_drive(f"{_doc_name} - Research", text)
                            ok_drive = bool(fid)
                            if not ok_drive and err:
                                st.toast(f"Drive: {err}")
                        ok_notion = True
                        if page_id and text and len(text) >= 500:
                            with st.spinner("Writing to Notion…"):
                                ok_notion = write_research_to_notion(page_id, text, comp.get("domain", ""))
                            if ok_notion and cache_key in st.session_state.get("research_cache", {}):
                                del st.session_state.research_cache[cache_key]
                        if not text:
                            st.success("Saved to DB.")
                        elif ok_drive and (not (page_id and len(text) >= 500) or ok_notion):
                            st.success("Saved to DB, Drive, and Notion." if (page_id and len(text) >= 500 and ok_notion) else "Saved to DB and Drive.")
                        else:
                            st.success("Saved to DB." + (" Drive failed." if not ok_drive else " Notion failed."))
                        st.rerun()
                with delete_col:
                    if st.button("🗑️ Delete deep research", key=f"delete_research_{cache_key}", use_container_width=True, help="Clear deep research from Command Center (DB)"):
                        if is_advisor:
                            merge_advisor_company_extra_data(aid, {"research_full": "", "research_full_updated_at": datetime.utcnow().isoformat()})
                        else:
                            merge_company_extra_data(cid, {"research_full": "", "research_full_updated_at": datetime.utcnow().isoformat()})
                        st.session_state.auto_brief_text = None
                        if cache_key in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cache_key]
                        st.success("Deep research deleted.")
                        st.rerun()

                st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
                st.markdown(sh_small("Deep research — generate or extend"), unsafe_allow_html=True)
                st.caption("e.g. \"Add a section on competitor landscape\" or \"Expand the power mapping with the CFO\".")
                cmd_col1, cmd_col2 = st.columns([3, 1])
                with cmd_col1:
                    research_cmd = st.text_input(
                        "Deep research command",
                        placeholder="Describe what to add or change…",
                        key=f"research_cmd_{cache_key}",
                        label_visibility="collapsed",
                    )
                with cmd_col2:
                    run_cmd = st.button("Run", key=f"research_cmd_run_{cache_key}", type="primary", use_container_width=True)
                if run_cmd and (research_cmd or "").strip():
                    with st.spinner("Running command…"):
                        cmd_result = research_command_ai(comp.get("domain", ""), current_research_text or research_full_display, research_cmd.strip())
                    if cmd_result:
                        st.session_state["research_command_result"] = cmd_result
                        st.session_state["research_command_result_cid"] = cache_key
                        st.session_state["research_command_replace_key"] = f"research_full_edit_{cache_key}"
                        st.rerun()
                    else:
                        st.error("Command failed.")

                if st.session_state.get("research_command_result_cid") == cache_key and st.session_state.get("research_command_result"):
                    cmd_result = st.session_state["research_command_result"]
                    with st.expander("AI result — Replace or Append", expanded=True):
                        _cmd_display = cmd_result[:25000] + ("…" if len(cmd_result) > 25000 else "")
                        st.markdown(f"<div class='markdown-rendered' style='max-height:300px;overflow-y:auto;'>{markdown_to_html(_cmd_display)}</div>", unsafe_allow_html=True)
                        app_col1, app_col2, app_col3 = st.columns([1, 1, 2])
                        with app_col1:
                            if st.button("Replace document", key=f"cmd_replace_{cache_key}", use_container_width=True):
                                if is_advisor:
                                    merge_advisor_company_extra_data(aid, {"research_full": cmd_result, "research_full_updated_at": datetime.utcnow().isoformat()})
                                else:
                                    merge_company_extra_data(cid, {"research_full": cmd_result, "research_full_updated_at": datetime.utcnow().isoformat()})
                                st.session_state.auto_brief_text = None
                                st.session_state.pop("research_command_result", None)
                                st.session_state.pop("research_command_result_cid", None)
                                st.success("Document replaced.")
                                st.rerun()
                        with app_col2:
                            if st.button("Append to document", key=f"cmd_append_{cache_key}", use_container_width=True):
                                current = (current_research_text or research_full_display or "").strip()
                                merged = f"{current}\n\n{cmd_result}" if current else cmd_result
                                if is_advisor:
                                    merge_advisor_company_extra_data(aid, {"research_full": merged, "research_full_updated_at": datetime.utcnow().isoformat()})
                                else:
                                    merge_company_extra_data(cid, {"research_full": merged, "research_full_updated_at": datetime.utcnow().isoformat()})
                                st.session_state.auto_brief_text = None
                                st.session_state.pop("research_command_result", None)
                                st.session_state.pop("research_command_result_cid", None)
                                st.success("Appended.")
                                st.rerun()
                        with app_col3:
                            if st.button("Dismiss", key=f"cmd_dismiss_{cache_key}", use_container_width=True):
                                st.session_state.pop("research_command_result", None)
                                st.session_state.pop("research_command_result_cid", None)
                                st.rerun()

            st.markdown(sh_small("Request / suggest / look up person"), unsafe_allow_html=True)
            st.caption("Paste a LinkedIn URL or email to run AI research on that person (in context of this company), or describe what’s missing to get a suggestion.")
            intel_input = st.text_area(
                "Request",
                placeholder="E.g. paste linkedin.com/in/... or name@company.com · or 'Power mapping missing the CFO'",
                height=88,
                key="intel_request",
                label_visibility="collapsed",
            )
            if st.button("Look up person or get suggestion", key="intel_submit", type="secondary", help="Paste LinkedIn/email to research that person, or describe what to add for an AI suggestion"):
                raw = (intel_input or "").strip()
                if not raw:
                    st.warning("Enter a request, LinkedIn URL, or email.")
                else:
                    linkedin_match = re.search(r"https?://[^\s]*linkedin\.com[^\s]*", raw, re.IGNORECASE)
                    email_match = re.search(r"[\w.-]+@[\w.-]+\.\w+", raw)
                    if linkedin_match:
                        url = linkedin_match.group(0).strip().rstrip(".,;:)")
                        web_snippet = None
                        with st.spinner("Searching the web for this person..."):
                            web_snippet = search_web_for_person(linkedin_url=url, company_domain=comp.get("domain"))
                        st.session_state.intel_lookup_lead = {"linkedin": url, "name": "", "email": "", "role": "Contact", "domain": comp.get("domain", "")}
                        with st.spinner("Running AI research on this person..."):
                            brief = research_person_for_company(comp=comp, research_notes=rn, linkedin_url=url, web_search_results=web_snippet)
                        st.session_state.intel_person_brief = brief
                        st.session_state.intel_suggestion = None
                        st.rerun()
                    elif email_match:
                        em = email_match.group(0).strip()
                        web_snippet = None
                        with st.spinner("Searching the web for this person..."):
                            web_snippet = search_web_for_person(email=em, company_domain=comp.get("domain"))
                        st.session_state.intel_lookup_lead = {"email": em, "name": "", "linkedin": "", "role": "Contact", "domain": comp.get("domain", "")}
                        with st.spinner("Running AI research on this person..."):
                            brief = research_person_for_company(comp=comp, research_notes=rn, email=em, web_search_results=web_snippet)
                        st.session_state.intel_person_brief = brief
                        st.session_state.intel_suggestion = None
                        st.rerun()
                    else:
                        web_snippet = None
                        with st.spinner("Searching the web for this person..."):
                            web_snippet = search_web_for_person_by_name(raw, comp.get("domain"))
                        if web_snippet:
                            with st.spinner("Running AI research on this person..."):
                                brief = research_person_for_company(comp=comp, research_notes=rn, person_name=raw, web_search_results=web_snippet)
                            st.session_state.intel_lookup_lead = {"name": raw, "email": "", "linkedin": "", "role": "Contact", "domain": comp.get("domain", "")}
                            st.session_state.intel_person_brief = brief
                            st.session_state.intel_suggestion = None
                            st.rerun()
                        else:
                            with st.spinner("Asking AI for suggestion..."):
                                suggestion = intelligence_request(comp, rn, raw)
                            st.session_state.intel_suggestion = suggestion
                            st.session_state.intel_suggestion_raw = raw
                            st.session_state.intel_lookup_lead = None
                            st.session_state.intel_person_brief = None
                            st.rerun()

            if st.session_state.get("intel_lookup_lead") or st.session_state.get("intel_person_brief"):
                lead = st.session_state.get("intel_lookup_lead") or {}
                brief = st.session_state.get("intel_person_brief") or ""
                label = (lead.get("name") or lead.get("email") or "Contact").strip() or "Person"
                st.markdown(sh_small(f"Research — {label}"), unsafe_allow_html=True)
                if brief:
                    st.markdown(render_brief_card_html(brief), unsafe_allow_html=True)
                if lead.get("domain") or lead.get("linkedin") or lead.get("email"):
                    st.caption("You can add this person to this company's contacts and optionally sync to Notion below.")
                with st.form("add_contact_from_intel"):
                    name = st.text_input("Name", value=lead.get("name") or "", key="intel_add_name")
                    role = st.text_input("Role", value=lead.get("role") or "Contact", key="intel_add_role")
                    email = st.text_input("Email", value=lead.get("email") or "", key="intel_add_email")
                    linkedin = st.text_input("LinkedIn URL", value=lead.get("linkedin") or "", key="intel_add_li")
                    city = st.text_input("City", value=lead.get("city") or "", key="intel_add_city")
                    state = st.text_input("State", value=lead.get("state") or "", key="intel_add_state")
                    country = st.text_input("Country", value=lead.get("country") or "", key="intel_add_country")
                    add_notion = st.checkbox("Also create/update in Notion", value=True, key="intel_add_notion")
                    sub1, sub2 = st.columns(2)
                    with sub1:
                        if st.form_submit_button("Add SPOC to database & Notion"):
                            display_name = (name or email or "Unknown").strip()
                            if not display_name:
                                st.error("Name or email required.")
                            else:
                                notion_page_id = None
                                if add_notion:
                                    notion_page_id = create_notion_contact_page(
                                        comp.get("domain", ""), display_name,
                                        email=email.strip() or None, linkedin=linkedin.strip() or None,
                                        role=role.strip() or None, city=city.strip() or None,
                                        state=state.strip() or None, country=country.strip() or None,
                                    )
                                upsert_contact(
                                    cid,
                                    name=display_name,
                                    role=role.strip() or None,
                                    email=email.strip() or None,
                                    linkedin=linkedin.strip() or None,
                                    city=city.strip() or None,
                                    state=state.strip() or None,
                                    country=country.strip() or None,
                                    notion_page_id=notion_page_id,
                                )
                                st.session_state.intel_lookup_lead = None
                                st.session_state.intel_person_brief = None
                                st.success("Contact added to database." + (" Synced to Notion." if notion_page_id else ""))
                                st.rerun()
                    with sub2:
                        if st.form_submit_button("Dismiss"):
                            st.session_state.intel_lookup_lead = None
                            st.session_state.intel_person_brief = None
                            st.rerun()
                if st.button("Dismiss", key="intel_dismiss_lead", use_container_width=True):
                    st.session_state.intel_lookup_lead = None
                    st.session_state.intel_person_brief = None
                    st.rerun()

            if st.session_state.get("intel_suggestion"):
                st.markdown(sh_small("Suggestion — save as override?"), unsafe_allow_html=True)
                st.markdown(render_brief_card_html(st.session_state.intel_suggestion or ""), unsafe_allow_html=True)
                section_options = [n.get("title", "Untitled") for n in rn] if rn else []
                section_options.append("New section")
                apply_to = st.selectbox("Apply to section", options=section_options, key="intel_apply_section")
                new_title = ""
                if apply_to == "New section":
                    new_title = st.text_input("New section title", placeholder="e.g. Competitor overview", key="intel_new_title")
                if st.button("Save as research override", key="intel_save_override", type="primary"):
                    overrides = (comp.get("extra_data") or {}).get("research_overrides") or {}
                    title = (new_title or "").strip() if apply_to == "New section" else apply_to
                    if title:
                        overrides[title] = st.session_state.intel_suggestion
                        merge_company_extra_data(cid, {"research_overrides": overrides})
                        st.session_state.intel_suggestion = None
                        st.session_state.auto_brief_text = None
                        st.success("Saved as override. Strategist will use it.")
                        st.rerun()
                    else:
                        st.warning("Enter a section title for 'New section'.")
                if st.button("Dismiss suggestion", key="intel_dismiss_sugg"):
                    st.session_state.intel_suggestion = None
                    st.rerun()

            st.markdown(sh_small("Research notes"), unsafe_allow_html=True)
            if rn:
                if "research_summary_cache" not in st.session_state:
                    st.session_state.research_summary_cache = {}
                if cid in st.session_state.research_summary_cache:
                    st.markdown(sh_small("Company context (short)"), unsafe_allow_html=True)
                    st.markdown(render_brief_card_html(st.session_state.research_summary_cache.get(cid) or ""), unsafe_allow_html=True)
                elif st.button("Get short context summary", key="intel_summary_btn"):
                    with st.spinner("Summarizing..."):
                        summary = summarize_research_to_context(comp, rn)
                        if summary:
                            st.session_state.research_summary_cache[cid] = summary
                            st.rerun()
                st.caption(f"{len(rn)} sections below. Use the request box above to research a person in this context.")
                st.markdown(sh_small("Full sections"), unsafe_allow_html=True)
                for idx, note in enumerate(rn):
                    with st.expander(note["title"]):
                        title = note.get("title", "").strip() or "Untitled"
                        overrides = (comp.get("extra_data") or {}).get("research_overrides") or {}
                        existing = overrides.get(title, "")
                        has_override = isinstance(existing, str) and existing.strip()

                        if has_override:
                            st.info("Using local override for strategist (Notion unchanged).")

                        st.markdown(
                            f"<div style='font-size:12px;color:var(--text-3);margin-bottom:6px;'>"
                            f"Source: Notion</div>",
                            unsafe_allow_html=True,
                        )
                        _note_content = (note.get("content") or "")[:6000]
                        st.markdown(markdown_to_html(_note_content), unsafe_allow_html=True)

                        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
                        st.markdown(sh_small("Edit / correct (local override)"), unsafe_allow_html=True)
                        new_txt = st.text_area(
                            "Override",
                            value=(existing if has_override else (note.get("content") or "")),
                            height=220,
                            key=f"ovr_{cid}_{idx}",
                            label_visibility="collapsed",
                        )
                        b1, b2, b3 = st.columns([1, 1, 2])
                        with b1:
                            if st.button("💾 Save override", key=f"saveovr_{cid}_{idx}", use_container_width=True):
                                overrides = (comp.get("extra_data") or {}).get("research_overrides") or {}
                                overrides[title] = new_txt
                                merge_company_extra_data(cid, {"research_overrides": overrides})
                                st.session_state.auto_brief_text = None
                                st.success("Saved.")
                                st.rerun()
                        with b2:
                            if st.button("🧹 Clear override", key=f"clr_{cid}_{idx}", use_container_width=True):
                                overrides = (comp.get("extra_data") or {}).get("research_overrides") or {}
                                if title in overrides:
                                    del overrides[title]
                                merge_company_extra_data(cid, {"research_overrides": overrides})
                                st.session_state.auto_brief_text = None
                                st.success("Cleared.")
                                st.rerun()
                        with b3:
                            st.caption("Overrides are stored locally (in your DB) and used by the strategist + briefs.")
            else:
                st.info("No research in Notion. Use **Run deep research** in the Research document section above to generate full research (same as Notion bot).")

            if GOOGLE_SERVICE_ACCOUNT_JSON:
                df = list_drive_files(company_name=comp.get("domain", ""))
                if df:
                    st.markdown(sh_small(f"Drive ({len(df)})"), unsafe_allow_html=True)
                    for f in df[:10]:
                        st.markdown(f"<div class='c' style='padding:8px 14px;'><a href='{f.get('webViewLink','#')}' target='_blank'>{f['name']}</a></div>", unsafe_allow_html=True)

            st.markdown(sh_small("Contacts"), unsafe_allow_html=True)
            for ct in get_contacts(cid):
                ct1, ct2 = st.columns([3, 0.5])
                with ct1:
                    scolor = CONTACT_STATUS_COLORS.get(ct.get("status", "not_contacted"), "gray")
                    slabel = CONTACT_STATUS_LABELS.get(ct.get("status", "not_contacted"), "")
                    lt, _ = get_local_time(ct.get("country", ""), ct.get("state", ""))
                    active = ctid == ct["id"]
                    bdr = "border-left:3px solid var(--accent);" if active else ""
                    st.markdown(
                        f"<div class='c' style='padding:10px 14px;{bdr}'>"
                        f"<div style='display:flex;justify-content:space-between;'>"
                        f"<span style='font-weight:500;'>{ct['name']}</span>{bdg(slabel, scolor)}</div>"
                        f"<div style='font-size:12px;color:var(--text-3);margin-top:3px;'>"
                        f"{ct.get('role','')} · {ct.get('email','')} · {lt}</div></div>",
                        unsafe_allow_html=True)
                with ct2:
                    if st.button("Select" if not active else "✓", key=f"sct_{ct['id']}", use_container_width=True):
                        select_contact(cid, ct["id"])
                        st.rerun()


# ════════════════════ STRATEGIST ════════════════════

with tabs[3]:  # STRATEGIST
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact
    sid = st.session_state.session_id
    _src_s = st.session_state.get("notion_companies_source", "Independent")
    _aid_s = st.session_state.get("sel_advisor_company_id")
    if _src_s == "Media Houses" and _aid_s:
        st.markdown(
            "<div style='text-align:center;padding:80px 0;color:var(--text-3);'>"
            "<div style='font-size:24px;opacity:.2;margin-bottom:12px;'>◆</div>"
            "<div style='font-size:14px;font-weight:500;'>Strategist is contact-based</div>"
            "<div style='font-size:13px;margin-top:6px;'>You have a Media House child selected. For strategy and outreach plans, switch to <b>Independent</b> companies, add this domain and contacts, then select a contact.</div></div>",
            unsafe_allow_html=True)
    elif not cid or not ctid or not sid:
        st.markdown(
            "<div style='text-align:center;padding:80px 0;color:var(--text-3);'>"
            "<div style='font-size:24px;opacity:.2;margin-bottom:12px;'>◆</div>"
            "<div style='font-size:14px;font-weight:500;'>Select a company and contact to start</div>"
            "<div style='font-size:13px;margin-top:6px;'>COMPANIES → expand → click →</div></div>",
            unsafe_allow_html=True)
    elif not has_perm("can_use_strategist"):
        st.warning("Strategist access required.")
    else:
        comp = get_company(cid)
        ct = get_contact(ctid)
        if not comp or not ct:
            st.error("Not found.")
        else:
            # ── FROM / TO selectors ──
            sel1, sel2, sel3 = st.columns([1.5, 1.5, 1])
            with sel1:
                all_users = get_all_users()
                user_names = {u["id"]: display_full_name(u["username"]) for u in all_users}
                sender_id = st.selectbox(
                    "FROM (sender)",
                    options=[u["id"] for u in all_users],
                    format_func=lambda x: f"👤 {user_names.get(x, '?')}",
                    index=[u["id"] for u in all_users].index(st.session_state.sender_id) if st.session_state.sender_id in [u["id"] for u in all_users] else 0,
                    key="from_sel",
                )
                st.session_state.sender_id = sender_id
                sender_name = user_names.get(sender_id, "")

            with sel2:
                contacts = get_contacts(cid)
                ct_names = {c["id"]: f"{c['name']} — {c.get('role','')}" for c in contacts}
                to_id = st.selectbox(
                    "TO (recipient)",
                    options=[c["id"] for c in contacts],
                    format_func=lambda x: f"📨 {ct_names.get(x, '?')}",
                    index=[c["id"] for c in contacts].index(ctid) if ctid in [c["id"] for c in contacts] else 0,
                    key="to_sel",
                )
                if to_id != ctid:
                    select_contact(cid, to_id)
                    st.rerun()

            with sel3:
                score = compute_lead_score(comp, ct)
                lt, _ = get_local_time(ct.get("country", ""), ct.get("state", ""))
                si = "🟢" if score >= 60 else "🟡" if score >= 35 else "🔴"
                st.markdown(
                    f"<div style='padding:8px 0;'>"
                    f"<div style='font-size:12px;color:var(--text-3);'>Score: {si} {score} · {lt}</div>"
                    f"<div style='font-size:11px;color:var(--text-3);'>{comp['domain']}</div></div>",
                    unsafe_allow_html=True)

            # ── Contact details & enrichment ──
            st.markdown(sh_small("Contact details & enrichment"), unsafe_allow_html=True)
            # Clear enrichment input when user clicks Clear (must run before text_input is created)
            enrich_result_key = f"enrich_result_{ctid}"
            if st.session_state.get("enrich_clear_input"):
                st.session_state["enrich_lookup_input"] = ""
                st.session_state.pop("last_enrichment_record_id", None)
                st.session_state.pop(enrich_result_key, None)
                st.session_state.pop("enrich_clear_input", None)
            db_email = (ct.get("email") or "").strip()
            db_linkedin = (ct.get("linkedin") or "").strip()
            needs_enrichment = not db_email and not db_linkedin
            if db_email or db_linkedin:
                ecol1, ecol2 = st.columns(2)
                with ecol1:
                    st.markdown(
                        f"<div class='c' style='padding:10px 14px;'><div class='c-l'>Email (DB)</div>"
                        f"<div class='c-v'>{db_email or '—'} {bdg('From DB', 'gray') if db_email else ''}</div></div>",
                        unsafe_allow_html=True)
                with ecol2:
                    st.markdown(
                        f"<div class='c' style='padding:10px 14px;'><div class='c-l'>LinkedIn (DB)</div>"
                        f"<div class='c-v'>{db_linkedin[:50] + '…' if db_linkedin and len(db_linkedin) > 50 else (db_linkedin or '—')} "
                        f"{bdg('From DB', 'gray') if db_linkedin else ''}</div></div>",
                        unsafe_allow_html=True)
            if needs_enrichment or not db_email:
                st.markdown(
                    "<div style='background:var(--warn-bg);border:1px solid #f59e0b;border-radius:var(--radius);"
                    "padding:12px 14px;margin:8px 0;font-size:13px;'>"
                    "No verified email found. Run enrichment?</div>",
                    unsafe_allow_html=True)
            enrich_input = st.text_input(
                "Look up by LinkedIn URL or email",
                placeholder="Paste LinkedIn URL or email to enrich...",
                key="enrich_lookup_input",
                label_visibility="collapsed",
            )
            lookup_identifier = (enrich_input or "").strip() or (db_linkedin or db_email or "").strip()
            run_col, clear_col, credits_col = st.columns([1, 0.6, 1])
            with run_col:
                run_enrich = st.button("🔍 Run enrichment", key="run_enrichment", type="primary", use_container_width=True)
            with clear_col:
                if st.button("Clear", key="enrich_clear_btn", use_container_width=True, help="Clear box and result to add another LinkedIn/email"):
                    st.session_state.enrich_clear_input = True
                    st.rerun()
            with credits_col:
                credits = get_signalhire_credits()
                if credits is not None:
                    st.caption(f"SignalHire credits: {credits}")
                else:
                    st.caption("Enrichment uses mock data when no API/callback")
            if run_enrich:
                if not lookup_identifier:
                    st.warning("Enter a LinkedIn URL or email above, or select a contact that has one.")
                else:
                    try:
                        with st.spinner("Enriching contact..."):
                            record_id, req_id, status = request_enrichment(lookup_identifier, user["id"])
                        if record_id and status == "completed":
                            st.session_state[enrich_result_key] = record_id
                            st.success("Enrichment complete.")
                            st.rerun()
                        elif record_id and status == "pending":
                            st.info("Enrichment requested. Results will appear when the callback is received. You can refresh in a moment.")
                            st.session_state[enrich_result_key] = record_id
                            st.rerun()
                        else:
                            st.error("Enrichment failed. Check API key and identifier, or see terminal logs.")
                    except Exception as e:
                        st.error(f"Enrichment error: {e}")
                        import traceback
                        st.code(traceback.format_exc())

            # Show enrichment result only for this contact (so switching company/contact doesn't show another person)
            show_record_id = st.session_state.get(enrich_result_key)
            if show_record_id:
                try:
                    er = get_enrichment_result(show_record_id)
                except Exception:
                    er = None
                if er and er.get("status") == "completed" and er.get("result_json"):
                    res = er["result_json"]
                    display_name = (res.get("fullName") or "").strip() or (ct.get("name") or "").strip() or "Contact"
                    st.markdown(
                        "<div style='background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);"
                        "padding:16px;margin-top:12px;'>"
                        "<div style='font-size:12px;font-weight:600;color:var(--text-2);margin-bottom:10px;'>"
                        "Enrichment result — Verified vs Enriched</div>",
                        unsafe_allow_html=True)
                    st.markdown(
                        f"<div style='font-size:13px;font-weight:500;margin-bottom:8px;'>{display_name}"
                        f" <span style='font-size:11px;color:var(--text-3);font-weight:400;'>· {comp.get('domain','')}</span></div>"
                        f"<div style='font-size:12px;color:var(--text-3);margin-bottom:12px;'>{res.get('headline') or ''}</div>",
                        unsafe_allow_html=True)
                    for email in res.get("emails") or []:
                        src = email.get("source", "Enriched")
                        conf = email.get("confidence", 0)
                        st.markdown(
                            f"<div class='c' style='padding:8px 12px;'><div class='c-l'>📧 {email.get('value','')}</div>"
                            f"<div class='c-v'>{bdg(src, 'green' if src == 'Verified' else 'blue')} "
                            f"Confidence: {conf}% · {res.get('source','SignalHire')}</div></div>",
                            unsafe_allow_html=True)
                    for phone in res.get("phones") or []:
                        src = phone.get("source", "Enriched")
                        conf = phone.get("confidence", 0)
                        st.markdown(
                            f"<div class='c' style='padding:8px 12px;'><div class='c-l'>📞 {phone.get('value','')}</div>"
                            f"<div class='c-v'>{bdg(src, 'green' if src == 'Verified' else 'blue')} Confidence: {conf}%</div></div>",
                            unsafe_allow_html=True)
                    if res.get("linkedin"):
                        st.markdown(
                            f"<div class='c' style='padding:8px 12px;'><div class='c-l'>LinkedIn</div>"
                            f"<div class='c-v'><a href='{res['linkedin']}' target='_blank' rel='noopener'>Profile</a></div></div>",
                            unsafe_allow_html=True)
                    if not (res.get("emails") or res.get("phones")) and res.get("linkedin"):
                        st.caption("To get company/personal email and phone for this profile, set up SignalHire callback (SIGNALHIRE_CALLBACK_BASE_URL in keys.env).")
                    st.markdown("</div>", unsafe_allow_html=True)
                    # Add contact to company
                    st.markdown(sh_small("Add enriched contact to company"), unsafe_allow_html=True)
                    add_cid = st.selectbox(
                        "Company",
                        options=[cid] + [c["id"] for c in get_all_companies() if c["id"] != cid],
                        format_func=lambda x: get_company(x).get("domain", str(x)) if get_company(x) else str(x),
                        index=0,
                        key="enrich_add_company",
                    )
                    add_name = st.text_input("Contact name", value=res.get("fullName") or "", key="enrich_add_name")
                    if st.button("✅ Add contact to company", key="add_enriched_contact", type="primary"):
                        if not add_name or not add_cid:
                            st.warning("Select company and enter contact name.")
                        else:
                            primary_email = (res.get("emails") or [{}])[0].get("value", "") if res.get("emails") else ""
                            primary_phone = (res.get("phones") or [{}])[0].get("value", "") if res.get("phones") else ""
                            extra = {
                                "enrichment": {
                                    "source": res.get("source", "SignalHire"),
                                    "confidence": res.get("confidence", 0),
                                    "enriched_at": datetime.utcnow().isoformat(),
                                }
                            }
                            new_id = upsert_contact(
                                add_cid, add_name.strip(),
                                email=primary_email or None,
                                linkedin=res.get("linkedin") or None,
                                role=res.get("headline") or None,
                                extra_data=extra,
                            )
                            st.success(f"Contact “{add_name.strip()}” added to {get_company(add_cid).get('domain','company')}.")
                            st.session_state.pop(enrich_result_key, None)
                            st.rerun()

            # ── Quick actions + Sequence builder ──
            q1, q2, q3, q4, q5 = st.columns(5)
            with q1:
                if st.button("💬 LinkedIn", key="qa1", use_container_width=True):
                    st.session_state.qa = "Draft a LinkedIn connection request for this contact. Under 300 characters. Specific to research."
            with q2:
                if st.button("📧 Email", key="qa2", use_container_width=True):
                    st.session_state.qa = "Draft the initial outreach email with subject line. Human and direct."
            with q3:
                if st.button("⏰ Timing", key="qa3", use_container_width=True):
                    st.session_state.qa = "What's the optimal day and time for first outreach? Specific: day, time, timezone."
            with q4:
                if st.button("📋 Sequence", key="qa4", use_container_width=True):
                    st.session_state.qa = "Design a 4-touch outreach sequence with timing, channel, and angle per touch."
            with q5:
                if st.button("🎯 Full Plan", key="gen_plan", use_container_width=True):
                    with st.spinner("Building outreach plan..."):
                        rn = st.session_state.research_cache.get(cid, [])
                        plan = generate_outreach_plan(comp, ct, user["id"], sid, sender_name, rn)
                        if plan:
                            st.session_state.pending_plan = plan
                        else:
                            st.error("Could not generate plan.")

            # ── Pending plan preview ──
            if st.session_state.pending_plan:
                plan = st.session_state.pending_plan
                st.markdown(sh(f"OUTREACH PLAN — {len(plan)} STEPS"), unsafe_allow_html=True)
                st.markdown(
                    "<div style='background:var(--accent-bg);border:1px solid #c7d2fe;border-radius:var(--radius);"
                    "padding:16px;margin-bottom:12px;'>",
                    unsafe_allow_html=True)

                for step in plan:
                    with st.container():
                        st.markdown("<div class='sequence-step-card'></div>", unsafe_allow_html=True)
                        ch_icon = "💬" if "linkedin" in step["channel"].lower() else "📧"
                        content_draft = (step.get("content_draft") or "").strip()
                        preview_len = 120
                        st.markdown(
                            f"<div class='sequence-step-header'>"
                            f"<span style='font-size:13px;font-weight:600;'>"
                            f"{ch_icon} Step {step['step']}: {step['channel']} — Day {step['day_offset']}</span>"
                            f"<span style='font-size:11px;color:var(--text-3);'>{step.get('timing_note','')}</span></div>"
                            f"<div style='font-size:12px;color:var(--text-2);'>{step['description']}</div>"
                            f"<div class='sequence-step-divider'></div>",
                            unsafe_allow_html=True)
                        if content_draft:
                            with st.expander(f"📄 Read full message" + (f" — {content_draft[:preview_len]}..." if len(content_draft) > preview_len else ""), expanded=False):
                                st.markdown(
                                    f"<div style='font-size:12px;background:#fff;padding:10px 12px;border-radius:6px;border:1px solid #e5e7eb;white-space:pre-wrap;'>{(content_draft)}</div>",
                                    unsafe_allow_html=True)
                        if "email" in step.get("channel", "").lower() and step.get("content_draft"):
                            st.markdown("<div class='sequence-step-divider'></div>", unsafe_allow_html=True)
                            subj, bod = parse_email_content(step["content_draft"])
                            _render_save_to_gmail(
                                ct.get("email", ""), subj, bod,
                                f"plan_{st.session_state.session_id}_{step['step']}",
                            )

                st.markdown("</div>", unsafe_allow_html=True)

                pc1, pc2 = st.columns(2)
                with pc1:
                    if st.button("✅ Add Full Plan to Timeline", key="add_plan", use_container_width=True, type="primary"):
                        actions_for_db = []
                        for step in plan:
                            actions_for_db.append({
                                "action_type": "Outreach",
                                "channel": step["channel"],
                                "description": f"Step {step['step']}: {step['description']}",
                                "status": "planned",
                                "scheduled_at": step.get("scheduled_at"),
                                "content_sent": step.get("content_draft"),
                                "context_note": step.get("timing_note"),
                            })
                        ids, seq_id = bulk_log_actions(sid, sender_id, actions_for_db)
                        st.session_state.pending_plan = None
                        st.success(f"Added {len(ids)} steps as sequence {seq_id}")
                        st.rerun()
                with pc2:
                    c1, c2 = st.columns(2)
                    with c1:
                        ics = _build_ics(plan, title=f"LH2 Outreach — {comp.get('domain','')}")
                        st.download_button(
                            "📅 Download .ics",
                            data=ics,
                            file_name=f"lh2_{comp.get('domain','outreach')}_plan.ics",
                            mime="text/calendar",
                            use_container_width=True,
                        )
                    with c2:
                        if st.button("❌ Discard", key="discard_plan", use_container_width=True):
                            st.session_state.pending_plan = None
                            st.rerun()

            # ── Chat messages ──
            for m in st.session_state.chat_messages:
                ts = (m.get("created_at") or "")
                ts_short = ts[11:16] if len(ts) > 16 else ""
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])
                    if ts_short:
                        st.markdown(f"<div style='font-size:10px;color:var(--text-3);margin-top:4px;'>{ts_short}</div>", unsafe_allow_html=True)

            # ── Input ──
            qa = st.session_state.pop("qa", None)
            user_input = st.chat_input(f"Message strategist as {sender_name}...")
            prompt = qa or user_input

            if prompt:
                with st.chat_message("user"):
                    st.markdown(prompt)
                save_message(sid, "user", prompt)
                st.session_state.chat_messages.append({"role": "user", "content": prompt, "created_at": datetime.utcnow().isoformat()})
                rn = st.session_state.research_cache.get(cid, [])
                with st.chat_message("assistant"):
                    chunks = []
                    ph = st.empty()
                    for chunk in get_strategist_response_stream(comp, ct, st.session_state.chat_messages, prompt, user["id"], sid, rn):
                        chunks.append(chunk)
                        ph.markdown("".join(chunks) + "▌")
                    response = "".join(chunks)
                    ph.markdown(response)
                save_message(sid, "assistant", response)
                st.session_state.chat_messages.append({"role": "assistant", "content": response, "created_at": datetime.utcnow().isoformat()})
                maybe_update_summary(sid, comp, ct)
                extract_episodic_memories(sid, response, comp)
                detected = parse_ai_actions(response)
                if detected:
                    st.markdown(sh(f"{len(detected)} ACTION(S) DETECTED"), unsafe_allow_html=True)
                    for i, da in enumerate(detected):
                        dc1, dc2 = st.columns([3, 1])
                        with dc1:
                            st.markdown(f"<div class='c' style='padding:10px;'><b>{da['channel']}</b>: {da['description'][:200]}</div>", unsafe_allow_html=True)
                        with dc2:
                            if st.button("Add", key=f"aa_{i}"):
                                log_action(sid, sender_id, da["type"], da["channel"], da["description"], "planned", scheduled_at=da.get("scheduled_at"))
                                st.success("Added!")


# ════════════════════ ACTIONS ════════════════════

with tabs[4]:  # ACTIONS
    sid = st.session_state.session_id
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact
    _src_a = st.session_state.get("notion_companies_source", "Independent")
    _aid_a = st.session_state.get("sel_advisor_company_id")
    if _src_a == "Media Houses" and _aid_a:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);'>Actions are contact-based. Switch to <b>Independent</b> companies and select a company + contact to log and plan actions.</div>", unsafe_allow_html=True)
    elif not sid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);'>Select a company and contact first.</div>", unsafe_allow_html=True)
    else:
        comp = get_company(cid) or {}
        ct = get_contact(ctid) or {}
        st.markdown(sh(f"ACTIONS — {comp.get('domain','')} / {ct.get('name','')}"), unsafe_allow_html=True)

        # ── Active Sequences ──
        sequences = get_active_sequences(sid)
        if sequences:
            st.markdown(sh(f"ACTIVE SEQUENCES ({len(sequences)})"), unsafe_allow_html=True)
            for seq in sequences:
                progress = seq["done"] / seq["total"] if seq["total"] else 0
                st.markdown(
                    f"<div class='c' style='padding:14px 18px;border-left:3px solid var(--accent);'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                    f"<span style='font-size:13px;font-weight:600;'>Sequence {seq['id']}</span>"
                    f"<span style='font-size:12px;color:var(--text-3);'>{seq['done']}/{seq['total']} done</span></div></div>",
                    unsafe_allow_html=True)
                st.progress(progress)

                for step in seq["steps"]:
                    sc = ACTION_STATUS_COLORS.get(step["status"], "gray")
                    is_done = step["status"] in ("sent", "completed", "replied_positive", "replied_negative", "meeting_booked")
                    icon = "✅" if is_done else "📋"
                    ch_icon = "💬" if "linkedin" in (step.get("channel") or "").lower() else "📧" if "email" in (step.get("channel") or "").lower() else "📞"
                    sched = f" · 📅 {step['scheduled_at'][:10]}" if step.get("scheduled_at") else ""
                    step_expander_label = f"{icon} {ch_icon} Step {step.get('sequence_step', '?')}: {step.get('channel', '')}{sched} — {step.get('status', '')}"

                    with st.expander(step_expander_label, expanded=False):
                        with st.container():
                            st.markdown("<div class='sequence-step-card'></div>", unsafe_allow_html=True)
                            st.markdown(
                                f"<div class='sequence-step-header'>"
                                f"<span>{icon} {ch_icon} <b>Step {step.get('sequence_step','?')}</b>: {step['channel']}{sched}</span>"
                                f"{bdg(step['status'], sc)}</div>"
                                f"<div style='font-size:12px;color:var(--text-2);margin-top:2px;'>{step['description'][:200]}</div>"
                                f"<div class='sequence-step-divider'></div>",
                                unsafe_allow_html=True)
                            s1, s2, s3 = st.columns([3, 1.2, 0.8])
                            with s1:
                                if step.get("content_sent"):
                                    ch_lower = (step.get("channel") or "").lower()
                                    rev_list_key = f"draft_revisions_list_{step['id']}"
                                    orig_stored_key = f"draft_original_{step['id']}"
                                    revisions_list = st.session_state.get(rev_list_key) or []
                                    if not isinstance(revisions_list, list):
                                        revisions_list = [revisions_list] if revisions_list else []
                                    if not revisions_list and step.get("draft_revisions"):
                                        revs = step["draft_revisions"]
                                        revisions_list = revs if isinstance(revs, list) else list(revs) if revs else []
                                        st.session_state[rev_list_key] = revisions_list
                                    version_sel = st.session_state.get(f"refine_version_sel_{step['id']}")
                                    original_for_display = (step.get("draft_original") or "").strip() or st.session_state.get(orig_stored_key) or _display_original_from_content_sent(step.get("content_sent"))
                                    if revisions_list and version_sel:
                                        if version_sel == "Version 1 (Original)":
                                            display_content = original_for_display
                                        else:
                                            try:
                                                m = re.search(r"Version\s+(\d+)", version_sel)
                                                v = int(m.group(1)) if m else 2
                                                idx = v - 2
                                                raw = (revisions_list[idx] if 0 <= idx < len(revisions_list) else (revisions_list[-1] if revisions_list else original_for_display))
                                                display_content = (raw if isinstance(raw, str) else str(raw)) if raw else original_for_display
                                            except Exception:
                                                raw = revisions_list[-1] if revisions_list else original_for_display
                                                display_content = (raw if isinstance(raw, str) else str(raw)) if raw else original_for_display
                                    else:
                                        display_content = original_for_display
                                    read_label = "💬 Read full LinkedIn draft" if "linkedin" in ch_lower else "📧 Read full email"
                                    with st.expander(read_label, expanded=False):
                                        st.markdown(
                                            f"<div style='font-size:12px;background:#f8f9fa;padding:8px 10px;border-radius:6px;border-left:2px solid #4caf50;white-space:pre-wrap;'>{display_content}</div>",
                                            unsafe_allow_html=True)
                                if step.get("response_content"):
                                    resp_content = (step["response_content"] or "").strip()
                                    with st.expander("💬 Read full response", expanded=False):
                                        st.markdown(
                                            f"<div style='font-size:12px;background:#f0f4ff;padding:8px 10px;border-radius:6px;border-left:2px solid #2196f3;white-space:pre-wrap;'>{resp_content}</div>",
                                            unsafe_allow_html=True)
                                if step.get("content_sent") and ("email" in (step.get("channel") or "").lower() or "linkedin" in (step.get("channel") or "").lower()):
                                    _render_ai_refinement(step, seq["id"], step.get("channel", ""))
                            with s2:
                                if has_perm("can_log_outcomes"):
                                    new_s = st.selectbox("Status", ACTION_STATUSES,
                                        index=ACTION_STATUSES.index(step["status"]) if step["status"] in ACTION_STATUSES else 0,
                                        key=f"seq_{step['id']}", label_visibility="collapsed")
                                    if new_s != step["status"]:
                                        update_action(step["id"], status=new_s)
                                        if new_s in ("replied_positive", "replied_negative"):
                                            st.session_state[f"replan_{seq['id']}"] = step["id"]
                                        st.rerun()
                            with s3:
                                if not is_done and has_perm("can_log_outcomes"):
                                    resp = st.text_input("Response", key=f"resp_{step['id']}", label_visibility="collapsed", placeholder="Log response...")
                                    if resp:
                                        update_action(step["id"], response_content=resp, status="replied_positive")
                                        st.session_state[f"replan_{seq['id']}"] = step["id"]
                                        st.rerun()
                            if "email" in step.get("channel", "").lower() and step.get("content_sent"):
                                st.markdown("<div class='sequence-step-divider'></div>", unsafe_allow_html=True)
                                single_draft = _display_original_from_content_sent(step["content_sent"]) if "\n\n---\n\n" in (step.get("content_sent") or "") else (step["content_sent"] or "")
                                subj, bod = parse_email_content(single_draft)
                                _render_save_to_gmail(ct.get("email", ""), subj, bod, f"act_{step['id']}")

                # ── Replan trigger ──
                replan_key = f"replan_{seq['id']}"
                if replan_key in st.session_state:
                    trigger_id = st.session_state.pop(replan_key)
                    trigger_action = next((s for s in seq["steps"] if s["id"] == trigger_id), None)
                    remaining = [s for s in seq["steps"] if s["status"] in ("planned", "scheduled", "drafted")]
                    if trigger_action and remaining:
                        with st.spinner("AI replanning remaining steps..."):
                            rn = st.session_state.research_cache.get(cid, [])
                            advice = replan_on_response(
                                comp, ct, user["id"], sid, trigger_action,
                                trigger_action.get("response_content", ""), remaining, rn)
                            if advice:
                                st.markdown(sh("AI REPLAN RECOMMENDATION"), unsafe_allow_html=True)
                                st.markdown(
                                    render_brief_card_html(advice),
                                    unsafe_allow_html=True)

                st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

        # ── Log action ──
        if has_perm("can_log_outcomes"):
            with st.expander("➕ Log Action", expanded=False):
                with st.form("actform"):
                    af1, af2, af3 = st.columns(3)
                    with af1:
                        atype = st.selectbox("Type", ACTION_TYPES)
                    with af2:
                        achan = st.selectbox("Channel", ACTION_CHANNELS)
                    with af3:
                        astatus = st.selectbox("Status", ACTION_STATUSES)
                    adesc = st.text_area("Description", height=60)
                    acontent = st.text_area("Content Sent", placeholder="Paste exact message text", height=80)
                    af4, af5 = st.columns(2)
                    with af4:
                        acontext = st.text_input("Context")
                    with af5:
                        asched = st.date_input("Scheduled", value=None)
                    if st.form_submit_button("Log"):
                        log_action(sid, user["id"], atype, achan, adesc, astatus,
                            content_sent=acontent or None, context_note=acontext or None,
                            scheduled_at=asched.isoformat() if asched else None)
                        if astatus in ("replied_positive", "replied_negative", "meeting_booked", "deal_closed", "deal_lost", "no_response"):
                            generate_insight_from_outcome(user["id"], sid,
                                {"action_type": atype, "channel": achan, "description": adesc, "status": astatus}, comp)
                        st.success("Logged")
                        st.rerun()

        # ── Non-sequence actions ──
        all_actions = get_session_actions(sid)
        non_seq = [a for a in all_actions if not a.get("sequence_id")]
        if non_seq:
            st.markdown(sh("STANDALONE ACTIONS"), unsafe_allow_html=True)
            for a in non_seq:
                sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
                a1, a2 = st.columns([4, 1])
                with a1:
                    st.markdown(
                        f"<div class='c' style='padding:10px 14px;'>"
                        f"<div style='display:flex;justify-content:space-between;'>"
                        f"<span class='c-l'>{a['action_type']} · {a['channel']}</span>{bdg(a['status'],sc)}</div>"
                        f"<div style='font-size:12px;margin-top:3px;'>{a['description'][:200]}</div></div>",
                        unsafe_allow_html=True)
                with a2:
                    if has_perm("can_log_outcomes"):
                        new_s = st.selectbox("s", ACTION_STATUSES,
                            index=ACTION_STATUSES.index(a["status"]) if a["status"] in ACTION_STATUSES else 0,
                            key=f"as_{a['id']}", label_visibility="collapsed")
                        if new_s != a["status"]:
                            update_action(a["id"], status=new_s)
                            st.rerun()

        if not all_actions:
            st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);'>No actions yet. Use the Strategist to generate a plan.</div>", unsafe_allow_html=True)

        ec, _ = st.columns([1, 3])
        with ec:
            st.download_button("Export CSV",
                data=export_session_csv(sid, comp.get("domain", "")),
                file_name=f"lh2_{comp.get('domain','export')}_{datetime.utcnow().strftime('%Y%m%d')}.csv",
                mime="text/csv")


# ════════════════════ TIMELINE ════════════════════

with tabs[5]:  # TIMELINE
    cid = st.session_state.sel_company
    _src_t = st.session_state.get("notion_companies_source", "Independent")
    _aid_t = st.session_state.get("sel_advisor_company_id")
    if _src_t == "Media Houses" and _aid_t:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);'>Timeline is for Independent company/contact actions. Switch to Independent and select a company + contact.</div>", unsafe_allow_html=True)
    elif not cid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);'>Select a company first.</div>", unsafe_allow_html=True)
    else:
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
            st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);'>No timeline entries yet.</div>", unsafe_allow_html=True)


# ════════════════════ CALENDAR ════════════════════

with tabs[6]:  # CALENDAR
    st.markdown(sh("UPCOMING ACTIONS"), unsafe_allow_html=True)
    # Always show all scheduled events (not scoped to selected company/contact)
    tl = get_timeline_actions(limit=2000)

    upcoming = []
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    horizon = now + timedelta(days=90)
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
        st.info("No scheduled actions in the next 90 days. Generate an outreach plan in STRATEGIST and add it, or schedule actions manually.")
    else:
        day = None
        batch = []
        for dt, a in upcoming:
            dkey = dt.date().isoformat()
            if day is None:
                day = dkey
            if dkey != day:
                st.markdown(sh(day), unsafe_allow_html=True)
                for _, ax in batch:
                    _render_calendar_event(ax)
                batch = []
                day = dkey
            batch.append((dt, a))

        if batch:
            st.markdown(sh(day), unsafe_allow_html=True)
            for _, ax in batch:
                _render_calendar_event(ax)

# ════════════════════ PIPELINE ════════════════════

with tabs[7]:  # PIPELINE
    # === UI REFACTOR START ===
    st.markdown(lh2_section_header("CONTACT PIPELINE"), unsafe_allow_html=True)
    all_comp = get_all_companies()
    pipeline = {s: [] for s in CONTACT_STATUSES}
    for cd in all_comp:
        for ctd in get_contacts(cd["id"]):
            status = ctd.get("status", "not_contacted")
            if status in pipeline:
                pipeline[status].append({"contact": ctd, "company": cd})

    kpi_pipeline = [(len(pipeline[s]), CONTACT_STATUS_LABELS_STRIP.get(s, s)) for s in CONTACT_STATUSES]
    st.markdown(
        "<div class='lh2-kpi-strip'>" + "".join(lh2_kpi_cell(n, lbl) for n, lbl in kpi_pipeline) + "</div>",
        unsafe_allow_html=True)

    bcols = st.columns(len(CONTACT_STATUSES))
    for i, s in enumerate(CONTACT_STATUSES):
        with bcols[i]:
            for item in pipeline[s]:
                ctd, cd = item["contact"], item["company"]
                score = compute_lead_score(cd, ctd)
                si = "🟢" if score >= 60 else "🟡" if score >= 35 else "🔴"
                st.markdown(lh2_pipeline_row(ctd["name"], cd["domain"], f"{si} {score}/100"), unsafe_allow_html=True)
            if not pipeline[s]:
                st.markdown("<div style='text-align:center;padding:24px 0;font-size:11px;color:var(--text-2);'>—</div>", unsafe_allow_html=True)
    # === UI REFACTOR END ===


# ════════════════════ HISTORY ════════════════════

with tabs[8]:  # HISTORY
    # === UI REFACTOR START ===
    ginsights = get_global_insights(user["id"], limit=8)
    if ginsights:
        st.markdown(lh2_section_header("GLOBAL PATTERNS"), unsafe_allow_html=True)
        for g in ginsights:
            cc2 = "green" if g["confidence_score"] >= 0.7 else "yellow" if g["confidence_score"] >= 0.4 else "gray"
            conf_lbl = f"conf: {g['confidence_score']:.1f}"
            st.markdown(
                f"<div class='c' style='padding:12px 16px;'><div style='display:flex;justify-content:space-between;'>"
                f"<span style='font-size:13px;'>{g['insight']}</span>"
                f"{bdg(conf_lbl, cc2)}</div></div>",
                unsafe_allow_html=True)

    all_ua = get_user_actions(user["id"], limit=200)
    if all_ua:
        st.markdown(lh2_section_header("ANALYTICS"), unsafe_allow_html=True)
        total = len(all_ua)
        sent = sum(1 for a in all_ua if a["status"] in ("sent", "completed", "replied_positive", "replied_negative", "meeting_booked", "deal_closed"))
        pos = sum(1 for a in all_ua if a["status"] in ("replied_positive", "meeting_booked", "deal_closed"))
        mtg = sum(1 for a in all_ua if a["status"] == "meeting_booked")
        nr = sum(1 for a in all_ua if a["status"] == "no_response")
        rate = f"{(pos/sent*100):.0f}%" if sent else "—"
        analytics_items = [(total, "Total"), (sent, "Sent"), (rate, "Positive Rate"), (mtg, "Meetings"), (nr, "No Response")]
        st.markdown(
            "<div class='lh2-kpi-strip'>" + "".join(lh2_kpi_cell(n, l) for n, l in analytics_items) + "</div>",
            unsafe_allow_html=True)

    st.markdown(lh2_section_header("ALL SESSIONS"), unsafe_allow_html=True)
    # === UI REFACTOR END ===
    sessions = get_user_sessions(user["id"])
    for sx in sessions:
        sh1, sh2, sh3 = st.columns([2, 1.5, 1])
        with sh1:
            mt = " · 🧠" if sx.get("strategic_summary") else ""
            st.markdown(
                f"<div class='c'><div class='c-l'>{sx.get('company_domain','?')}</div>"
                f"<div class='c-v'>{sx.get('contact_name','?')}{mt}</div></div>",
                unsafe_allow_html=True)
        with sh2:
            mc2 = get_message_count(sx["id"])
            ac2 = len(get_session_actions(sx["id"]))
            st.markdown(f"<div class='c'><div class='c-l'>Activity</div><div class='c-v'>{mc2} msgs · {ac2} actions</div></div>", unsafe_allow_html=True)
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
        st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);'>No sessions.</div>", unsafe_allow_html=True)

with tabs[9]:  # ADVISOR
    # Header with history button
    h1, h2 = st.columns([3, 1])
    with h1:
        st.markdown(sh("ADVISOR"), unsafe_allow_html=True)
        st.caption("Ask for company suggestions, outreach plans, or advice. I remember our conversation and your pipeline.")
    with h2:
        past_convs = get_advisor_conversations(user["id"], limit=20)
        if past_convs:
            def format_conv_option(conv_id):
                if conv_id is None:
                    return "💬 Current Chat"
                conv = next((c for c in past_convs if c["id"] == conv_id), None)
                if not conv:
                    return ""
                
                from datetime import datetime
                updated = datetime.fromisoformat(conv["updated_at"])
                now = datetime.utcnow()
                diff = now - updated
                
                if diff.days == 0:
                    if diff.seconds < 3600:
                        time_str = f"{diff.seconds // 60}m ago"
                    else:
                        time_str = f"{diff.seconds // 3600}h ago"
                elif diff.days == 1:
                    time_str = "Yesterday"
                elif diff.days < 7:
                    time_str = f"{diff.days}d ago"
                else:
                    time_str = updated.strftime("%b %d")
                
                return f"{conv['title']} • {time_str} • {conv['msg_count']} msgs"
            
            def on_history_select():
                selected = st.session_state.advisor_history_select
                if selected:
                    loaded = load_advisor_conversation(selected)
                    if loaded:
                        st.session_state.advisor_messages = loaded["messages"]
                        st.session_state.advisor_loaded_conv_id = selected
            
            current_selection = st.session_state.get("advisor_loaded_conv_id")
            
            selected_conv = st.selectbox(
                "History",
                options=[None] + [c["id"] for c in past_convs],
                format_func=format_conv_option,
                key="advisor_history_select",
                on_change=on_history_select,
                index=0
            )
    
    # Chat interface
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
                    user["id"],
                    prompt,
                    [{"role": m["role"], "content": m["content"]} for m in st.session_state.advisor_messages[:-1]],
                )
            st.markdown(reply or "No response.")
        st.session_state.advisor_messages.append({"role": "assistant", "content": reply or ""})
        
        # Auto-save conversation after each exchange
        save_advisor_conversation(user["id"], st.session_state.advisor_messages)
        st.rerun()