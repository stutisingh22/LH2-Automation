"""Strategist page — FROM/TO selectors, enrichment, sequence builder, chat."""

import streamlit as st
from datetime import datetime

from config import ACTION_TYPES
from db_ops import (
    get_company,
    get_contact,
    get_contacts,
    get_spoc_contacts_by_company,
    get_all_companies,
    get_all_users,
    save_message,
    bulk_log_actions,
    update_contact_status,
    merge_company_extra_data,
    get_enrichment_result,
    upsert_contact,
    log_action,
)
from sender_profiles import get_all_sender_profiles
from ai_engine import (
    compute_lead_score,
    get_local_time,
    get_strategist_response_stream,
    generate_outreach_plan,
    maybe_update_summary,
    extract_episodic_memories,
    parse_ai_actions,
)
from signalhire_enrichment import (
    get_credits as get_signalhire_credits,
    request_enrichment,
)
from gmail_service import parse_email_content
from ui_helpers import display_full_name, sh, sh_small, bdg, has_perm
from app_helpers import select_contact, build_ics, render_save_to_gmail


def render():
    user = st.session_state.get("user") or {}
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
            # ── FROM / TO selectors (FROM = sender profiles for planning, TO = company contacts with email) ──
            sel1, sel2, sel3 = st.columns([1.5, 1.5, 1])
            with sel1:
                all_senders = get_all_sender_profiles()
                sender_emails_map = {p["email"]: p["name"] for p in all_senders}
                from_options = [p["email"] for p in all_senders]
                
                # Try to find current sender_id in the list, fallback to first
                current_sender_id = st.session_state.get("sender_id")
                from_index = 0
                if current_sender_id:
                    # Check if current sender_id is an email (new format)
                    if current_sender_id in from_options:
                        from_index = from_options.index(current_sender_id)
                    else:
                        # Old format: sender_id was a user ID, try to find by matching email
                        all_users = get_all_users()
                        old_user = next((u for u in all_users if u["id"] == current_sender_id), None)
                        if old_user and old_user.get("email"):
                            old_email = old_user["email"].strip().lower()
                            if old_email in from_options:
                                from_index = from_options.index(old_email)
                
                sender_email = st.selectbox(
                    "FROM (sender)",
                    options=from_options,
                    format_func=lambda x: f"📧 {sender_emails_map.get(x, '?')}",
                    index=from_index,
                    key="from_sel",
                )
                st.session_state.sender_id = sender_email  # Store email now instead of user ID
                sender_name = sender_emails_map.get(sender_email, "")

            with sel2:
                contacts = get_contacts(cid)
                spoc_contacts = get_spoc_contacts_by_company(cid)
                spoc_email_by_name = {}
                spoc_email_by_parent_id = {}
                for sc in (spoc_contacts or []):
                    em = (sc.get("email") or "").strip()
                    if em:
                        nm = (sc.get("name") or "").strip()
                        if nm and nm not in spoc_email_by_name:
                            spoc_email_by_name[nm] = em
                        pid = sc.get("spoc_contact_id")
                        if pid and pid not in spoc_email_by_parent_id:
                            spoc_email_by_parent_id[pid] = em
                def _recipient_display(c):
                    em = (c.get("email") or "").strip()
                    if not em:
                        em = spoc_email_by_name.get((c.get("name") or "").strip(), "")
                    if not em:
                        em = spoc_email_by_parent_id.get(c["id"], "")
                    return em or f"{c.get('name', '?')} (no email)"
                def _has_email(c):
                    return (c.get("email") or "").strip() or spoc_email_by_name.get((c.get("name") or "").strip()) or spoc_email_by_parent_id.get(c["id"])
                contacts_with_email = [c for c in contacts if _has_email(c)]
                to_options = [c["id"] for c in contacts_with_email] if contacts_with_email else [c["id"] for c in contacts]
                ct_display = {c["id"]: _recipient_display(c) for c in contacts}
                to_index = to_options.index(ctid) if ctid in to_options else 0
                to_id = st.selectbox(
                    "TO (recipient)",
                    options=to_options,
                    format_func=lambda x: f"📨 {ct_display.get(x, '?')}",
                    index=to_index,
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
            
            # ── Sender selection for outreach tone/voice ──
            st.markdown("---")
            from sender_profiles import get_all_sender_profiles, format_sender_display_name
            sender_profiles = get_all_sender_profiles()
            sender_emails = [None] + [p["email"] for p in sender_profiles]
            
            if "selected_sender" not in st.session_state:
                st.session_state.selected_sender = None
            
            sender_col1, sender_col2 = st.columns([2, 1])
            with sender_col1:
                selected_sender_email = st.selectbox(
                    "🎭 Outreach Voice (select sender for AI-generated messages)",
                    options=sender_emails,
                    format_func=lambda x: "Select sender for outreach..." if x is None else format_sender_display_name(
                        next((p for p in sender_profiles if p["email"] == x), None)
                    ),
                    index=sender_emails.index(st.session_state.selected_sender) if st.session_state.selected_sender in sender_emails else 0,
                    key="sender_voice_select",
                    help="Choose who is sending the outreach. This affects the tone, voice, and signature in AI-generated emails and LinkedIn messages."
                )
                st.session_state.selected_sender = selected_sender_email
            
            with sender_col2:
                if st.session_state.selected_sender:
                    selected_profile = next((p for p in sender_profiles if p["email"] == st.session_state.selected_sender), None)
                    if selected_profile:
                        st.markdown(
                            f"<div style='padding:12px 0;'>"
                            f"<div style='font-size:11px;color:var(--text-3);'>{selected_profile.get('voice_type', 'professional').replace('_', ' ').title()}</div>"
                            f"<div style='font-size:10px;color:var(--text-3);margin-top:4px;'>Source: {selected_profile.get('source', 'unknown')}</div></div>",
                            unsafe_allow_html=True)
                else:
                    st.markdown(
                        f"<div style='padding:12px 0;'>"
                        f"<div style='font-size:11px;color:var(--text-warning);'>⚠️ No sender selected</div>"
                        f"<div style='font-size:10px;color:var(--text-3);margin-top:4px;'>Select a sender to generate outreach</div></div>",
                        unsafe_allow_html=True)
            st.markdown("---")

            # ── Contact details & enrichment ──
            st.markdown(sh_small("Contact details & enrichment"), unsafe_allow_html=True)
            enrich_result_key = f"enrich_result_{ctid}"
            db_email = (ct.get("email") or "").strip()
            db_linkedin = (ct.get("linkedin") or "").strip()
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
                            st.success(f'Contact "{add_name.strip()}" added to {get_company(add_cid).get("domain", "company")}.')
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
                            render_save_to_gmail(
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
                        ics = build_ics(plan, title=f"LH2 Outreach — {comp.get('domain','')}")
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
                    for chunk in get_strategist_response_stream(comp, ct, st.session_state.chat_messages, prompt, user["id"], sid, rn, sender_email=st.session_state.selected_sender):
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
