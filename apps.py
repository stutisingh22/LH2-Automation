"""
app.py — LH2 Command Center v3

Features:
  - Sidebar: conversation history
  - From/To selectors: who is sending, who is receiving
  - Sequence builder: one-click full outreach plan
  - Adaptive replanning: AI tweaks plan when responses arrive
  - Streaming chat with proper styling
"""

import re
import streamlit as st
from datetime import datetime

st.set_page_config(
    page_title="LH2 Command Center", page_icon="◆",
    layout="wide", initial_sidebar_state="expanded",
)

from config import (
    CONTACT_STATUSES, CONTACT_STATUS_LABELS, CONTACT_STATUS_COLORS,
    ACTION_TYPES, ACTION_CHANNELS, ACTION_STATUSES, ACTION_STATUS_COLORS,
    GOOGLE_SERVICE_ACCOUNT_JSON,
)
from db_ops import (
    create_user, authenticate, get_all_users,
    get_company, get_all_companies, search_companies,
    upsert_company, upsert_contact,
    get_contacts, get_contact, update_contact_status,
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
)
from notion_sync import sync_notion_to_db, lookup_lead_in_notion, fetch_notes_for_page, create_notion_contact_page
from drive_sync import list_drive_files
from ai_engine import (
    compute_lead_score, get_local_time,
    get_strategist_response_stream, auto_brief,
    generate_outreach_plan, replan_on_response,
    maybe_update_summary, extract_episodic_memories,
    generate_insight_from_outcome, parse_ai_actions, export_session_csv,
    intelligence_request, research_person_for_company, summarize_research_to_context,
    advisor_chat,
)
from research import fetch_research_from_notion, run_research, write_research_to_notion
from ui_helpers import inject_css, bdg, crd, mcard, sh, sh_small, tl_item, alert, fmt_v, has_perm

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

def render_auth():
    if "user" not in st.session_state:
        st.session_state.user = None
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


# ══════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════

