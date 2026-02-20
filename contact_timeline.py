"""
contact_timeline.py — Unified per-contact timeline (messages, actions, outcomes, notes, AI, Drive)

Builds a single chronological list for display when user clicks a contact.
"""

from datetime import datetime

from db_ops import (
    get_user_sessions,
    load_messages,
    get_session_actions,
    get_timeline_actions,
    get_memory_events,
    get_session_summary,
)
from drive_sync import list_drive_files


# Timeline item type labels and display order for sorting by type when ts equal
TYPE_ORDER = {
    "Message": 0,
    "Reply": 1,
    "Outreach": 2,
    "Follow-up": 3,
    "Meeting": 4,
    "Note": 5,
    "Research": 6,
    "Other": 7,
    "Outcome": 8,
    "AI Insight": 9,
    "AI Summary": 10,
    "Drive": 11,
}


def _parse_ts(iso_str):
    """Parse ISO timestamp to datetime for sorting; return epoch if invalid."""
    if not iso_str:
        return datetime.min
    try:
        return datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except Exception:
        return datetime.min


def _summary(text, max_len=120):
    if not text:
        return ""
    s = (text or "").strip().replace("\n", " ")
    return s[:max_len] + ("..." if len(s) > max_len else "")


def build_contact_timeline(company_id, contact_id, user_id, company_domain=None, company_name=None):
    """
    Build a single chronological timeline for the given contact.
    Returns list of dicts: ts, type, channel, status, summary, full_content, source_id.
    """
    items = []
    session_id = None

    # Resolve session for this contact (current user's session)
    for sess in get_user_sessions(user_id):
        if sess.get("company_id") == company_id and sess.get("contact_id") == contact_id:
            session_id = sess.get("id")
            break

    # —— Messages ——
    if session_id:
        for m in load_messages(session_id):
            ts = m.get("created_at")
            role = (m.get("role") or "").strip().lower()
            content = (m.get("content") or "").strip()
            if role == "assistant":
                item_type = "Reply"
            else:
                item_type = "Message"
            items.append({
                "ts": ts,
                "type": item_type,
                "channel": "Chat",
                "status": None,
                "summary": _summary(content, 100),
                "full_content": content,
                "source_id": f"msg_{m.get('id')}",
            })

    # —— Actions (and outcomes / context notes) ——
    actions = get_timeline_actions(company_id=company_id, contact_id=contact_id, limit=200)
    for a in actions:
        ts = a.get("created_at") or a.get("scheduled_at")
        atype = (a.get("action_type") or "Other").strip()
        channel = (a.get("channel") or "").strip() or "—"
        status = a.get("status")
        desc = (a.get("description") or "").strip()
        content_sent = (a.get("content_sent") or "").strip()
        response_content = (a.get("response_content") or "").strip()
        outcome = (a.get("outcome") or "").strip()
        context_note = (a.get("context_note") or "").strip()

        # Main action item
        full_parts = [desc] if desc else []
        if content_sent:
            full_parts.append(f"Sent:\n{content_sent}")
        full_content = "\n\n".join(full_parts) if full_parts else desc or "(No description)"

        items.append({
            "ts": ts,
            "type": atype if atype in TYPE_ORDER else "Other",
            "channel": channel,
            "status": status,
            "summary": _summary(desc or content_sent or "(Action)", 100),
            "full_content": full_content,
            "source_id": f"act_{a.get('id')}",
        })

        # Outcome / response as separate item if present (use completed_at or created_at)
        if response_content or outcome:
            outcome_ts = a.get("completed_at") or a.get("created_at") or ts
            items.append({
                "ts": outcome_ts,
                "type": "Outcome",
                "channel": channel,
                "status": status,
                "summary": _summary(response_content or outcome, 100),
                "full_content": (response_content + "\n\n" + outcome).strip() or outcome or response_content,
                "source_id": f"out_{a.get('id')}",
            })

        # Context note as separate "Note" item if present
        if context_note:
            items.append({
                "ts": a.get("created_at") or ts,
                "type": "Note",
                "channel": "Internal",
                "status": None,
                "summary": _summary(context_note, 100),
                "full_content": context_note,
                "source_id": f"note_{a.get('id')}",
            })

    # —— AI memory events (insights) ——
    if session_id:
        for e in get_memory_events(session_id, min_imp=0, limit=50):
            items.append({
                "ts": e.get("created_at"),
                "type": "AI Insight",
                "channel": "—",
                "status": None,
                "summary": _summary(e.get("summary", ""), 100),
                "full_content": e.get("summary", ""),
                "source_id": f"mem_{e.get('id')}",
            })

    # —— Session AI summary (one item) ——
    if session_id:
        summary_text, _ = get_session_summary(session_id)
        if summary_text:
            items.append({
                "ts": None,  # no single timestamp; put at end or use session updated
                "type": "AI Summary",
                "channel": "—",
                "status": None,
                "summary": _summary(summary_text, 120),
                "full_content": summary_text,
                "source_id": "sess_summary",
            })

    # —— Drive document references ——
    name_for_drive = company_name or company_domain
    if name_for_drive:
        try:
            files = list_drive_files(company_name=name_for_drive)
            if not files and company_domain:
                files = list_drive_files(company_name=company_domain)
            if files:
                names = [f.get("name") or f.get("id", "") for f in files[:20]]
                full = "\n".join(names) if names else ""
                items.append({
                    "ts": None,
                    "type": "Drive",
                    "channel": "—",
                    "status": None,
                    "summary": f"{len(files)} document(s) referenced",
                    "full_content": full,
                    "source_id": "drive_refs",
                })
        except Exception:
            pass

    # Sort: by timestamp ascending; items without ts go last
    def sort_key(it):
        raw_ts = it.get("ts")
        if raw_ts is None:
            t = datetime.max
        else:
            t = _parse_ts(raw_ts)
        return (t, TYPE_ORDER.get(it.get("type"), 99))

    items.sort(key=sort_key)
    return items
