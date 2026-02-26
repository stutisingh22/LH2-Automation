"""
LH2 RELATIONSHIP INTELLIGENCE PLATFORM v4

Company → Person → Session flow
PostgreSQL backend (SQLAlchemy)
Layered AI memory (L1–L5)
Google Drive integration
Notion sync
Team-accessible, cloud-deployable

Run:
    streamlit run app.py
"""

import streamlit as st
import json
import csv
import io
from datetime import datetime, timedelta

# ============================================================
# PAGE CONFIG (must be first)
# ============================================================

st.set_page_config(
    page_title="LH2 Command Center",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# IMPORTS (after page config)
# ============================================================

from lh2_platform.core.config import (
    CONTACT_STATUSES, CONTACT_STATUS_LABELS, CONTACT_STATUS_COLORS,
    ACTION_TYPES, ACTION_CHANNELS, ACTION_STATUSES, ACTION_STATUS_COLORS,
    ROLE_PERMISSIONS,
)
try:
    from lh2_platform.core.config import normalize_contact_status
except ImportError:
    def normalize_contact_status(s):
        return "outreach_sent" if s in ("awaiting_reply", "replied_negative") else s
from lh2_platform.core.models import init_database
from lh2_platform.core.database import (
    create_user, authenticate_user, get_all_users, update_user_role,
    upsert_company, get_company_by_id, get_all_companies, search_companies,
    get_contacts_for_company, get_contact_by_id, upsert_contact, update_contact_status,
    get_or_create_session, get_session_by_id, get_sessions_for_company,
    get_sessions_for_user, get_all_sessions_with_details, delete_session,
    save_message, load_messages, get_message_count,
    log_action, get_session_actions, get_all_actions, update_action,
    get_all_actions_for_timeline, get_overdue_followups, get_todays_scheduled,
    get_session_summary, get_memory_events, get_all_global_insights,
    get_pipeline_stats, get_recent_activity,
)
from lh2_platform.core.strategist import (
    get_strategist_response, compute_lead_score,
    maybe_update_summary, extract_episodic_memories,
    generate_insight_from_outcome, parse_ai_actions, get_local_time,
)
from lh2_platform.core.export import export_session_csv
from lh2_platform.integrations.notion_sync import (
    sync_notion_to_db, fetch_all_companies_from_notion,
    fetch_lead_from_notion,
)
from lh2_platform.integrations.drive import (
    is_drive_configured, list_drive_files,
)
from lh2_platform.ui.components import (
    inject_css, badge, card, metric_card, section_header,
    timeline_item, alert_card, fmt_visits,
)


# ============================================================
# INIT
# ============================================================

@st.cache_resource
def setup_database():
    return init_database()

setup_database()
inject_css()


# ============================================================
# AUTH HELPERS
# ============================================================

def has_permission(permission: str) -> bool:
    user = st.session_state.get("user")
    if not user:
        return False
    role = user.get("role", "viewer")
    return ROLE_PERMISSIONS.get(role, {}).get(permission, False)


# ============================================================
# AUTH GATE
# ============================================================

def render_auth():
    if "user" not in st.session_state:
        st.session_state.user = None
    if st.session_state.user:
        return True

    _, col, _ = st.columns([1.1, 0.8, 1.1])
    with col:
        st.markdown("""
        <div style='text-align:center; margin-top:14vh; margin-bottom:36px;'>
            <div style='font-size:14px; font-weight:700; letter-spacing:5px; color:var(--accent); margin-bottom:6px;'>◆ LH2</div>
            <div style='font-size:22px; font-weight:600; color:var(--text);'>Command Center</div>
            <div style='font-size:13px; color:var(--text-3); margin-top:6px;'>Relationship intelligence platform</div>
        </div>
        """, unsafe_allow_html=True)

        t_login, t_reg = st.tabs(["SIGN IN", "REGISTER"])
        with t_login:
            with st.form("login"):
                u = st.text_input("Username", placeholder="Username")
                p = st.text_input("Password", type="password", placeholder="Password")
                if st.form_submit_button("Sign In"):
                    user = authenticate_user(u, p)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    else:
                        st.error("Invalid credentials")
        with t_reg:
            with st.form("register"):
                nu = st.text_input("Username", placeholder="Choose username")
                ne = st.text_input("Email", placeholder="your@email.com")
                np = st.text_input("Password", type="password", placeholder="Min 6 characters")
                if st.form_submit_button("Create Account"):
                    if len(nu) < 3 or len(np) < 6:
                        st.error("Username 3+, password 6+ chars")
                    else:
                        result = create_user(nu, np, ne)
                        if result:
                            st.success("Created. Sign in above.")
                        else:
                            st.error("Username taken")
    return False


if not render_auth():
    st.stop()


# ============================================================
# SESSION STATE DEFAULTS
# ============================================================

user = st.session_state.user
defaults = {
    "selected_company_id": None,
    "selected_contact_id": None,
    "session_id": None,
    "chat_messages": [],
}
for key, default in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ============================================================
# HEADER
# ============================================================

h1, h2 = st.columns([3, 1])
with h1:
    header_parts = ["◆ LH2 COMMAND CENTER"]
    if st.session_state.selected_company_id:
        comp = get_company_by_id(st.session_state.selected_company_id)
        if comp:
            header_parts.append(f"<span style='color:var(--text-3); font-weight:400;'>→ {comp['domain']}</span>")
    if st.session_state.selected_contact_id:
        contact = get_contact_by_id(st.session_state.selected_contact_id)
        if contact:
            header_parts.append(f"<span style='color:var(--text-3); font-weight:400;'>→ {contact['name']}</span>")
    st.markdown(
        f"<div style='font-size:11px; font-weight:700; letter-spacing:3px; color:var(--accent); padding-bottom:6px; border-bottom:1px solid var(--border); margin-bottom:4px;'>{' '.join(header_parts)}</div>",
        unsafe_allow_html=True,
    )
with h2:
    c1, c2 = st.columns([2.5, 1])
    with c1:
        sid = st.session_state.session_id
        if sid:
            summary, smcount = get_session_summary(sid)
            total_msgs = get_message_count(sid)
            events_count = len(get_memory_events(sid, min_importance=1))
            mem_class = "mem-active" if summary else "mem-stale"
            mem_label = f"Memory: {total_msgs}msg / {events_count}ev" if summary else f"Memory: {total_msgs}msg"
            st.markdown(f"<div style='text-align:right; margin-top:2px;'><span class='mem-indicator {mem_class}'>{mem_label}</span></div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<p style='text-align:right; font-size:12px; color:var(--text-3) !important; margin-top:4px;'>{user['username']} ({user['role']})</p>", unsafe_allow_html=True)
    with c2:
        if st.button("Sign Out", key="logout"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()


# ============================================================
# TABS
# ============================================================

tab_dash, tab_companies, tab_intel, tab_strat, tab_actions, tab_timeline, tab_pipeline, tab_history = st.tabs([
    "DASHBOARD", "COMPANIES", "INTELLIGENCE", "STRATEGIST", "ACTIONS", "TIMELINE", "PIPELINE", "HISTORY"
])


# ============================================================
# TAB: DASHBOARD
# ============================================================

with tab_dash:
    st.markdown(section_header("OVERVIEW"), unsafe_allow_html=True)

    # Pipeline stats
    stats = get_pipeline_stats()
    mcols = st.columns(4)
    total_companies = len(get_all_companies())
    total_active = sum(1 for s in stats.values() if s > 0)

    with mcols[0]:
        st.markdown(metric_card(total_companies, "Companies"), unsafe_allow_html=True)
    with mcols[1]:
        st.markdown(metric_card(stats.get("outreach_sent", 0), "Active Outreach"), unsafe_allow_html=True)
    with mcols[2]:
        st.markdown(metric_card(stats.get("meeting_booked", 0), "Meetings Booked"), unsafe_allow_html=True)
    with mcols[3]:
        st.markdown(metric_card(stats.get("partnership_active", 0), "Partnerships"), unsafe_allow_html=True)

    # Status breakdown
    st.markdown(section_header("STATUS BREAKDOWN"), unsafe_allow_html=True)
    status_cols = st.columns(len(CONTACT_STATUSES))
    for col, status in zip(status_cols, CONTACT_STATUSES):
        with col:
            count = stats.get(status, 0)
            color = CONTACT_STATUS_COLORS.get(status, "gray")
            st.markdown(
                f"<div style='text-align:center;'><div style='font-size:20px; font-weight:700;'>{count}</div>"
                f"<div style='margin-top:4px;'>{badge(CONTACT_STATUS_LABELS.get(status, status), color)}</div></div>",
                unsafe_allow_html=True,
            )

    # Alerts
    overdue = get_overdue_followups(days=3)
    todays = get_todays_scheduled()

    if overdue or todays:
        st.markdown(section_header("SMART ALERTS"), unsafe_allow_html=True)
        for a in overdue[:5]:
            st.markdown(
                alert_card(f"⚠️ Overdue follow-up: {a.get('contact_name', '?')} at {a.get('company_domain', '?')} — {a['description'][:100]}", "danger"),
                unsafe_allow_html=True,
            )
        for a in todays[:5]:
            scheduled = a.get("scheduled_at", "")[:16] if a.get("scheduled_at") else "today"
            st.markdown(
                alert_card(f"📅 Scheduled today ({scheduled}): {a.get('contact_name', '?')} — {a['description'][:100]}"),
                unsafe_allow_html=True,
            )

    # Recent activity
    st.markdown(section_header("RECENT ACTIVITY"), unsafe_allow_html=True)
    recent = get_recent_activity(limit=10)
    if recent:
        for a in recent:
            sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
            st.markdown(
                f"<div class='c' style='padding:10px 16px;'>"
                f"<div style='display:flex; justify-content:space-between;'>"
                f"<span class='c-l'>{a.get('company_domain', '?')} · {a.get('contact_name', '?')} · {a['action_type']}</span>"
                f"{badge(a['status'], sc)}"
                f"</div>"
                f"<div style='font-size:12px; margin-top:4px;'>{a['description'][:200]}</div>"
                f"<div style='font-size:10px; color:var(--text-3); margin-top:3px;'>{a.get('username', '?')} · {a['channel']} · {a['created_at'][:16] if a.get('created_at') else ''}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown("<div style='text-align:center; padding:40px; color:var(--text-3);'>No activity yet.</div>", unsafe_allow_html=True)


# ============================================================
# TAB: COMPANIES (Step 1 — Company Selection)
# ============================================================

with tab_companies:
    ch1, ch2, ch3 = st.columns([2, 1, 1])
    with ch1:
        search_q = st.text_input("Search companies", placeholder="Search by domain or name...", label_visibility="collapsed", key="company_search")
    with ch2:
        if st.button("🔄 Sync from Notion", key="notion_sync", use_container_width=True):
            with st.spinner("Syncing all companies from Notion..."):
                result = sync_notion_to_db()
                st.success(f"Synced {result['companies']} companies, {result['contacts']} contacts")
                st.rerun()
    with ch3:
        if st.button("Reset Selection", key="reset_sel", use_container_width=True):
            st.session_state.selected_company_id = None
            st.session_state.selected_contact_id = None
            st.session_state.session_id = None
            st.session_state.chat_messages = []
            st.rerun()

    # Also support legacy LinkedIn URL input
    with st.expander("Quick add via LinkedIn URL"):
        li_url = st.text_input("LinkedIn URL", placeholder="Paste LinkedIn URL...", key="li_url_input")
        if st.button("Sync from LinkedIn", key="li_sync"):
            if li_url:
                with st.spinner("Syncing..."):
                    lead = fetch_lead_from_notion(li_url)
                    if lead and lead.get("domain"):
                        cid = upsert_company(
                            domain=lead["domain"],
                            name=lead.get("name") or lead["domain"],
                            publisher_type=lead.get("publisher_type"),
                            content_type=lead.get("content_type"),
                            monthly_visits=lead.get("monthly_visits"),
                            notion_page_id=lead.get("page_id"),
                            research_status=lead.get("research_status"),
                            gmail_thread=lead.get("gmail_thread"),
                            extra_data={"research_notes": lead.get("research_notes", [])},
                        )
                        if lead.get("name"):
                            upsert_contact(
                                company_id=cid, name=lead["name"],
                                role="SPOC / Editor",
                                email=lead.get("email"),
                                linkedin=lead.get("linkedin"),
                                city=lead.get("city"),
                                state=lead.get("state"),
                                country=lead.get("country"),
                                notion_page_id=lead.get("page_id"),
                            )
                        st.session_state.selected_company_id = cid
                        st.success(f"Synced: {lead['domain']}")
                        st.rerun()
                    else:
                        st.error("No match in Notion")

    # Company list
    companies = search_companies(search_q) if search_q else get_all_companies()

    if not companies:
        st.markdown(
            "<div style='text-align:center; padding:80px 0; color:var(--text-3);'>"
            "<div style='font-size:32px; opacity:0.2; margin-bottom:14px;'>◆</div>"
            "<div style='font-size:14px; font-weight:500;'>No companies yet</div>"
            "<div style='font-size:13px; margin-top:4px; opacity:0.7;'>Click 'Sync from Notion' to import your database</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(section_header(f"COMPANIES ({len(companies)})"), unsafe_allow_html=True)
        for comp in companies:
            cc1, cc2, cc3 = st.columns([2.5, 1.5, 0.5])
            with cc1:
                visits_str = fmt_visits(comp.get("monthly_visits"))
                st.markdown(
                    f"<div class='c' style='padding:10px 14px;'>"
                    f"<div style='font-size:14px; font-weight:500;'>{comp['domain']}</div>"
                    f"<div style='font-size:12px; color:var(--text-3); margin-top:2px;'>{comp.get('name', '')} · {comp.get('publisher_type', '')} · {visits_str}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with cc2:
                contacts = get_contacts_for_company(comp["id"])
                contact_statuses = [c.get("status", "not_contacted") for c in contacts]
                status_display = ", ".join(set(CONTACT_STATUS_LABELS.get(s, s) for s in contact_statuses)) if contact_statuses else "No contacts"
                st.markdown(f"<div style='font-size:12px; color:var(--text-2); margin-top:8px;'>{len(contacts)} contact(s) · {status_display}</div>", unsafe_allow_html=True)
            with cc3:
                if st.button("→", key=f"sel_comp_{comp['id']}", use_container_width=True):
                    st.session_state.selected_company_id = comp["id"]
                    st.session_state.selected_contact_id = None
                    st.session_state.session_id = None
                    st.session_state.chat_messages = []
                    st.rerun()


# ============================================================
# TAB: INTELLIGENCE (Step 2+3 — Person Selection + Workspace)
# ============================================================

with tab_intel:
    company_id = st.session_state.selected_company_id
    contact_id = st.session_state.selected_contact_id

    if not company_id:
        st.markdown(
            "<div style='text-align:center; padding:60px 0; color:var(--text-3); font-size:13px;'>"
            "Select a company from the COMPANIES tab first."
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        comp = get_company_by_id(company_id)
        if not comp:
            st.error("Company not found.")
        else:
            # Company profile
            st.markdown(section_header("COMPANY PROFILE"), unsafe_allow_html=True)
            score_base = compute_lead_score(comp)
            r1 = st.columns(5)
            fields1 = [
                ("Domain", comp.get("domain")),
                ("Publisher Type", comp.get("publisher_type")),
                ("Content Type", comp.get("content_type")),
                ("Monthly Visits", fmt_visits(comp.get("monthly_visits"))),
                ("Research", comp.get("research_status")),
            ]
            for col, (lbl, val) in zip(r1, fields1):
                with col:
                    st.markdown(card(lbl, val), unsafe_allow_html=True)

            # Research notes
            extra = comp.get("extra_data") or {}
            notes = extra.get("research_notes", [])
            if notes:
                st.markdown(section_header("RESEARCH NOTES"), unsafe_allow_html=True)
                for note in notes:
                    with st.expander(note["title"]):
                        st.markdown(f"<div class='rb'>{note['content'][:4000]}</div>", unsafe_allow_html=True)

            # Drive files
            if is_drive_configured():
                drive_files = list_drive_files(company_name=comp.get("domain", ""))
                if drive_files:
                    st.markdown(section_header(f"DRIVE DOCUMENTS ({len(drive_files)})"), unsafe_allow_html=True)
                    for f in drive_files[:10]:
                        link = f.get("webViewLink", "#")
                        st.markdown(
                            f"<div class='c' style='padding:8px 14px;'>"
                            f"<a href='{link}' target='_blank'>{f['name']}</a>"
                            f"<span style='font-size:11px; color:var(--text-3); margin-left:12px;'>{f.get('mimeType', '')}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

            # Gmail thread
            if comp.get("gmail_thread"):
                st.markdown(f"{section_header('GMAIL')}<a href='{comp['gmail_thread']}' target='_blank'>Open Thread →</a>", unsafe_allow_html=True)

            # ---- Step 2: Contact selection ----
            st.markdown(section_header("CONTACTS"), unsafe_allow_html=True)
            contacts = get_contacts_for_company(company_id)

            if not contacts:
                st.markdown("<div style='padding:20px; color:var(--text-3); font-size:13px;'>No contacts synced. Sync from Notion or add manually.</div>", unsafe_allow_html=True)

            for ct in contacts:
                ct_c1, ct_c2, ct_c3 = st.columns([2.5, 1.5, 0.5])
                with ct_c1:
                    status_color = CONTACT_STATUS_COLORS.get(ct.get("status", "not_contacted"), "gray")
                    status_label = CONTACT_STATUS_LABELS.get(ct.get("status", "not_contacted"), ct.get("status", ""))
                    local_time, _ = get_local_time(ct.get("country", ""), ct.get("state", ""))
                    st.markdown(
                        f"<div class='c' style='padding:10px 14px;'>"
                        f"<div style='display:flex; justify-content:space-between;'>"
                        f"<span style='font-size:14px; font-weight:500;'>{ct['name']}</span>"
                        f"{badge(status_label, status_color)}"
                        f"</div>"
                        f"<div style='font-size:12px; color:var(--text-3); margin-top:3px;'>"
                        f"{ct.get('role', '')} · {ct.get('email', '')} · {local_time}"
                        f"</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with ct_c2:
                    if ct.get("linkedin"):
                        st.markdown(f"<a href='{ct['linkedin']}' target='_blank' style='font-size:12px;'>LinkedIn →</a>", unsafe_allow_html=True)
                with ct_c3:
                    if st.button("Select", key=f"sel_ct_{ct['id']}", use_container_width=True):
                        st.session_state.selected_contact_id = ct["id"]
                        # Create/load session
                        sid = get_or_create_session(company_id, ct["id"], user["id"], compute_lead_score(comp, ct))
                        st.session_state.session_id = sid
                        st.session_state.chat_messages = load_messages(sid)
                        st.rerun()

            # ---- Step 3: Loaded workspace for selected contact ----
            if contact_id:
                ct = get_contact_by_id(contact_id)
                if ct:
                    st.markdown(section_header(f"ACTIVE WORKSPACE — {ct['name']}"), unsafe_allow_html=True)

                    r2 = st.columns(5)
                    fields2 = [
                        ("Lead Score", f"{'🟢' if score_base >= 60 else '🟡' if score_base >= 35 else '🔴'} {compute_lead_score(comp, ct)}/100"),
                        ("Email", ct.get("email")),
                        ("Location", ", ".join(filter(None, [ct.get("city"), ct.get("state"), ct.get("country")]))),
                        ("Local Time", get_local_time(ct.get("country", ""), ct.get("state", ""))[0]),
                        ("Status", CONTACT_STATUS_LABELS.get(ct.get("status", "not_contacted"), ct.get("status", ""))),
                    ]
                    for col, (lbl, val) in zip(r2, fields2):
                        with col:
                            st.markdown(card(lbl, val), unsafe_allow_html=True)

                    # Contact status update
                    _norm = normalize_contact_status(ct.get("status", "not_contacted"))
                    new_status = st.selectbox(
                        "Update Contact Status",
                        CONTACT_STATUSES,
                        index=CONTACT_STATUSES.index(_norm) if _norm in CONTACT_STATUSES else 0,
                        format_func=lambda x: CONTACT_STATUS_LABELS.get(x, x),
                        key="ct_status_sel",
                    )
                    if new_status != ct.get("status"):
                        update_contact_status(contact_id, new_status)
                        st.rerun()

                    # Past sessions (team-wide)
                    all_company_sessions = get_sessions_for_company(company_id)
                    if all_company_sessions:
                        st.markdown(section_header(f"ALL SESSIONS FOR {comp['domain']} ({len(all_company_sessions)})"), unsafe_allow_html=True)
                        for s in all_company_sessions[:10]:
                            msg_count = get_message_count(s["id"])
                            has_mem = "✓ Memory" if s.get("strategic_summary") else ""
                            st.markdown(
                                f"<div class='c' style='padding:8px 14px;'>"
                                f"<span style='font-size:12px;'>Session #{s['id']} · {msg_count} messages {has_mem}</span>"
                                f"<span style='font-size:11px; color:var(--text-3); margin-left:12px;'>Updated: {s['updated_at'][:16] if s.get('updated_at') else ''}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                    # Session memory
                    sid = st.session_state.session_id
                    if sid:
                        summary, smcount = get_session_summary(sid)
                        events = get_memory_events(sid, min_importance=1)
                        if summary or events:
                            st.markdown(section_header("SESSION MEMORY"), unsafe_allow_html=True)
                            if summary:
                                with st.expander(f"Rolling Summary (compressed from {smcount} messages)"):
                                    st.markdown(f"<div class='rb'>{summary}</div>", unsafe_allow_html=True)
                            if events:
                                with st.expander(f"Episodic Events ({len(events)} recorded)"):
                                    for e in events:
                                        imp_color = "green" if e["importance_score"] >= 7 else "yellow" if e["importance_score"] >= 4 else "gray"
                                        st.markdown(
                                            f"<div class='c' style='padding:10px 14px;'>"
                                            f"<span style='font-size:11px; color:var(--text-3);'>{e['event_type']}</span> "
                                            f"{badge(str(e['importance_score'])+'/10', imp_color)}"
                                            f"<div style='font-size:13px; margin-top:4px;'>{e['summary']}</div>"
                                            f"</div>",
                                            unsafe_allow_html=True,
                                        )


# ============================================================
# TAB: STRATEGIST
# ============================================================

with tab_strat:
    company_id = st.session_state.selected_company_id
    contact_id = st.session_state.selected_contact_id
    sid = st.session_state.session_id

    if not company_id or not contact_id or not sid:
        st.markdown(
            "<div style='text-align:center; padding:60px 0; color:var(--text-3); font-size:13px;'>"
            "Select a company and contact in COMPANIES → INTELLIGENCE first."
            "</div>",
            unsafe_allow_html=True,
        )
    elif not has_permission("can_use_strategist"):
        st.warning("Your role does not have strategist access.")
    else:
        comp = get_company_by_id(company_id)
        ct = get_contact_by_id(contact_id)

        if not comp or not ct:
            st.error("Company or contact not found.")
        else:
            # Quick actions
            q1, q2, q3, q4 = st.columns(4)
            with q1:
                if st.button("Draft LinkedIn", key="qa1"):
                    st.session_state.qa = "Draft a LinkedIn connection request for this contact. Under 300 characters. Specific to research."
            with q2:
                if st.button("Draft Email", key="qa2"):
                    st.session_state.qa = "Draft the initial outreach email with subject line. Ground in research. Human and direct."
            with q3:
                if st.button("Send Time", key="qa3"):
                    st.session_state.qa = "What's the optimal day and time for first outreach? Be specific: day, time, timezone."
            with q4:
                if st.button("Full Sequence", key="qa4"):
                    st.session_state.qa = "Design a 4-touch outreach sequence. Timing, channel, angle per touch. Format each step as ACTION: [details]"

            # Chat display
            for m in st.session_state.chat_messages:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])

            qa = st.session_state.pop("qa", None)
            user_input = st.chat_input("Ask the strategist...")
            prompt = qa or user_input

            if prompt:
                with st.chat_message("user"):
                    st.markdown(prompt)
                save_message(sid, "user", prompt)
                st.session_state.chat_messages.append({"role": "user", "content": prompt, "created_at": datetime.utcnow().isoformat()})

                with st.chat_message("assistant"):
                    with st.spinner("Analyzing..."):
                        response = get_strategist_response(comp, ct, st.session_state.chat_messages, prompt, user["id"], sid)
                        st.markdown(response)

                save_message(sid, "assistant", response)
                st.session_state.chat_messages.append({"role": "assistant", "content": response, "created_at": datetime.utcnow().isoformat()})

                # L2: Update rolling summary
                maybe_update_summary(sid, comp, ct)

                # L3: Extract episodic memories
                extract_episodic_memories(sid, response, comp)

                # Auto-detect actions
                detected = parse_ai_actions(response)
                if detected:
                    st.markdown(section_header(f"DETECTED {len(detected)} ACTION(S)"), unsafe_allow_html=True)
                    for i, da in enumerate(detected):
                        dc1, dc2 = st.columns([3, 1])
                        with dc1:
                            st.markdown(
                                f"<div class='c' style='padding:10px 14px;'>"
                                f"<span class='c-l'>{da['channel']}</span>"
                                f"<div style='font-size:12px; margin-top:3px;'>{da['description'][:200]}</div>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                        with dc2:
                            if st.button("Add to Plan", key=f"aa_{i}"):
                                log_action(sid, user["id"], da["type"], da["channel"], da["description"], "planned", scheduled_at=da.get("scheduled_at"))
                                st.success("Added!")
                                st.rerun()


# ============================================================
# TAB: ACTIONS
# ============================================================

with tab_actions:
    company_id = st.session_state.selected_company_id
    contact_id = st.session_state.selected_contact_id
    sid = st.session_state.session_id

    if not sid:
        st.markdown("<div style='text-align:center; padding:60px 0; color:var(--text-3);'>Select a company and contact first.</div>", unsafe_allow_html=True)
    else:
        comp = get_company_by_id(company_id) or {}
        ct = get_contact_by_id(contact_id) or {}

        st.markdown(section_header(f"ACTIONS — {comp.get('domain', '')} / {ct.get('name', '')}"), unsafe_allow_html=True)

        if has_permission("can_log_outcomes"):
            with st.expander("Log New Action"):
                with st.form("act_form"):
                    af1, af2 = st.columns(2)
                    with af1:
                        atype = st.selectbox("Type", ACTION_TYPES)
                    with af2:
                        achan = st.selectbox("Channel", ACTION_CHANNELS)
                    adesc = st.text_area("Description", placeholder="What happened or what's planned?", height=80)
                    acontent = st.text_area("Content Sent", placeholder="Exact message/email text (optional)", height=60)
                    acontext = st.text_input("Context", placeholder="Why this action? (optional)")
                    af3, af4 = st.columns(2)
                    with af3:
                        astatus = st.selectbox("Status", ACTION_STATUSES)
                    with af4:
                        asched = st.date_input("Scheduled", value=None)

                    if st.form_submit_button("Log Action"):
                        log_action(
                            sid, user["id"], atype, achan, adesc, astatus,
                            content_sent=acontent if acontent else None,
                            context_note=acontext if acontext else None,
                            scheduled_at=asched.isoformat() if asched else None,
                        )
                        # Generate insight from significant outcomes
                        if astatus in ("replied_positive", "replied_negative", "meeting_booked", "deal_closed", "deal_lost", "no_response"):
                            ins = generate_insight_from_outcome(
                                user["id"], sid,
                                {"action_type": atype, "channel": achan, "description": adesc, "status": astatus},
                                comp,
                            )
                            if ins:
                                st.info(f"Insight: {ins}")
                        st.success("Logged")
                        st.rerun()

        # Action list
        actions = get_session_actions(sid)
        if actions:
            for a in actions:
                ac1, ac2 = st.columns([3.5, 1])
                with ac1:
                    sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
                    seq = f" · Step {a['sequence_step']}" if a.get("sequence_step") else ""
                    response_info = ""
                    if a.get("response_content"):
                        response_info = f"<div style='font-size:12px; color:var(--text-2); margin-top:4px; background:#f8f9fa; padding:6px 10px; border-radius:6px;'>Response: {a['response_content'][:200]}</div>"
                    st.markdown(
                        f"<div class='c'>"
                        f"<div style='display:flex; justify-content:space-between; align-items:center;'>"
                        f"<span class='c-l'>{a['action_type']} · {a['channel']}{seq}</span>"
                        f"{badge(a['status'], sc)}"
                        f"</div>"
                        f"<div class='c-v' style='font-size:13px; margin-top:6px;'>{a['description'][:250]}</div>"
                        f"{response_info}"
                        f"<div style='font-size:11px; color:var(--text-3); margin-top:5px;'>{a['created_at'][:16] if a.get('created_at') else ''}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with ac2:
                    if has_permission("can_log_outcomes"):
                        ns = st.selectbox(
                            "Status", ACTION_STATUSES,
                            index=ACTION_STATUSES.index(a["status"]) if a["status"] in ACTION_STATUSES else 0,
                            key=f"as_{a['id']}", label_visibility="collapsed",
                        )
                        if ns != a["status"]:
                            update_action(a["id"], status=ns)
                            if ns in ("replied_positive", "replied_negative", "meeting_booked", "deal_closed", "deal_lost", "no_response"):
                                generate_insight_from_outcome(user["id"], sid, {**a, "status": ns}, comp)
                            st.rerun()
        else:
            st.markdown("<div style='text-align:center; padding:40px; color:var(--text-3); font-size:13px;'>No actions yet.</div>", unsafe_allow_html=True)

        # Export
        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        ec, _ = st.columns([1, 3])
        with ec:
            st.download_button(
                "Export CSV",
                data=export_session_csv(sid, comp.get("domain", "")),
                file_name=f"lh2_{comp.get('domain', 'export')}_{datetime.utcnow().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )


# ============================================================
# TAB: TIMELINE
# ============================================================

with tab_timeline:
    company_id = st.session_state.selected_company_id

    if not company_id:
        st.markdown("<div style='text-align:center; padding:60px 0; color:var(--text-3); font-size:13px;'>Select a company first.</div>", unsafe_allow_html=True)
    else:
        comp = get_company_by_id(company_id) or {}
        contact_id = st.session_state.selected_contact_id

        filter_label = f"OUTREACH TIMELINE — {comp.get('domain', '')}"
        if contact_id:
            ct = get_contact_by_id(contact_id) or {}
            filter_label += f" / {ct.get('name', '')}"

        st.markdown(section_header(filter_label), unsafe_allow_html=True)

        timeline_actions = get_all_actions_for_timeline(
            company_id=company_id,
            contact_id=contact_id if contact_id else None,
            limit=50,
        )

        if timeline_actions:
            for a in timeline_actions:
                date = a.get("created_at", "")[:10] if a.get("created_at") else "—"
                desc = f"{a['action_type']} via {a['channel']}"
                if a.get("contact_name"):
                    desc += f" → {a['contact_name']}"
                desc += f": {a['description'][:150]}"
                if a.get("username"):
                    desc += f" (by {a['username']})"

                sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
                st.markdown(
                    timeline_item(date, desc, a["status"], sc),
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("<div style='text-align:center; padding:40px; color:var(--text-3);'>No timeline entries yet.</div>", unsafe_allow_html=True)


# ============================================================
# TAB: PIPELINE
# ============================================================

with tab_pipeline:
    st.markdown(section_header("CONTACT PIPELINE"), unsafe_allow_html=True)

    all_companies = get_all_companies()

    # Build contact pipeline
    pipeline = {s: [] for s in CONTACT_STATUSES}
    for comp_data in all_companies:
        contacts = get_contacts_for_company(comp_data["id"])
        for ct_data in contacts:
            status = normalize_contact_status(ct_data.get("status", "not_contacted"))
            if status in pipeline:
                pipeline[status].append({
                    "contact": ct_data,
                    "company": comp_data,
                })

    # Metrics
    mcols = st.columns(len(CONTACT_STATUSES))
    for col, status in zip(mcols, CONTACT_STATUSES):
        with col:
            st.markdown(metric_card(len(pipeline[status]), CONTACT_STATUS_LABELS.get(status, status)), unsafe_allow_html=True)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

    # Board
    bcols = st.columns(len(CONTACT_STATUSES))
    for i, status in enumerate(CONTACT_STATUSES):
        with bcols[i]:
            sc = CONTACT_STATUS_COLORS.get(status, "gray")
            st.markdown(f"<div style='text-align:center; margin-bottom:8px;'>{badge(CONTACT_STATUS_LABELS.get(status, status), sc)}</div>", unsafe_allow_html=True)
            items = pipeline[status]
            if items:
                for item in items:
                    ct_data = item["contact"]
                    comp_data = item["company"]
                    score = compute_lead_score(comp_data, ct_data)
                    score_icon = "🟢" if score >= 60 else "🟡" if score >= 35 else "🔴"
                    st.markdown(
                        f"<div class='pc'>"
                        f"<div style='font-size:13px; font-weight:500;'>{ct_data['name']}</div>"
                        f"<div style='font-size:11px; color:var(--text-3); margin-top:2px;'>{comp_data['domain']}</div>"
                        f"<div style='font-size:11px; color:var(--text-2); margin-top:3px;'>{score_icon} {score}/100</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown("<div style='text-align:center; padding:24px 0; font-size:11px; color:#ccc;'>—</div>", unsafe_allow_html=True)


# ============================================================
# TAB: HISTORY
# ============================================================

with tab_history:
    # Global insights
    ginsights = get_all_global_insights(limit=8)
    if ginsights:
        st.markdown(section_header("GLOBAL OUTREACH PATTERNS"), unsafe_allow_html=True)
        for g in ginsights:
            conf_color = "green" if g["confidence_score"] >= 0.7 else "yellow" if g["confidence_score"] >= 0.4 else "gray"
            conf_label = f"conf: {g['confidence_score']:.1f}"
            st.markdown(
                f"<div class='c' style='padding:12px 16px;'>"
                f"<div style='display:flex; justify-content:space-between;'>"
                f"<span style='font-size:13px; color:var(--text);'>{g['insight']}</span>"
                f"{badge(conf_label, conf_color)}"
                f"</div>"
                f"<div style='font-size:10px; color:var(--text-3); margin-top:4px;'>{g['created_at'][:10] if g.get('created_at') else ''}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Analytics
    all_user_actions = get_all_actions(limit=200)
    if all_user_actions:
        st.markdown(section_header("OUTREACH ANALYTICS"), unsafe_allow_html=True)
        total = len(all_user_actions)
        sent = sum(1 for a in all_user_actions if a["status"] in ("sent", "completed", "replied_positive", "replied_negative", "meeting_booked", "deal_closed"))
        positive = sum(1 for a in all_user_actions if a["status"] in ("replied_positive", "meeting_booked", "deal_closed"))
        meetings = sum(1 for a in all_user_actions if a["status"] == "meeting_booked")
        no_resp = sum(1 for a in all_user_actions if a["status"] == "no_response")

        am = st.columns(5)
        metrics = [
            (total, "Total Actions"),
            (sent, "Sent"),
            (f"{(positive / sent * 100):.0f}%" if sent else "—", "Positive Rate"),
            (meetings, "Meetings"),
            (no_resp, "No Response"),
        ]
        for col, (n, l) in zip(am, metrics):
            with col:
                st.markdown(metric_card(n, l), unsafe_allow_html=True)

    # All sessions
    st.markdown(section_header("ALL SESSIONS"), unsafe_allow_html=True)
    sessions = get_sessions_for_user(user["id"])
    if sessions:
        for s in sessions:
            sh1, sh2, sh3 = st.columns([2, 1.5, 1])
            with sh1:
                mem_tag = " · ✓ Memory" if s.get("strategic_summary") else ""
                st.markdown(
                    f"<div class='c'>"
                    f"<div class='c-l'>{s.get('company_domain', '?')}</div>"
                    f"<div class='c-v'>{s.get('contact_name', '?')}{mem_tag}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with sh2:
                mc = get_message_count(s["id"])
                ac = len(get_session_actions(s["id"]))
                st.markdown(
                    f"<div class='c'><div class='c-l'>Activity</div>"
                    f"<div class='c-v' style='font-size:13px;'>{mc} messages · {ac} actions · Score {s['lead_score']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with sh3:
                b1, b2 = st.columns(2)
                with b1:
                    if st.button("Open", key=f"ho_{s['id']}"):
                        st.session_state.selected_company_id = s["company_id"]
                        st.session_state.selected_contact_id = s["contact_id"]
                        st.session_state.session_id = s["id"]
                        st.session_state.chat_messages = load_messages(s["id"])
                        st.rerun()
                with b2:
                    if has_permission("can_delete_sessions"):
                        if st.button("Del", key=f"hd_{s['id']}"):
                            delete_session(s["id"])
                            if st.session_state.session_id == s["id"]:
                                st.session_state.session_id = None
                                st.session_state.chat_messages = []
                            st.rerun()
    else:
        st.markdown("<div style='text-align:center; padding:40px; color:var(--text-3);'>No sessions.</div>", unsafe_allow_html=True)

    # Recent actions
    if all_user_actions:
        st.markdown(section_header("RECENT ACTIONS"), unsafe_allow_html=True)
        for a in all_user_actions[:12]:
            sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
            st.markdown(
                f"<div class='c' style='padding:10px 16px;'>"
                f"<div style='display:flex; justify-content:space-between;'>"
                f"<span class='c-l'>{a.get('company_domain', '?')} · {a['action_type']}</span>"
                f"{badge(a['status'], sc)}"
                f"</div>"
                f"<div style='font-size:12px; margin-top:4px;'>{a['description'][:200]}</div>"
                f"<div style='font-size:10px; color:var(--text-3); margin-top:3px;'>{a['channel']} · {a['created_at'][:16] if a.get('created_at') else ''}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
