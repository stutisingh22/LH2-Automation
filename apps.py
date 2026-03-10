"""
app.py — LH2 Command Center v3

Features:
  - Sidebar: conversation history
  - From/To selectors: who is sending, who is receiving
  - Sequence builder: one-click full outreach plan
  - Adaptive replanning: AI tweaks plan when responses arrive
  - Streaming chat with proper styling
"""

import html as html_module
import inspect
import re
import time
import pandas as pd
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
    normalize_contact_status,
    ACTION_TYPES, ACTION_CHANNELS, ACTION_STATUSES, ACTION_STATUS_COLORS,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    SIGNALHIRE_CALLBACK_BASE_URL,
)
from db_ops import (
    create_user, authenticate, get_user, get_all_users,
    get_company, get_all_companies, search_companies,
    get_or_create_shadow_company_for_advisor,
    upsert_company, upsert_contact,
    upsert_spoc_contact, get_spoc_contacts_by_spoc, get_spoc_contacts_by_company,
    get_contacts, get_contact, update_contact_status, update_contact,
    delete_spoc_contact,
    get_or_create_session, get_session, get_sessions_for_company,
    get_company_ids_with_session_summary,
    get_all_sessions, get_user_sessions, delete_session,
    save_message, load_messages, get_message_count,
    log_action, bulk_log_actions, get_session_actions, get_all_actions, get_user_actions,
    get_timeline_actions, get_active_sequences, get_sequence_actions,
    update_action, delete_action,
    get_overdue_followups, get_todays_scheduled, get_recent_activity,
    get_memory_events, get_all_global_insights, get_global_insights,
    get_session_summary, save_session_summary, get_pipeline_stats,
    merge_company_extra_data,
    save_advisor_conversation, get_all_advisor_conversations, get_advisor_conversations, load_advisor_conversation, delete_advisor_conversation,
    get_enrichment_result, get_latest_enrichment_for_user, get_enrichment_by_request_id, get_completed_enrichment_by_identifier,
)
from notion_sync import sync_notion_to_db, lookup_lead_in_notion, fetch_notes_for_page, create_notion_contact_page
from notion_sync_advisor import sync_notion_advisor_to_db
from db_advisor import get_all_advisor_companies, get_advisor_company, search_advisor_companies, get_advisor_parents_with_children, merge_advisor_company_extra_data
from drive_sync import list_drive_files, create_doc_in_drive, refresh_all_drive_contexts, get_lh2_knowledge_context
from ai_engine import (
    compute_lead_score, get_local_time,
    get_strategist_response_stream, auto_brief,
    generate_outreach_plan, replan_on_response,
    recommend_offering_for_company,
    maybe_update_summary, extract_episodic_memories,
    generate_insight_from_outcome, parse_ai_actions, export_session_csv,
    intelligence_request, research_person_for_company, summarize_research_to_context,
    advisor_chat, refine_email_draft, rewrite_sequence_steps_with_research,
    learn_user_preference,
)
from product_profiles import get_product_options
from research import (
    fetch_research_from_notion,
    run_research,
    research_command_ai,
    split_research_into_sections,
    get_next_section_number,
    format_appended_section,
)
from platform_analytics import build_platform_analytics_for_domain
from linkedin_lookup import (
    extract_spoc_table_contacts,
    run_linkedin_lookup_for_contacts,
    get_linkedin_url_for_contact,
)


def _strip_linkedin_command_prefix(text):
    """Strip 'find XYZ' / 'FIND XYZ' style prefixes so the table gets the person name, not the command."""
    t = (text or "").strip()
    if not t:
        return t
    lower = t.lower()
    prefixes = [
        "find linkedin for ", "find linkedin ", "get linkedin for ", "get linkedin ",
        "look up ", "lookup ", "search for ", "find ",
    ]
    for p in prefixes:
        if lower.startswith(p):
            return t[len(p) :].strip()
    return t


def _is_sequence_edit_request(prompt: str) -> bool:
    p = (prompt or "").lower()
    if not p:
        return False
    edit_words = ("edit", "rewrite", "revise", "modify", "change", "update", "redo")
    draft_words = ("email", "linkedin", "sequence", "step", "draft", "message", "touch")
    return any(w in p for w in edit_words) and any(w in p for w in draft_words)


def _latest_sequence_with_drafts(sequences):
    if not sequences:
        return None
    best = None
    best_ts = ""
    for seq in sequences:
        steps = seq.get("steps") or []
        drafted_steps = [s for s in steps if (s.get("content_sent") or "").strip()]
        if not drafted_steps:
            continue
        seq_ts = max([(s.get("created_at") or "") for s in drafted_steps], default="")
        if best is None or seq_ts > best_ts:
            best = seq
            best_ts = seq_ts
    return best


def _resolve_instruction_targets(prompt: str, steps):
    """
    Select which steps should be edited from a natural-language instruction.
    Rules:
    - "Email N" targets the Nth email in the sequence.
    - "Step N" targets sequence step N.
    - "LinkedIn" with optional number targets LinkedIn steps.
    - "sequence"/"all emails"/"all steps" can target multiple.
    """
    prompt_l = (prompt or "").lower()
    if not steps:
        return []

    ordered = sorted(steps, key=lambda s: (s.get("sequence_step") or 0))
    email_steps = [s for s in ordered if "email" in (s.get("channel") or "").lower()]
    li_steps = [s for s in ordered if "linkedin" in (s.get("channel") or "").lower()]

    explicit_targets = []

    for m in re.findall(r"\bstep\s*(\d+)\b", prompt_l):
        n = int(m)
        explicit_targets.extend([s for s in ordered if (s.get("sequence_step") or 0) == n])

    for m in re.findall(r"\bemail\s*(\d+)\b", prompt_l):
        idx = int(m) - 1
        if 0 <= idx < len(email_steps):
            explicit_targets.append(email_steps[idx])

    li_num = re.findall(r"\blinkedin(?:\s*(?:step|message|touch))?\s*(\d+)\b", prompt_l)
    if li_num:
        idx = int(li_num[0]) - 1
        if 0 <= idx < len(li_steps):
            explicit_targets.append(li_steps[idx])
    elif "linkedin" in prompt_l and any(k in prompt_l for k in ("edit", "rewrite", "revise", "modify", "change", "update")):
        explicit_targets.extend(li_steps[:1])

    # Deduplicate explicit targets by action id
    dedup = {}
    for s in explicit_targets:
        sid = s.get("id")
        if sid is not None:
            dedup[sid] = s
    explicit_targets = list(dedup.values())
    if explicit_targets:
        return explicit_targets

    if "all emails" in prompt_l:
        return email_steps
    if "entire sequence" in prompt_l or "whole sequence" in prompt_l or ("sequence" in prompt_l and "email" not in prompt_l and "linkedin" not in prompt_l):
        return [s for s in ordered if (s.get("content_sent") or "").strip()]

    # No explicit target found: do not auto-update multiple steps silently.
    return []


from contact_timeline import build_contact_timeline
from signalhire_enrichment import (
    get_credits as get_signalhire_credits,
    request_enrichment,
    has_verified_email,
)
from gmail_service import create_gmail_draft, parse_email_content
from google_auth import get_google_credentials
from ui_helpers import inject_css, bdg, crd, mcard, sh, sh_small, tl_item, alert, fmt_v, has_perm, timeline_item_html, lh2_kpi_cell, lh2_status_cell, lh2_section_header, lh2_pipeline_row, display_full_name, render_research_with_proper_tables, research_display_to_editor_html, is_html_content, html_to_plain_text, markdown_to_html, wysiwyg_editor_html_page

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
    "timeline_contact_id": None, "timeline_company_id": None, "timeline_contact_name": None,
}
for k, d in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = d


def _is_placeholder_contact(ct, comp):
    """Placeholder = no real identity (no email, no LinkedIn, no real name). Plan/strategist run for any SPOC with name or LinkedIn; email required only at Gmail draft/send."""
    if not ct:
        return True
    email = (ct.get("email") or "").strip()
    linkedin = (ct.get("linkedin") or "").strip()
    name = (ct.get("name") or "").strip()
    domain_like = not name or name in ("—", "?") or "@" in name or ".com" in name.lower()
    has_identity = email or linkedin or (name and not domain_like)
    return not has_identity


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
        has_to_email = bool((to_email or "").strip())
        disabled = not (subject or body) or not has_to_email
        if not has_to_email:
            st.caption("Add email for this contact to create draft.")
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

    if st.button("↻ Refresh knowledge", key="refresh_drive_knowledge", use_container_width=True, help="Rebuild Drive knowledge cache"):
        with st.spinner("Refreshing knowledge from Drive..."):
            info = refresh_all_drive_contexts()
        st.toast(f"Knowledge refreshed. Company caches cleared: {info.get('cleared_company_caches', 0)}.")

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

    _sessions_all = get_all_sessions()
    if st.session_state.get("notion_companies_source") == "Media Houses":
        _sessions_all = [s for s in _sessions_all if s.get("source_advisor_company_id")]
    else:
        _sessions_all = [s for s in _sessions_all if not s.get("source_advisor_company_id")]
    sessions = _sessions_all
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
                st.session_state.sel_advisor_company_id = sx.get("source_advisor_company_id")
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

TAB_NAMES = ["DASHBOARD", "COMPANIES", "INTELLIGENCE", "STRATEGIST", "ACTIONS", "TIMELINE", "CALENDAR", "PIPELINE", "SPOC TRACKING", "HISTORY", "ADVISOR", "BULK IMPORT"]

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
    with st.expander("Account", expanded=False):
        user_email = (user.get("email") or "").strip()
        if user_email:
            if st.button("Connect Gmail", key="connect_gmail_btn", help="Authorize Gmail for Save to Drafts and Sent Sync"):
                with st.spinner("Opening browser for Gmail compose…"):
                    c1 = get_google_credentials(user_email, scope_set="gmail_compose")
                if c1:
                    with st.spinner("Opening browser for Gmail read (Sent Sync)…"):
                        c2 = get_google_credentials(user_email, scope_set="gmail_read")
                    if c2:
                        st.success("Gmail connected. You can use Save to Gmail and Sent Sync.")
                    else:
                        st.warning("Compose connected; read scope failed. Sent Sync may prompt again.")
                else:
                    st.error("Could not connect. Check credentials.json and try again.")
        else:
            st.caption("Set your email in your profile to connect Gmail.")
    st.selectbox(
        "Companies DB",
        options=["Independent", "Media Houses"],
        index=["Independent", "Media Houses"].index(st.session_state.get("notion_companies_source", "Independent")),
        key="notion_companies_source",
        label_visibility="collapsed",
    )

# Professional Admin Navigation using st.tabs()
def _tab_label(n):
    if n == "ADVISOR": return "Advisor"
    if n == "BULK IMPORT": return "Bulk Import"
    return n.title()
tab_labels = [_tab_label(n) for n in TAB_NAMES]
default_tab_label = tab_labels[TAB_NAMES.index(current_tab)] if current_tab in TAB_NAMES else tab_labels[0]
tabs = st.tabs(tab_labels, default=default_tab_label)

# ════════════════════ DASHBOARD ════════════════════

