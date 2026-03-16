"""
Strategist Add to Action — Use latest chat draft as new sequence or overwrite a step.

When the user is in the Strategist chat and the AI has generated a draft (latest
assistant message), this module provides an "Add to Action" flow similar to
"Add Full Plan to Timeline": create a new sequence with this draft, or overwrite
a specific email/step in an existing sequence.

Also adds deck links, portfolio photo HTML, and Save to Gmail for email drafts
(same behavior as Full Plan) so the latest draft can be viewed with links and
saved to Gmail with injection applied.
"""

import re
import streamlit as st

from db_ops import (
    bulk_log_actions,
    get_active_sequences,
    get_sequence_actions,
    update_action,
    get_all_users,
    log_action,
)
from integrations import parse_email_content, create_gmail_draft
from services.deck_links import inject_deck_links_into_email_body
from services.ui_data_loader import clear_ui_cache
from ui_helpers import get_add_to_action_labels, display_full_name


def get_cleaned_draft_for_save(draft_content):
    """
    Return draft content with headers, footer, platform notes, and content after
    the signature (e.g. IMPROVEMENTS MADE:) stripped. Use for Save to Gmail,
    expander display, Add to Action, and Overwrite. Returns full draft text
    (Subject: ... + body) when subject is present, else body only.
    """
    if not (draft_content or "").strip():
        return draft_content or ""
    draft_plain = _markdown_to_plain(draft_content)
    subj, bod = parse_email_content(draft_plain)
    bod, extracted_subj = _strip_strategist_draft_headers_and_footer(bod or "")
    if extracted_subj is not None:
        subj = extracted_subj
    bod = _strip_platform_note_lines(bod or "")
    bod = _strip_content_after_signature(bod or "")
    if (subj or "").strip():
        return (f"Subject: {subj.strip()}\n\n{bod or ''}").strip()
    return (bod or "").strip()


