"""Intelligence page — company profile, research document, person lookup, research notes, contacts."""

import inspect
import re
from datetime import datetime
import streamlit as st

from config import CONTACT_STATUS_COLORS, CONTACT_STATUS_LABELS, GOOGLE_SERVICE_ACCOUNT_JSON
from db_ops import get_company, get_contact, get_contacts, merge_company_extra_data, upsert_contact
from notion_sync import create_notion_contact_page
from web_search import search_web_for_person, search_web_for_person_by_name, power_scrape_company_spocs
from research import fetch_research_from_notion, write_research_to_notion, run_research, split_research_into_sections, research_command_ai
from ai_engine import (
    auto_brief,
    research_person_for_company,
    intelligence_request,
    summarize_research_to_context,
    get_local_time,
)
from drive_sync import list_drive_files, create_doc_in_drive
from ui_helpers import sh_small, crd, fmt_v, bdg, has_perm, render_brief_card_html, markdown_to_html
from app_helpers import select_contact, get_current_notion_database_id


def render():
    user = st.session_state.get("user") or {}
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
                    _contact_label = ct.get("name") or "Unknown"
                    _domain = comp.get("domain") or ""
                    st.markdown(sh_small(f"Brief — {_contact_label}" + (f" · {_domain}" if _domain else "")), unsafe_allow_html=True)
                    st.caption("One entry for the brief: optional focus or instruction, then Improve brief.")
                    brief_entry = st.text_input(
                        "Brief",
                        placeholder="e.g. Focus on power mapping · or leave blank for full brief",
                        key=f"brief_entry_{cid}_{ctid}",
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
                                comp, ct, user.get("id"), st.session_state.session_id, rn,
                                contact_web_context=contact_web_ctx,
                                company_spocs_web_context=company_spocs_ctx,
                            )
                            st.session_state.auto_brief_text = brief or "Could not generate brief."
                    st.markdown(render_brief_card_html(st.session_state.auto_brief_text or ""), unsafe_allow_html=True)
                    if st.button("✨ Improve brief", key="rb", help="Regenerate the strategic brief using current research"):
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

            # ── Research document: replicate from Notion or run same as Notion bot; editable; AI commands ──
            st.markdown(sh_small("Research document"), unsafe_allow_html=True)
            extra = comp.get("extra_data") or {}
            research_full_db = (extra.get("research_full") or "").strip()
            if not research_full_db and rn:
                research_full_display = "\n\n".join(
                    f"{'='*60}\n{note.get('title') or 'Section'}\n{'='*60}\n\n{(note.get('content') or '').strip()}"
                    for note in rn
                ).strip()
            else:
                research_full_display = research_full_db

            if not research_full_display and not rn:
                st.caption("No research yet. Use **Run deep research** below (same prompt as the Notion research bot). Saves to Command Center and optionally to Notion.")
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
                        merge_company_extra_data(cid, {"research_full": result, "research_full_updated_at": datetime.utcnow().isoformat()})
                        if page_id:
                            with st.spinner("Writing to Notion…"):
                                write_research_to_notion(page_id, result, comp.get("domain", ""))
                        if cid in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cid]
                        st.success("Research saved. Edit below and Save to store in Command Center.")
                        st.rerun()
                    else:
                        st.error("Research failed." + (f" {err_msg}" if err_msg else " Check ANTHROPIC_API_KEY in keys.env and that the model is available."))
            else:
                st.caption("Edit by section (same structure as Notion) and click Save to store in DB, Drive, and Notion. Use the AI command below to generate or extend.")
                sections = split_research_into_sections(research_full_display)
                section_bodies = []
                for i, (sec_title, body) in enumerate(sections):
                    with st.expander(sec_title, expanded=(i == 0)):
                        edited = st.text_area(
                            "Content",
                            value=body,
                            height=min(180, 120 + body.count("\n") * 18),
                            key=f"research_sec_{cid}_{i}",
                            label_visibility="collapsed",
                            placeholder="(No content for this section.)",
                        )
                        section_bodies.append(edited)
                # Current full document = concatenation of section edits (for AI command and Save)
                current_research_text = "\n\n".join(section_bodies).strip() if section_bodies else research_full_display
                page_id = comp.get("notion_page_id")
                save_col, delete_col, _ = st.columns([1, 1, 3])
                with save_col:
                    if st.button("💾 Save", key=f"save_research_full_{cid}", use_container_width=True, help="Save research to DB, Drive, and Notion"):
                        text = (current_research_text or "").strip()
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
                            if ok_notion and cid in st.session_state.get("research_cache", {}):
                                del st.session_state.research_cache[cid]
                        if not text:
                            st.success("Saved to DB.")
                        elif ok_drive and (not (page_id and len(text) >= 500) or ok_notion):
                            st.success("Saved to DB, Drive, and Notion." if (page_id and len(text) >= 500 and ok_notion) else "Saved to DB and Drive.")
                        else:
                            st.success("Saved to DB." + (" Drive failed." if not ok_drive else " Notion failed."))
                        st.rerun()
                with delete_col:
                    if st.button("🗑️ Delete deep research", key=f"delete_research_{cid}", use_container_width=True, help="Clear deep research from Command Center (DB)"):
                        merge_company_extra_data(cid, {"research_full": "", "research_full_updated_at": datetime.utcnow().isoformat()})
                        st.session_state.auto_brief_text = None
                        if cid in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cid]
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
                        key=f"research_cmd_{cid}",
                        label_visibility="collapsed",
                    )
                with cmd_col2:
                    run_cmd = st.button("Run", key=f"research_cmd_run_{cid}", type="primary", use_container_width=True)
                if run_cmd and (research_cmd or "").strip():
                    with st.spinner("Running command…"):
                        cmd_result = research_command_ai(comp.get("domain", ""), current_research_text or research_full_display, research_cmd.strip())
                    if cmd_result:
                        st.session_state["research_command_result"] = cmd_result
                        st.session_state["research_command_result_cid"] = cid
                        st.session_state["research_command_replace_key"] = f"research_full_edit_{cid}"
                        st.rerun()
                    else:
                        st.error("Command failed.")

                if st.session_state.get("research_command_result_cid") == cid and st.session_state.get("research_command_result"):
                    cmd_result = st.session_state["research_command_result"]
                    with st.expander("AI result — Replace or Append", expanded=True):
                        _cmd_display = cmd_result[:25000] + ("…" if len(cmd_result) > 25000 else "")
                        st.markdown(f"<div class='markdown-rendered' style='max-height:300px;overflow-y:auto;'>{markdown_to_html(_cmd_display)}</div>", unsafe_allow_html=True)
                    app_col1, app_col2, app_col3 = st.columns([1, 1, 2])
                    with app_col1:
                        if st.button("Replace document", key=f"cmd_replace_{cid}", use_container_width=True):
                            merge_company_extra_data(cid, {"research_full": cmd_result, "research_full_updated_at": datetime.utcnow().isoformat()})
                            st.session_state.auto_brief_text = None
                            st.session_state.pop("research_command_result", None)
                            st.session_state.pop("research_command_result_cid", None)
                            st.success("Document replaced.")
                            st.rerun()
                    with app_col2:
                        if st.button("Append to document", key=f"cmd_append_{cid}", use_container_width=True):
                            current = (current_research_text or research_full_display or "").strip()
                            merged = f"{current}\n\n{cmd_result}" if current else cmd_result
                            merge_company_extra_data(cid, {"research_full": merged, "research_full_updated_at": datetime.utcnow().isoformat()})
                            st.session_state.auto_brief_text = None
                            st.session_state.pop("research_command_result", None)
                            st.session_state.pop("research_command_result_cid", None)
                            st.success("Appended.")
                            st.rerun()
                    with app_col3:
                        if st.button("Dismiss", key=f"cmd_dismiss_{cid}", use_container_width=True):
                            st.session_state.pop("research_command_result", None)
                            st.session_state.pop("research_command_result_cid", None)
                            st.rerun()

            st.markdown(sh_small("Request / suggest / look up person"), unsafe_allow_html=True)
            st.caption("Paste a LinkedIn URL, email, or a person's name (or role like 'the editor') to research from the web. Results are always from web search — never from Notion.")
            intel_input = st.text_area(
                "Request",
                placeholder="E.g. linkedin.com/in/... · name@company.com · or 'Mitch Williams' / 'research the editor'",
                height=88,
                key="intel_request",
                label_visibility="collapsed",
            )
            if st.button("Look up person or get suggestion", key="intel_submit", type="secondary", help="Research person from the web (LinkedIn/email/name) or get an AI suggestion"):
                raw = (intel_input or "").strip()
                if not raw:
                    st.warning("Enter a request, LinkedIn URL, email, or person name.")
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
                            _kw = dict(comp=comp, research_notes=rn, linkedin_url=url, web_search_results=web_snippet)
                            brief = research_person_for_company(**_kw)
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
                            _kw = dict(comp=comp, research_notes=rn, email=em, web_search_results=web_snippet)
                            brief = research_person_for_company(**_kw)
                        st.session_state.intel_person_brief = brief
                        st.session_state.intel_suggestion = None
                        st.rerun()
                    else:
                        # Person name or role (e.g. "Mitch Williams", "the editor") — look up from web, then run person research
                        web_snippet = None
                        with st.spinner("Searching the web for this person..."):
                            web_snippet = search_web_for_person_by_name(raw, comp.get("domain"))
                        if web_snippet:
                            with st.spinner("Running AI research on this person..."):
                                _kw = dict(comp=comp, research_notes=rn, person_name=raw, web_search_results=web_snippet)
                                brief = research_person_for_company(**_kw)
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
                                        database_id=get_current_notion_database_id(),
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
                        st.markdown(
                            markdown_to_html((note.get("content") or "")[:6000]),
                            unsafe_allow_html=True,
                        )

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
