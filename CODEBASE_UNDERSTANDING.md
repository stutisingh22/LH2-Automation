# Codebase Understanding — No Changes Made

This document summarizes how the LH2 Command Center codebase is structured and how the Strategist tab (and recipient research) fit in. **No code was modified**; this is read-only understanding.

---

## 1. Entry Point & Tab Structure

- **Main app:** `apps.py` is the single Streamlit entry (run via `streamlit run apps.py`).
- **Tabs:** The app uses `st.tabs()` with 11 tabs:
  - `TAB_NAMES = ["DASHBOARD", "COMPANIES", "INTELLIGENCE", "STRATEGIST", "ACTIONS", "TIMELINE", "CALENDAR", "PIPELINE", "SPOC TRACKING", "HISTORY", "ADVISOR"]`
  - Each tab’s content is **inline** in `apps.py` under `with tabs[N]:`.
- **`pages/` folder:** Contains modules like `pages/strategist.py`, `pages/advisor.py`, etc. **None of these are imported or called from `apps.py`.** The Strategist UI is implemented entirely inside `apps.py` under `with tabs[3]:` (STRATEGIST). So `pages/strategist.py` is currently **dead code** from the app’s perspective.

---

## 2. Session State (Selection & Context)

- **Defaults** (around line 296):  
  `sel_company`, `sel_contact`, `session_id`, `sel_advisor_company_id`, `chat_messages`, `research_cache`, `pending_plan`, `sender_id`, `current_tab`, etc.
- **Company/contact selection:**
  - **COMPANIES tab (tabs[1]):** User picks a company (and optionally a contact). That sets:
    - `st.session_state.sel_company` = company id  
    - `st.session_state.sel_contact` = contact id (or None for “Company level”)  
    - `st.session_state.session_id` = session for that company/contact  
    - Can also switch to `current_tab = "STRATEGIST"` and `st.rerun()`.
  - **Independent vs Media Houses:** Two Notion-backed company lists. “Media Houses” uses advisor companies and shadow companies; selection is stored in `sel_advisor_company_id` and then mapped to `sel_company` / `sel_contact`.
- **`select_contact(comp_id, ct_id)`** (in apps.py): Sets `sel_company`, `sel_contact`, `session_id`, loads `chat_messages`, clears `auto_brief_text` and `pending_plan`, and switches to INTELLIGENCE tab.

---

## 3. STRATEGIST Tab (tabs[3]) — Where It Lives

- **Start:** Around line 1715: `with tabs[3]:  # STRATEGIST`
- **Flow:**
  1. Reads `cid = st.session_state.sel_company`, `ctid = st.session_state.sel_contact`, `sid = st.session_state.session_id`.
  2. If no `cid`: show “Select a company to start”. Else if no permission: show “Strategist access required”.
  3. Else: resolve company and contact:
     - **Advisor path** (`is_advisor_strategist`): company from `get_advisor_company(_aid_s)`, contact from `get_contact(ctid)`.
     - **Normal path:** `comp = get_company(cid)`, `ct = get_contact(ctid)`.
  4. If no contact, a placeholder `ct` dict is used (e.g. name = domain).
  5. **Only if `ct` is truthy** (i.e. we have a contact or placeholder), the rest of the Strategist UI runs:
     - **Research cache key:** `research_cache_key = f"advisor_{_aid_s}"` (Media Houses) or `cid` (Independent).
     - **FROM/TO selectors:** FROM = sender (user id from `get_all_users()`), TO = recipient (contact id, or `None` for “Company level (no contact)”).
     - **Contact context `ct_ctx`:** Copy of `ct` with email/LinkedIn/name/role filled from SPOC data when the contact record is missing them. Used everywhere for display and AI.
     - **db_email / db_linkedin:** `(ct_ctx.get("email") or "").strip()` and same for linkedin — used for enrichment display and for **recipient research** (research only when `db_email` is present).
     - Product selector (Pitching), then “Contact details & enrichment”, then **Recipient Research**, then Quick actions (LinkedIn, Email, Timing, Sequence, Full Plan), then pending plan preview, then chat messages and chat input.

---

## 4. Recipient Research Block (in apps.py)

- **Placement:** After the enrichment section and before “Quick actions + Sequence builder” (around lines 2512–2592).
- **Condition:** Runs only inside the same `if ct:` block as the rest of the strategist content (so only when a contact or placeholder is present).
- **Variables in scope:**  
  `ctid`, `ct_ctx`, `db_email`, `cid`, `comp`, etc. All come from the surrounding Strategist block.