def _extract_email_num_from_description(desc):
    """
    Try to extract an 'Email N' number from a step description.
    Returns int or None.
    """
    if not desc:
        return None
    m = re.search(r"Email\s+(\d+)", str(desc), re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _compute_email_numbers_for_sequence(steps):
    """
    For a given list of action dicts (steps in one sequence), compute email numbers
    purely for display, without changing stored data.

    Strategy:
    - Consider only steps where channel looks like Email.
    - Sort those email steps by sequence_step (fallback to id order).
    - Assign Email 1, Email 2, ... in that sorted order.

    Returns:
        email_num_by_id: {action_id: email_number}
    """
    if not steps:
        return {}
    email_steps = []
    for s in steps:
        ch = (s.get("channel") or "").lower()
        if "email" in ch:
            email_steps.append(s)
    if not email_steps:
        return {}
    # Sort by sequence_step, then id for stability
    def _key(s):
        try:
            return (int(s.get("sequence_step") or 0), str(s.get("id") or ""))
        except Exception:
            return (0, str(s.get("id") or ""))
    email_steps_sorted = sorted(email_steps, key=_key)
    email_num_by_id = {}
    n = 1
    for s in email_steps_sorted:
        sid = s.get("id")
        if sid is None:
            continue
        email_num_by_id[sid] = n
        n += 1
    return email_num_by_id

def get_latest_assistant_draft_content(chat_messages):
    """
    Return the content of the latest assistant message, if any.
    That message is treated as the "latest draft" for Add to Action.
    """
    if not chat_messages:
        return None
    last = chat_messages[-1]
    if last.get("role") != "assistant":
        return None
    content = (last.get("content") or "").strip()
    return content if content else None


def parse_multi_email_sequence(draft_content):
    """
    If the draft contains multiple emails (e.g. "Email 1 — ...", "Email 2 — ..."),
    split into a list of steps. Returns list of dicts: {step, email_num, content_draft, description}.
    step is 2 for Email 1, 3 for Email 2, etc. (for deck_links step_index). Empty list if single or no emails.
    """
    if not (draft_content or "").strip():
        return []
    text = (draft_content or "").strip()
    # Match line that starts with optional ** then "Email N —" or "Email N -"
    pattern = re.compile(r"^(?:\*\*)?\s*Email\s+(\d+)\s*[—\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
    matches = list(pattern.finditer(text))
    if len(matches) < 2:
        return []
    steps = []
    for i, m in enumerate(matches):
        email_num = int(m.group(1))
        if email_num < 1 or email_num > 5:
            continue
        # step_index for deck_links: Email 1 = 2, Email 2 = 3, ...
        step = email_num + 1
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        desc_line = m.group(0).strip()
        # Strip leading ** if present for description
        description = re.sub(r"^\*\*\s*|\s*\*\*$", "", desc_line)
        steps.append({
            "step": step,
            "email_num": email_num,
            "content_draft": block,
            "description": description,
        })
    return steps


def add_draft_as_new_sequence(session_id, sender_id, draft_content, channel="Email", description=None):
    """
    Create a new sequence with a single step containing the draft.
    Returns (list of action ids, sequence_id).
    """
    if not draft_content or not session_id or not sender_id:
        return [], None
    desc = description or f"Step 1: {channel} draft from strategist"
    actions_list = [
        {
            "action_type": "Outreach",
            "channel": channel,
            "description": desc,
            "status": "planned",
            "content_sent": draft_content,
            "context_note": None,
        }
    ]
    ids, seq_id = bulk_log_actions(session_id, sender_id, actions_list)
    return ids, seq_id


def overwrite_sequence_step(action_id, draft_content):
    """Overwrite the content_sent of an existing action (sequence step)."""
    if not action_id or not draft_content:
        return False
    update_action(
        action_id,
        content_sent=draft_content.strip(),
        draft_revisions=[],
        draft_original="",
    )
    return True


def add_step_to_existing_sequence(session_id, sender_id, seq_id, draft_content, channel="Email", description=None, email_num=None):
    """
    Append a new planned step to an existing sequence, preserving existing steps and
    assigning the next available sequence_step.
    Returns the created action_id, or None on failure.
    """
    if not draft_content or not session_id or not sender_id or not seq_id:
        return None
    steps = get_sequence_actions(session_id, seq_id) or []
    next_step = 1
    if steps:
        try:
            next_step = max(int(s.get("sequence_step") or 0) for s in steps) + 1
        except Exception:
            next_step = (len(steps) or 0) + 1
    if description:
        desc = description
    elif email_num is not None and (channel or "").lower().startswith("email"):
        desc = f"Email {email_num} — {channel} draft from Strategist"
    else:
        desc = f"Step {next_step}: {channel} draft from Strategist"
    cleaned = get_cleaned_draft_for_save(draft_content)
    content = (cleaned or draft_content or "").strip()
    if not content:
        return None

    # Try to infer a reasonable scheduled_at for the new step when we know its email number
    scheduled_str = None
    if email_num is not None:
        try:
            # Map existing actions by email number using description prefix "Email N"
            email_date_by_num = {}
            for s in steps:
                desc_existing = s.get("description") or ""
                num = _extract_email_num_from_description(desc_existing)
                sched = s.get("scheduled_at")
                if num is not None and sched:
                    # scheduled_at from db_ops is isoformat string
                    email_date_by_num[num] = datetime.fromisoformat(str(sched))

            if email_date_by_num:
                from datetime import timedelta
                target = email_num
                lower_nums = [n for n in email_date_by_num.keys() if n < target]
                higher_nums = [n for n in email_date_by_num.keys() if n > target]
                dt = None
                if lower_nums and higher_nums:
                    n_lo = max(lower_nums)
                    n_hi = min(higher_nums)
                    d_lo = email_date_by_num[n_lo]
                    d_hi = email_date_by_num[n_hi]
                    # Midpoint between previous and next emails
                    delta = d_hi - d_lo
                    dt = d_lo + delta / 2 if delta.total_seconds() > 0 else d_lo
                elif lower_nums:
                    n_lo = max(lower_nums)
                    d_lo = email_date_by_num[n_lo]
                    # Place a bit after the previous email
                    dt = d_lo + timedelta(days=2)
                elif higher_nums:
                    n_hi = min(higher_nums)
                    d_hi = email_date_by_num[n_hi]
                    # Place a bit before the next email
                    dt = d_hi - timedelta(days=2)

                if dt:
                    scheduled_str = dt.isoformat()
        except Exception:
            scheduled_str = None

    action_id = log_action(
        session_id,
        sender_id,
        "Outreach",
        channel,
        desc,
        status="planned",
        content_sent=content,
        context_note=None,
        scheduled_at=scheduled_str,
        seq_id=seq_id,
        seq_step=next_step,
    )
    return action_id

def add_sequence_to_timeline(session_id, sender_id, steps):
    """
    Create one sequence with multiple steps (from parse_multi_email_sequence).
    steps: list of dicts with content_draft, description. Each content_draft is cleaned before save.
    Returns (list of action ids, sequence_id) or ([], None).
    """
    if not steps or not session_id or not sender_id:
        return [], None
    actions_list = []
    for s in steps:
        cleaned = get_cleaned_draft_for_save(s.get("content_draft") or "")
        actions_list.append({
            "action_type": "Outreach",
            "channel": "Email",
            "description": s.get("description") or f"Email {s.get('email_num', '?')}",
            "status": "planned",
            "content_sent": cleaned or (s.get("content_draft") or "").strip(),
            "context_note": None,
        })
    if not actions_list:
        return [], None
    return bulk_log_actions(session_id, sender_id, actions_list)


def _strip_strategist_draft_headers_and_footer(body):
    """
    Remove from body: (1) leading 'Email N — ...' and 'Subject: ...' lines so expansion
    doesn't show them; (2) trailing bullet list of deck names (not required footer).
    Returns (cleaned_body, extracted_subject_or_none). If subject was found in the
    leading block, second value is that subject; else None (caller keeps existing subject).
    """
    import re
    if not (body or "").strip():
        return body or "", None
    lines = (body or "").strip().split("\n")
    extracted_subject = None
    start = 0
    i = 0
    while i < len(lines):
        s = (lines[i] or "").strip()
        if re.match(r"^Email\s+\d+\s*—\s*.+", s, re.IGNORECASE):
            start = i + 1
            i += 1
            continue
        if s.lower().startswith("subject:") and extracted_subject is None:
            rest = s.split(":", 1)[-1].strip()
            if rest:
                extracted_subject = rest
            else:
                for j in range(i + 1, len(lines)):
                    next_line = (lines[j] or "").strip()
                    if next_line and not next_line.lower().startswith("subject:"):
                        extracted_subject = next_line
                        i = j
                        break
            start = i + 1
            i += 1
            continue
        if not s:
            start = i + 1
            i += 1
            continue
        start = i
        break
    body_lines = lines[start:]
    # Remove trailing footer: bullet lines like "- LH2 Accelerate Deck", "- Case Studies Deck"
    while body_lines:
        last = (body_lines[-1] or "").strip()
        if re.match(r"^-\s+.+", last) and (
            "deck" in last.lower() or "case study" in last.lower() or "portfolio" in last.lower()
        ):
            body_lines.pop()
            continue
        break
    # Remove trailing blank lines after footer
    while body_lines and not (body_lines[-1] or "").strip():
        body_lines.pop()
    return "\n".join(body_lines).strip(), extracted_subject


def _strip_platform_note_lines(body):
    """Remove 'Note: ... portfolio image ... saved to Gmail / deck links ...' so it never appears in the draft."""
    import re
    if not (body or "").strip():
        return body or ""
    lines = (body or "").split("\n")
    out = []
    for line in lines:
        s = (line or "").strip()
        if re.match(r"^Note\s*:\s*", s, re.IGNORECASE) and (
            "automatically appear" in s.lower()
            or "deck links" in s.lower()
            or "saved to gmail" in s.lower()
            or "portfolio numbers image" in s.lower()
        ):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _strip_content_after_signature(body):
    """
    Strip anything after the email sign-off (Best, / Cheers, / Thanks, etc.) and signature lines.
    Removes meta sections like "IMPROVEMENTS MADE:", "SUMMARY:", numbered lists, etc., so they
    never appear in the expander, Save to Gmail, or Add to Action/Timeline.
    """
    import re
    if not (body or "").strip():
        return body or ""
    lines = (body or "").split("\n")
    sign_offs = ("best,", "cheers,", "thanks,", "regards,", "sincerely,", "kind regards,")
    meta_starters = (
        "improvements made",
        "improvements:",
        "summary:",
        "summary ",
        "note:",
        "---",
    )
    out = []
    seen_sign_off = False
    signature_lines_left = 5
    i = 0
    while i < len(lines):
        line = lines[i]
        s = (line or "").strip()
        if not seen_sign_off:
            if s.lower().rstrip(",").strip() in sign_offs or (
                len(s) < 20
                and s.lower().startswith(("best", "cheers", "thanks", "regards", "sincerely", "kind regards"))
                and (s.endswith(",") or not s.endswith("."))
            ):
                seen_sign_off = True
                out.append(line)
                signature_lines_left = 5
                i += 1
                continue
            out.append(line)
            i += 1
            continue
        # Past sign-off: keep only blank or short signature lines (name, company)
        if not s:
            out.append(line)
            i += 1
            continue
        if len(s) >= 60:
            # Long line after sign-off = likely meta section start; strip from here
            break
        s_lower = s.lower()
        if any(s_lower.startswith(m) for m in meta_starters) or re.match(r"^\d+\.\s", s):
            break
        if signature_lines_left > 0:
            out.append(line)
            signature_lines_left -= 1
            i += 1
            continue
        # Extra short line after we've used signature allowance — still keep if it looks like name/company
        if len(s) < 40:
            out.append(line)
        i += 1
    return "\n".join(out).strip()


def _markdown_to_plain(text):
    """Strip Markdown so draft saves as clean plain text (no ** or ### in Gmail)."""
    import re
    if not (text or "").strip():
        return text or ""
    t = text
    # Bold: **x** or __x__ -> x
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"__([^_]+)__", r"\1", t)
    # Italic: *x* or _x_ -> x (single; avoid breaking **)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", t)
    t = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", t)
    # Headers: ### x -> x
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    # Horizontal rule
    t = re.sub(r"^\s*---\s*$", "", t, flags=re.MULTILINE)
    return t.strip()


def _render_save_to_gmail_chat(to_email, subject, body, draft_id, product_key=None, step_index=None, lead_brand=None):
    """Save to Gmail UI for strategist chat (no app_helpers dependency). Deck links + photo applied."""
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
            body_plain = _markdown_to_plain(body or "")
            body_html = None
            try:
                body_html = inject_deck_links_into_email_body(
                    body_plain, product_key=product_key, step_index=step_index, lead_brand=lead_brand
                )
                if not (body_html or "").strip():
                    body_html = None
            except Exception:
                body_html = None
            subject_plain = _markdown_to_plain(subject or "")
            out = create_gmail_draft(
                gmail_sel, to_email or "", subject_plain, body_plain, body_html=body_html
            )
            if out.get("success"):
                st.success("Draft saved to Gmail.")
            else:
                st.error(out.get("error") or "Authentication required.")
    st.markdown("</div>", unsafe_allow_html=True)


def _draft_parses_as_email(draft_content):
    """True if draft looks like an email (has Subject: and/or body)."""
    if not (draft_content or "").strip():
        return False
    subj, bod = parse_email_content(draft_content)
    return bool((subj or "").strip() or (bod or "").strip())


def render_add_to_action_ui(
    session_id,
    sender_id,
    chat_messages,
    key_suffix="last",
    product_key=None,
    step_index=5,
    to_email=None,
    lead_brand=None,
    save_to_gmail_fn=None,
):
    """
    Render "Add to Action" for the Strategist chat: show a button and flow only when
    the latest message is from the assistant (the latest draft). User can either
    create a new sequence with this draft or overwrite a step in an existing sequence.

    When product_key and/or to_email are provided and the draft parses as an email,
    also shows "Read full message" (with deck links + portfolio photo HTML) and
    "Save to Gmail". If save_to_gmail_fn is provided (e.g. apps._render_save_to_gmail),
    that function is used so behavior matches Full Plan exactly; otherwise uses inline UI.

    key_suffix: unique suffix for widget keys so the same UI can be rendered in multiple
    places (e.g. in the message loop vs after streaming) without duplicate key errors.
    """
    labels = get_add_to_action_labels()
    draft_content = get_latest_assistant_draft_content(chat_messages)
    if not draft_content:
        return

    k = key_suffix  # short alias for key suffix
    multi_steps = parse_multi_email_sequence(draft_content)
    has_context = bool(product_key or (to_email or "").strip())

    # Multi-email sequence (3, 4, or 5 emails): render a block per email like Full Plan
    if len(multi_steps) >= 2 and has_context:
        st.markdown(
            "<div style='background:var(--accent-bg);border:1px solid #c7d2fe;border-radius:var(--radius);"
            "padding:16px;margin-bottom:12px;'>",
            unsafe_allow_html=True,
        )
        for step in multi_steps:
            with st.container():
                st.markdown("<div class='sequence-step-card'></div>", unsafe_allow_html=True)
                content_draft = (step.get("content_draft") or "").strip()
                step_num = step.get("step", 2)
                desc = step.get("description") or f"Email {step.get('email_num', '?')}"
                preview_len = 80
                st.markdown(
                    f"<div class='sequence-step-header'>"
                    f"<span style='font-size:13px;font-weight:600;'>"
                    f"📧 Step {step_num}: {desc[:60]}{'...' if len(desc) > 60 else ''}</span></div>"
                    f"<div class='sequence-step-divider'></div>",
                    unsafe_allow_html=True,
                )
                if content_draft:
                    draft_plain = _markdown_to_plain(content_draft)
                    subj, bod = parse_email_content(draft_plain)
                    bod, extracted_subj = _strip_strategist_draft_headers_and_footer(bod or "")
                    if extracted_subj is not None:
                        subj = extracted_subj
                    bod = _strip_platform_note_lines(bod or "")
                    bod = _strip_content_after_signature(bod or "")
                    bod_plain = _markdown_to_plain(bod or "")
                    try:
                        bod_html = inject_deck_links_into_email_body(
                            bod_plain,
                            product_key=product_key,
                            step_index=step_num,
                            lead_brand=lead_brand,
                        )
                    except Exception:
                        bod_html = (bod_plain or "").replace("\n", "<br>\n")
                    with st.expander(
                        f"📄 Read full message" + (f" — {content_draft[:preview_len]}..." if len(content_draft) > preview_len else ""),
                        expanded=False,
                        key=f"strategist_read_full_{k}_step{step_num}",
                    ):
                        subj_display = _markdown_to_plain(subj or "") or "(none)"
                        st.markdown(
                            "<div style='font-size:12px;background:#fff;padding:10px 12px;border-radius:6px;border:1px solid #e5e7eb;'>"
                            f"<div style='white-space:pre-wrap;'><b>Subject:</b> {subj_display}</div>"
                            "<div style='height:8px;'></div>"
                            f"<div style='white-space:normal;'>{bod_html}</div>"
                            "</div>",
                            unsafe_allow_html=True,
                        )
                    if save_to_gmail_fn is not None:
                        save_to_gmail_fn(
                            to_email or "",
                            subj or "",
                            bod or "",
                            f"strategist_chat_{k}_step{step_num}",
                            product_key=product_key,
                            step_index=step_num,
                            lead_brand=lead_brand,
                        )
                    else:
                        _render_save_to_gmail_chat(
                            to_email or "",
                            subj or "",
                            bod or "",
                            draft_id=f"strategist_chat_{k}_step{step_num}",
                            product_key=product_key,
                            step_index=step_num,
                            lead_brand=lead_brand,
                        )
        if st.button("✅ Add full sequence to Timeline", key=f"add_full_seq_{k}", type="primary", use_container_width=True):
            ids, seq_id = add_sequence_to_timeline(session_id, sender_id, multi_steps)
            if ids and seq_id:
                clear_ui_cache()
                st.session_state[f"strategist_add_to_action_open_{k}"] = False
                st.success(f"Added {len(ids)} emails as sequence {seq_id}.")
                st.rerun()
            else:
                st.error(labels["error_generic"])
        st.markdown("</div>", unsafe_allow_html=True)

    # Single-email: Read full message (deck links + photo) and Save to Gmail
    elif _draft_parses_as_email(draft_content) and has_context:
        draft_plain_for_parse = _markdown_to_plain(draft_content)
        subj, bod = parse_email_content(draft_plain_for_parse)
        bod, extracted_subj = _strip_strategist_draft_headers_and_footer(bod or "")
        if extracted_subj is not None:
            subj = extracted_subj
        bod = _strip_platform_note_lines(bod or "")
        bod = _strip_content_after_signature(bod or "")
        bod_plain = _markdown_to_plain(bod or "")

        step_options = [
            labels.get("step_email_1", "Email 1 (opener — portfolio image included)"),
            labels.get("step_email_2", "Email 2"),
            labels.get("step_email_3", "Email 3"),
            labels.get("step_email_4", "Email 4"),
            labels.get("step_email_5", "Email 5"),
        ]
        step_choice = st.selectbox(
            labels.get("treat_as_step", "Treat as step"),
            options=list(range(5)),
            format_func=lambda i: step_options[i],
            index=0,
            key=f"strategist_treat_as_step_{k}",
        )
        step_index_used = 2 + int(step_choice)
        # Email numbering for Strategist single-email drafts: Email 1..5
        email_num_used = 1 + int(step_choice)
        st.session_state[f"strategist_email_num_{k}"] = email_num_used

        try:
            bod_html = inject_deck_links_into_email_body(
                bod_plain,
                product_key=product_key,
                step_index=step_index_used,
                lead_brand=lead_brand,
            )
        except Exception:
            bod_html = (bod_plain or "").replace("\n", "<br>\n")
        with st.expander("📄 Read full message (with deck links)", expanded=False):
            subj_display = _markdown_to_plain(subj or "") or "(none)"
            st.markdown(
                "<div style='font-size:12px;background:#fff;padding:10px 12px;border-radius:6px;border:1px solid #e5e7eb;'>"
                f"<div style='white-space:pre-wrap;'><b>Subject:</b> {subj_display}</div>"
                "<div style='height:8px;'></div>"
                f"<div style='white-space:normal;'>{bod_html}</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        if save_to_gmail_fn is not None:
            save_to_gmail_fn(
                to_email or "",
                subj or "",
                bod or "",
                f"strategist_chat_{k}",
                product_key=product_key,
                step_index=step_index_used,
                lead_brand=lead_brand,
            )
        else:
            _render_save_to_gmail_chat(
                to_email or "",
                subj or "",
                bod or "",
                draft_id=f"strategist_chat_{k}",
                product_key=product_key,
                step_index=step_index_used,
                lead_brand=lead_brand,
            )

    # Session state key for expander open state and choices (suffixed so each call site has its own)
    key_open = f"strategist_add_to_action_open_{k}"
    key_mode = f"strategist_add_to_action_mode_{k}"
    key_seq = f"strategist_add_to_action_seq_id_{k}"
    key_step = f"strategist_add_to_action_step_id_{k}"

    if st.button(labels["button"], key=f"btn_add_to_action_{k}", type="primary", use_container_width=True):
        st.session_state[key_open] = True
        st.session_state[key_mode] = None
        st.session_state[key_seq] = None
        st.session_state[key_step] = None
        st.rerun()

    if not st.session_state.get(key_open):
        return

    with st.expander(labels["expander_title"], expanded=True):
        if st.button("Close", key=f"add_to_action_close_{k}"):
            st.session_state[key_open] = False
            st.rerun()
        mode = st.radio(
            labels["mode_label"],
            options=["new", "append", "overwrite"],
            format_func=lambda x: labels["new_sequence"] if x == "new" else (labels["append"] if x == "append" else labels["overwrite"]),
            key=f"add_to_action_radio_{k}",
            horizontal=True,
        )
        st.session_state[key_mode] = mode

        if mode == "new":
            if multi_steps:
                if st.button("Add full sequence to Timeline", key=f"add_full_seq_expander_{k}", type="primary"):
                    ids, seq_id = add_sequence_to_timeline(session_id, sender_id, multi_steps)
                    if ids and seq_id:
                        clear_ui_cache()
                        st.session_state[key_open] = False
                        st.success(f"Added {len(ids)} emails as sequence {seq_id}.")
                        st.rerun()
                    else:
                        st.error(labels["error_generic"])
            else:
                channel = st.selectbox(
                    labels["channel_label"],
                    options=["Email", "LinkedIn"],
                    key=f"add_to_action_channel_{k}",
                )
                # Single-email drafts: always allow choosing Email 1–5 when channel is Email,
                # even if the "Treat as step" UI above isn't shown (e.g. missing context).
                email_num = None
                if (channel or "").lower().startswith("email"):
                    email_num = st.selectbox(
                        "Email number",
                        options=[1, 2, 3, 4, 5],
                        index=0,
                        key=f"add_to_action_new_email_num_{k}",
                    )
                    st.session_state[f"strategist_email_num_{k}"] = email_num
                if st.button(labels["confirm_new"], key=f"add_to_action_confirm_new_{k}", type="primary"):
                    cleaned = get_cleaned_draft_for_save(draft_content)
                    desc = None
                    if email_num is not None and (channel or "").lower().startswith("email"):
                        desc = f"Email {email_num} — {channel} draft from Strategist"
                    ids, seq_id = add_draft_as_new_sequence(
                        session_id, sender_id, cleaned or draft_content, channel=channel, description=desc
                    )
                    if ids and seq_id:
                        clear_ui_cache()
                        st.session_state[key_open] = False
                        st.success(labels["success_new"].format(seq_id=seq_id))
                        st.rerun()
                    else:
                        st.error(labels["error_generic"])

        elif mode == "append":
            sequences = get_active_sequences(session_id) if session_id else []
            if not sequences:
                st.info(labels["no_sequences"])
                return
            # Show existing/missing email numbers for append as well.
            seq_option_labels = {}
            for s in sequences:
                steps = s.get("steps") or []
                email_num_by_id = _compute_email_numbers_for_sequence(steps)
                existing_nums = sorted(set(email_num_by_id.values()))
                if existing_nums:
                    present_str = ", ".join(str(n) for n in existing_nums)
                    # Assume standard 1–5 email sequence for display of missing slots.
                    all_nums = set(range(1, 6))
                    missing = sorted(all_nums - set(existing_nums))
                    if missing:
                        missing_str = ", ".join(str(n) for n in missing)
                        label = f"{s['id']} ({s['total']} steps) — Emails present: {present_str} · Missing: {missing_str}"
                    else:
                        label = f"{s['id']} ({s['total']} steps) — Emails present: {present_str}"
                else:
                    label = f"{s['id']} ({s['total']} steps)"
                seq_option_labels[label] = s["id"]

            chosen_seq_label = st.selectbox(
                labels.get("select_sequence_append", labels["select_sequence"]),
                options=list(seq_option_labels.keys()),
                key=f"add_to_action_select_seq_append_{k}",
            )
            seq_id = seq_option_labels.get(chosen_seq_label) if chosen_seq_label else None
            st.session_state[key_seq] = seq_id

            if not seq_id:
                return

            channel = st.selectbox(
                labels["channel_label"],
                options=["Email", "LinkedIn"],
                key=f"add_to_action_channel_append_{k}",
            )

            content_to_add = draft_content
            chosen_email_num = None
            if multi_steps:
                email_choice = st.selectbox(
                    "Use which email from the sequence?",
                    options=list(range(len(multi_steps))),
                    format_func=lambda i: multi_steps[i].get("description") or f"Email {multi_steps[i].get('email_num', i + 1)}",
                    key=f"add_to_action_which_email_append_{k}",
                )
                chosen_step = multi_steps[email_choice]
                content_to_add = get_cleaned_draft_for_save(chosen_step.get("content_draft") or "")
                # Use explicit email_num from parsed multi-email sequence when available
                chosen_email_num = chosen_step.get("email_num")
            else:
                # Single-email: let user pick which Email N to add, preferring missing slots.
                if (channel or "").lower().startswith("email"):
                    existing_steps = get_sequence_actions(session_id, seq_id) if session_id and seq_id else []
                    existing_nums = sorted({
                        n for n in (
                            _extract_email_num_from_description((s.get("description") or "")) for s in (existing_steps or [])
                        )
                        if n is not None
                    })
                    available = [n for n in [1, 2, 3, 4, 5] if n not in set(existing_nums)]
                    if not available:
                        available = [1, 2, 3, 4, 5]
                    chosen_email_num = st.selectbox(
                        "Add as Email number",
                        options=available,
                        index=0,
                        key=f"add_to_action_append_email_num_{k}",
                    )
                    st.session_state[f"strategist_email_num_{k}"] = chosen_email_num
                else:
                    chosen_email_num = None

            if st.button(labels["confirm_append"], key=f"add_to_action_confirm_append_{k}", type="primary"):
                desc = None
                if chosen_email_num is not None and (channel or "").lower().startswith("email"):
                    desc = f"Email {chosen_email_num} — {channel} draft from Strategist"
                aid = add_step_to_existing_sequence(
                    session_id,
                    sender_id,
                    seq_id,
                    content_to_add or draft_content,
                    channel=channel,
                    description=desc,
                    email_num=chosen_email_num,
                )
                if aid:
                    clear_ui_cache()
                    st.session_state[key_open] = False
                    st.success(labels["success_append"].format(seq_id=seq_id))
                    st.rerun()
                else:
                    st.error(labels["error_generic"])

        elif mode == "overwrite":
            sequences = get_active_sequences(session_id) if session_id else []
            if not sequences:
                st.info(labels["no_sequences"])
                return
            # Show which email numbers already exist in each sequence for clarity.
            seq_option_labels = {}
            for s in sequences:
                steps = s.get("steps") or []
                email_num_by_id = _compute_email_numbers_for_sequence(steps)
                existing_nums = sorted(set(email_num_by_id.values()))
                if existing_nums:
                    emails_str = ", ".join(str(n) for n in existing_nums)
                    label = f"{s['id']} ({s['total']} steps) — Emails: {emails_str}"
                else:
                    label = f"{s['id']} ({s['total']} steps)"
                seq_option_labels[label] = s["id"]

            chosen_seq_label = st.selectbox(
                labels["select_sequence"],
                options=list(seq_option_labels.keys()),
                key=f"add_to_action_select_seq_{k}",
            )
            seq_id = seq_option_labels.get(chosen_seq_label) if chosen_seq_label else None
            st.session_state[key_seq] = seq_id

            if not seq_id:
                return
            steps = get_sequence_actions(session_id, seq_id)
            email_num_by_id = _compute_email_numbers_for_sequence(steps)
            step_options = []
            for s in steps:
                ch = (s.get("channel") or "").strip() or "Step"
                step_num = s.get("sequence_step") or "?"
                email_num = email_num_by_id.get(s.get("id"))
                if email_num is not None:
                    label = f"Email {email_num} — Step {step_num}: {ch}"
                else:
                    label = f"Step {step_num}: {ch}"
                step_options.append((s["id"], label))
            if not step_options:
                st.warning(labels["no_steps"])
                return
            step_labels = [opt[1] for opt in step_options]
            step_ids = [opt[0] for opt in step_options]
            chosen_idx = st.selectbox(
                labels["select_step"],
                options=list(range(len(step_labels))),
                format_func=lambda i: step_labels[i],
                key=f"add_to_action_select_step_{k}",
            )
            action_id = step_ids[chosen_idx] if chosen_idx is not None else None
            st.session_state[key_step] = action_id

            # When we have a multi-email sequence, pick which email's content to use for overwrite
            content_to_overwrite = draft_content
            if multi_steps:
                email_choice = st.selectbox(
                    "Use which email from the sequence?",
                    options=list(range(len(multi_steps))),
                    format_func=lambda i: multi_steps[i].get("description") or f"Email {multi_steps[i].get('email_num', i + 1)}",
                    key=f"add_to_action_which_email_{k}",
                )
                chosen_step = multi_steps[email_choice]
                content_to_overwrite = get_cleaned_draft_for_save(chosen_step.get("content_draft") or "")

            if st.button(labels["confirm_overwrite"], key=f"add_to_action_confirm_overwrite_{k}", type="primary"):
                if action_id:
                    if not multi_steps:
                        content_to_overwrite = get_cleaned_draft_for_save(draft_content)
                    overwrite_sequence_step(action_id, content_to_overwrite or draft_content)
                    st.session_state.pop(f"draft_revisions_list_{action_id}", None)
                    st.session_state[f"draft_original_{action_id}"] = (content_to_overwrite or draft_content).strip()
                    st.session_state.pop(f"refine_version_sel_{action_id}", None)
                    clear_ui_cache()
                    st.session_state[key_open] = False
                    st.success(labels["success_overwrite"])
                    st.rerun()
                else:
                    st.error(labels["error_generic"])