with tabs[0]:  # DASHBOARD
    # === UI REFACTOR START ===
    st.markdown(lh2_section_header("OVERVIEW"), unsafe_allow_html=True)
    stats = get_pipeline_stats()
    _companies_count = len(get_all_advisor_companies()) if st.session_state.get("notion_companies_source") == "Media Houses" else len(get_all_companies())
    kpi_items = [
        (_companies_count, "Companies"),
        (stats.get("outreach_sent", 0), "Active Outreach"),
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
            _shadow_cache = st.session_state.setdefault("_advisor_shadow", {})
            _contacts_cache = st.session_state.setdefault("_advisor_contacts", {})
            for grp in advisor_groups:
                with st.expander(f"**{grp['parent_name']}** — {len(grp['children'])} child(ren)", expanded=False):
                    for ch in grp["children"]:
                        is_sel_adv = st.session_state.get("sel_advisor_company_id") == ch["id"]
                        ch_id = ch["id"]
                        if ch_id not in _shadow_cache:
                            _shadow_cache[ch_id] = get_or_create_shadow_company_for_advisor(ch_id)
                        shadow_cid, _ = _shadow_cache[ch_id]
                        row_col1, row_col2 = st.columns([4, 0.5])
                        with row_col1:
                            st.markdown(
                                f"<div class='c' style='padding:8px 12px;'>"
                                f"<span style='font-size:13px;'>{ch['domain']}</span>"
                                f"</div>",
                                unsafe_allow_html=True)
                        with row_col2:
                            if st.button("→", key=f"adv_go_{ch['id']}", type="secondary" if is_sel_adv else "primary", use_container_width=True):
                                st.session_state.sel_advisor_company_id = ch["id"]
                                shadow_cid, shadow_ctid = get_or_create_shadow_company_for_advisor(ch["id"])
                                if shadow_cid and shadow_ctid:
                                    st.session_state.sel_company = shadow_cid
                                    st.session_state.sel_contact = shadow_ctid
                                    st.session_state.session_id = get_or_create_session(shadow_cid, shadow_ctid, user["id"])
                                else:
                                    st.session_state.sel_company = None
                                    st.session_state.sel_contact = None
                                    st.session_state.session_id = None
                                st.session_state.chat_messages = load_messages(st.session_state.session_id) if st.session_state.session_id else []
                                st.session_state.auto_brief_text = None
                                st.session_state.pending_plan = None
                                st.session_state.current_tab = "STRATEGIST"
                                st.rerun()
                        # Same as Independent: expander with contacts for this child (shadow company)
                        if shadow_cid and shadow_cid not in _contacts_cache:
                            _contacts_cache[shadow_cid] = get_contacts(shadow_cid)
                        cts_adv = (_contacts_cache.get(shadow_cid) or []) if shadow_cid else []
                        if cts_adv:
                            with st.expander(f"👥 {len(cts_adv)} contact(s)", expanded=is_sel_adv):
                                for ct in cts_adv:
                                    _norm_adv = normalize_contact_status(ct.get("status", "not_contacted"))
                                    scolor_adv = CONTACT_STATUS_COLORS.get(_norm_adv, "gray")
                                    slabel_adv = CONTACT_STATUS_LABELS.get(_norm_adv, "")
                                    cols_adv = st.columns([2.5, 1.2, 0.4, 0.4])
                                    with cols_adv[0]:
                                        st.markdown(
                                            f"<b>{ct['name']}</b> <span style='font-size:11px;color:var(--text-3);'>"
                                            f"{ct.get('role','')}</span> {bdg(slabel_adv, scolor_adv)}",
                                            unsafe_allow_html=True)
                                    with cols_adv[1]:
                                        ns_adv = st.selectbox("s", CONTACT_STATUSES,
                                            index=CONTACT_STATUSES.index(_norm_adv) if _norm_adv in CONTACT_STATUSES else 0,
                                            format_func=lambda x: CONTACT_STATUS_LABELS.get(x, x),
                                            key=f"adv_qs_{ch['id']}_{ct['id']}", label_visibility="collapsed")
                                        if ns_adv != ct.get("status"):
                                            update_contact_status(ct["id"], ns_adv)
                                            st.rerun()
                                    with cols_adv[2]:
                                        if st.button("→", key=f"adv_go_ct_{ch['id']}_{ct['id']}", use_container_width=True):
                                            st.session_state.sel_advisor_company_id = ch["id"]
                                            select_contact(shadow_cid, ct["id"])
                                            st.rerun()
                                    with cols_adv[3]:
                                        if st.button("📅", key=f"adv_tl_{ch['id']}_{ct['id']}", use_container_width=True, help="View contact timeline"):
                                            st.session_state.timeline_contact_id = ct["id"]
                                            st.session_state.timeline_company_id = shadow_cid
                                            st.session_state.timeline_contact_name = ct.get("name", "Contact")
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
            st.caption("Optional: import company/contact from Notion (sync only). Person and company research is done by Claude only — Notion contact details are not used for research.")
            if st.button("Find in Notion", key="lisync"):
                if li_url:
                    with st.spinner("Checking Notion..."):
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
                        st.warning("No match in Notion. Paste this URL in Intelligence → Look up person for an AI brief.")

        companies = search_companies(sq) if sq else get_all_companies()

        # Filter by platform presence (multi-select: show companies with presence on ALL selected platforms)
        from tracker_sources import load_domain_to_platforms, get_presence_for_domain
        _tracker_map = load_domain_to_platforms()

        _platform_labels = ["MSN", "Yahoo", "NewsBreak"]
        _label_to_key = {"MSN": "msn", "Yahoo": "yahoo", "NewsBreak": "newsbreak"}

        # Prefer a small icon + dropdown (popover). Fall back gracefully if Streamlit lacks popover().
        _selected_labels = []
        if hasattr(st, "popover"):
            with st.popover("🔽", help="Filter companies by platform presence"):
                _selected_labels = st.multiselect(
                    "Platform presence",
                    options=_platform_labels,
                    default=st.session_state.get("companies_platform_filter", []),
                    key="companies_platform_filter",
                )
        else:
            # Fallback (older Streamlit): keep functionality available.
            with st.expander("🔽", expanded=False):
                _selected_labels = st.multiselect(
                    "Platform presence",
                    options=_platform_labels,
                    default=st.session_state.get("companies_platform_filter", []),
                    key="companies_platform_filter",
                )

        _selected_platforms = [_label_to_key[x] for x in (_selected_labels or []) if x in _label_to_key]
        if _selected_platforms:
            def _has_all_presence(c):
                pp = (c.get("extra_data") or {}).get("platform_presence") or {}
                if not pp:
                    pp = get_presence_for_domain(c.get("domain") or "", _tracker_map)
                return all(pp.get(plat, False) for plat in _selected_platforms)
            companies = [c for c in companies if _has_all_presence(c)]

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
            _company_ids_with_session_summary = get_company_ids_with_session_summary()
            for comp in companies:
                is_sel = st.session_state.sel_company == comp["id"]
                border = "border-left:3px solid var(--accent);" if is_sel else ""
                has_research = bool(((comp.get("extra_data") or {}).get("research_full") or "").strip()) or (comp["id"] in _company_ids_with_session_summary)
                # Right side of card: one slot — "researched" (dark green) or visits/–
                right_label = "researched" if has_research else fmt_v(comp.get("monthly_visits"))
                right_style = "font-size:11px;font-weight:500;color:#166534;" if has_research else "font-size:12px;color:var(--text-2);"
                row_name, row_arrow = st.columns([4, 0.5])
                with row_name:
                    st.markdown(
                        f"<div class='c' style='padding:10px 14px;{border}'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<div><span style='font-size:14px;font-weight:500;'>{comp['domain']}</span></div>"
                        f"<span style='{right_style}'>{right_label}</span></div></div>",
                        unsafe_allow_html=True)
                with row_arrow:
                    if st.button("→", key=f"comp_go_{comp['id']}", use_container_width=True, help="Open intelligence/brief for this company"):
                        st.session_state.sel_company = comp["id"]
                        st.session_state.sel_contact = None
                        st.session_state.session_id = None
                        st.session_state.current_tab = "INTELLIGENCE"
                        st.rerun()

                cts = get_contacts(comp["id"])
                if cts:
                    with st.expander(f"👥 {len(cts)} contact(s)", expanded=is_sel):
                        for ct in cts:
                            cols = st.columns([2.5, 1.2, 0.4, 0.4])
                            with cols[0]:
                                _norm = normalize_contact_status(ct.get("status", "not_contacted"))
                                scolor = CONTACT_STATUS_COLORS.get(_norm, "gray")
                                slabel = CONTACT_STATUS_LABELS.get(_norm, "")
                                st.markdown(
                                    f"<b>{ct['name']}</b> <span style='font-size:11px;color:var(--text-3);'>"
                                    f"{ct.get('role','')}</span> {bdg(slabel, scolor)}",
                                    unsafe_allow_html=True)
                            with cols[1]:
                                ns = st.selectbox("s", CONTACT_STATUSES,
                                    index=CONTACT_STATUSES.index(_norm) if _norm in CONTACT_STATUSES else 0,
                                    format_func=lambda x: CONTACT_STATUS_LABELS.get(x, x),
                                    key=f"qs_{ct['id']}", label_visibility="collapsed")
                                if ns != ct.get("status"):
                                    update_contact_status(ct["id"], ns)
                                    st.rerun()
                            with cols[2]:
                                if st.button("→", key=f"go_{ct['id']}", use_container_width=True):
                                    select_contact(comp["id"], ct["id"])
                                    st.rerun()
                            with cols[3]:
                                if st.button("📅", key=f"tl_{ct['id']}", use_container_width=True, help="View contact timeline"):
                                    st.session_state.timeline_contact_id = ct["id"]
                                    st.session_state.timeline_company_id = comp["id"]
                                    st.session_state.timeline_contact_name = ct.get("name", "Contact")
                                    st.rerun()
                        # Platform presence: from DB if saved; else from CSV for display (e.g. before first "Update from CSV")
                        ed = comp.get("extra_data") or {}
                        pp = ed.get("platform_presence") or {}
                        if not pp:
                            pp = get_presence_for_domain(comp.get("domain") or "", _tracker_map)
                        msn = pp.get("msn", False)
                        yahoo = pp.get("yahoo", False)
                        newsbreak = pp.get("newsbreak", False)
                        def _mark(present):
                            return "✓" if present else "<span style='color:#dc2626;'>✗</span>"
                        st.markdown(
                            f"<span style='font-size:12px;color:var(--text-3);'>Platform presence: "
                            f"MSN {_mark(msn)}  Yahoo {_mark(yahoo)}  NewsBreak {_mark(newsbreak)}"
                            f"</span>",
                            unsafe_allow_html=True,
                        )
                        search_results = ed.get("platform_presence_search_results") or {}
                        if search_results:
                            with st.expander("Search results", expanded=False):
                                for plat in ("msn", "yahoo", "newsbreak"):
                                    items = search_results.get(plat) or []
                                    if items:
                                        st.caption(plat.upper())
                                        for r in items:
                                            title = (r.get("title") or "").strip() or "—"
                                            href = (r.get("href") or "").strip()
                                            if href:
                                                st.markdown(f"- [{title[:80]}{'…' if len(title) > 80 else ''}]({href})")
                                            else:
                                                st.markdown(f"- {title[:80]}{'…' if len(title) > 80 else ''}")


# ════════════════════ INTELLIGENCE ════════════════════

with tabs[2]:  # INTELLIGENCE
    # ── Initial Load: Use cached service layer for fast page load (<100ms) ──
    from services.ui_data_loader import (
        load_company_profile,
        load_action_history,
        force_refresh_research,
        clear_ui_cache,
    )
    
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

        _intel_sid = st.session_state.session_id
        if not _intel_sid and cid:
            _intel_sid = get_or_create_session(cid, ctid, user["id"])
            st.session_state.session_id = _intel_sid
        _intel_ct = get_contact(ctid) if ctid else None
        if not _intel_ct:
            _intel_ct = {"id": None, "name": comp.get("domain", "Company") if comp else "Company", "role": "", "email": "", "linkedin": "", "country": "", "state": "", "city": ""}
        if _intel_sid and _intel_ct:
            ct = _intel_ct
            if ct:
                if is_advisor and aid:
                    _fa = get_advisor_company(aid)
                    if _fa:
                        _adv_ed = _fa.get("extra_data") or {}
                        if isinstance(_adv_ed, str):
                            try:
                                _adv_ed = json.loads(_adv_ed) if (_adv_ed or "").strip() else {}
                            except Exception:
                                _adv_ed = {}
                        comp = {**comp, "extra_data": _adv_ed if isinstance(_adv_ed, dict) else {}}
                elif cid:
                    _fc = get_company(cid)
                    if _fc:
                        comp = _fc

        st.markdown(sh_small("Company profile"), unsafe_allow_html=True)
        r1 = st.columns(5)
        for col, (l, v) in zip(r1, [
            ("Domain", comp.get("domain")), ("Publisher", comp.get("publisher_type")),
            ("Content", comp.get("content_type")), ("Visits", fmt_v(comp.get("monthly_visits"))),
            ("Research", comp.get("research_status")),
        ]):
            with col:
                st.markdown(crd(l, v), unsafe_allow_html=True)

        # ── Offering recommendation: AI suggests MediaOS vs Accelerate from research + Drive; stored per company ──
        _extra_offering = comp.get("extra_data") or {}
        if isinstance(_extra_offering, str):
            try:
                _extra_offering = json.loads(_extra_offering) if (_extra_offering or "").strip() else {}
            except Exception:
                _extra_offering = {}
        _recommended = _extra_offering.get("recommended_offering") or {}
        if st.session_state.get("offering_recommendation_just_saved_cid") == cid and st.session_state.get("offering_recommendation_just_saved"):
            _recommended = st.session_state.pop("offering_recommendation_just_saved", _recommended)
            st.session_state.pop("offering_recommendation_just_saved_cid", None)
        _stored_key = (_recommended.get("product_key") or "").strip().lower()
        if _stored_key not in ("mediaos", "accelerate"):
            _stored_key = None
        _stored_rationale = (_recommended.get("rationale") or "").strip()
        st.markdown(sh_small("Offering recommendation"), unsafe_allow_html=True)
        if _stored_key:
            _label = "MediaOS" if _stored_key == "mediaos" else "LH2 Accelerate"
            st.caption(f"Recommended: **{_label}**")
            if _stored_rationale:
                st.markdown(f"<div style='font-size:12px;color:var(--text-2);margin-bottom:8px;'>{_stored_rationale}</div>", unsafe_allow_html=True)
        if has_perm("can_use_strategist") and st.button("Get recommendation" if not _stored_key else "Refresh recommendation", key="btn_offering_recommend", use_container_width=False, help="AI suggests MediaOS or Accelerate from company research and Drive context"):
            try:
                with st.spinner("Checking research and Drive..."):
                    _research_for_offering = (_extra_offering.get("research_full") or "").strip()
                    if not _research_for_offering and _intel_sid:
                        _sess_sum, _ = get_session_summary(_intel_sid)
                        _research_for_offering = (_sess_sum or "").strip()
                    if not _research_for_offering:
                        _research_for_offering = (st.session_state.get(f"research_fresh_result_{cache_key}") or "").strip()
                    _lh2 = get_lh2_knowledge_context()
                with st.spinner("Recommending offering..."):
                    _result = recommend_offering_for_company(_research_for_offering, _lh2, comp.get("domain"))
                _pk = (_result.get("product_key") or "").strip().lower() if _result else None
                if _pk in ("mediaos", "accelerate"):
                    _ro = {"product_key": _pk, "rationale": (_result.get("rationale") or "").strip() or "No rationale.", "updated_at": datetime.utcnow().isoformat()}
                    if is_advisor:
                        merge_advisor_company_extra_data(aid, {"recommended_offering": _ro})
                    else:
                        merge_company_extra_data(cid, {"recommended_offering": _ro})
                    st.session_state["offering_recommendation_just_saved"] = _ro
                    st.session_state["offering_recommendation_just_saved_cid"] = cid
                    st.rerun()
                else:
                    st.warning(_result.get("rationale", "Could not get a recommendation. Run deep research and ensure Drive has LH2/offerings context."))
            except Exception as _e:
                st.error(f"Get recommendation failed: {_e}")

        # ── Research document: from Notion, from DB, or run deep research; always same edit + AI options ──
        # Re-fetch comp so extra_data (research_full) is fresh after save/run
        if is_advisor and aid:
            _fresh_adv = get_advisor_company(aid)
            if _fresh_adv:
                _ed = _fresh_adv.get("extra_data")
                if isinstance(_ed, str) and (_ed or "").strip():
                    try:
                        _ed = json.loads(_ed)
                    except Exception:
                        _ed = {}
                comp = {**comp, "extra_data": _ed if isinstance(_ed, dict) else {}}
        elif cid:
            _fresh_comp = get_company(cid)
            if _fresh_comp:
                comp = _fresh_comp
        st.markdown(sh_small("Research document"), unsafe_allow_html=True)
        _fresh_key = f"research_fresh_result_{cache_key}"
        
        # Load research for display. Company is primary so revisit/refresh always shows saved deep research.
        research_full_db = None
        _raw_extra = comp.get("extra_data")
        if isinstance(_raw_extra, str):
            try:
                extra = json.loads(_raw_extra) if (_raw_extra or "").strip() else {}
            except Exception:
                extra = {}
        else:
            extra = _raw_extra if isinstance(_raw_extra, dict) else {}

        # Priority 1: company.extra_data (so refresh/revisit same company shows research)
        research_full_db = (extra.get("research_full") or "").strip()
        if research_full_db:
            print(f"[DEBUG] Loaded from company.extra_data: {len(research_full_db)} chars")

        # Priority 2: session strategic_summary when company has none (e.g. old data)
        if not research_full_db and _intel_sid:
            session_summary, _ = get_session_summary(_intel_sid)
            if session_summary and session_summary.strip():
                research_full_db = session_summary.strip()
                print(f"[DEBUG] Loaded from sessions.strategic_summary: {len(research_full_db)} chars")

        # Priority 3: session_state for immediate post-save display before next rerun
        if not research_full_db:
            _from_session = (st.session_state.get(_fresh_key) or "").strip()
            if _from_session:
                research_full_db = _from_session
                print(f"[DEBUG] Loaded from session_state: {len(research_full_db)} chars")
                st.session_state.pop(_fresh_key, None)

        research_full_display = research_full_db

        if not research_full_display:
            st.caption("No research yet. Run **deep research** below (same prompt as the Notion research bot). Saves to Command Center only until you click **Save** (DB + Notion + Drive).")
            if has_perm("can_use_strategist") and st.button("🔬 Run deep research", key="run_res_doc", type="primary"):
                # Deep Research Trigger: NOT cached, always runs live
                err_msg = None
                result = None
                try:
                    pb = st.progress(0, text="Starting…")
                    def _prog(c, e):
                        pb.progress(min(c / 40000, 0.95), text=f"Researching… {c:,} chars")
                    
                    # LIVE: Execute drive_sync and ai_engine logic (NOT cached)
                    result = run_research(comp.get("domain", ""), progress_callback=_prog)
                except Exception as e:
                    err_msg = str(e)
                
                if result:
                    # Get or create session for this company
                    session_id = _intel_sid if _intel_sid else get_or_create_session(cid, ctid, user["id"])
                    
                    # Debug: Verify session_id
                    print(f"[DEBUG] Saving research for session_id: {session_id}")
                    
                    # Save to CANONICAL location: sessions.strategic_summary
                    save_session_summary(session_id, result, msg_count=0)
                    
                    # LIVE external analytics fetch for deep research expander (do not cache API call)
                    try:
                        _ed_local = comp.get("extra_data") if isinstance(comp.get("extra_data"), dict) else {}
                        _presence_local = (_ed_local.get("platform_presence") or {}) if isinstance(_ed_local, dict) else {}
                        platform_analytics_snapshot = build_platform_analytics_for_domain(
                            comp.get("domain", ""),
                            platform_presence=_presence_local,
                        )
                    except Exception as _pae:
                        platform_analytics_snapshot = {
                            "status": "error",
                            "error": str(_pae),
                            "updated_at": datetime.utcnow().isoformat(),
                        }

                    # Also save to company.extra_data for backward compatibility
                    if is_advisor:
                        merge_advisor_company_extra_data(aid, {
                            "research_full": result,
                            "research_full_updated_at": datetime.utcnow().isoformat(),
                            "platform_presence_analytics": platform_analytics_snapshot,
                        })
                    else:
                        merge_company_extra_data(cid, {
                            "research_full": result,
                            "research_full_updated_at": datetime.utcnow().isoformat(),
                            "platform_presence_analytics": platform_analytics_snapshot,
                        })
                    
                    # Clear old caches
                    if cache_key in st.session_state.get("research_cache", {}):
                        del st.session_state.research_cache[cache_key]
                    st.session_state.pop(f"research_wysiwyg_html_{cache_key}", None)
                    st.session_state.pop("research_command_result", None)
                    st.session_state.pop("research_command_result_cid", None)
                    st.session_state[f"research_fresh_result_{cache_key}"] = result
                    
                    # Update session_state with session_id for next load
                    st.session_state.session_id = session_id
                    
                    # Deep Research Safety Valve: Kill the old cache
                    force_refresh_research(session_id)
                    
                    st.success("Research saved to Command Center. Edit below if needed, then click **Save** to write to DB, Notion, and Drive.")
                    
                    # st.rerun() to show the brand new research results
                    st.rerun()
                else:
                    st.error("Research failed." + (f" {err_msg}" if err_msg else " Check ANTHROPIC_API_KEY in keys.env and that the model is available."))
        else:
            st.caption("Edit in place (or by section below); **Save** writes to DB and Drive.")
            ref_col1, ref_col2, ref_col3 = st.columns([1, 1, 1])
            with ref_col1:
                if has_perm("can_use_strategist") and st.button("🔬 Re-run deep research", key=f"rerun_res_doc_{cache_key}", use_container_width=False, help="Re-run with latest prompts and signals; overwrites current research"):
                    # Deep Research Trigger: NOT cached, always runs live
                    err_msg = None
                    result = None
                    try:
                        pb = st.progress(0, text="Re-running deep research…")
                        def _prog(c, e):
                            pb.progress(min(c / 40000, 0.95), text=f"Researching… {c:,} chars")
                        
                        # LIVE: Execute drive_sync and ai_engine logic (NOT cached)
                        result = run_research(comp.get("domain", ""), progress_callback=_prog)
                    except Exception as e:
                        err_msg = str(e)
                    
                    if result:
                        # Get or create session for this company
                        session_id = _intel_sid if _intel_sid else get_or_create_session(cid, ctid, user["id"])
                        
                        # Debug: Verify session_id
                        print(f"[DEBUG] Re-running research for session_id: {session_id}")
                        
                        # Save to CANONICAL location: sessions.strategic_summary
                        save_session_summary(session_id, result, msg_count=0)
                        
                        # LIVE external analytics fetch for deep research expander (do not cache API call)
                        try:
                            _ed_local = comp.get("extra_data") if isinstance(comp.get("extra_data"), dict) else {}
                            _presence_local = (_ed_local.get("platform_presence") or {}) if isinstance(_ed_local, dict) else {}
                            platform_analytics_snapshot = build_platform_analytics_for_domain(
                                comp.get("domain", ""),
                                platform_presence=_presence_local,
                            )
                        except Exception as _pae:
                            platform_analytics_snapshot = {
                                "status": "error",
                                "error": str(_pae),
                                "updated_at": datetime.utcnow().isoformat(),
                            }

                        # Also save to company.extra_data for backward compatibility
                        if is_advisor:
                            merge_advisor_company_extra_data(aid, {
                                "research_full": result,
                                "research_full_updated_at": datetime.utcnow().isoformat(),
                                "platform_presence_analytics": platform_analytics_snapshot,
                            })
                        else:
                            merge_company_extra_data(cid, {
                                "research_full": result,
                                "research_full_updated_at": datetime.utcnow().isoformat(),
                                "platform_presence_analytics": platform_analytics_snapshot,
                            })
                        
                        # Clear old caches
                        if cache_key in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cache_key]
                        st.session_state.auto_brief_text = None
                        st.session_state.pop(f"research_wysiwyg_html_{cache_key}", None)
                        st.session_state.pop("research_command_result", None)
                        st.session_state.pop("research_command_result_cid", None)
                        st.session_state[f"research_fresh_result_{cache_key}"] = result
                        
                        # Update session_state with session_id for next load
                        st.session_state.session_id = session_id
                        
                        # Deep Research Safety Valve: Kill the old cache
                        force_refresh_research(session_id)
                        
                        st.success("Research re-run complete. Edit below if needed, then **Save** to write to DB, Notion, and Drive.")
                        
                        # st.rerun() to show the brand new research results
                        st.rerun()
                    else:
                        st.error("Research failed." + (f" {err_msg}" if err_msg else " Check ANTHROPIC_API_KEY in keys.env and that the model is available."))
            with ref_col2:
                if st.button("🗑 Delete research", key=f"delete_research_{cache_key}", use_container_width=False, help="Delete deep research (DB + session); Run deep research will be available again"):
                    # Delete from sessions.strategic_summary (canonical) — clear all sessions for this company so "researched" goes back to –
                    if cid:
                        for _sess in get_sessions_for_company(cid):
                            save_session_summary(_sess["id"], "", msg_count=0)
                    elif _intel_sid:
                        save_session_summary(_intel_sid, "", msg_count=0)
                    
                    # Delete from company.extra_data (backward compatibility)
                    if is_advisor:
                        merge_advisor_company_extra_data(aid, {"research_full": "", "research_full_updated_at": datetime.utcnow().isoformat()})
                    else:
                        merge_company_extra_data(cid, {"research_full": "", "research_full_updated_at": datetime.utcnow().isoformat()})
                    
                    st.session_state.pop(_fresh_key, None)
                    
                    # Targeted cache invalidation
                    from services.cache_registry import invalidate_group
                    invalidate_group("research")
                    
                    st.toast("Deep research deleted.")
                    st.rerun()
            # Always use expandable sections for deep research (format we use for pipeline / Command Center)
            editor_html = research_display_to_editor_html(research_full_display)
            use_wysiwyg = False  # Sectioned expanders only; WYSIWYG iframe skipped for consistent expandable format
            wysiwyg_key = f"research_wysiwyg_html_{cache_key}"
            if use_wysiwyg:
                initial_for_editor = st.session_state.get(wysiwyg_key) or editor_html or ""
                editor_page = wysiwyg_editor_html_page(initial_for_editor)
                import urllib.parse
                data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(editor_page)
                event_value = st_iframe_event(data_url, key=f"wysiwyg_editor_{cache_key}", default_width="100%")
                if event_value is not None:
                    st.session_state[wysiwyg_key] = event_value
                    st.rerun()
                current_research_text = (st.session_state.get(wysiwyg_key) or editor_html or "").strip() or research_full_display
                st.caption("Edit above, then click **Done editing** in the frame. Use **Save** below to write to DB and Drive.")
            else:
                try:
                    from streamlit_iframe_event import st_iframe_event
                except Exception:
                    st_iframe_event = None
                plain_for_split = html_to_plain_text(research_full_display) if is_html_content(research_full_display) else research_full_display
                sections = split_research_into_sections(plain_for_split)

                # Platform presence (MSN/Yahoo/NewsBreak) shown before Eligibility Verdict (display-only; does not change prompts or research text)
                _pp_ed = (comp.get("extra_data") or {}) if isinstance(comp.get("extra_data"), dict) else {}
                try:
                    from tracker_sources import load_domain_to_platforms, get_presence_for_domain
                    platform_presence = (_pp_ed.get("platform_presence") or {}) if isinstance(_pp_ed, dict) else {}
                    if not platform_presence:
                        _tracker_map = load_domain_to_platforms()
                        platform_presence = get_presence_for_domain(comp.get("domain") or "", _tracker_map) or {}
                except Exception:
                    platform_presence = {}
                _presence_mark = (lambda present: "✓" if present else "<span style='color:#dc2626;'>✗</span>")
                _has_any_platform_presence = bool(
                    (platform_presence.get("msn") or False) or
                    (platform_presence.get("yahoo") or False) or
                    (platform_presence.get("newsbreak") or False)
                )

                # Verdict override rule (display-only):
                # If verdict is Stop/Fail or Proceed with Caveats AND platform presence exists, we proceed (Syndication/Accelerate path).
                import re as _re
                _elig_line = ""
                _m = _re.search(r"(?im)^\s*Eligibility\s*Verdict\s*:\s*(.+?)\s*$", plain_for_split or "")
                if _m:
                    _elig_line = (_m.group(1) or "").strip()
                _elig_norm = _elig_line.lower()
                _elig_is_caveats = ("caveat" in _elig_norm) or ("caveats" in _elig_norm)
                _elig_is_stop_or_fail = ("stop" in _elig_norm) or ("fail" in _elig_norm)
                _elig_override_proceed = bool(_has_any_platform_presence and (_elig_is_caveats or _elig_is_stop_or_fail))

                # Dynamic platform analytics expander (Similarweb + presence signals)
                platform_presence_analytics = (_pp_ed.get("platform_presence_analytics") or {}) if isinstance(_pp_ed, dict) else {}
                if st.button("🔄 Refresh platform analytics", key=f"refresh_platform_analytics_{cache_key}", use_container_width=False):
                    with st.spinner("Refreshing platform analytics..."):
                        fresh_analytics = build_platform_analytics_for_domain(
                            comp.get("domain", ""),
                            platform_presence=platform_presence,
                        )
                        if is_advisor:
                            merge_advisor_company_extra_data(aid, {"platform_presence_analytics": fresh_analytics})
                        else:
                            merge_company_extra_data(cid, {"platform_presence_analytics": fresh_analytics})
                        st.session_state["platform_presence_analytics_fresh"] = fresh_analytics
                        st.rerun()
                if st.session_state.get("platform_presence_analytics_fresh"):
                    platform_presence_analytics = st.session_state.pop("platform_presence_analytics_fresh")

                with st.expander("📊 Platform presence analytics", expanded=False):
                    pa = platform_presence_analytics if isinstance(platform_presence_analytics, dict) else {}
                    sw = pa.get("similarweb") if isinstance(pa.get("similarweb"), dict) else {}
                    latest_visits = sw.get("latest_monthly_visits")
                    latest_month = sw.get("latest_visits_month") or "latest"
                    status = pa.get("status") or "unknown"
                    updated = pa.get("updated_at") or "n/a"
                    st.markdown(
                        f"<div style='font-size:12px;color:var(--text-2);margin-bottom:8px;'>"
                        f"Status: <b>{status}</b> · Updated: {updated}</div>",
                        unsafe_allow_html=True,
                    )
                    pc1, pc2, pc3 = st.columns(3)
                    with pc1:
                        st.metric("Monthly visits", f"{int(latest_visits):,}" if isinstance(latest_visits, (int, float)) else "n/a")
                    with pc2:
                        st.metric("Visits month", str(latest_month))
                    with pc3:
                        st.metric("Source", pa.get("source") or "similarweb+presence")

                    channels = sw.get("channel_shares") if isinstance(sw.get("channel_shares"), dict) else {}
                    if channels:
                        chan_txt = ", ".join(
                            f"{k}: {round(float(v) * 100, 1)}%"
                            for k, v in sorted(channels.items(), key=lambda kv: (kv[1] or 0), reverse=True)
                            if isinstance(v, (int, float))
                        )
                        st.markdown(f"**Traffic channels:** {chan_txt}" if chan_txt else "**Traffic channels:** n/a")
                    else:
                        st.caption("Traffic channels: n/a")

                    socials = sw.get("top_social_networks") if isinstance(sw.get("top_social_networks"), list) else []
                    if socials:
                        social_details = pa.get("social_presence_details") if isinstance(pa.get("social_presence_details"), list) else []
                        if social_details:
                            s_txt = ", ".join(
                                f"{d.get('network','unknown')} ({'yes' if d.get('present') else 'no'}, {d.get('confidence','low')})"
                                for d in social_details[:7] if isinstance(d, dict)
                            )
                            st.markdown(f"**Social presence (search-confirmed):** {s_txt}" if s_txt else "**Social presence:** n/a")
                        else:
                            s_txt = ", ".join((s.get("network") or "unknown") for s in socials[:6] if isinstance(s, dict))
                            st.markdown(f"**Top social networks:** {s_txt}" if s_txt else "**Top social networks:** n/a")
                    else:
                        social_details = pa.get("social_presence_details") if isinstance(pa.get("social_presence_details"), list) else []
                        if social_details:
                            s_txt = ", ".join(
                                f"{d.get('network','unknown')} ({'yes' if d.get('present') else 'no'}, {d.get('confidence','low')})"
                                for d in social_details[:7] if isinstance(d, dict)
                            )
                            st.markdown(f"**Social presence (search-confirmed):** {s_txt}" if s_txt else "Social presence: n/a")
                        else:
                            st.caption("Top social networks: n/a")

                    rows = pa.get("platform_details") if isinstance(pa.get("platform_details"), list) else []
                    if rows:
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            pname = (row.get("platform") or "").upper()
                            present = "yes" if row.get("present") else "no"
                            site_vis = row.get("site_monthly_visits")
                            site_vis_txt = f"{int(site_vis):,}" if isinstance(site_vis, (int, float)) else "n/a"
                            st.markdown(f"- **{pname}** · Presence: {present} · Site monthly visits: {site_vis_txt}")
                            pos_sum = (row.get("positioning_summary") or "").strip()
                            if pos_sum:
                                st.caption(f"{pname} positioning: {pos_sum}")
                    else:
                        st.caption("No platform detail rows yet. Click refresh to fetch live analytics.")

                    errs = sw.get("errors") if isinstance(sw.get("errors"), list) else []
                    if errs:
                        st.caption("Similarweb notes: " + "; ".join(str(e) for e in errs[:4]))

                research_editing_section_key = f"research_editing_section_{cache_key}"
                research_section_edits_key = f"research_section_edits_{cache_key}"
                research_section_pending_key = f"research_section_pending_{cache_key}"
                if research_section_edits_key not in st.session_state:
                    st.session_state[research_section_edits_key] = {}
                overrides = st.session_state.get(research_section_edits_key, {})
                editing_section = st.session_state.get(research_editing_section_key)
                section_edits = []
                for i, (sec_title, body) in enumerate(sections):
                    section_html = research_display_to_editor_html(body) if body else ""
                    effective_content = (overrides.get(i) if i in overrides and overrides.get(i) is not None else section_html) or ""
                    with st.expander(sec_title, expanded=(i == 0)):
                        if i == 0:
                            msn = bool(platform_presence.get("msn", False))
                            yahoo = bool(platform_presence.get("yahoo", False))
                            newsbreak = bool(platform_presence.get("newsbreak", False))
                            _override_div = (
                                "<div style='font-size:12px;color:#166534;margin-top:6px;'><b>Proceed override:</b> "
                                "Verdict is Stop/Fail or Proceed with Caveats, but platform distribution is available → proceed (Syndication/Accelerate path).</div>"
                                if _elig_override_proceed else ""
                            )
                            st.markdown(
                                "<div style='background:var(--accent-bg);border:1px solid #c7d2fe;border-radius:10px;"
                                "padding:10px 12px;margin:2px 0 10px 0;'>"
                                "<div style='font-size:12px;font-weight:600;margin-bottom:4px;'>Platform presence</div>"
                                f"<div style='font-size:12px;color:var(--text-2);'>"
                                f"MSN {_presence_mark(msn)}&nbsp;&nbsp; Yahoo {_presence_mark(yahoo)}&nbsp;&nbsp; NewsBreak {_presence_mark(newsbreak)}"
                                "</div>"
                                + _override_div
                                + "</div>",
                                unsafe_allow_html=True,
                            )
                        if editing_section == i and st_iframe_event:
                            pending_key = f"{research_section_pending_key}_{i}"
                            editor_page = wysiwyg_editor_html_page(effective_content or section_html or "")
                            import urllib.parse
                            _data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(editor_page)
                            st.markdown('<div class="research-section-editor-outer" style="height:0;overflow:visible;margin:0;padding:0;"></div>', unsafe_allow_html=True)
                            event_value = st_iframe_event(_data_url, key=f"research_sec_editor_{cache_key}_{i}", default_width="100%")
                            if event_value is not None:
                                _edited = event_value
                                if isinstance(event_value, dict):
                                    _edited = event_value.get("value") or event_value.get("html") or ""
                                elif isinstance(event_value, str) and event_value.strip().startswith("{"):
                                    try:
                                        import json
                                        _parsed = json.loads(event_value)
                                        _edited = (_parsed.get("value") or _parsed.get("html") or "") if isinstance(_parsed, dict) else event_value
                                    except Exception:
                                        _edited = event_value
                                if not isinstance(_edited, str):
                                    _edited = str(_edited) if _edited else ""
                                _edited = (_edited or "").strip() or section_html or ""
                                st.session_state[pending_key] = _edited
                                _new_overrides = dict(st.session_state.get(research_section_edits_key, {}))
                                _new_overrides[i] = _edited
                                st.session_state[research_section_edits_key] = _new_overrides
                                st.session_state[research_editing_section_key] = None
                                st.session_state.pop(pending_key, None)
                                clear_ui_cache()
                                st.rerun()
                            _pending = st.session_state.get(pending_key)
                            if st.button("Save edit", key=f"research_sec_save_btn_{cache_key}_{i}", type="primary", use_container_width=True):
                                _to_apply = (_pending or effective_content or section_html or "").strip() or section_html or ""
                                _new_overrides = dict(st.session_state.get(research_section_edits_key, {}))
                                _new_overrides[i] = _to_apply
                                st.session_state[research_section_edits_key] = _new_overrides
                                st.session_state[research_editing_section_key] = None
                                st.session_state.pop(pending_key, None)
                                clear_ui_cache()
                                st.rerun()
                            st.caption("Edit in the frame above (same format, tables). Click **Save edit** here when done to return to the normal view.")
                        elif editing_section == i:
                            st.caption("Edit the section HTML below, then click **Apply edit** to save.")
                            with st.form(key=f"research_sec_edit_{cache_key}_{i}"):
                                _edit_html = st.text_area("Section HTML", value=effective_content or section_html or "", height=500, key=f"research_sec_ta_{cache_key}_{i}", label_visibility="collapsed")
                                if st.form_submit_button("Apply edit"):
                                    _apply = (_edit_html or "").strip() or section_html or ""
                                    _new_overrides = dict(st.session_state.get(research_section_edits_key, {}))
                                    _new_overrides[i] = _apply
                                    st.session_state[research_section_edits_key] = _new_overrides
                                    st.session_state[research_editing_section_key] = None
                                    clear_ui_cache()
                                    st.rerun()
                        else:
                            st.markdown("<div class='research-doc-view'>" + (effective_content or "(No content)") + "</div>", unsafe_allow_html=True)
                            if st.button("✏️ Edit", key=f"research_edit_sec_{cache_key}_{i}", help="Edit this section (keeps format, tables)"):
                                st.session_state[research_editing_section_key] = i
                                st.rerun()
                    section_edits.append((effective_content or "").strip() or section_html or "")
                current_research_text = "\n\n".join(
                    f"<h2>{sec_title}</h2>\n\n{html}" for (sec_title, _), html in zip(sections, section_edits)
                ).strip() if section_edits else research_full_display
            if st.button("💾 Save", key=f"save_research_all_{cache_key}", use_container_width=True, help="Save research to DB and Drive (proper format with tables)"):
                _research = (current_research_text or research_full_display or "").strip()
                if not _research:
                    st.warning("No research to save.")
                else:
                    if is_advisor:
                        merge_advisor_company_extra_data(aid, {"research_full": _research, "research_full_updated_at": datetime.utcnow().isoformat()})
                    else:
                        merge_company_extra_data(cid, {"research_full": _research, "research_full_updated_at": datetime.utcnow().isoformat()})
                    _doc_name = comp.get("domain") or comp.get("name", "Company")
                    _content_for_drive = research_display_to_editor_html(_research) if _research and not is_html_content(_research) else _research
                    fid, _, err_drive = create_doc_in_drive(f"{_doc_name} - Research", _content_for_drive or _research)
                    st.session_state.auto_brief_text = None
                    st.session_state.pop(wysiwyg_key, None)
                    st.session_state.pop(f"research_editing_section_{cache_key}", None)
                    st.session_state.pop(f"research_section_edits_{cache_key}", None)
                    st.session_state[_fresh_key] = _research
                    if fid:
                        st.toast("Research saved to DB and Drive (with tables and formatting).")
                    else:
                        st.toast("Research saved to DB." + (" Drive: " + (err_drive or "failed") if err_drive else ""))
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
                    current_for_ai = (html_to_plain_text(current_research_text) if is_html_content(current_research_text) else current_research_text) or research_full_display
                    cmd_result = research_command_ai(comp.get("domain", ""), current_for_ai, research_cmd.strip())
                if cmd_result:
                    st.session_state["research_command_result"] = cmd_result
                    st.session_state["research_command_result_cid"] = cache_key
                    st.session_state["research_command_replace_key"] = f"research_full_edit_{cache_key}"
                    st.rerun()
                else:
                    st.error("Command failed.")

            if st.session_state.get("research_command_result_cid") == cache_key and st.session_state.get("research_command_result"):
                cmd_result = st.session_state["research_command_result"]
                cmd_title = cmd_result.get("title", "Extended research") if isinstance(cmd_result, dict) else "Extended research"
                cmd_content = cmd_result.get("content", cmd_result) if isinstance(cmd_result, dict) else (cmd_result or "")
                with st.expander(f"{cmd_title} — Add to research or Dismiss", expanded=True):
                    st.caption("Use **Add to research** to append this as the next numbered section (8., 9., …) and save.")
                    st.markdown(f"<div style='font-size:12px;white-space:pre-wrap;max-height:300px;overflow-y:auto;'>{cmd_content[:25000]}{'…' if len(cmd_content) > 25000 else ''}</div>", unsafe_allow_html=True)
                    app_col1, app_col2 = st.columns([1, 3])
                    with app_col1:
                        if st.button("Add to research", key=f"cmd_append_{cache_key}", use_container_width=True, help="Append as next numbered section (8., 9., …) and save to DB"):
                            current = (current_research_text or research_full_display or "").strip()
                            plain_current = html_to_plain_text(current) if is_html_content(current) else current
                            next_num = get_next_section_number(plain_current or "")
                            new_block = format_appended_section(cmd_content, cmd_title, next_num)
                            merged = f"{current}\n\n{new_block}" if current else new_block
                            if not (merged or "").strip():
                                st.warning("No content to add.")
                            else:
                                st.session_state[_fresh_key] = merged
                                if is_advisor:
                                    merge_advisor_company_extra_data(aid, {"research_full": merged, "research_full_updated_at": datetime.utcnow().isoformat()})
                                else:
                                    merge_company_extra_data(cid, {"research_full": merged, "research_full_updated_at": datetime.utcnow().isoformat()})
                                if _intel_sid:
                                    save_session_summary(_intel_sid, merged, msg_count=0)
                                clear_ui_cache()
                                st.session_state.auto_brief_text = None
                                st.session_state.pop(wysiwyg_key, None)
                                st.session_state.pop("research_command_result", None)
                                st.session_state.pop("research_command_result_cid", None)
                                st.toast(f"Added as section {next_num} and saved. Full document updated.")
                                st.rerun()
                    with app_col2:
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
                    with st.spinner("Running AI research on this person..."):
                        brief = research_person_for_company(comp=comp, research_notes=rn, linkedin_url=url)
                    st.session_state.intel_lookup_lead = {"linkedin": url, "name": "", "email": "", "role": "Contact", "domain": comp.get("domain", "")}
                    st.session_state.intel_person_brief = brief
                    st.session_state.intel_suggestion = None
                    st.rerun()
                elif email_match:
                    em = email_match.group(0).strip()
                    with st.spinner("Running AI research on this person..."):
                        brief = research_person_for_company(comp=comp, research_notes=rn, email=em)
                    st.session_state.intel_lookup_lead = {"email": em, "name": "", "linkedin": "", "role": "Contact", "domain": comp.get("domain", "")}
                    st.session_state.intel_person_brief = brief
                    st.session_state.intel_suggestion = None
                    st.rerun()
                else:
                    suggestion_phrases = ("missing", "add ", "add a", "expand", "section on", "suggest", "what's missing", "what to add")
                    is_suggestion = any(p in raw.lower() for p in suggestion_phrases)
                    if is_suggestion:
                        with st.spinner("Asking AI for suggestion..."):
                            suggestion = intelligence_request(comp, rn, raw)
                        st.session_state.intel_suggestion = suggestion
                        st.session_state.intel_suggestion_raw = raw
                        st.session_state.intel_lookup_lead = None
                        st.session_state.intel_person_brief = None
                        st.rerun()
                    else:
                        with st.spinner("Running AI research on this person..."):
                            brief = research_person_for_company(comp=comp, research_notes=rn, person_name=raw)
                        st.session_state.intel_lookup_lead = {"name": raw, "email": "", "linkedin": "", "role": "Contact", "domain": comp.get("domain", "")}
                        st.session_state.intel_person_brief = brief
                        st.session_state.intel_suggestion = None
                        st.rerun()

        if st.session_state.get("intel_lookup_lead") or st.session_state.get("intel_person_brief"):
            lead = st.session_state.get("intel_lookup_lead") or {}
            brief = st.session_state.get("intel_person_brief") or ""
            label = (lead.get("name") or lead.get("email") or "Contact").strip() or "Person"
            st.markdown(sh_small(f"Research — {label}"), unsafe_allow_html=True)
            if brief:
                st.markdown(f"<div class='brief-card'><div style='font-size:13px;white-space:pre-wrap;'>{brief}</div></div>", unsafe_allow_html=True)
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
            st.markdown(f"<div class='brief-card'><div style='font-size:13px;white-space:pre-wrap;'>{st.session_state.intel_suggestion}</div></div>", unsafe_allow_html=True)
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
                _norm = normalize_contact_status(ct.get("status", "not_contacted"))
                scolor = CONTACT_STATUS_COLORS.get(_norm, "gray")
                slabel = CONTACT_STATUS_LABELS.get(_norm, "")
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
    is_advisor_strategist = (_src_s == "Media Houses" and _aid_s and cid)
    if not cid:
        st.markdown(
            "<div style='text-align:center;padding:80px 0;color:var(--text-3);'>"
            "<div style='font-size:24px;opacity:.2;margin-bottom:12px;'>◆</div>"
            "<div style='font-size:14px;font-weight:500;'>Select a company to start</div>"
            "<div style='font-size:13px;margin-top:6px;'>COMPANIES → click →</div></div>",
            unsafe_allow_html=True)
    elif not has_perm("can_use_strategist"):
        st.warning("Strategist access required.")
    else:
        if not sid:
            sid = get_or_create_session(cid, ctid, user["id"])
            st.session_state.session_id = sid
        if is_advisor_strategist:
            adv_comp = get_advisor_company(_aid_s)
            comp = {"id": cid, "domain": adv_comp.get("domain", "—"), "name": adv_comp.get("domain") or "—", "publisher_type": adv_comp.get("parent_name") or "—", "content_type": "—", "monthly_visits": None, "research_status": "—", "notion_page_id": adv_comp.get("notion_page_id"), "extra_data": adv_comp.get("extra_data") or {}} if adv_comp else None
            ct = get_contact(ctid)
            comp["_child_company_focus"] = True
        else:
            comp = get_company(cid)
            ct = get_contact(ctid)
        if not ct:
            ct = {"id": None, "name": comp.get("domain", "Company") if comp else "Company", "role": "", "email": "", "linkedin": "", "country": "", "state": "", "city": ""}
        if not comp:
            st.error("Company not found.")
        else:
            # Research cache: Intelligence uses advisor_{id} for Media Houses, cid for Independent
            research_cache_key = f"advisor_{_aid_s}" if is_advisor_strategist else cid
            sid = st.session_state.session_id
            ctid = st.session_state.sel_contact
            _ro = (comp.get("extra_data") or {}).get("recommended_offering") or {}
            recommended_product_key = (_ro.get("product_key") or "").strip().lower()
            if recommended_product_key not in ("mediaos", "accelerate"):
                recommended_product_key = None
            ct = get_contact(ctid) if ctid else None
            if not ct:
                ct = {"id": None, "name": comp.get("domain", "Company"), "role": "", "email": "", "linkedin": "", "country": "", "state": "", "city": ""}
            if ct:
                # ── FROM / TO selectors ──
                # FROM: only registered users, displayed by email
                # TO: only contacts that have an email for this company, displayed by email
                sel1, sel2, sel3 = st.columns([1.5, 1.5, 1])
                with sel1:
                    all_users = get_all_users()
                    user_emails = {u["id"]: (u.get("email") or "").strip() or (u.get("username") or "?") for u in all_users}
                    from_options = [u["id"] for u in all_users if (u.get("email") or "").strip()]
                    if not from_options:
                        from_options = [u["id"] for u in all_users]
                    from_index = from_options.index(st.session_state.sender_id) if st.session_state.sender_id in from_options else 0
                    sender_id = st.selectbox(
                        "FROM (sender)",
                        options=from_options,
                        format_func=lambda x: f"📧 {user_emails.get(x, '?')}",
                        index=from_index,
                        key="from_sel",
                    )
                    st.session_state.sender_id = sender_id
                    sender_name = user_emails.get(sender_id, "")
                    sender_email = user_emails.get(sender_id, "").strip()  # Get email for sender profile lookup

                with sel2:
                    contacts = get_contacts(cid)
                    spoc_contacts = get_spoc_contacts_by_company(cid)
                    spoc_email_by_name = {}
                    spoc_email_by_parent_id = {}
                    spoc_linkedin_by_parent_id = {}
                    spoc_name_by_parent_id = {}
                    spoc_name_by_email = {}  # track down name using email for outreach plan
                    spoc_role_by_parent_id = {}
                    spoc_role_by_email = {}
                    for sc in (spoc_contacts or []):
                        em = (sc.get("email") or "").strip()
                        nm = (sc.get("name") or "").strip()
                        role = (sc.get("role") or "").strip()
                        pid = sc.get("spoc_contact_id")
                        if pid and nm and pid not in spoc_name_by_parent_id:
                            spoc_name_by_parent_id[pid] = nm
                        if pid and role and pid not in spoc_role_by_parent_id:
                            spoc_role_by_parent_id[pid] = role
                        if em and nm:
                            spoc_name_by_email[em] = nm
                        if em and role:
                            spoc_role_by_email[em] = role
                        if em:
                            if nm and nm not in spoc_email_by_name:
                                spoc_email_by_name[nm] = em
                            if pid and pid not in spoc_email_by_parent_id:
                                spoc_email_by_parent_id[pid] = em
                        link_url = (sc.get("linkedin_url") or "").strip()
                        if link_url and pid and pid not in spoc_linkedin_by_parent_id:
                            spoc_linkedin_by_parent_id[pid] = link_url
                    def _recipient_display(c):
                        em = (c.get("email") or "").strip()
                        if not em:
                            em = spoc_email_by_name.get((c.get("name") or "").strip(), "")
                        if not em:
                            em = spoc_email_by_parent_id.get(c["id"], "")
                        return em or f"{c.get('name', '?')} (no email)"
                    def _has_email(c):
                        return (c.get("email") or "").strip() or spoc_email_by_name.get((c.get("name") or "").strip()) or spoc_email_by_parent_id.get(c["id"])
                    # Show all contacts in TO; email required only at draft/send
                    to_options = [c["id"] for c in contacts]
                    ct_display = {c["id"]: _recipient_display(c) for c in contacts}
                    to_options_with_company = [None] + to_options
                    ct_display[None] = "Company level (no contact)"
                    to_index = to_options_with_company.index(ctid) if ctid in to_options_with_company else 0
                    to_id = st.selectbox(
                        "TO (recipient)",
                        options=to_options_with_company,
                        format_func=lambda x: f"🏢 {ct_display.get(x, '?')}" if x is None else f"📨 {ct_display.get(x, '?')}",
                        index=to_index,
                        key="to_sel",
                    )
                    if to_id != ctid:
                        # If current session is company-level (research at company, no contact), only update
                        # the selected recipient for display; keep session and chat unchanged. Emails/content
                        # stay in recipient context via sel_contact.
                        sid = st.session_state.get("session_id")
                        sess = get_session(sid) if sid else None
                        is_company_level_session = bool(sess and sess.get("contact_id") is None)
                        if is_company_level_session:
                            st.session_state.sel_contact = to_id
                        else:
                            if to_id is not None:
                                select_contact(cid, to_id)
                            else:
                                st.session_state.sel_contact = None
                                st.session_state.session_id = get_or_create_session(cid, None, user["id"])
                                st.session_state.chat_messages = load_messages(st.session_state.session_id) if st.session_state.session_id else []
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

                # Enrich contact with SPOC email/LinkedIn/name so plan and strategist use context for the selected recipient
                ct_ctx = dict(ct)
                if not (ct_ctx.get("email") or "").strip():
                    ct_ctx["email"] = (spoc_email_by_parent_id.get(ctid) or spoc_email_by_name.get((ct.get("name") or "").strip()) or "").strip()
                if not (ct_ctx.get("linkedin") or "").strip():
                    ct_ctx["linkedin"] = (spoc_linkedin_by_parent_id.get(ctid) or "").strip()
                # Track down name using email: SPOC has email → use SPOC name for outreach plan (role, person-specific)
                _name = (ct_ctx.get("name") or "").strip()
                if not _name or "@" in _name or ".com" in _name or _name == "—" or _name == "?":
                    ct_ctx["name"] = (
                        spoc_name_by_email.get((ct_ctx.get("email") or "").strip()) or
                        spoc_name_by_parent_id.get(ctid) or _name or ""
                    ).strip() or ct_ctx.get("name", "")
                _email = (ct_ctx.get("email") or "").strip()
                if not (ct_ctx.get("role") or "").strip() and _email:
                    ct_ctx["role"] = spoc_role_by_email.get(_email) or spoc_role_by_parent_id.get(ctid) or ct_ctx.get("role", "")

                if not recommended_product_key:
                    st.warning("Run offering recommendation in Intelligence first so outreach uses the right product (MediaOS or Accelerate).")
                # ── Contact details & enrichment ──
                st.markdown(sh_small("Contact details & enrichment"), unsafe_allow_html=True)
                enrich_result_key = f"enrich_result_{ctid}"
                db_email = (ct_ctx.get("email") or "").strip()
                db_linkedin = (ct_ctx.get("linkedin") or "").strip()
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
                credits = get_signalhire_credits()
                if credits is not None:
                    st.caption(f"SignalHire credits: {credits}")
                else:
                    st.caption("Enrichment uses mock data when no API/callback")

                # ── Contacts from deep research: extract names → find LinkedIn URLs ──
                st.markdown(sh_small("Contacts from research"), unsafe_allow_html=True)
                linkedin_results_key = f"linkedin_lookup_{research_cache_key}"
                if is_advisor_strategist and _aid_s:
                    _fa = get_advisor_company(_aid_s)
                    if _fa:
                        comp = {**comp, "extra_data": _fa.get("extra_data") or {}}
                else:
                    _fc = get_company(cid)
                    if _fc:
                        comp = _fc
                def _persist_linkedin_results(lst):
                    if is_advisor_strategist and _aid_s:
                        merge_advisor_company_extra_data(_aid_s, {"linkedin_lookup_results": lst})
                    else:
                        merge_company_extra_data(cid, {"linkedin_lookup_results": lst})

                do_linkedin_lookup = st.button(
                    "🔗 Extract contacts & find LinkedIn URLs",
                    key="btn_linkedin_lookup",
                    type="secondary",
                    use_container_width=True,
                    help="Parse deep research for people (SPOC/stakeholder tables), then find their LinkedIn profiles via search.",
                )
                if do_linkedin_lookup:
                    rn = st.session_state.research_cache.get(research_cache_key, [])
                    if not rn and comp.get("notion_page_id"):
                        with st.spinner("Loading research from Notion…"):
                            rn = fetch_research_from_notion(comp["notion_page_id"])
                            if rn:
                                st.session_state.research_cache[research_cache_key] = rn
                    # Same source order as Intelligence: company.extra_data → session strategic_summary → session_state
                    research_full = ((comp.get("extra_data") or {}).get("research_full") or "").strip()
                    if not research_full and sid:
                        _sess_summary, _ = get_session_summary(sid)
                        research_full = (_sess_summary or "").strip()
                    if not research_full:
                        research_full = (st.session_state.get(f"research_fresh_result_{research_cache_key}") or "").strip()
                    research_notes_text = "\n\n".join(
                        f"{(n.get('title') or 'Section')}\n{(n.get('content') or '').strip()}"
                        for n in (rn or [])
                    ).strip()
                    research_combined = (research_full or research_notes_text or "").strip()
                    company_name = comp.get("domain") or comp.get("name") or "this company"
                    with st.spinner("Extracting names from SPOC tables…"):
                        contacts = extract_spoc_table_contacts("", research_combined, company_name)
                    if not contacts:
                        st.info("No SPOC table found in research. Run deep research in Intelligence to get a SPOC/stakeholder table first.")
                    else:
                        progress = st.progress(0, text=f"Finding LinkedIn URLs (0/{len(contacts)})…")
                        def _prog(i, total, name):
                            progress.progress((i + 1) / max(1, total), text=f"Finding LinkedIn URLs ({i + 1}/{total}): {name[:30]}…")
                        results = run_linkedin_lookup_for_contacts(contacts, progress_callback=_prog)
                        progress.empty()
                        st.session_state[linkedin_results_key] = results
                        _persist_linkedin_results(results)
                        st.success(f"Found {sum(1 for r in results if r.get('linkedin_url'))} LinkedIn URLs for {len(results)} contacts.")
                        st.rerun()

                def _linkedin_result_matches_company(result_company, comp):
                    """True only if this result is for the current company. Strict: brief/deep research only; exclude other companies."""
                    if not comp:
                        return True
                    rc = (result_company or "").strip()
                    rc_lower = rc.lower()
                    domain = (comp.get("domain") or "").strip().lower()
                    name = (comp.get("name") or "").strip().lower()
                    if not domain and not name:
                        return True
                    # Unspecified or "this company" -> treat as current (extraction sets company_name for all)
                    if not rc or rc_lower == "this company":
                        return True
                    identifiers = [x for x in [domain, name] if x]
                    for id in identifiers:
                        if id and (rc_lower == id or (len(id) >= 3 and id in rc_lower) or (len(rc_lower) >= 3 and rc_lower in id)):
                            return True
                    return False

                _saved_list = None
                if is_advisor_strategist and _aid_s:
                    _adv = get_advisor_company(_aid_s)
                    _saved_list = (_adv.get("extra_data") or {}).get("linkedin_lookup_results")
                else:
                    _saved_list = (comp.get("extra_data") or {}).get("linkedin_lookup_results")
                if _saved_list and isinstance(_saved_list, list):
                    _raw_len = len(_saved_list)
                    _saved_list = [r for r in _saved_list if _linkedin_result_matches_company(r.get("company"), comp)]
                    _existing = st.session_state.get(linkedin_results_key)
                    if _saved_list or not (isinstance(_existing, list) and len(_existing) > 0):
                        st.session_state[linkedin_results_key] = _saved_list
                    if _raw_len != len(_saved_list) and _saved_list:
                        _persist_linkedin_results(_saved_list)
                # Always ensure we have a list for the strategist contacts table (extraction or DB fallback)
                if linkedin_results_key not in st.session_state:
                    st.session_state[linkedin_results_key] = []

                def _strategist_contacts_display_list(contacts_for_fallback=None, spocs_for_fallback=None):
                    """Rows for the strategist contacts table: extraction results if any, else company contacts from DB (same columns)."""
                    raw = st.session_state.get(linkedin_results_key) or []
                    filtered = [r for r in raw if _linkedin_result_matches_company(r.get("company"), comp)]
                    if filtered:
                        return filtered, True  # from extraction
                    # Fallback: company contacts from DB in same shape (Name, Role, Company, LinkedIn, Email, Phone, Status)
                    company_name = (comp.get("domain") or comp.get("name") or "—").strip()
                    contacts = contacts_for_fallback if contacts_for_fallback is not None else (get_contacts(cid) if cid else [])
                    spocs = spocs_for_fallback if spocs_for_fallback is not None else (get_spoc_contacts_by_company(cid) if cid else [])
                    spoc_by_contact_id = {}
                    for s in (spocs or []):
                        pid = s.get("spoc_contact_id")
                        if pid and pid not in spoc_by_contact_id:
                            spoc_by_contact_id[pid] = s
                    rows = []
                    for c in (contacts or []):
                        s = spoc_by_contact_id.get(c.get("id"))
                        rows.append({
                            "id": c.get("id"),
                            "name": (c.get("name") or "").strip() or "—",
                            "role": (c.get("role") or "").strip() or (s.get("role") or "").strip() if s else (c.get("role") or "").strip() or "—",
                            "company": company_name or "—",
                            "linkedin_url": (c.get("linkedin") or "").strip() or ((s.get("linkedin_url") or "").strip() if s else "") or "—",
                            "email": (c.get("email") or "").strip() or ((s.get("email") or "").strip() if s else "") or "—",
                            "phone": (s.get("phone") or "").strip() if s else "—",
                            "enrichment_status": "saved" if (c.get("email") or (s and s.get("email"))) else "—",
                        })
                    return rows, False

                default_company = (comp.get("domain") or comp.get("name") or "")
                # ── Look up people (chat): append to same table as Extract; table appears even without running extract ──
                lookup_chat_key = f"linkedin_lookup_chat_{research_cache_key}"
                if lookup_chat_key not in st.session_state:
                    st.session_state[lookup_chat_key] = []
                st.caption("Look up a person: type a name to find their LinkedIn and add to the table below.")
                for _msg in st.session_state[lookup_chat_key]:
                    with st.chat_message(_msg["role"]):
                        st.markdown(_msg["content"])
                _lookup_inp = st.chat_input("Name or \"Name — Role\" to look up", key=f"linkedin_lookup_chat_inp_{research_cache_key}")
                if _lookup_inp and (_lookup_inp or "").strip():
                    _raw = (_lookup_inp or "").strip()
                    st.session_state[lookup_chat_key].append({"role": "user", "content": _raw})
                    _raw = _strip_linkedin_command_prefix(_raw)
                    if not _raw:
                        st.session_state[lookup_chat_key].append({"role": "assistant", "content": "No name to look up. Type a person's name (e.g. John Smith or find Jane Doe)."})
                    else:
                        _to_add = []
                        if " and " in _raw:
                            for _part in _raw.split(" and "):
                                _part = _part.strip()
                                if not _part:
                                    continue
                                if " — " in _part:
                                    _nm, _rest = _part.split(" — ", 1)
                                    _to_add.append((_nm.strip(), _rest.strip(), default_company))
                                elif ", " in _part:
                                    _p = re.split(r"\s*,\s*", _part, 1)
                                    _to_add.append((_p[0].strip(), "", default_company if len(_p) <= 1 else (_p[1] or "").strip()))
                                else:
                                    _to_add.append((_part, "", default_company))
                        else:
                            if " — " in _raw:
                                _nm, _rest = _raw.split(" — ", 1)
                                _to_add.append((_nm.strip(), _rest.strip(), default_company))
                            elif ", " in _raw:
                                _p = re.split(r"\s*,\s*", _raw, 1)
                                _nm = (_p[0] or "").strip()
                                _company = (_p[1] or "").strip() if len(_p) > 1 else default_company
                                _to_add.append((_nm, "", _company))
                            else:
                                _to_add.append((_raw, "", default_company))
                        _results_list = st.session_state.get(linkedin_results_key, []) or []
                        _new_list = list(_results_list)
                        for _name, _role, _company in _to_add:
                            _name = (_name or "").strip()
                            if not _name:
                                continue
                            with st.spinner(f"Finding LinkedIn for {_name}…"):
                                _url = get_linkedin_url_for_contact(_name, _company)
                            _new_list.append({"name": _name, "role": _role, "company": _company, "linkedin_url": _url or ""})
                        st.session_state[linkedin_results_key] = _new_list
                        _persist_linkedin_results(_new_list)
                        st.session_state[lookup_chat_key].append({"role": "assistant", "content": f"Added {len(_to_add)} contact(s) to the table below."})
                    st.rerun()

                # Always show the same clean contacts table (extraction list or DB fallback); same columns regardless of recipient
                display_list, from_extraction = _strategist_contacts_display_list(contacts_for_fallback=contacts, spocs_for_fallback=spoc_contacts)
                _contacts_edit_key = f"strategist_contacts_edit_mode_{research_cache_key}"
                if _contacts_edit_key not in st.session_state:
                    st.session_state[_contacts_edit_key] = False
                _table_df = pd.DataFrame([
                    {
                        "Name": (x.get("name") or "").strip() or "—",
                        "Role": (x.get("role") or "").strip() or "—",
                        "Company": (x.get("company") or "").strip() or "—",
                        "LinkedIn": (x.get("linkedin_url") or "").strip() or "—",
                        "Email": (x.get("email") or "").strip() or "—",
                        "Phone": (x.get("phone") or "").strip() or "—",
                        "Status": (x.get("enrichment_status") or "").strip() or "—",
                    }
                    for x in display_list
                ])
                contact_ids = [x.get("id") for x in display_list] if not from_extraction else []
                # Toolbar: title + pencil (edit) icon; edit only when showing DB contacts
                _toolbar_col1, _toolbar_col2 = st.columns([6, 1])
                with _toolbar_col1:
                    st.markdown(sh_small("Contacts table"), unsafe_allow_html=True)
                with _toolbar_col2:
                    if not from_extraction:
                        _edit_label = "Done" if st.session_state[_contacts_edit_key] else "✏️"
                        if st.button(_edit_label, key=f"contacts_edit_btn_{research_cache_key}", help="Edit table (add rows, edit cells). Changes save automatically."):
                            st.session_state[_contacts_edit_key] = not st.session_state[_contacts_edit_key]
                            st.rerun()
                if st.session_state[_contacts_edit_key] and not from_extraction and cid:
                    _editor_key = f"strategist_contacts_editor_{research_cache_key}"
                    _edited_df = st.data_editor(
                        _table_df,
                        use_container_width=True,
                        hide_index=True,
                        num_rows="dynamic",
                        key=_editor_key,
                        column_config={
                            "Name": st.column_config.TextColumn("Name", width="medium", required=True),
                            "Role": st.column_config.TextColumn("Role", width="medium"),
                            "Company": st.column_config.TextColumn("Company", width="medium"),
                            "LinkedIn": st.column_config.TextColumn("LinkedIn", width="large"),
                            "Email": st.column_config.TextColumn("Email", width="medium"),
                            "Phone": st.column_config.TextColumn("Phone", width="small"),
                            "Status": st.column_config.TextColumn("Status", width="small"),
                        },
                    )
                    if _edited_df is not None and not _edited_df.equals(_table_df):
                        for i in range(len(_edited_df)):
                            row = _edited_df.iloc[i]
                            _nm = (row.get("Name") or "").strip() or ""
                            if not _nm:
                                continue
                            _role = (row.get("Role") or "").strip() or None
                            _link = (row.get("LinkedIn") or "").strip() or None
                            if _link == "—":
                                _link = None
                            _em = (row.get("Email") or "").strip() or None
                            if _em == "—":
                                _em = None
                            if i < len(contact_ids) and contact_ids[i]:
                                update_contact(contact_ids[i], name=_nm, role=_role, email=_em, linkedin=_link)
                            else:
                                upsert_contact(cid, name=_nm, role=_role, email=_em, linkedin=_link)
                        clear_ui_cache()
                        st.toast("Contacts saved.")
                        st.rerun()
                else:
                    st.dataframe(_table_df, use_container_width=True, hide_index=True)
                if not from_extraction and display_list:
                    st.caption("Company contacts (from database). Use \"Extract contacts & find LinkedIn URLs\" above to pull from research.")
                st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

                if from_extraction:
                    results_list = st.session_state[linkedin_results_key]
                    filtered_results = [r for r in results_list if _linkedin_result_matches_company(r.get("company"), comp)]
                    # Separation: Action tracking (SignalHire) remains LIVE for real-time status
                    # Check pending SignalHire enrichments and merge if completed
                    # This is NOT cached - always queries DB directly to show real-time pending/completed status
                    pending_updated = False
                    _new_list = []
                    for _r in results_list:
                        if _r.get("enrichment_status") == "pending" and _r.get("enrichment_request_id"):
                            _er = get_enrichment_by_request_id(_r["enrichment_request_id"])  # LIVE query
                            if _er and _er.get("status") == "completed" and _er.get("result_json"):
                                _res = _er["result_json"]
                                _email = (_res.get("emails") or [{}])[0].get("value", "") if _res.get("emails") else ""
                                _phone = (_res.get("phones") or [{}])[0].get("value", "") if _res.get("phones") else ""
                                _r = {**_r, "email": _email, "phone": _phone, "enrichment_status": "completed", "enrichment_request_id": None}
                                _nm = (_r.get("name") or "").strip()
                                if _nm:
                                    upsert_spoc_contact(cid, ctid, _nm, role=(_r.get("role") or "").strip() or None, email=_email or None, phone=_phone or None, linkedin_url=(_r.get("linkedin_url") or "").strip() or None)
                                pending_updated = True
                        else:
                            # Completed but email/phone empty (e.g. callback arrived after we marked completed): fill from enrichment_results by LinkedIn URL
                            _link = (_r.get("linkedin_url") or "").strip()
                            _has_contact = (_r.get("email") or "").strip() or (_r.get("phone") or "").strip()
                            if _r.get("enrichment_status") == "completed" and not _has_contact and _link:
                                _er = get_completed_enrichment_by_identifier(_link)
                                if _er and _er.get("result_json"):
                                    _res = _er["result_json"]
                                    _email = (_res.get("emails") or [{}])[0].get("value", "") if _res.get("emails") else ""
                                    _phone = (_res.get("phones") or [{}])[0].get("value", "") if _res.get("phones") else ""
                                    if _email or _phone:
                                        _r = {**_r, "email": _email, "phone": _phone}
                                        _nm = (_r.get("name") or "").strip()
                                        if _nm:
                                            upsert_spoc_contact(cid, ctid, _nm, role=(_r.get("role") or "").strip() or None, email=_email or None, phone=_phone or None, linkedin_url=_link or None)
                                        pending_updated = True
                        _new_list.append(_r)
                    still_pending = any(_r.get("enrichment_status") == "pending" for _r in _new_list)
                    if pending_updated:
                        st.session_state[linkedin_results_key] = _new_list
                        _persist_linkedin_results(_new_list)
                        st.rerun()
                    elif still_pending:
                        import time as _time
                        _time.sleep(2)
                        st.rerun()
                    results_list = st.session_state[linkedin_results_key]
                    filtered_results = [r for r in results_list if _linkedin_result_matches_company(r.get("company"), comp)]
                    with st.expander(f"Contacts with LinkedIn URLs ({len(filtered_results)}) — add, recheck, add to company", expanded=True):
                        st.caption("Add to company adds contacts to **this company only** (not under any SPOC). Use the block below to get email via SignalHire for each added contact.")
                        add_input_key = f"linkedin_add_input_{research_cache_key}"
                        add_val = st.text_input(
                            "Add or recheck",
                            placeholder="Add: Jane Smith | Jane — CEO. Re-check URL: recheck Jane Smith",
                            key=add_input_key,
                            label_visibility="collapsed",
                        )
                        add_btn = st.button("Add to list & find LinkedIn", key=f"linkedin_add_btn_{research_cache_key}", use_container_width=False)
                        if add_btn and (add_val or "").strip():
                            raw = (add_val or "").strip()
                            raw_lower = raw.lower()
                            default_company = (comp.get("domain") or comp.get("name") or "")

                            recheck_match = re.search(r"(?:recheck|re-check|find again)\s+(.+)", raw_lower, re.IGNORECASE)
                            if recheck_match:
                                name_asked = recheck_match.group(1).strip()
                                for idx, entry in enumerate(results_list):
                                    entry_name = (entry.get("name") or "").strip()
                                    if name_asked in entry_name or entry_name.lower() == name_asked.lower():
                                        with st.spinner(f"Re-checking LinkedIn for {entry_name}…"):
                                            new_url = get_linkedin_url_for_contact(entry_name, entry.get("company") or default_company)
                                        new_list = list(results_list)
                                        new_list[idx] = {**new_list[idx], "linkedin_url": new_url or ""}
                                        st.session_state[linkedin_results_key] = new_list
                                        _persist_linkedin_results(new_list)
                                        st.success(f"Updated LinkedIn for **{entry_name}**." if new_url else "No new URL found.")
                                        st.rerun()
                                st.warning(f"No contact named \"{name_asked}\" in the list.")
                            else:
                                raw = _strip_linkedin_command_prefix(raw)
                                if not raw:
                                    st.warning("No name to look up. Type a person's name (e.g. XYZ or John Smith).")
                                else:
                                    to_add = []
                                    if " and " in raw:
                                        for part in raw.split(" and "):
                                            part = part.strip()
                                            if not part:
                                                continue
                                            if " — " in part:
                                                nm, rest = part.split(" — ", 1)
                                                to_add.append((nm.strip(), rest.strip(), default_company))
                                            elif ", " in part:
                                                p = re.split(r"\s*,\s*", part, 1)
                                                to_add.append((p[0].strip(), "", default_company if len(p) <= 1 else (p[1] or "").strip()))
                                            else:
                                                to_add.append((part, "", default_company))
                                    else:
                                        if " — " in raw:
                                            nm, rest = raw.split(" — ", 1)
                                            to_add.append((nm.strip(), rest.strip(), default_company))
                                        elif ", " in raw:
                                            p = re.split(r"\s*,\s*", raw, 1)
                                            nm = (p[0] or "").strip()
                                            company = (p[1] or "").strip() if len(p) > 1 else default_company
                                            to_add.append((nm, "", company))
                                        else:
                                            to_add.append((raw, "", default_company))

                                    new_list = list(results_list)
                                    for name, role, company in to_add:
                                        name = (name or "").strip()
                                        if not name:
                                            continue
                                        with st.spinner(f"Finding LinkedIn for {name}…"):
                                            new_url = get_linkedin_url_for_contact(name, company)
                                        new_list.append({"name": name, "role": role, "company": company, "linkedin_url": new_url or ""})
                                    if new_list != results_list:
                                        st.session_state[linkedin_results_key] = new_list
                                        _persist_linkedin_results(new_list)
                                        st.success(f"Added {len(to_add)} contact(s).")
                                        st.rerun()
                        st.caption("Add: name, or \"Name — Role\", or \"Name, Company\". Re-check: **recheck** or **find again** + name.")
                        st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
                        for i, r in enumerate(results_list):
                            if not _linkedin_result_matches_company(r.get("company"), comp):
                                continue
                            name = (r.get("name") or "").strip() or "—"
                            role = (r.get("role") or "").strip()
                            company_display = r.get("company") or "—"
                            url = (r.get("linkedin_url") or "").strip()
                            row_col1, row_col2, row_col3, row_col4 = st.columns([2, 0.8, 0.8, 0.4])
                            with row_col1:
                                if url:
                                    link_html = f"<a href='{url}' target='_blank' rel='noopener' style='font-size:12px;'>Profile</a>"
                                else:
                                    link_html = "<span style='font-size:12px;color:var(--text-3);'>—</span>"
                                _email = (r.get("email") or "").strip()
                                _phone = (r.get("phone") or "").strip()
                                _status = (r.get("enrichment_status") or "").strip() or "—"
                                _extra = ""
                                if _email or _phone:
                                    _extra = f"<div style='font-size:11px;color:var(--text-3);margin-top:4px;'>📧 {_email or '—'} · 📞 {_phone or '—'}</div>"
                                if _status and _status != "—":
                                    _extra += f"<div style='font-size:10px;color:var(--text-3);'>{_status}</div>"
                                st.markdown(
                                    f"<div class='c' style='padding:10px 14px;margin-bottom:6px;'>"
                                    f"<div style='font-weight:500;'>{name}</div>"
                                    f"<div style='font-size:12px;color:var(--text-2);'>{role or '—'} · {company_display}</div>"
                                    f"<div style='font-size:12px;margin-top:4px;'>{link_html}</div>{_extra}</div>",
                                    unsafe_allow_html=True)
                            with row_col2:
                                if name != "—":
                                    if st.button("Find again", key=f"relookup_{linkedin_results_key}_{i}", use_container_width=True, help="Re-run search for this person"):
                                        with st.spinner(f"Searching again for {name}…"):
                                            new_url = get_linkedin_url_for_contact(name, r.get("company") or comp.get("domain") or "")
                                        new_list = list(results_list)
                                        new_list[i] = {**new_list[i], "linkedin_url": new_url or ""}
                                        st.session_state[linkedin_results_key] = new_list
                                        _persist_linkedin_results(new_list)
                                        st.success("Updated." if new_url else "No new URL found.")
                                        st.rerun()
                            with row_col3:
                                if name != "—" and st.button("Add to company", key=f"add_contact_{linkedin_results_key}_{i}", use_container_width=True):
                                    new_contact_id = upsert_contact(cid, name, role=role or None, linkedin=url or None)
                                    if new_contact_id:
                                        added_key = "strategist_added_for_signalhire"
                                        added_list = st.session_state.get(added_key, [])
                                        added_list.append({
                                            "company_id": cid,
                                            "contact_id": new_contact_id,
                                            "name": name,
                                            "role": role or "",
                                            "linkedin_url": url or "",
                                        })
                                        st.session_state[added_key] = added_list
                                    st.success(f"Added **{name}** to company. Use the block below to get email (SignalHire) for this contact.")
                                    st.rerun()
                            with row_col4:
                                if st.button("🗑", key=f"del_contact_{linkedin_results_key}_{i}", use_container_width=True, help="Remove from list"):
                                    new_list = [x for j, x in enumerate(results_list) if j != i]
                                    st.session_state[linkedin_results_key] = new_list
                                    _persist_linkedin_results(new_list)
                                    st.success("Removed from list.")
                                    st.rerun()
                        # SignalHire: callback URL for localhost use ngrok and set SIGNALHIRE_CALLBACK_BASE_URL in keys.env
                        _callback_url = (SIGNALHIRE_CALLBACK_BASE_URL + "/api/signalhire-callback") if (SIGNALHIRE_CALLBACK_BASE_URL and SIGNALHIRE_CALLBACK_BASE_URL.strip().startswith("http")) else None
                        if st.button("📧 Get emails (SignalHire)", key="signalhire_bulk_enrich", use_container_width=True, help="Enrich all contacts with LinkedIn URLs via SignalHire; results save to the table below and persist when you come back. On localhost: run signalhire_callback_server.py and set SIGNALHIRE_CALLBACK_BASE_URL to your ngrok URL."):
                            _with_url = [(_i, _r) for _i, _r in enumerate(results_list) if (_r.get("linkedin_url") or "").strip() and _linkedin_result_matches_company(_r.get("company"), comp)]
                            if not _with_url:
                                st.warning("No contacts with LinkedIn URLs in the list.")
                            else:
                                _prog = st.progress(0, text=f"Enriching 0/{len(_with_url)}…")
                                _new_list = list(results_list)
                                for _idx, (_i, _r) in enumerate(_with_url):
                                    _prog.progress((_idx + 1) / len(_with_url), text=f"Enriching {_idx + 1}/{len(_with_url)}: {(_r.get('name') or '')[:25]}…")
                                    _url = (_r.get("linkedin_url") or "").strip()
                                    _rec_id, _req_id, _status = request_enrichment(_url, user["id"], callback_url=_callback_url)
                                    if _status == "completed" and _rec_id:
                                        try:
                                            _er = get_enrichment_result(_rec_id)
                                            if _er and _er.get("result_json"):
                                                _res = _er["result_json"]
                                                _email = (_res.get("emails") or [{}])[0].get("value", "") if _res.get("emails") else ""
                                                _phone = (_res.get("phones") or [{}])[0].get("value", "") if _res.get("phones") else ""
                                                _new_list[_i] = {**_new_list[_i], "email": _email, "phone": _phone, "enrichment_status": "completed"}
                                                _nm = (_new_list[_i].get("name") or "").strip()
                                                if _nm:
                                                    upsert_spoc_contact(cid, ctid, _nm, role=(_new_list[_i].get("role") or "").strip() or None, email=_email or None, phone=_phone or None, linkedin_url=_url or None)
                                        except Exception:
                                            pass
                                    elif _status == "pending" and _req_id:
                                        _new_list[_i] = {**_new_list[_i], "enrichment_status": "pending", "enrichment_request_id": _req_id}
                                _prog.empty()
                                st.session_state[linkedin_results_key] = _new_list
                                _persist_linkedin_results(_new_list)
                                st.success(f"Enrichment run for {len(_with_url)} contact(s). Refresh in a moment if any are pending (callback).")
                                st.rerun()
                        st.caption("On localhost: run `python signalhire_callback_server.py` and set SIGNALHIRE_CALLBACK_BASE_URL to your ngrok URL in keys.env.")

                        # ── Added to company: dedicated SignalHire block per contact ──
                        added_key = "strategist_added_for_signalhire"
                        added_list = st.session_state.get(added_key, [])
                        added_for_company = [a for a in added_list if a.get("company_id") == cid]
                        if added_for_company:
                            st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
                            st.markdown(sh_small("Added to company — get email (SignalHire) for each"), unsafe_allow_html=True)
                            _callback_url = (SIGNALHIRE_CALLBACK_BASE_URL + "/api/signalhire-callback") if (SIGNALHIRE_CALLBACK_BASE_URL and (SIGNALHIRE_CALLBACK_BASE_URL or "").strip().startswith("http")) else None
                            for _aidx, _a in enumerate(added_for_company):
                                _req_id = _a.get("enrichment_request_id")
                                if _req_id:
                                    _er = get_enrichment_by_request_id(_req_id)
                                    if _er and _er.get("status") == "completed" and _er.get("result_json"):
                                        _res = _er["result_json"]
                                        _e = (_res.get("emails") or [{}])[0].get("value", "") if _res.get("emails") else ""
                                        _p = (_res.get("phones") or [{}])[0].get("value", "") if _res.get("phones") else ""
                                        update_contact(_a["contact_id"], email=_e or None, extra_data={"enrichment": {"source": "SignalHire", "enriched_at": datetime.utcnow().isoformat()}})
                                        _new_list = list(st.session_state.get(linkedin_results_key, []))
                                        for _ri, _row in enumerate(_new_list):
                                            if (_row.get("name") or "").strip() == (_a.get("name") or "").strip():
                                                _new_list[_ri] = {**_row, "email": _e, "phone": _p, "enrichment_status": "completed"}
                                                break
                                        st.session_state[linkedin_results_key] = _new_list
                                        _persist_linkedin_results(_new_list)
                                        _new_added = [x for x in added_list if x.get("contact_id") != _a.get("contact_id")]
                                        st.session_state[added_key] = _new_added
                                        st.rerun()
                                _name_d = _a.get("name") or "—"
                                _url_d = (_a.get("linkedin_url") or "").strip()
                                _blk_col1, _blk_col2 = st.columns([3, 1])
                                with _blk_col1:
                                    st.markdown(
                                        f"<div class='c' style='padding:10px 14px;margin-bottom:6px;'>"
                                        f"<div style='font-weight:500;'>{_name_d}</div>"
                                        f"<div style='font-size:12px;color:var(--text-2);'>{_a.get('role') or '—'}</div>"
                                        f"<div style='font-size:11px;'>{'<a href=\"' + _url_d + '\" target=\"_blank\">LinkedIn</a>' if _url_d else '—'}</div></div>",
                                        unsafe_allow_html=True)
                                with _blk_col2:
                                    if _url_d:
                                        if _a.get("enrichment_request_id"):
                                            st.caption("Pending… refresh in a moment.")
                                        elif st.button("📧 Get email (SignalHire)", key=f"added_signalhire_{cid}_{_a['contact_id']}_{_aidx}", use_container_width=True, help="Fetch email/phone for this contact"):
                                            _rec_id, _req_id, _status = request_enrichment(_url_d, user["id"], callback_url=_callback_url)
                                            if _status == "completed" and _rec_id:
                                                try:
                                                    _er = get_enrichment_result(_rec_id)
                                                    if _er and _er.get("result_json"):
                                                        _res = _er["result_json"]
                                                        _e = (_res.get("emails") or [{}])[0].get("value", "") if _res.get("emails") else ""
                                                        _p = (_res.get("phones") or [{}])[0].get("value", "") if _res.get("phones") else ""
                                                        update_contact(_a["contact_id"], email=_e or None, extra_data={"enrichment": {"source": "SignalHire", "enriched_at": datetime.utcnow().isoformat()}})
                                                        _new_list = list(st.session_state.get(linkedin_results_key, []))
                                                        for _ri, _row in enumerate(_new_list):
                                                            if (_row.get("name") or "").strip() == (_a.get("name") or "").strip():
                                                                _new_list[_ri] = {**_row, "email": _e, "phone": _p, "enrichment_status": "completed"}
                                                                break
                                                        st.session_state[linkedin_results_key] = _new_list
                                                        _persist_linkedin_results(_new_list)
                                                        _new_added = [x for x in added_list if x.get("contact_id") != _a.get("contact_id")]
                                                        st.session_state[added_key] = _new_added
                                                        st.success("Email/phone updated. Contact remains in the table above.")
                                                        st.rerun()
                                                except Exception:
                                                    pass
                                            elif _status == "pending" and _req_id:
                                                _new_added = []
                                                for x in added_list:
                                                    if x.get("contact_id") == _a.get("contact_id"):
                                                        x = {**x, "enrichment_request_id": _req_id}
                                                    _new_added.append(x)
                                                st.session_state[added_key] = _new_added
                                                st.info("Enrichment requested; refresh in a moment for results.")
                                                st.rerun()
                                            else:
                                                st.warning("Enrichment failed or no result.")

                    clear_col, save_col = st.columns(2)
                    with clear_col:
                        if st.button("Clear results", key="clear_linkedin_lookup", use_container_width=True):
                            st.session_state.pop(linkedin_results_key, None)
                            _persist_linkedin_results([])
                            st.rerun()
                    with save_col:
                        if st.button("Save all to DB (under this SPOC)", key="save_all_spoc_contacts", use_container_width=True, help="Optionally save every contact in the list under the current SPOC (separate from Add to company)."):
                            for r in results_list:
                                nm = (r.get("name") or "").strip()
                                if not nm:
                                    continue
                                upsert_spoc_contact(
                                    cid, ctid, nm,
                                    role=(r.get("role") or "").strip() or None,
                                    email=(r.get("email") or "").strip() or None,
                                    linkedin_url=(r.get("linkedin_url") or "").strip() or None,
                                    phone=(r.get("phone") or "").strip() or None,
                                )
                            st.success(f"Saved {len([x for x in results_list if (x.get('name') or '').strip()])} contact(s) under this SPOC.")
                            st.rerun()

                # ── Contacts under this SPOC (from spoc_contacts table) ──
                st.markdown(sh_small("Contacts under this SPOC"), unsafe_allow_html=True)
                spoc_list = get_spoc_contacts_by_spoc(ctid)
                # Separation: Action tracking (SignalHire) remains LIVE for real-time status
                # Check pending SignalHire enrichments for saved contacts and merge if completed
                # This is NOT cached - always queries DB directly to show real-time pending/completed status
                _spoc_callback_url = (SIGNALHIRE_CALLBACK_BASE_URL + "/api/signalhire-callback") if (SIGNALHIRE_CALLBACK_BASE_URL and SIGNALHIRE_CALLBACK_BASE_URL.strip().startswith("http")) else None
                _spoc_updated = False
                for _s in spoc_list:
                    _ed = _s.get("extra_data") or {}
                    _req_id = _ed.get("enrichment_request_id") if isinstance(_ed, dict) else None
                    if _req_id:
                        _er = get_enrichment_by_request_id(_req_id)  # LIVE query
                        if _er and _er.get("status") == "completed" and _er.get("result_json"):
                            _res = _er["result_json"]
                            _email = (_res.get("emails") or [{}])[0].get("value", "") if _res.get("emails") else ""
                            _phone = (_res.get("phones") or [{}])[0].get("value", "") if _res.get("phones") else ""
                            _new_extra = {k: v for k, v in (_ed or {}).items() if k != "enrichment_request_id"}
                            _new_extra["enrichment_request_id"] = None
                            upsert_spoc_contact(cid, ctid, _s.get("name") or "", role=_s.get("role"), email=_email or None, phone=_phone or None, linkedin_url=_s.get("linkedin_url"), extra_data=_new_extra)
                            _spoc_updated = True
                if _spoc_updated:
                    st.rerun()
                if not spoc_list:
                    st.caption("No contacts saved under this SPOC yet. Use «Add to company» or «Save all to DB (under this SPOC)» from the list above.")
                else:
                    for _idx, s in enumerate(spoc_list):
                        _name = s.get("name") or "—"
                        _role = s.get("role") or "—"
                        _email = s.get("email") or "—"
                        _linkedin = (s.get("linkedin_url") or "").strip()
                        _phone = s.get("phone") or "—"
                        _col1, _col2 = st.columns([4, 1])
                        with _col1:
                            st.markdown(
                                f"<div class='c' style='padding:10px 14px;margin-bottom:6px;'>"
                                f"<div style='font-weight:500;'>{_name}</div>"
                                f"<div style='font-size:12px;color:var(--text-2);'>{_role} · 📧 {_email} · 📞 {_phone}</div>"
                                f"<div style='font-size:11px;'>{'<a href=\"' + _linkedin + '\" target=\"_blank\">LinkedIn</a>' if _linkedin else '—'}</div></div>",
                                unsafe_allow_html=True)
                        with _col2:
                            if _linkedin and st.button("Get email (SignalHire)", key=f"spoc_signalhire_{ctid}_{_idx}_{s.get('id')}", use_container_width=True, help="Fetch email/phone via SignalHire for this saved contact"):
                                _sname = (s.get("name") or "").strip()
                                if not _sname:
                                    st.warning("Contact has no name.")
                                else:
                                    _rec_id, _req_id, _status = request_enrichment(_linkedin, user["id"], callback_url=_spoc_callback_url)
                                    if _status == "completed" and _rec_id:
                                        try:
                                            _er = get_enrichment_result(_rec_id)
                                            if _er and _er.get("result_json"):
                                                _res = _er["result_json"]
                                                _e = (_res.get("emails") or [{}])[0].get("value", "") if _res.get("emails") else ""
                                                _p = (_res.get("phones") or [{}])[0].get("value", "") if _res.get("phones") else ""
                                                upsert_spoc_contact(cid, ctid, _sname, role=(s.get("role") or "").strip() or None, email=_e or None, phone=_p or None, linkedin_url=_linkedin or None)
                                                st.success("Email/phone updated.")
                                                st.rerun()
                                        except Exception:
                                            pass
                                    elif _status == "pending" and _req_id:
                                        upsert_spoc_contact(cid, ctid, _sname, role=(s.get("role") or "").strip() or None, email=s.get("email"), phone=s.get("phone"), linkedin_url=_linkedin or None, extra_data={"enrichment_request_id": _req_id})
                                        st.info("Enrichment requested; refresh in a moment for results.")
                                        st.rerun()
                                    else:
                                        st.warning("Enrichment failed or no result.")
                    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

                run_enrich = False  # Single-contact enrichment UI removed; credits only shown
                lookup_identifier = ""
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
                                upsert_spoc_contact(
                                    add_cid, ctid, add_name.strip(),
                                    role=res.get("headline") or None,
                                    email=primary_email or None,
                                    linkedin_url=res.get("linkedin") or None,
                                    phone=primary_phone or None,
                                )
                                st.success(f"Contact “{add_name.strip()}” added to {get_company(add_cid).get('domain','company')} (and under this SPOC).")
                                st.session_state.pop(enrich_result_key, None)
                                st.rerun()

                # ── Recipient Research ──
                st.markdown("---")
                st.markdown(sh_small("Recipient research"), unsafe_allow_html=True)
                research_key = f"recipient_research_{ctid}"
                research = None
                try:
                    from recipient_research import research_recipient, format_time_ago
                    
                    # Initialize or load research
                    if research_key not in st.session_state:
                        # Check if we should auto-load research
                        if db_email:  # Only research if recipient has email
                            with st.spinner("Researching recipient..."):
                                st.session_state[research_key] = research_recipient(ctid)
                        else:
                            st.session_state[research_key] = None
                    
                    research = st.session_state.get(research_key)
                except Exception as _e:
                    research = None
                    if f"recipient_research_{ctid}" in st.session_state:
                        st.session_state.pop(f"recipient_research_{ctid}", None)
                    st.caption("Recipient research unavailable. You can still use the strategist below.")
                
                if research and research.get("status") == "completed":
                    recipient_name = ct_ctx.get("name", "Contact")
                    time_ago = format_time_ago(research)
                    
                    with st.expander(
                        f"📊 Recipient Brief: {recipient_name} · Updated {time_ago}",
                        expanded=False
                    ):
                        # Brief summary
                        brief = research.get("brief", "")
                        if brief:
                            st.markdown(
                                f"<div style='background:var(--accent-bg);padding:12px;border-radius:6px;margin-bottom:12px;'>"
                                f"<strong>Quick Context:</strong> {brief}</div>",
                                unsafe_allow_html=True
                            )
                        
                        # LinkedIn: show actual recent posts first, then themes
                        linkedin_data = research.get("linkedin", {})
                        recent_posts = linkedin_data.get("recent_posts") or []
                        if recent_posts:
                            st.markdown("**Recent LinkedIn (specific):**")
                            for p in recent_posts[:5]:
                                snip = (p.get("snippet") or "").strip()
                                date = (p.get("date_if_known") or "").strip()
                                if snip:
                                    st.markdown(f"• {snip}" + (f" _{date}_" if date else ""))
                        if linkedin_data and (linkedin_data.get("recent_activity") or linkedin_data.get("professional_focus")):
                            if not recent_posts:
                                st.markdown("**LinkedIn themes:**")
                            for topic in linkedin_data.get("recent_activity", [])[:3]:
                                if topic:
                                    st.markdown(f"• {topic}")
                            prof_focus = (linkedin_data.get("professional_focus") or "").strip()
                            if prof_focus:
                                st.caption(f"_{prof_focus}_")
                        
                        # Twitter: show actual recent tweets first, then handle/topics
                        twitter_data = research.get("twitter", {})
                        recent_tweets = twitter_data.get("recent_tweets") or []
                        if recent_tweets:
                            handle = (twitter_data.get("handle") or "").strip()
                            st.markdown(f"**Twitter{' (' + handle + ')' if handle else ''} (specific):**")
                            for t in recent_tweets[:5]:
                                snip = (t.get("snippet") or "").strip()
                                date = (t.get("date_if_known") or "").strip()
                                if snip:
                                    st.markdown(f"• {snip}" + (f" _{date}_" if date else ""))
                        elif twitter_data and (twitter_data.get("handle") or twitter_data.get("topics")):
                            if twitter_data.get("handle") and twitter_data.get("topics"):
                                st.markdown(f"**Twitter ({twitter_data.get('handle')}):**")
                                st.markdown(f"Discusses: {', '.join(twitter_data.get('topics', [])[:3])}")
                        
                        # Blog posts (only when we have real titles, not generic)
                        blog_posts = research.get("blog_posts", [])
                        if blog_posts:
                            st.markdown("**Recent content:**")
                            for post in blog_posts[:3]:
                                title = (post.get("title") or "").strip()
                                topic = (post.get("topic") or "").strip()
                                url = post.get("url")
                                if title and "various" not in title.lower() and "editorial" not in title.lower():
                                    if url:
                                        st.markdown(f"• [{title}]({url})" + (f" · {topic}" if topic else ""))
                                    else:
                                        st.markdown(f"• {title}" + (f" · {topic}" if topic else ""))
                        
                        # Refresh button
                        if st.button("🔄 Refresh Research", key="refresh_recipient_research"):
                            with st.spinner("Researching recipient..."):
                                st.session_state[research_key] = research_recipient(ctid, force_refresh=True)
                                st.rerun()
                elif db_email:
                    # Show manual trigger if no research yet
                    st.caption("💡 Recipient research helps personalize your outreach")
                    if st.button("🔍 Research Recipient", key="trigger_recipient_research"):
                        with st.spinner("Researching recipient..."):
                            st.session_state[research_key] = research_recipient(ctid, force_refresh=True)
                            st.rerun()
                else:
                    st.caption("Select a contact with an email to enable recipient research.")

                # ── Quick actions + Sequence builder ──
                st.markdown("---")
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
                        if _is_placeholder_contact(ct_ctx, comp):
                            st.warning("Select a specific SPOC with a real name and LinkedIn. Current contact looks like a placeholder (e.g. domain as name).")
                        else:
                            with st.spinner("Building outreach plan..."):
                                rn = st.session_state.research_cache.get(research_cache_key, [])
                                recipient_research_data = st.session_state.get(f"recipient_research_{ctid}")
                                plan = generate_outreach_plan(comp, ct_ctx, user["id"], sid, sender_name, rn, sender_email=sender_email, product_key=recommended_product_key, recipient_research=recipient_research_data)
                                if plan:
                                    st.session_state.pending_plan = plan

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
                                    ct_ctx.get("email", ""), subj, bod,
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
                    if _is_placeholder_contact(ct_ctx, comp):
                        with st.chat_message("user"):
                            st.markdown(prompt)
                        save_message(sid, "user", prompt)
                        st.session_state.chat_messages.append({"role": "user", "content": prompt, "created_at": datetime.utcnow().isoformat()})
                        with st.chat_message("assistant"):
                            st.warning("Select a specific SPOC with a real name and LinkedIn. Current contact looks like a placeholder (e.g. domain as name). Drafts and plans are only generated for real contacts.")
                    else:
                        with st.chat_message("user"):
                            st.markdown(prompt)
                        save_message(sid, "user", prompt)
                        st.session_state.chat_messages.append({"role": "user", "content": prompt, "created_at": datetime.utcnow().isoformat()})
                        rn = st.session_state.research_cache.get(research_cache_key, [])
                        recipient_research_data = st.session_state.get(f"recipient_research_{ctid}")
                        with st.chat_message("assistant"):
                            chunks = []
                            ph = st.empty()
                            for chunk in get_strategist_response_stream(comp, ct_ctx, st.session_state.chat_messages, prompt, user["id"], sid, rn, sender_email=sender_email, product_key=recommended_product_key, recipient_research=recipient_research_data):
                                chunks.append(chunk)
                                ph.markdown("".join(chunks) + "▌")
                            response = "".join(chunks)
                            ph.markdown(response)
                        save_message(sid, "assistant", response)
                        st.session_state.chat_messages.append({"role": "assistant", "content": response, "created_at": datetime.utcnow().isoformat()})

                        # If user asked to edit sequence drafts, apply targeted rewrite directly to stored steps.
                        if _is_sequence_edit_request(prompt):
                            seqs_now = get_active_sequences(sid)
                            seq_to_edit = _latest_sequence_with_drafts(seqs_now)
                            if seq_to_edit:
                                target_steps = _resolve_instruction_targets(prompt, seq_to_edit.get("steps") or [])
                                if target_steps:
                                    rewrites = rewrite_sequence_steps_with_research(
                                        comp,
                                        ct_ctx,
                                        user["id"],
                                        sid,
                                        target_steps,
                                        prompt,
                                        research_notes=rn,
                                        sender_email=sender_email,
                                        product_key=recommended_product_key,
                                        recipient_research=recipient_research_data,
                                    )
                                    applied = 0
                                    for rw in rewrites:
                                        rid = rw.get("id")
                                        new_text = (rw.get("revised_content") or "").strip()
                                        if not rid or not new_text:
                                            continue
                                        update_action(
                                            rid,
                                            content_sent=new_text,
                                            draft_revisions=[],
                                            draft_original="",
                                        )
                                        st.session_state.pop(f"draft_revisions_list_{rid}", None)
                                        st.session_state[f"draft_original_{rid}"] = new_text
                                        st.session_state.pop(f"refine_version_sel_{rid}", None)
                                        applied += 1
                                    if applied:
                                        st.success(f"Updated {applied} draft step(s) from strategist instruction.")
                                elif re.search(r"\b(email|linkedin|step)\s*\d+\b", (prompt or "").lower()):
                                    st.warning("I couldn't map that target to an existing draft step in the active sequence.")
                        maybe_update_summary(sid, comp, ct_ctx)
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
    if not cid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);'>Select a company first.</div>", unsafe_allow_html=True)
    else:
        if not sid:
            sid = get_or_create_session(cid, ctid, user["id"])
            st.session_state.session_id = sid
        if _src_a == "Media Houses" and _aid_a:
            adv_a = get_advisor_company(_aid_a) or {}
            comp = {"domain": adv_a.get("domain", "—"), "name": adv_a.get("domain") or "—", "extra_data": adv_a.get("extra_data") or {}}
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
                            rn = st.session_state.research_cache.get(research_cache_key, [])
                            advice = replan_on_response(
                                comp, ct, user["id"], sid, trigger_action,
                                trigger_action.get("response_content", ""), remaining, rn)
                            if advice:
                                st.markdown(sh("AI REPLAN RECOMMENDATION"), unsafe_allow_html=True)
                                st.markdown(
                                    f"<div class='brief-card'><div style='font-size:13px;white-space:pre-wrap;'>{advice}</div></div>",
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
    if not cid:
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
    tl = get_timeline_actions(limit=2000)
    _cal_src = st.session_state.get("notion_companies_source", "Independent")
    if _cal_src == "Media Houses":
        tl = [a for a in tl if a.get("source_advisor_company_id")]
    else:
        tl = [a for a in tl if not a.get("source_advisor_company_id")]

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
    _src_p = st.session_state.get("notion_companies_source", "Independent")
    if _src_p == "Media Houses":
        # Media Houses pipeline: expandable parent cards; each child has status change
        st.markdown(lh2_section_header("MEDIA HOUSES PIPELINE"), unsafe_allow_html=True)
        parents_with_children = get_advisor_parents_with_children()
        # Enrich each child once (shadow, spoc, score, status) and count by status for KPI
        pipeline_advisor = {s: [] for s in CONTACT_STATUSES}
        groups_enriched = []
        for group in parents_with_children:
            parent_name = group["parent_name"]
            display_parent = (parent_name or "(No parent)").strip()
            enriched = []
            for ch in group["children"]:
                status = normalize_contact_status((ch.get("extra_data") or {}).get("pipeline_status", "not_contacted"))
                shadow_cid, shadow_ctid = get_or_create_shadow_company_for_advisor(ch["id"])
                spoc_name = "—"
                score = 0
                if shadow_ctid:
                    ct = get_contact(shadow_ctid)
                    if ct:
                        spoc_name = ct.get("name") or "Leadership"
                        comp_for_score = {"domain": ch.get("domain"), "monthly_visits": None}
                        score = compute_lead_score(comp_for_score, ct)
                if status in pipeline_advisor:
                    pipeline_advisor[status].append(1)
                enriched.append({"child": ch, "spoc_name": spoc_name, "score": score, "status": status})
            groups_enriched.append({"parent_name": display_parent, "children": enriched})
        kpi_pipeline = [(len(pipeline_advisor[s]), CONTACT_STATUS_LABELS_STRIP.get(s, s)) for s in CONTACT_STATUSES]
        st.markdown(
            "<div class='lh2-kpi-strip'>" + "".join(lh2_kpi_cell(n, lbl) for n, lbl in kpi_pipeline) + "</div>",
            unsafe_allow_html=True)
        # Expandable parent cards (collapsed by default); each child row with status
        for grp in groups_enriched:
            parent_name = grp["parent_name"]
            children = grp["children"]
            if not children:
                continue
            with st.expander(f"**{parent_name}** — {len(children)} companies", expanded=False):
                for item in children:
                    ch = item["child"]
                    spoc_name = item["spoc_name"]
                    score = item["score"]
                    status = item["status"]
                    domain_line = ch.get("domain", "—") + (" · " + parent_name if parent_name != "(No parent)" else "")
                    si = "🟢" if score >= 60 else "🟡" if score >= 35 else "🔴"
                    st.markdown(lh2_pipeline_row(spoc_name, domain_line, f"{si} {score}/100"), unsafe_allow_html=True)
                    new_status = st.selectbox(
                        "Status",
                        CONTACT_STATUSES,
                        index=CONTACT_STATUSES.index(status) if status in CONTACT_STATUSES else 0,
                        format_func=lambda x: CONTACT_STATUS_LABELS.get(x, x),
                        key=f"pipeline_advisor_{ch['id']}",
                    )
                    if new_status != status:
                        merge_advisor_company_extra_data(ch["id"], {"pipeline_status": new_status})
                        st.rerun()
    else:
        # Independent pipeline (unchanged)
        st.markdown(lh2_section_header("CONTACT PIPELINE"), unsafe_allow_html=True)
        all_comp = get_all_companies()
        pipeline = {s: [] for s in CONTACT_STATUSES}
        for cd in all_comp:
            for ctd in get_contacts(cd["id"]):
                status = normalize_contact_status(ctd.get("status", "not_contacted"))
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


# ════════════════════ SPOC TRACKING ════════════════════

def render_spoc_row(row, today):
    """
    Pure UI component: renders a single SPOC tracking row.
    
    Architecture: No database calls, only st.* rendering components.
    All data must be pre-loaded and passed via the row parameter.
    
    Args:
        row: Dict with company, contact, session, actions, etc.
        today: datetime.date object for comparison
    """
    cd, ctd, sess, actions = row["company"], row["contact"], row["session"], row["actions"]
    _norm = normalize_contact_status(ctd.get("status", "not_contacted"))
    company_display = (cd.get("name") or cd.get("domain") or "—").strip() or "—"
    ps = row.get("primary_spoc")
    subs = row.get("subs") or []
    
    # Collect any emails/phones from all saved SPOC contacts under this person
    emails_from_subs = [(s.get("email") or "").strip() for s in subs if (s.get("email") or "").strip()]
    phones_from_subs = [(s.get("phone") or "").strip() for s in subs if (s.get("phone") or "").strip()]
    
    if ps:
        name_display = (ps.get("name") or "—").strip() or "—"
        role_display = (ps.get("role") or "—").strip() or "—"
        linkedin_val = (ps.get("linkedin_url") or "").strip()
        email_display = (
            (ps.get("email") or "").strip()
            or (emails_from_subs[0] if emails_from_subs else "")
            or (ctd.get("email") or "").strip()
            or "—"
        )
        phone_display = (
            (ps.get("phone") or "").strip()
            or (phones_from_subs[0] if phones_from_subs else "")
            or ((ctd.get("extra_data") or {}).get("phone") or "").strip()
            or "—"
        )
        phone_display = phone_display or "—"
    else:
        name_display = (ctd.get("name") or "—").strip() or "—"
        role_display = (ctd.get("role") or "—").strip() or "—"
        linkedin_val = (ctd.get("linkedin") or "").strip()
        email_display = (ctd.get("email") or "—").strip() or "—"
        phone_display = ((ctd.get("extra_data") or {}).get("phone") or "—")
        if isinstance(phone_display, str):
            phone_display = phone_display.strip() or "—"
        else:
            phone_display = "—"
    
    linkedin_html = f"<a href='{html_module.escape(linkedin_val)}' target='_blank'>Profile</a>" if linkedin_val and linkedin_val.startswith("http") else "—"
    next_step_display = row["next_step_date"]
    open_key = f"spoc_open_{ctd['id']}"
    is_open = st.session_state.get(open_key, False)

    # ── Card row: expand button | company+name+role+contacts | status | next step | delete ──
    with st.container():
        c_btn, c_info, c_status, c_next, c_del = st.columns([0.4, 4.0, 1.0, 1.0, 0.4])
        with c_btn:
            if st.button("▾" if is_open else "▸", key=f"spoc_toggle_{ctd['id']}", help="Show/hide details"):
                st.session_state[open_key] = not is_open
                st.rerun()
        with c_info:
            st.markdown(
                f"<div class='spoc-card'>"
                f"<div class='spoc-card-company'>{html_module.escape(company_display)}</div>"
                f"<div class='spoc-card-name'>{html_module.escape(name_display)}"
                f" <span class='spoc-card-role'>· {html_module.escape(role_display)}</span></div>"
                f"<div class='spoc-card-contacts'>"
                f"📧 {html_module.escape(email_display)}"
                f" &nbsp;·&nbsp; 🔗 {linkedin_html}"
                f" &nbsp;·&nbsp; 📞 {html_module.escape(phone_display)}"
                f"</div></div>",
                unsafe_allow_html=True)
        with c_status:
            current_index = CONTACT_STATUSES.index(_norm) if _norm in CONTACT_STATUSES else 0
            new_status = st.selectbox(
                "Status",
                CONTACT_STATUSES,
                index=current_index,
                format_func=lambda x: CONTACT_STATUS_LABELS.get(x, x),
                key=f"spoc_status_{ctd['id']}",
                label_visibility="collapsed",
            )
            if new_status != _norm:
                # Surgical cache invalidation
                from services.cache_registry import invalidate_multiple
                update_contact_status(ctd["id"], new_status)
                invalidate_multiple("contact", "pipeline")  # Status affects both
                st.rerun()
        with c_next:
            if row.get("first_overdue_date_str"):
                st.markdown(f"<div class='spoc-card-overdue'>⚠ Overdue ({html_module.escape(row['first_overdue_date_str'])})</div>", unsafe_allow_html=True)
                if row.get("next_step_date") and row["next_step_date"] != "—":
                    st.caption(f"Next: {row['next_step_date']}")
            else:
                st.markdown(f"<div class='spoc-card-next'>{html_module.escape(next_step_display)}</div>", unsafe_allow_html=True)
        with c_del:
            if ps and st.button("🗑️", key=f"spoc_del_{ps.get('id')}_{ctd['id']}", help="Delete this SPOC", use_container_width=True):
                from services.cache_registry import invalidate_group
                if delete_spoc_contact(ps.get("id")):
                    invalidate_group("spoc")  # Only clear SPOC caches
                    st.success("✓")
                    time.sleep(0.5)
                    st.rerun()

    # ── Expanded: action history only (contact info already visible above) ──
    if is_open:
        st.markdown("<div class='spoc-detail'>", unsafe_allow_html=True)
        st.markdown(f"*Contacts under this SPOC: {row['sub_count']}*")
        completed = [a for a in actions if a.get("status") and (a.get("status") or "").strip().lower() not in ("", "planned")]
        planned = row.get("planned") or []
        st.markdown("**What happened**")
        if completed:
            for a in completed[:20]:
                done_at = a.get("completed_at") or a.get("created_at") or "—"
                if isinstance(done_at, str) and len(done_at) > 10:
                    done_at = done_at[:10]
                desc = (a.get("description") or "").strip() or "(no description)"
                content = (a.get("content_sent") or "").strip()
                if content:
                    content_preview = content[:120] + "…" if len(content) > 120 else content
                    st.markdown(f"- {done_at} · {desc}\n  `{content_preview}`")
                else:
                    st.markdown(f"- {done_at} · {desc}")
        else:
            st.markdown("*No completed actions yet.*")
        st.markdown("**What's planned**")
        if planned:
            for a in planned[:20]:
                sch = (a.get("scheduled_at") or "")[:10] if a.get("scheduled_at") else "—"
                desc = (a.get("description") or "").strip() or "(no description)"
                st.markdown(f"- {sch} · {desc}")
        else:
            st.markdown("*No planned actions.*")
        # Reschedule first overdue and cascade
        if row.get("first_overdue_action") and row.get("planned"):
            from datetime import datetime
            st.markdown("**Reschedule first overdue**")
            _resched_key = f"spoc_rd_{ctd['id']}"
            _default_d = today
            try:
                _fo = row["first_overdue_action"]
                _fo_dt = datetime.fromisoformat((_fo.get("scheduled_at") or "").replace("Z", "+00:00"))
                _default_d = _fo_dt.date()
                if _default_d < today:
                    _default_d = today
            except Exception:
                pass
            _new_date = st.date_input("New date", value=_default_d, key=_resched_key, label_visibility="collapsed")
            if st.button("Apply reschedule", key=f"spoc_ra_{ctd['id']}"):
                _fo = row["first_overdue_action"]
                _planned_sorted = sorted(row["planned"], key=lambda x: (x.get("scheduled_at") or ""))
                try:
                    _old_first = datetime.fromisoformat(_fo["scheduled_at"].replace("Z", "+00:00"))
                    _new_first = _old_first.replace(year=_new_date.year, month=_new_date.month, day=_new_date.day)
                    _offset = _new_first - _old_first
                    for _a in _planned_sorted:
                        _s = _a.get("scheduled_at")
                        if not _s:
                            continue
                        _dt = datetime.fromisoformat(_s.replace("Z", "+00:00"))
                        if _dt < _old_first:
                            continue
                        if _a["id"] == _fo["id"]:
                            update_action(_a["id"], scheduled_at=_new_first.isoformat())
                        else:
                            update_action(_a["id"], scheduled_at=(_dt + _offset).isoformat())
                    from services.cache_registry import invalidate_group
                    invalidate_group("action")  # Only clear action caches
                    st.toast("Rescheduled; later steps shifted.")
                except Exception as e:
                    st.error(f"Reschedule failed: {e}")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


with tabs[8]:  # SPOC TRACKING
    st.markdown(lh2_section_header("SPOC TRACKING"), unsafe_allow_html=True)
    st.caption("SPOCs added to company: pipeline status, messages sent, and next step.")
    
    # ── DATA HOISTING: Load all data at top via caching layer ──
    from datetime import datetime, timedelta
    from services.ui_data_loader import load_all_companies
    
    _today = datetime.utcnow().date()
    all_comp_spoc = load_all_companies()  # Use cached loader
    
    # Build SPOC rows with all necessary data
    spoc_rows = []
    for cd in all_comp_spoc:
        for ctd in get_contacts(cd["id"]):
            sub = get_spoc_contacts_by_spoc(ctd["id"])
            if not sub:
                continue
            sessions = get_sessions_for_company(cd["id"])
            sess = next((s for s in sessions if s.get("contact_id") == ctd["id"]), None)
            actions = get_session_actions(sess["id"]) if sess else []
            planned = [a for a in actions if (a.get("status") or "").strip().lower() == "planned" and a.get("scheduled_at")]
            next_dates = []
            planned_past = []
            for a in planned:
                try:
                    dt = datetime.fromisoformat(a["scheduled_at"].replace("Z", "+00:00"))
                    if dt.date() >= _today:
                        next_dates.append(dt)
                    else:
                        planned_past.append((a, dt))
                except Exception:
                    pass
            planned_past.sort(key=lambda x: x[1])
            first_overdue_action = planned_past[0][0] if planned_past else None
            first_overdue_date_str = planned_past[0][1].strftime("%b %d, %Y") if planned_past else None
            next_step_date = min(next_dates).strftime("%b %d, %Y") if next_dates else "—"
            primary_spoc = sub[0] if sub else None
            
            # Filter: skip rows with no real SPOC data
            if primary_spoc:
                _has_real = (primary_spoc.get("name") or "").strip() or (primary_spoc.get("email") or "").strip() or (primary_spoc.get("linkedin_url") or "").strip()
                if not _has_real:
                    continue
            else:
                _ct_name = (ctd.get("name") or "").strip()
                _ct_email = (ctd.get("email") or "").strip()
                _ct_li = (ctd.get("linkedin") or "").strip()
                _ct_domain = (cd.get("domain") or "").strip().lower()
                _name_is_placeholder = (not _ct_name) or (_ct_name.lower() == _ct_domain) or (_ct_name.lower().replace("www.", "") == _ct_domain.replace("www.", ""))
                if _name_is_placeholder and not _ct_email and not _ct_li:
                    continue
            
            company_display = (cd.get("name") or cd.get("domain") or "—").strip() or "—"
            if primary_spoc:
                _nm = (primary_spoc.get("name") or "—").strip() or "—"
                _role = (primary_spoc.get("role") or "—").strip() or "—"
                _em = (primary_spoc.get("email") or "—").strip() or "—"
                _ph = (primary_spoc.get("phone") or "—")
                _ph = _ph.strip() if isinstance(_ph, str) else "—"
            else:
                _nm = (ctd.get("name") or "—").strip() or "—"
                _role = (ctd.get("role") or "—").strip() or "—"
                _em = (ctd.get("email") or "—").strip() or "—"
                _ph = ((ctd.get("extra_data") or {}).get("phone") or "—")
                _ph = _ph.strip() if isinstance(_ph, str) else "—"
            _norm = normalize_contact_status(ctd.get("status", "not_contacted"))
            _status_lbl = CONTACT_STATUS_LABELS.get(_norm, _norm)
            _search_text = " ".join([company_display, _nm, _role, _em, _ph, _status_lbl, next_step_date, first_overdue_date_str or ""]).lower()
            
            spoc_rows.append({
                "company": cd,
                "contact": ctd,
                "session": sess,
                "actions": actions,
                "planned": planned,
                "next_step_date": next_step_date,
                "sub_count": len(sub),
                "primary_spoc": primary_spoc,
                "subs": sub,
                "first_overdue_action": first_overdue_action,
                "first_overdue_date_str": first_overdue_date_str,
                "search_text": _search_text,
            })
    
    # ── RENDER UI ──
    if not spoc_rows:
        st.markdown("<div style='text-align:center;padding:32px;color:var(--text-3);'>No contacts yet. Add contacts to companies (e.g. from Strategist) to see them here.</div>", unsafe_allow_html=True)
    else:
        spoc_search = st.text_input(
            "Search",
            placeholder="Company, name, role, email, phone, status…",
            key="spoc_tracking_search",
            label_visibility="collapsed",
        )
        search_q = (spoc_search or "").strip().lower()
        if search_q:
            spoc_rows = [r for r in spoc_rows if search_q in r.get("search_text", "")]
        if search_q and not spoc_rows:
            st.info("No matches for your search.")
        elif spoc_rows:
            # COMPONENTIZED RENDERING: Pure UI, no DB calls in loop
            for row in spoc_rows:
                render_spoc_row(row, _today)

# ════════════════════ HISTORY ════════════════════

with tabs[9]:  # HISTORY
    # === UI REFACTOR START ===
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
                unsafe_allow_html=True)

    all_ua = get_all_actions(limit=200)
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
    sessions = get_all_sessions()
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