user = st.session_state.user
defaults = {
    "sel_company": None, "sel_contact": None, "session_id": None,
    "chat_messages": [], "auto_brief_text": None, "research_cache": {},
    "pending_plan": None, "sender_id": user["id"],
    "intel_lookup_lead": None, "intel_person_brief": None,
    "intel_suggestion": None, "intel_suggestion_raw": None,
    "current_tab": "DASHBOARD", "advisor_messages": [],
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


# ══════════════════════════════════════════════════════════════
# SIDEBAR — CONVERSATION HISTORY
# ══════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown(
        "<div style='font-size:12px;font-weight:700;letter-spacing:3px;"
        "color:var(--accent);padding:4px 0 12px;'>◆ LH2</div>",
        unsafe_allow_html=True)

    if st.session_state.sel_company and st.session_state.sel_contact:
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
        st.session_state.chat_messages = []
        st.session_state.auto_brief_text = None
        st.session_state.pending_plan = None
        st.rerun()

    st.markdown(
        "<div style='font-size:11px;font-weight:600;letter-spacing:1.2px;"
        "text-transform:uppercase;color:var(--text-3);margin:12px 0 8px;'>Recent</div>",
        unsafe_allow_html=True)

    sessions = get_user_sessions(user["id"])
    for sx in sessions[:15]:
        is_active = st.session_state.session_id == sx["id"]
        mc2 = get_message_count(sx["id"])
        domain = sx.get("company_domain", "?")
        contact = sx.get("contact_name", "?")
        updated = (sx.get("updated_at") or "")[:10]
        mem = " 🧠" if sx.get("strategic_summary") else ""
        bg = "background:var(--accent-bg);border:1px solid #c7d2fe;" if is_active else "border:1px solid transparent;"
        st.markdown(
            f"<div style='{bg}padding:8px 12px;border-radius:8px;margin-bottom:4px;'>"
            f"<div class='sidebar-session-block' style='font-size:13px;font-weight:500;color:var(--text);'>{domain}{mem}</div>"
            f"<div class='sidebar-session-meta' style='font-size:12px;color:var(--text-2);margin-top:3px;'>{contact} · {mc2} msgs · {updated}</div></div>",
            unsafe_allow_html=True)
        if not is_active:
            if st.button("Open", key=f"sb_{sx['id']}", use_container_width=True):
                st.session_state.sel_company = sx["company_id"]
                st.session_state.sel_contact = sx["contact_id"]
                st.session_state.session_id = sx["id"]
                st.session_state.chat_messages = load_messages(sx["id"])
                st.session_state.auto_brief_text = None
                st.session_state.pending_plan = None
                st.session_state.current_tab = "INTELLIGENCE"
                st.rerun()

    st.markdown("---")
    st.markdown(f"<div style='font-size:11px;color:var(--text-3);'>{user['username']} · {user['role']}</div>", unsafe_allow_html=True)
    if st.button("Sign Out", key="sb_logout", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ══════════════════════════════════════════════════════════════
# HEADER + TOP TAB BAR (buttons)
# ══════════════════════════════════════════════════════════════

TAB_NAMES = ["DASHBOARD", "COMPANIES", "INTELLIGENCE", "STRATEGIST", "ACTIONS", "TIMELINE", "CALENDAR", "PIPELINE", "HISTORY", "ADVISOR"]

current_tab = st.session_state.get("current_tab", "DASHBOARD")
if current_tab not in TAB_NAMES:
    current_tab = "DASHBOARD"
    st.session_state.current_tab = current_tab

# Header (same as before)
parts = ["◆ LH2 COMMAND CENTER"]
if st.session_state.sel_company:
    _c = get_company(st.session_state.sel_company)
    if _c:
        parts.append(f"<span style='color:var(--text-3);font-weight:400;'>→ {_c['domain']}</span>")
if st.session_state.sel_contact:
    _t = get_contact(st.session_state.sel_contact)
    if _t:
        parts.append(f"<span style='color:var(--text-3);font-weight:400;'>→ {_t['name']}</span>")
st.markdown(
    f"<div style='font-size:11px;font-weight:700;letter-spacing:3px;color:var(--accent);"
    f"padding-bottom:6px;border-bottom:1px solid var(--border);margin-bottom:4px;'>{' '.join(parts)}</div>",
    unsafe_allow_html=True)

# Tab bar: single radio so selected and unselected stay same size
st.markdown('<div class="nav-tab-bar-wrap">', unsafe_allow_html=True)
tab_labels = [n.title() if n != "ADVISOR" else "Advisor" for n in TAB_NAMES]
idx = TAB_NAMES.index(current_tab) if current_tab in TAB_NAMES else 0
chosen = st.radio("Section", tab_labels, index=idx, horizontal=True, key="main_section_radio", label_visibility="collapsed")
if chosen:
    chosen_tab = TAB_NAMES[tab_labels.index(chosen)]
    if chosen_tab != current_tab:
        st.session_state.current_tab = chosen_tab
        st.rerun()
st.markdown("</div>", unsafe_allow_html=True)

current_tab = st.session_state.current_tab

# ════════════════════ DASHBOARD ════════════════════

if current_tab == "DASHBOARD":
    st.markdown(sh("OVERVIEW"), unsafe_allow_html=True)
    stats = get_pipeline_stats()
    mc = st.columns(4)
    for col, (n, l) in zip(mc, [
        (len(get_all_companies()), "Companies"),
        (stats.get("outreach_sent", 0) + stats.get("awaiting_reply", 0), "Active Outreach"),
        (stats.get("meeting_booked", 0), "Meetings"), (stats.get("partnership_active", 0), "Partnerships"),
    ]):
        with col:
            st.markdown(mcard(n, l), unsafe_allow_html=True)

    st.markdown(sh("STATUS BREAKDOWN"), unsafe_allow_html=True)
    scols = st.columns(len(CONTACT_STATUSES))
    for col, s in zip(scols, CONTACT_STATUSES):
        with col:
            st.markdown(
                f"<div style='text-align:center;'><div style='font-size:20px;font-weight:700;'>{stats.get(s,0)}</div>"
                f"<div style='margin-top:4px;'>{bdg(CONTACT_STATUS_LABELS.get(s,s), CONTACT_STATUS_COLORS.get(s,'gray'))}</div></div>",
                unsafe_allow_html=True)

    overdue = get_overdue_followups(3)
    todays = get_todays_scheduled()
    if overdue or todays:
        st.markdown(sh("ALERTS"), unsafe_allow_html=True)
        for a in overdue[:5]:
            st.markdown(alert(f"⚠️ Overdue: {a.get('contact_name','?')} at {a.get('company_domain','?')} — {a['description'][:100]}", "danger"), unsafe_allow_html=True)
        for a in todays[:5]:
            st.markdown(alert(f"📅 Today: {a.get('contact_name','?')} — {a['description'][:100]}"), unsafe_allow_html=True)

    st.markdown(sh("RECENT ACTIVITY"), unsafe_allow_html=True)
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

elif current_tab == "COMPANIES":
    if st.session_state.get("clear_company_search"):
        st.session_state.pop("csearch", None)
        st.session_state.pop("clear_company_search", None)
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
            st.session_state.chat_messages = []
            st.session_state.clear_company_search = True
            st.rerun()

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
                        st.session_state.sel_company = cid
                        st.success(f"Found: {lead['domain']}")
                        st.rerun()
                    else:
                        st.error("No match in Notion")

    companies = search_companies(sq) if sq else get_all_companies()

    # Quick dropdown of matching companies when searching
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
            st.markdown(
                f"<div class='c' style='padding:10px 14px;{border}'>"
                f"<div style='display:flex;justify-content:space-between;'>"
                f"<div><span style='font-size:14px;font-weight:500;'>{comp['domain']}</span>"
                f" <span style='font-size:12px;color:var(--text-3);'>{comp.get('publisher_type','')}</span></div>"
                f"<span style='font-size:12px;color:var(--text-2);'>{fmt_v(comp.get('monthly_visits'))}</span></div></div>",
                unsafe_allow_html=True)
            cts = get_contacts(comp["id"])
            if cts:
                with st.expander(f"👥 {len(cts)} contact(s)", expanded=is_sel):
                    for ct in cts:
                        cols = st.columns([2.5, 1.2, 0.5])
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
                            if st.button("→", key=f"go_{ct['id']}", use_container_width=True):
                                select_contact(comp["id"], ct["id"])
                                st.rerun()


# ════════════════════ INTELLIGENCE ════════════════════

elif current_tab == "INTELLIGENCE":
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact
    if not cid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);font-size:14px;'>Select a company from COMPANIES tab.</div>", unsafe_allow_html=True)
    else:
        comp = get_company(cid)
        if not comp:
            st.error("Company not found.")
        else:
            page_id = comp.get("notion_page_id")
            rn = []
            if page_id:
                if cid not in st.session_state.research_cache:
                    with st.spinner("Loading from Notion..."):
                        rn = fetch_research_from_notion(page_id)
                        st.session_state.research_cache[cid] = rn
                else:
                    rn = st.session_state.research_cache[cid]

            if ctid and st.session_state.session_id:
                ct = get_contact(ctid)
                if ct:
                    if st.session_state.auto_brief_text is None:
                        with st.spinner("Generating brief..."):
                            brief = auto_brief(comp, ct, user["id"], st.session_state.session_id, rn)
                            st.session_state.auto_brief_text = brief or "Could not generate brief."
                    st.markdown(sh_small(f"Strategic brief — {ct['name']}"), unsafe_allow_html=True)
                    st.markdown(f"<div class='brief-card'><div style='font-size:13px;white-space:pre-wrap;'>{st.session_state.auto_brief_text}</div></div>", unsafe_allow_html=True)
                    if st.button("🔄 Refresh", key="rb"):
                        st.session_state.auto_brief_text = None
                        st.rerun()

            st.markdown(sh_small("Company profile"), unsafe_allow_html=True)
            r1 = st.columns(5)
            for col, (l, v) in zip(r1, [
                ("Domain", comp.get("domain")), ("Publisher", comp.get("publisher_type")),
                ("Content", comp.get("content_type")), ("Visits", fmt_v(comp.get("monthly_visits"))),
                ("Research", comp.get("research_status")),
            ]):
                with col:
                    st.markdown(crd(l, v), unsafe_allow_html=True)

            st.markdown(sh_small("Request / suggest / look up person"), unsafe_allow_html=True)
            st.caption("Paste a LinkedIn URL or email to run AI research on that person (in context of this company), or describe what’s missing to get a suggestion.")
            intel_input = st.text_area(
                "Request",
                placeholder="E.g. paste linkedin.com/in/... or name@company.com · or 'Power mapping missing the CFO'",
                height=88,
                key="intel_request",
                label_visibility="collapsed",
            )
            if st.button("Run research or get suggestion", key="intel_submit", type="primary"):
                raw = (intel_input or "").strip()
                if not raw:
                    st.warning("Enter a request, LinkedIn URL, or email.")
                else:
                    linkedin_match = re.search(r"https?://[^\s]*linkedin\.com[^\s]*", raw, re.IGNORECASE)
                    email_match = re.search(r"[\w.-]+@[\w.-]+\.\w+", raw)
                    if linkedin_match:
                        url = linkedin_match.group(0).strip().rstrip(".,;:)")
                        lead = None
                        with st.spinner("Checking Notion..."):
                            lead = lookup_lead_in_notion(url)
                        st.session_state.intel_lookup_lead = lead if lead else {"linkedin": url, "name": "", "email": "", "role": "Contact", "domain": comp.get("domain", "")}
                        with st.spinner("Running AI research on this person..."):
                            brief = research_person_for_company(
                                comp, rn,
                                linkedin_url=url,
                                notion_lead=lead,
                            )
                        st.session_state.intel_person_brief = brief
                        st.session_state.intel_suggestion = None
                        st.rerun()
                    elif email_match:
                        em = email_match.group(0).strip()
                        st.session_state.intel_lookup_lead = {"email": em, "name": "", "linkedin": "", "role": "Contact", "domain": comp.get("domain", "")}
                        with st.spinner("Running AI research on this person..."):
                            brief = research_person_for_company(comp, rn, email=em)
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
                    st.markdown(f"<div class='brief-card'><div style='font-size:13px;white-space:pre-wrap;'>{brief}</div></div>", unsafe_allow_html=True)
                if lead.get("domain") and lead.get("name"):
                    st.caption(f"Found in Notion: {lead.get('name')} @ {lead.get('domain')}. You can add them to this company’s contacts and sync to Notion below.")
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

            st.markdown(sh_small("Research notes"), unsafe_allow_html=True)
            if rn:
                if "research_summary_cache" not in st.session_state:
                    st.session_state.research_summary_cache = {}
                if cid in st.session_state.research_summary_cache:
                    st.markdown(sh_small("Company context (short)"), unsafe_allow_html=True)
                    st.markdown(f"<div class='brief-card'><div style='font-size:13px;white-space:pre-wrap;'>{st.session_state.research_summary_cache[cid]}</div></div>", unsafe_allow_html=True)
                elif st.button("Get short context summary", key="intel_summary_btn"):
                    with st.spinner("Summarizing..."):
                        summary = summarize_research_to_context(comp, rn)
                        if summary:
                            st.session_state.research_summary_cache[cid] = summary
                            st.rerun()
                st.caption(f"{len(rn)} sections below. Use the request box above to research a person in this context.")
                st.markdown(sh_small("Full sections"), unsafe_allow_html=True)
                for note in rn:
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
                        st.markdown(
                            f"<div style='font-size:13px;white-space:pre-wrap;font-family:monospace;'>{(note.get('content') or '')[:6000]}</div>",
                            unsafe_allow_html=True,
                        )

                        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
                        st.markdown(sh_small("Edit / correct (local override)"), unsafe_allow_html=True)
                        new_txt = st.text_area(
                            "Override",
                            value=(existing if has_override else (note.get("content") or "")),
                            height=220,
                            key=f"ovr_{cid}_{title}",
                            label_visibility="collapsed",
                        )
                        b1, b2, b3 = st.columns([1, 1, 2])
                        with b1:
                            if st.button("💾 Save override", key=f"saveovr_{cid}_{title}", use_container_width=True):
                                overrides = (comp.get("extra_data") or {}).get("research_overrides") or {}
                                overrides[title] = new_txt
                                merge_company_extra_data(cid, {"research_overrides": overrides})
                                st.session_state.auto_brief_text = None
                                st.success("Saved.")
                                st.rerun()
                        with b2:
                            if st.button("🧹 Clear override", key=f"clr_{cid}_{title}", use_container_width=True):
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
                st.info("No research in Notion.")
                if has_perm("can_use_strategist"):
                    if st.button("🔬 Run Deep Research", key="run_res"):
                        pb = st.progress(0, text="Starting...")
                        def _prog(c, e):
                            pb.progress(min(c/40000, .95), text=f"Researching... {c:,} chars ({e:.0f}s)")
                        result = run_research(comp["domain"], progress_callback=_prog)
                        pb.progress(1.0, text="Done!")
                        if result:
                            st.success(f"{len(result):,} chars")
                            if page_id:
                                with st.spinner("Writing to Notion..."):
                                    write_research_to_notion(page_id, result, comp["domain"])
                                if cid in st.session_state.research_cache:
                                    del st.session_state.research_cache[cid]
                                st.rerun()
                        else:
                            st.error("Failed.")

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

