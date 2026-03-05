"""Intelligence page — company profile, research document, person lookup, research notes, contacts."""

import inspect
import re
from datetime import datetime
import streamlit as st

from config import CONTACT_STATUS_COLORS, CONTACT_STATUS_LABELS, GOOGLE_SERVICE_ACCOUNT_JSON, normalize_contact_status
from db_ops import get_company, get_contact, get_contacts, merge_company_extra_data, upsert_contact
from notion_sync import create_notion_contact_page
from research import (
    fetch_research_from_notion,
    run_research,
    research_command_ai,
    split_research_into_sections,
    get_next_section_number,
    format_appended_section,
)
from ai_engine import (
    auto_brief,
    research_person_for_company,
    intelligence_request,
    summarize_research_to_context,
    get_local_time,
)
from drive_sync import list_drive_files, create_doc_in_drive
from ui_helpers import sh_small, crd, fmt_v, bdg, has_perm, render_research_with_proper_tables, research_display_to_editor_html, is_html_content, html_to_plain_text, markdown_to_html, wysiwyg_editor_html_page
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
                    st.markdown(sh_small(f"Brief — {ct['name']}"), unsafe_allow_html=True)
                    st.caption("One entry for the brief: optional focus or instruction, then Improve brief.")
                    brief_entry = st.text_input(
                        "Brief",
                        placeholder="e.g. Focus on power mapping · or leave blank for full brief",
                        key=f"brief_entry_{cid}_{ctid}",
                        label_visibility="collapsed",
                    )
                    # Load saved brief from DB first; only generate if nothing saved yet.
                    if st.session_state.auto_brief_text is None:
                        extra = comp.get("extra_data") or {}
                        briefs = extra.get("briefs") or {}
                        saved_brief = ((briefs.get(str(ctid)) or {}).get("content") or "").strip()
                        if not saved_brief:
                            saved_brief = (extra.get("brief") or "").strip()
                        if saved_brief:
                            st.session_state.auto_brief_text = saved_brief
                        else:
                            with st.spinner("Generating brief..."):
                                brief = auto_brief(comp, ct, user.get("id"), st.session_state.session_id, rn)
                                st.session_state.auto_brief_text = brief or "Could not generate brief."
                    st.markdown("<div class='brief-card'>" + render_research_with_proper_tables(st.session_state.auto_brief_text or "") + "</div>", unsafe_allow_html=True)
                    brief_btn1, brief_btn2 = st.columns(2)
                    with brief_btn1:
                        if st.button("✨ Improve brief", key="rb", help="Regenerate the strategic brief using current research"):
                            st.session_state.auto_brief_text = None
                            st.rerun()
                    with brief_btn2:
                        if st.button("💾 Save", key="brief_save_all_intel", help="Save brief to DB, Notion, and Drive"):
                            _brief = (st.session_state.auto_brief_text or "").strip()
                            if not _brief:
                                st.warning("No brief to save.")
                            else:
                                merge_company_extra_data(cid, {"brief": _brief, "brief_updated_at": datetime.utcnow().isoformat()})
                                _doc_name = comp.get("domain") or comp.get("name", "Company")
                                fid, _, err_drive = create_doc_in_drive(f"{_doc_name} - Brief", _brief)
                                if fid:
                                    st.toast("Brief saved to DB and Drive.")
                                else:
                                    st.toast("Brief saved to DB." + (" Drive: " + (err_drive or "failed") if err_drive else ""))
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
                st.caption("No research yet. Use **Run deep research** below (same prompt as the Notion research bot). Saves to Command Center only until you click **Save** (DB + Notion + Drive).")
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
                        if cid in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cid]
                        st.success("Research saved to Command Center. Edit below if needed, then click **Save** to write to DB, Notion, and Drive.")
                        st.rerun()
                    else:
                        st.error("Research failed." + (f" {err_msg}" if err_msg else " Check ANTHROPIC_API_KEY in keys.env and that the model is available."))
            else:
                st.caption("Edit in place (or by section below); Save writes to DB and Drive only.")
                if has_perm("can_use_strategist") and st.button("🔬 Re-run deep research", key=f"rerun_res_doc_{cid}", use_container_width=False, help="Re-run with latest prompts and signals; overwrites current research"):
                    err_msg = None
                    result = None
                    try:
                        pb = st.progress(0, text="Re-running deep research…")
                        def _prog(c, e):
                            pb.progress(min(c / 40000, 0.95), text=f"Researching… {c:,} chars")
                        result = run_research(comp.get("domain", ""), progress_callback=_prog)
                    except Exception as e:
                        err_msg = str(e)
                    if result:
                        merge_company_extra_data(cid, {"research_full": result, "research_full_updated_at": datetime.utcnow().isoformat()})
                        if cid in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cid]
                        st.session_state.auto_brief_text = None
                        st.session_state.pop(f"research_wysiwyg_html_{cid}", None)
                        st.success("Research re-run complete. Edit below if needed, then **Save** to write to DB and Drive.")
                        st.rerun()
                    else:
                        st.error("Research failed." + (f" {err_msg}" if err_msg else " Check ANTHROPIC_API_KEY in keys.env and that the model is available."))
                editor_html = research_display_to_editor_html(research_full_display)
                use_wysiwyg = False
                try:
                    from streamlit_iframe_event import st_iframe_event
                    use_wysiwyg = len(editor_html or "") < 150000
                except Exception:
                    pass
                wysiwyg_key = f"research_wysiwyg_html_{cid}"
                if use_wysiwyg:
                    initial_for_editor = st.session_state.get(wysiwyg_key) or editor_html or ""
                    editor_page = wysiwyg_editor_html_page(initial_for_editor)
                    import urllib.parse
                    data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(editor_page)
                    event_value = st_iframe_event(data_url, key=f"wysiwyg_editor_{cid}", default_width="100%")
                    if event_value is not None:
                        st.session_state[wysiwyg_key] = event_value
                        st.rerun()
                    current_research_text = (st.session_state.get(wysiwyg_key) or editor_html or "").strip() or research_full_display
                    st.caption("Edit above, then click **Done editing** in the frame. Use **Save** below to write to DB and Drive.")
                else:
                    plain_for_split = html_to_plain_text(research_full_display) if is_html_content(research_full_display) else research_full_display
                    sections = split_research_into_sections(plain_for_split)
                    section_edits = []
                    for i, (sec_title, body) in enumerate(sections):
                        section_html = research_display_to_editor_html(body) if body else ""
                        with st.expander(sec_title, expanded=(i == 0)):
                            st.markdown("<div class='research-doc-view'>" + (section_html or "(No content)") + "</div>", unsafe_allow_html=True)
                            with st.expander("Edit as HTML", expanded=False):
                                edited = st.text_area(
                                    "HTML",
                                    value=section_html or "",
                                    height=min(220, 120 + (section_html or "").count("\n") * 12),
                                    key=f"research_sec_{cid}_{i}",
                                    label_visibility="collapsed",
                                    placeholder="<p>Edit HTML for this section.</p>",
                                )
                            section_edits.append((edited or "").strip() or section_html or "")
                    current_research_text = "\n\n".join(
                        f"<h2>{sec_title}</h2>\n\n{html}" for (sec_title, _), html in zip(sections, section_edits)
                    ).strip() if section_edits else research_full_display
                if st.button("💾 Save", key=f"save_research_all_{cid}", use_container_width=True, help="Save research to DB and Drive (proper format with tables)"):
                    _research = (current_research_text or "").strip()
                    if not _research:
                        st.warning("No research to save.")
                    else:
                        merge_company_extra_data(cid, {"research_full": _research, "research_full_updated_at": datetime.utcnow().isoformat()})
                        _doc_name = comp.get("domain") or comp.get("name", "Company")
                        fid, _, err_drive = create_doc_in_drive(f"{_doc_name} - Research", _research)
                        if cid in st.session_state.get("research_cache", {}):
                            del st.session_state.research_cache[cid]
                        st.session_state.auto_brief_text = None
                        st.session_state.pop(wysiwyg_key, None)
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
                        key=f"research_cmd_{cid}",
                        label_visibility="collapsed",
                    )
                with cmd_col2:
                    run_cmd = st.button("Run", key=f"research_cmd_run_{cid}", type="primary", use_container_width=True)
                if run_cmd and (research_cmd or "").strip():
                    with st.spinner("Running command…"):
                        current_for_ai = (html_to_plain_text(current_research_text) if is_html_content(current_research_text) else current_research_text) or research_full_display
                        cmd_result = research_command_ai(comp.get("domain", ""), current_for_ai, research_cmd.strip())
                    if cmd_result:
                        st.session_state["research_command_result"] = cmd_result
                        st.session_state["research_command_result_cid"] = cid
                        st.session_state["research_command_replace_key"] = f"research_full_edit_{cid}"
                        st.rerun()
                    else:
                        st.error("Command failed.")

                if st.session_state.get("research_command_result_cid") == cid and st.session_state.get("research_command_result"):
                    cmd_result = st.session_state["research_command_result"]
                    cmd_title = cmd_result.get("title", "Extended research") if isinstance(cmd_result, dict) else "Extended research"
                    cmd_content = cmd_result.get("content", cmd_result) if isinstance(cmd_result, dict) else (cmd_result or "")
                    with st.expander(f"{cmd_title} — Replace or Append", expanded=True):
                        st.caption("Use **Append to document** to add this as the next numbered section (8., 9., …).")
                        st.markdown(f"<div style='font-size:12px;white-space:pre-wrap;max-height:300px;overflow-y:auto;'>{cmd_content[:25000]}{'…' if len(cmd_content) > 25000 else ''}</div>", unsafe_allow_html=True)
                    app_col1, app_col2, app_col3 = st.columns([1, 1, 2])
                    with app_col1:
                        if st.button("Replace document", key=f"cmd_replace_{cid}", use_container_width=True):
                            to_save = markdown_to_html(cmd_content) if cmd_content and not is_html_content(cmd_content) else (cmd_content or "")
                            merge_company_extra_data(cid, {"research_full": to_save, "research_full_updated_at": datetime.utcnow().isoformat()})
                            st.session_state.auto_brief_text = None
                            st.session_state.pop(wysiwyg_key, None)
                            st.session_state.pop("research_command_result", None)
                            st.session_state.pop("research_command_result_cid", None)
                            st.success("Document replaced.")
                            st.rerun()
                    with app_col2:
                        if st.button("Append to document", key=f"cmd_append_{cid}", use_container_width=True):
                            current = (current_research_text or research_full_display or "").strip()
                            plain_current = html_to_plain_text(current) if is_html_content(current) else current
                            next_num = get_next_section_number(plain_current or "")
                            new_block = format_appended_section(cmd_content, cmd_title, next_num)
                            merged = f"{current}\n\n{new_block}" if current else new_block
                            merge_company_extra_data(cid, {"research_full": merged, "research_full_updated_at": datetime.utcnow().isoformat()})
                            try:
                                from services.ui_data_loader import clear_ui_cache
                                clear_ui_cache()
                            except Exception:
                                pass
                            st.session_state.auto_brief_text = None
                            st.session_state.pop(wysiwyg_key, None)
                            st.session_state.pop("research_command_result", None)
                            st.session_state.pop("research_command_result_cid", None)
                            st.success(f"Appended as section {next_num}.")
                            st.rerun()
                    with app_col3:
                        if st.button("Dismiss", key=f"cmd_dismiss_{cid}", use_container_width=True):
                            st.session_state.pop("research_command_result", None)
                            st.session_state.pop("research_command_result_cid", None)
                            st.rerun()

            st.markdown(sh_small("Request / suggest / look up person"), unsafe_allow_html=True)
            st.caption("Paste a LinkedIn URL, email, or type a person's name to get an AI research brief. Or describe what's missing for an AI suggestion.")
            intel_input = st.text_area(
                "Request",
                placeholder="E.g. linkedin.com/in/... · name@company.com · or 'Mitch Williams' / 'research the editor'",
                height=88,
                key="intel_request",
                label_visibility="collapsed",
            )
            if st.button("Look up person or get suggestion", key="intel_submit", type="secondary", help="Look up person (name, LinkedIn, or email) for AI research brief, or get an AI suggestion"):
                raw = (intel_input or "").strip()
                if not raw:
                    st.warning("Enter a request, LinkedIn URL, email, or person name.")
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
                            f"<div style='font-size:13px;white-space:pre-wrap;font-family:monospace;'>{(note.get('content') or '')[:6000]}</div>",
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