with tabs[10]:  # ADVISOR
    # Header with history button
    h1, h2 = st.columns([3, 1])
    with h1:
        st.markdown(sh("ADVISOR"), unsafe_allow_html=True)
        st.caption("Ask for company suggestions, outreach plans, or advice. I remember our conversation and your pipeline.")
    with h2:
        past_convs = get_all_advisor_conversations(limit=20)
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
            st.markdown(reply)
        st.session_state.advisor_messages.append({"role": "assistant", "content": reply})
        learn_user_preference(user["id"], prompt, reply, source="advisor_chat")
        
        # Auto-save conversation after each exchange
        save_advisor_conversation(user["id"], st.session_state.advisor_messages)
        st.rerun()

# ════════════════════ BULK IMPORT ════════════════════
with tabs[11]:  # BULK IMPORT
    import pandas as pd
    from services.bulk_domain_import import (
        analyze_from_file, analyze_from_text, _parse_entries_from_text,
        commit_rows, undo_created, get_existing_parent_media_houses,
    )
    st.markdown(sh("BULK DOMAIN IMPORT"), unsafe_allow_html=True)
    st.caption("Step 1: Analyze (no writes). Step 2: Review/edit classifications and confirm the correct parent media house. Step 3: Commit. Media-house rows will NOT be written unless you explicitly pick an existing parent.")

    uploaded = st.file_uploader("Upload CSV or XLSX", type=["csv", "xlsx", "xls"], key="bulk_import_file")
    manual_text = st.text_area(
        "Or enter publishing house name(s) or domain(s)",
        placeholder="e.g. TechCrunch, The Verge, example.com (one per line or comma-separated)",
        key="bulk_import_manual",
        height=120,
    )

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        analyze_file = uploaded and st.button("Analyze file", key="bulk_import_analyze_file")
    with c2:
        analyze_text = bool((manual_text or "").strip()) and st.button("Analyze entered names/domains", key="bulk_import_analyze_text")
    with c3:
        if st.session_state.get("bulk_import_last_created"):
            if st.button("Undo last commit", key="bulk_import_undo"):
                with st.spinner("Undoing last commit…"):
                    res = undo_created(st.session_state.get("bulk_import_last_created") or [])
                st.success(f"Undone: {res.get('undone', 0)} item(s)")
                for e in res.get("errors") or []:
                    st.error(e)
                st.session_state.bulk_import_last_created = []

    if analyze_file:
        with st.spinner("Analyzing file (no writes)…"):
            preview = analyze_from_file(uploaded)
        st.session_state.bulk_import_preview = preview

    if analyze_text:
        with st.spinner("Analyzing entries (no writes)…"):
            preview = analyze_from_text(manual_text)
        st.session_state.bulk_import_preview = preview

    preview = st.session_state.get("bulk_import_preview") or {}
    rows = preview.get("rows") or []
    existing_parents = preview.get("existing_parents") or get_existing_parent_media_houses()
    if preview.get("errors"):
        for e in preview["errors"]:
            st.error(e)

    if rows:
        st.markdown("### Review before commit")
        st.caption("Edit `classification` and (for `media_house`) choose `parent_confirmed`. If you’re unsure, leave parent_confirmed blank and it will be flagged for review (no write).")

        df = pd.DataFrame(rows)
        parent_options = [""] + list(existing_parents)
        edited = st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "do_import": st.column_config.CheckboxColumn("Import", help="Uncheck to skip"),
                "input": st.column_config.TextColumn("Input"),
                "domain": st.column_config.TextColumn("Domain"),
                "exists": st.column_config.CheckboxColumn("Exists", disabled=True),
                "classification": st.column_config.SelectboxColumn("Classification", options=["independent", "media_house"]),
                "parent_name": st.column_config.TextColumn("AI parent (suggested)", disabled=True),
                "parent_suggested": st.column_config.TextColumn("DB match (suggested)", disabled=True, help="Suggested from your existing Media House parent names (no hallucination)"),
                "match_reason": st.column_config.TextColumn("Match reason", disabled=True),
                "parent_confirmed": st.column_config.SelectboxColumn("Parent (confirmed)", options=parent_options),
                "status": st.column_config.TextColumn("Status", disabled=True),
            },
        )

        if st.button("Commit reviewed rows", key="bulk_import_commit"):
            with st.spinner("Committing selected rows…"):
                summary = commit_rows(edited.to_dict(orient="records"))
            st.session_state.bulk_import_last_created = summary.get("created") or []

            added_i = summary.get("added_independent") or []
            added_m = summary.get("added_media_house") or []
            flagged_exists = summary.get("flagged_already_exists") or []
            flagged_review = summary.get("flagged_for_review") or []
            errors = summary.get("errors") or []

            if added_i:
                st.success(f"Added {len(added_i)} independent: " + ", ".join(added_i[:10]) + (" …" if len(added_i) > 10 else ""))
            if added_m:
                st.success(f"Added {len(added_m)} media house (child): " + ", ".join(added_m[:10]) + (" …" if len(added_m) > 10 else ""))
            if flagged_exists:
                st.warning(f"Already exists ({len(flagged_exists)}): " + ", ".join(flagged_exists[:10]) + (" …" if len(flagged_exists) > 10 else ""))
            if flagged_review:
                st.info(f"Flagged for review / not committed ({len(flagged_review)}): " + ", ".join(flagged_review[:10]) + (" …" if len(flagged_review) > 10 else ""))
            for e in errors:
                st.error(e)