elif current_tab == "STRATEGIST":
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact
    sid = st.session_state.session_id
    if not cid or not ctid or not sid:
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
                user_names = {u["id"]: u["username"] for u in all_users}
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
                    ch_icon = "💬" if "linkedin" in step["channel"].lower() else "📧"
                    st.markdown(
                        f"<div style='padding:10px 0;border-bottom:1px solid #e0e7ff;'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<span style='font-size:13px;font-weight:600;'>"
                        f"{ch_icon} Step {step['step']}: {step['channel']} — Day {step['day_offset']}</span>"
                        f"<span style='font-size:11px;color:var(--text-3);'>{step.get('timing_note','')}</span></div>"
                        f"<div style='font-size:12px;color:var(--text-2);margin-top:4px;'>{step['description']}</div>"
                        f"<div style='font-size:12px;margin-top:6px;background:#fff;padding:8px 12px;"
                        f"border-radius:6px;border:1px solid #e5e7eb;white-space:pre-wrap;'>"
                        f"{(step.get('content_draft',''))[:500]}</div></div>",
                        unsafe_allow_html=True)

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

elif current_tab == "ACTIONS":
    sid = st.session_state.session_id
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact
    if not sid:
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
                    ch_icon = "💬" if "linkedin" in step["channel"].lower() else "📧" if "email" in step["channel"].lower() else "📞"

                    s1, s2, s3 = st.columns([3, 1.2, 0.8])
                    with s1:
                        sent_html = ""
                        if step.get("content_sent"):
                            sent_html = f"<div style='font-size:11px;margin-top:4px;background:#f8f9fa;padding:6px 10px;border-radius:6px;border-left:2px solid #4caf50;'>{step['content_sent'][:200]}</div>"
                        resp_html = ""
                        if step.get("response_content"):
                            resp_html = f"<div style='font-size:11px;margin-top:4px;background:#f0f4ff;padding:6px 10px;border-radius:6px;border-left:2px solid #2196f3;'>Response: {step['response_content'][:200]}</div>"
                        sched = f" · 📅 {step['scheduled_at'][:10]}" if step.get("scheduled_at") else ""
                        st.markdown(
                            f"<div style='padding:6px 0;'>"
                            f"<div>{icon} {ch_icon} <b>Step {step.get('sequence_step','?')}</b>: {step['channel']}{sched} {bdg(step['status'], sc)}</div>"
                            f"<div style='font-size:12px;color:var(--text-2);margin-top:2px;'>{step['description'][:200]}</div>"
                            f"{sent_html}{resp_html}</div>",
                            unsafe_allow_html=True)
                    with s2:
                        if has_perm("can_log_outcomes"):
                            new_s = st.selectbox("Status", ACTION_STATUSES,
                                index=ACTION_STATUSES.index(step["status"]) if step["status"] in ACTION_STATUSES else 0,
                                key=f"seq_{step['id']}", label_visibility="collapsed")
                            if new_s != step["status"]:
                                update_action(step["id"], status=new_s)
                                # Trigger replan if response received
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

