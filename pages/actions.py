"""Actions page — active sequences, replan, log action, standalone actions, export CSV."""

import re
import streamlit as st
from datetime import datetime

from config import ACTION_TYPES, ACTION_CHANNELS, ACTION_STATUSES, ACTION_STATUS_COLORS
from db_ops import (
    get_company,
    get_contact,
    get_session_actions,
    get_active_sequences,
    update_action,
    log_action,
    delete_action,
)
from ai_engine import replan_on_response, generate_insight_from_outcome, export_session_csv
from gmail_service import parse_email_content
from ui_helpers import sh, bdg, has_perm, render_brief_card_html
from app_helpers import display_original_from_content_sent, render_save_to_gmail, render_ai_refinement


def render():
    user = st.session_state.get("user") or {}
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
                    with st.container():
                        st.markdown("<div class='sequence-step-card'></div>", unsafe_allow_html=True)
                        sc = ACTION_STATUS_COLORS.get(step["status"], "gray")
                        is_done = step["status"] in ("sent", "completed", "replied_positive", "replied_negative", "meeting_booked")
                        icon = "✅" if is_done else "📋"
                        ch_icon = "💬" if "linkedin" in step["channel"].lower() else "📧" if "email" in step["channel"].lower() else "📞"

                        s1, s2, s3 = st.columns([3, 1.2, 0.8])
                        with s1:
                            sched = f" · 📅 {step['scheduled_at'][:10]}" if step.get("scheduled_at") else ""
                            st.markdown(
                                f"<div class='sequence-step-header'>"
                                f"<span>{icon} {ch_icon} <b>Step {step.get('sequence_step','?')}</b>: {step['channel']}{sched}</span>"
                                f"{bdg(step['status'], sc)}</div>"
                                f"<div style='font-size:12px;color:var(--text-2);margin-top:2px;'>{step['description'][:200]}</div>"
                                f"<div class='sequence-step-divider'></div>",
                                unsafe_allow_html=True)
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
                                original_for_display = (step.get("draft_original") or "").strip() or st.session_state.get(orig_stored_key) or display_original_from_content_sent(step.get("content_sent"))
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
                                expander_label = "💬 Read full LinkedIn draft" if "linkedin" in ch_lower else "📧 Read full email"
                                with st.expander(expander_label, expanded=False):
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
                                render_ai_refinement(step, seq["id"], step.get("channel", ""))
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
                            single_draft = display_original_from_content_sent(step["content_sent"]) if "\n\n---\n\n" in (step.get("content_sent") or "") else (step["content_sent"] or "")
                            subj, bod = parse_email_content(single_draft)
                            render_save_to_gmail(ct.get("email", ""), subj, bod, f"act_{step['id']}")

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