- **Logic:**
  - `research_key = f"recipient_research_{ctid}"`.
  - If `research_key` not in `st.session_state`:  
    - If `db_email`: call `research_recipient(ctid)` and store result in `st.session_state[research_key]`.  
    - Else: set `st.session_state[research_key] = None`.
  - If stored research exists and `status == "completed"`: show expander “📊 Recipient Brief: {name} · Updated {time_ago}” with brief, LinkedIn, Twitter, blog posts, and “🔄 Refresh Research” button (refresh calls `research_recipient(ctid, force_refresh=True)` and reruns).
  - Else if `db_email`: show “💡 Recipient research helps personalize your outreach” and “🔍 Research Recipient” button to trigger research and rerun.
- **AI integration:**  
  `recipient_research_data = st.session_state.get(f"recipient_research_{ctid}")` is passed into:
  - `generate_outreach_plan(..., recipient_research=recipient_research_data)`
  - `get_strategist_response_stream(..., recipient_research=recipient_research_data)`

---

## 5. recipient_research.py (Service Layer)

- **Entry:** `research_recipient(contact_id, force_refresh=False)`.
- **Steps:**
  - `get_contact(contact_id)` from db_ops. If None, return None.
  - If not force_refresh: read cached research from `contact["extra_data"]["recipient_research"]`; if present and not stale (7-day TTL and has `brief`), return it.
  - Else: call `_run_recipient_research_ai(contact)` (Anthropic), then `_save_recipient_research(contact_id, research_data)` which uses `update_contact_extra_data(contact_id, extra_data)` to replace the contact’s `extra_data` with a dict that includes `"recipient_research": research_data`.
- **Data shape:** Expects `research_data` with `linkedin`, `twitter`, `blog_posts`, `brief`, and later `last_updated`, `status`.
- **Dependencies:** `db_ops.get_contact`, `db_ops.update_contact_extra_data` (implemented in db_ops), `anthropic_limiter.create_with_retry`, `config.ANTHROPIC_API_KEY`. Also uses `get_company(company_id)` from db_ops inside the AI step (contact has `company_id` from `_ct_dict`).

---

## 6. db_ops.py

- **get_contact(contact_id):** Returns `_ct_dict(c)` (dict with `id`, `company_id`, `name`, `role`, `email`, `linkedin`, etc.) or None.
- **update_contact_extra_data(contact_id, extra_data):** Replaces the contact’s `extra_data` column with the given dict. Used by recipient_research to persist research.

---

## 7. ai_engine.py

- **Strategist:** `_build_system_prompt(..., recipient_research=None)` and `get_strategist_response_stream(..., recipient_research=None)`, `generate_outreach_plan(..., recipient_research=None)`. When `recipient_research` is provided and has `status == "completed"`, a “RECIPIENT SOCIAL RESEARCH” block is added to the system prompt (brief, LinkedIn activity, Twitter, recent content) so the model can personalize.

---

## 8. ui_helpers.py & apps.py

- **apps.py** imports from `ui_helpers`: `inject_css`, `bdg`, `sh`, `sh_small`, `has_perm`, etc. No recipient-research-specific code was added to ui_helpers; recipient research uses only `st.*` and the existing helpers already used in the Strategist section.

---

## 9. Edge Cases (Understood, No Change)

- **Company level (no contact):** User can choose TO = “Company level (no contact)”. Then `to_id` is None, and `select_contact`/rerun can leave `ctid` as None. The Strategist still builds a placeholder `ct` so `if ct:` is True. Then `research_key = "recipient_research_None"`, and if `db_email` is falsy we set `st.session_state[research_key] = None`; if for some reason `db_email` were truthy we’d call `research_recipient(None)` and get_contact(None) would return None, so we’d get None back and no crash.
- **pages/strategist.py:** Not used by apps.py. Any feature that should appear in the live app must be implemented in the Strategist block inside apps.py, not only in pages/strategist.py.

---

## 10. Summary Table

| What | Where |
|------|--------|
| App entry & tabs | apps.py (single file, no pages.* import for tabs) |
| STRATEGIST UI | apps.py, `with tabs[3]:` (~line 1715 onward) |
| Company/contact selection | apps.py COMPANIES tab + session state |
| Recipient research UI | apps.py, inside Strategist block (~2512–2592) |
| Recipient research logic & API | recipient_research.py |
| Persistence of research | db_ops.update_contact_extra_data + Contact.extra_data |
| AI using research | ai_engine._build_system_prompt, get_strategist_response_stream, generate_outreach_plan |
| pages/strategist.py | Not used by the main app |

---

**No code was changed.** This file is a snapshot of current understanding for reference.