elif current_tab == "TIMELINE":
    cid = st.session_state.sel_company
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

elif current_tab == "CALENDAR":
    st.markdown(sh("UPCOMING ACTIONS"), unsafe_allow_html=True)
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact

    tl = get_timeline_actions(company_id=cid, contact_id=ctid, limit=2000) if cid else get_user_actions(user["id"], limit=2000)

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

elif current_tab == "PIPELINE":
    st.markdown(sh("CONTACT PIPELINE"), unsafe_allow_html=True)
    all_comp = get_all_companies()
    pipeline = {s: [] for s in CONTACT_STATUSES}
    for cd in all_comp:
        for ctd in get_contacts(cd["id"]):
            status = ctd.get("status", "not_contacted")
            if status in pipeline:
                pipeline[status].append({"contact": ctd, "company": cd})

    mcols = st.columns(len(CONTACT_STATUSES))
    for col, s in zip(mcols, CONTACT_STATUSES):
        with col:
            st.markdown(mcard(len(pipeline[s]), CONTACT_STATUS_LABELS.get(s, s)), unsafe_allow_html=True)

    bcols = st.columns(len(CONTACT_STATUSES))
    for i, s in enumerate(CONTACT_STATUSES):
        with bcols[i]:
            sc = CONTACT_STATUS_COLORS.get(s, "gray")
            st.markdown(f"<div style='text-align:center;margin:8px 0;'>{bdg(CONTACT_STATUS_LABELS.get(s,s),sc)}</div>", unsafe_allow_html=True)
            for item in pipeline[s]:
                ctd, cd = item["contact"], item["company"]
                score = compute_lead_score(cd, ctd)
                si = "🟢" if score >= 60 else "🟡" if score >= 35 else "🔴"
                st.markdown(
                    f"<div class='pc'><div style='font-size:13px;font-weight:500;'>{ctd['name']}</div>"
                    f"<div style='font-size:11px;color:var(--text-3);'>{cd['domain']}</div>"
                    f"<div style='font-size:11px;'>{si} {score}/100</div></div>",
                    unsafe_allow_html=True)
            if not pipeline[s]:
                st.markdown("<div style='text-align:center;padding:24px 0;font-size:11px;color:#ccc;'>—</div>", unsafe_allow_html=True)


# ════════════════════ HISTORY ════════════════════

elif current_tab == "HISTORY":
    ginsights = get_global_insights(user["id"], limit=8)
    if ginsights:
        st.markdown(sh("GLOBAL PATTERNS"), unsafe_allow_html=True)
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
        st.markdown(sh("ANALYTICS"), unsafe_allow_html=True)
        total = len(all_ua)
        sent = sum(1 for a in all_ua if a["status"] in ("sent", "completed", "replied_positive", "replied_negative", "meeting_booked", "deal_closed"))
        pos = sum(1 for a in all_ua if a["status"] in ("replied_positive", "meeting_booked", "deal_closed"))
        mtg = sum(1 for a in all_ua if a["status"] == "meeting_booked")
        nr = sum(1 for a in all_ua if a["status"] == "no_response")
        rate = f"{(pos/sent*100):.0f}%" if sent else "—"
        am = st.columns(5)
        for col, (n, l) in zip(am, [(total, "Total"), (sent, "Sent"), (rate, "Positive Rate"), (mtg, "Meetings"), (nr, "No Response")]):
            with col:
                st.markdown(mcard(n, l), unsafe_allow_html=True)

    st.markdown(sh("ALL SESSIONS"), unsafe_allow_html=True)
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

elif current_tab == "ADVISOR":
    st.markdown(sh("ADVISOR"), unsafe_allow_html=True)
    st.caption("Ask for company suggestions, outreach plans, or advice. I remember our conversation and your pipeline.")
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
        st.rerun()