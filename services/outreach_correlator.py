"""
Gmail message → Action correlation (idempotent). Called from gmail_sync_service after a new message is stored.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from db_ops import (
    db,
    get_contact_by_email,
    gmail_outreach_sync_already_applied,
    record_gmail_outreach_sync_event,
    get_session_id_for_contact_owner,
    update_action,
)
from models import Action, GmailOutreachSyncEvent, InboxEmail
from services.outreach_classifier import classify_inbound_reply
from services.outreach_state import canonical_outreach_status, resolve_action_status_for_patch

log = logging.getLogger(__name__)


def _norm_email(e: str | None) -> str:
    return (e or "").strip().lower()


def normalize_subject(subj: str | None) -> str:
    s = (subj or "").strip()
    while True:
        low = s[:20].lower() if len(s) > 20 else s.lower()
        for pref in ("re:", "fw:", "fwd:"):
            if low.startswith(pref):
                s = s[len(pref) :].strip()
                low = s[:20].lower() if len(s) > 20 else s.lower()
                break
        else:
            break
    return s.lower().strip()


def _subject_from_action_content(content: str | None) -> str:
    c = (content or "").strip()
    m = re.match(r"subject:\s*(.+?)(\n|$)", c, re.I | re.DOTALL)
    if m:
        return normalize_subject(m.group(1).split("\n")[0])
    return ""


def _is_email_channel(ch: str | None) -> bool:
    return "email" in (ch or "").lower() or (ch or "").strip() == ""


def _action_anchor_ts(a: Action) -> datetime | None:
    return a.completed_at or a.scheduled_at or a.created_at


def _participant_contact_email(
    *,
    from_email: str | None,
    to_emails: list[str] | None,
    account_email: str,
    is_sent: bool,
) -> str | None:
    """
    Best-effort: the external contact email in this message (not the mailbox owner).
    """
    acc = _norm_email(account_email)
    pool: set[str] = set()
    if from_email:
        pool.add(_norm_email(from_email))
    for t in to_emails or []:
        pool.add(_norm_email(t))
    pool.discard("")
    external = [e for e in pool if e and e != acc]
    if not external:
        return None
    if is_sent:
        # outbound: recipient is usually contact
        for e in external:
            if get_contact_by_email(e):
                return e
        return external[0]
    # inbound: sender is usually contact
    frm = _norm_email(from_email)
    if frm and frm != acc:
        if get_contact_by_email(frm):
            return frm
        return frm
    return external[0] if external else None


def _pick_action_outbound(session_id: int, user_id: int, subject_norm: str) -> Action | None:
    with db() as s:
        actions = (
            s.query(Action)
            .filter(Action.session_id == session_id)
            .order_by(Action.sequence_step.asc().nulls_last(), Action.created_at.asc())
            .all()
        )
        # Prefer actions owned by the same mailbox user, but don't drop valid session actions.
        same_owner = [a for a in actions if int(a.user_id or 0) == int(user_id)]
        if same_owner:
            actions = same_owner
        email_acts = [a for a in actions if _is_email_channel(a.channel)]
        if not email_acts:
            return None
        open_status = {"planned", "sent", "in_progress", "awaiting_reply"}
        open_acts = [a for a in email_acts if (a.status or "").lower() in open_status]
        seq = sorted(
            [a for a in open_acts if (a.sequence_id or "").strip()],
            key=lambda x: int(x.sequence_step or 0),
        )
        if seq:
            return seq[0]
        stand = [a for a in open_acts if not (a.sequence_id or "").strip()]
        if stand:
            return sorted(stand, key=lambda x: x.created_at or datetime.min)[0]
        if subject_norm:
            for a in email_acts:
                subj_a = _subject_from_action_content(a.content_sent) or normalize_subject(
                    a.description or ""
                )
                if subj_a and (subj_a in subject_norm or subject_norm in subj_a):
                    return a
        planned_any = [a for a in email_acts if (a.status or "").lower() == "planned"]
        if planned_any:
            return sorted(planned_any, key=lambda x: (x.sequence_step or 9999, x.created_at or datetime.min))[0]
        return None


def _pick_action_inbound(session_id: int, user_id: int, reply_ts: datetime) -> Action | None:
    with db() as s:
        acts = (
            s.query(Action)
            .filter(Action.session_id == session_id)
            .order_by(Action.created_at.desc())
            .all()
        )
        same_owner = [a for a in acts if int(a.user_id or 0) == int(user_id)]
        if same_owner:
            acts = same_owner
        email_acts = [a for a in acts if _is_email_channel(a.channel)]
        if not email_acts:
            return None
        rts = reply_ts.replace(tzinfo=None) if getattr(reply_ts, "tzinfo", None) else reply_ts

        def ok_status(x: Action) -> bool:
            return (x.status or "").lower() in {"sent", "completed", "planned"}

        candidates = []
        for a in email_acts:
            if not ok_status(a):
                continue
            ts = _action_anchor_ts(a)
            if ts and ts <= rts:
                candidates.append((ts, a))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
        # Fallback: latest email action overall
        return email_acts[0] if email_acts else None


def _pick_action_inbound_by_parent_message(
    *,
    thread_pk: int | None,
    gmail_account_id: int,
    user_id: int,
    reply_ts: datetime,
) -> Action | None:
    """
    Per-email correlation:
    1) Find nearest prior outbound message in this thread.
    2) Resolve which action that outbound message was mapped to via sync events.
    """
    if not thread_pk:
        return None
    rts = reply_ts.replace(tzinfo=None) if getattr(reply_ts, "tzinfo", None) else reply_ts
    with db() as s:
        parent = (
            s.query(InboxEmail)
            .filter(
                InboxEmail.gmail_account_id == int(gmail_account_id),
                InboxEmail.thread_id == int(thread_pk),
                InboxEmail.is_sent.is_(True),
                InboxEmail.timestamp <= rts,
            )
            .order_by(InboxEmail.timestamp.desc())
            .first()
        )
        if not parent:
            return None
        ev = (
            s.query(GmailOutreachSyncEvent)
            .filter(
                GmailOutreachSyncEvent.gmail_account_id == int(gmail_account_id),
                GmailOutreachSyncEvent.gmail_message_id == str(parent.gmail_message_id),
                GmailOutreachSyncEvent.event_kind == "sent_sync",
                GmailOutreachSyncEvent.action_id.isnot(None),
            )
            .order_by(GmailOutreachSyncEvent.created_at.desc())
            .first()
        )
        if not ev or not ev.action_id:
            return None
        act = s.get(Action, int(ev.action_id))
        if not act:
            return None
        return act


def _trim_reply_body(text: str | None, limit: int = 50000) -> str:
    t = (text or "").replace("\r", "").replace("\u00a0", " ").replace("\u202f", " ").strip()
    if not t:
        return ""
    # Common quote separators across Gmail/plaintext clients.
    cut_patterns = [
        r"(^|\n)\s*>?\s*On .+ wrote:\s*$",
        r"(^|\n)\s*>?\s*On .+\n\s*>?\s*wrote:\s*$",
        r"\nFrom:\s.+\nSent:\s.+\nTo:\s.+\nSubject:\s.+",
        r"\nFrom:\s.+\nDate:\s.+\nSubject:\s.+",
        r"\n-{2,}\s*Original Message\s*-{2,}",
        r"\nBegin forwarded message:",
        r"\nForwarded message:",
    ]
    for p in cut_patterns:
        m = re.search(p, t, flags=re.I | re.M)
        if m:
            t = t[: m.start()].strip()
            break
    # Line-level fallback for wrapped headers that escape regexes.
    lines = t.split("\n")
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = re.sub(r"^\s*>+\s?", "", lines[i]).strip()
        if re.match(r"^On .+ wrote:\s*$", line, flags=re.I):
            break
        if re.match(r"^On .+$", line, flags=re.I):
            nxt = re.sub(r"^\s*>+\s?", "", lines[i + 1]).strip() if i + 1 < len(lines) else ""
            if re.match(r"^wrote:\s*$", nxt, flags=re.I):
                break
        if kept and re.match(r"^(From|Sent|To|Cc|Date|Subject):\s+", line, flags=re.I):
            break
        kept.append(lines[i])
        i += 1
    t = "\n".join(kept).strip()
    return t[:limit]


def _close_following_sequence_steps(anchor_action_id: int, anchor_status: str, gmail_message_id: str) -> int:
    """
    Deterministically close remaining sequence steps after a terminal signal from Gmail.
    - meeting_booked => later planned/sent steps -> completed
    - deal_lost => later planned/sent steps -> deal_lost
    """
    status = (anchor_status or "").strip().lower()
    if status not in {"meeting_booked", "deal_lost"}:
        return 0
    follow_to = "completed" if status == "meeting_booked" else "deal_lost"
    with db() as s:
        anchor = s.get(Action, int(anchor_action_id))
        if not anchor:
            return 0
        seq_id = (anchor.sequence_id or "").strip()
        if not seq_id:
            return 0
        step = int(anchor.sequence_step or 0)
        if step <= 0:
            return 0
        rows = (
            s.query(Action)
            .filter(
                Action.session_id == anchor.session_id,
                Action.sequence_id == seq_id,
                Action.sequence_step > step,
            )
            .order_by(Action.sequence_step.asc())
            .all()
        )
        count = 0
        for row in rows:
            cur = (row.status or "").strip().lower()
            if cur not in {"planned", "sent", "in_progress"}:
                continue
            update_action(
                row.id,
                status=follow_to,
                outcome_reason=f"sequence_auto_close_after:{status}",
                outreach_sync_meta={
                    "source": "gmail_sync",
                    "event_kind": "sequence_auto_close",
                    "anchor_action_id": int(anchor_action_id),
                    "gmail_message_id": gmail_message_id,
                    "synced_at": datetime.utcnow().isoformat() + "Z",
                },
            )
            count += 1
        return count


def _build_question_aware_prefix(reply_text: str) -> str:
    raw = (reply_text or "").strip()
    t = raw.lower()
    asks_how = any(
        k in t
        for k in (
            "how this works",
            "how it works",
            "how does this work",
            "tell me how",
            "tell me more",
            "explain",
        )
    )
    asks_fit = any(
        k in t
        for k in (
            "editorial voice",
            "portfolio",
            "align",
            "scaled",
            "scale",
            "similar companies",
        )
    )
    asks_question = "?" in raw

    if asks_fit:
        return (
            "Great question — on fit and portfolio alignment:\n"
            "1) We preserve each brand's editorial voice and identity; we do not force a rewrite of what already works.\n"
            "2) We scale through shared infrastructure (SEO, distribution, monetization, and ops) while keeping brand-level autonomy.\n"
            "3) In similar brands, this typically improves execution speed and revenue quality without changing core voice.\n\n"
        )
    if asks_how or asks_question:
        return (
            "Thanks for the question — quick overview of how this process works:\n"
            "1) 15-minute intro to align on goals and fit.\n"
            "2) If aligned, we share a concise diligence checklist and timeline.\n"
            "3) Indicative terms in ~2 weeks, with a typical close in ~6 weeks.\n\n"
        )
    return ""


def _adapt_next_sequence_step_for_reply(anchor_action_id: int, reply_text: str, gmail_message_id: str) -> int:
    """
    Deterministic adaptation so Email #2 reflects prospect feedback immediately.
    Updates the next planned/sent email step body with a reply-aware prefix.
    """
    with db() as s:
        anchor = s.get(Action, int(anchor_action_id))
        if not anchor:
            return 0
        seq_id = (anchor.sequence_id or "").strip()
        step = int(anchor.sequence_step or 0)
        if not seq_id or step <= 0:
            return 0
        nxt = (
            s.query(Action)
            .filter(
                Action.session_id == anchor.session_id,
                Action.sequence_id == seq_id,
                Action.sequence_step > step,
            )
            .order_by(Action.sequence_step.asc())
            .first()
        )
        if not nxt:
            return 0
        if (nxt.status or "").strip().lower() not in {"planned", "sent", "in_progress"}:
            return 0

        existing = (nxt.content_sent or "").strip()
        if not existing:
            return 0
        if f"sync-adapt:{gmail_message_id}" in existing:
            return 0

        prefix = _build_question_aware_prefix(reply_text)
        if not prefix:
            return 0

        adapted = f"{prefix}{existing}\n\n[sync-adapt:{gmail_message_id}]".strip()
        update_action(
            nxt.id,
            content_sent=adapted[:50000],
            outcome_reason="sequence_next_step_adapted_from_reply",
            outreach_sync_meta={
                "source": "gmail_sync",
                "event_kind": "sequence_next_step_adapt",
                "anchor_action_id": int(anchor_action_id),
                "gmail_message_id": gmail_message_id,
                "synced_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        return 1


def _adapt_following_sequence_steps_with_ai(anchor_action_id: int, reply_text: str, gmail_message_id: str) -> int:
    """
    AI-first adaptation for following sequence steps using existing LH2 rewriter.
    Falls back to deterministic next-step adaptation when AI yields no revisions.
    """
    with db() as s:
        anchor = s.get(Action, int(anchor_action_id))
        if not anchor:
            return 0
        seq_id = (anchor.sequence_id or "").strip()
        step = int(anchor.sequence_step or 0)
        if not seq_id or step <= 0:
            return 0
        following = (
            s.query(Action)
            .filter(
                Action.session_id == anchor.session_id,
                Action.sequence_id == seq_id,
                Action.sequence_step > step,
            )
            .order_by(Action.sequence_step.asc())
            .all()
        )
        following_rows = [
            r
            for r in following
            if (r.status or "").strip().lower() in {"planned", "sent", "in_progress"}
            and (r.content_sent or "").strip()
        ]
        if not following_rows:
            return 0

        anchor_text = (anchor.content_sent or "").strip()
        if not anchor_text:
            return 0

        payload = [
            {
                "id": int(r.id),
                "sequence_step": int(r.sequence_step or 0),
                "channel": r.channel or "",
                "description": r.description or "",
                "content_sent": (r.content_sent or "").strip(),
            }
            for r in following_rows
        ]

    try:
        from integrations.ai.ai_engine import adapt_following_sequence_drafts

        user_note = (
            "Prospect replied on thread. Rewrite following steps to respond naturally.\n"
            f"Reply text:\n{(reply_text or '').strip()[:4000]}"
        )
        revised = adapt_following_sequence_drafts(step, anchor_text, payload, user_note=user_note)
    except Exception:
        log.exception("AI adapt following failed for action %s", anchor_action_id)
        revised = []

    updated = 0
    for row in revised or []:
        try:
            aid = int(row.get("id"))
        except Exception:
            continue
        rev = (row.get("revised_content") or "").strip()
        if not rev:
            continue
        why = (row.get("change_reason") or "").strip() or "ai_adapt_following_from_reply"
        update_action(
            aid,
            content_sent=rev[:50000],
            outcome_reason=f"ai_following_adapted:{why}",
            outreach_sync_meta={
                "source": "gmail_sync",
                "event_kind": "sequence_ai_following_adapt",
                "anchor_action_id": int(anchor_action_id),
                "gmail_message_id": gmail_message_id,
                "synced_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        updated += 1

    if updated > 0:
        return updated
    return _adapt_next_sequence_step_for_reply(anchor_action_id, reply_text, gmail_message_id)


def _retime_following_sequence_steps(anchor_action_id: int, interaction_ts: datetime, gmail_message_id: str) -> int:
    """
    Re-time following sequence step scheduled_at values after back-and-forth.
    Keeps relative spacing between later steps while anchoring next step to interaction+1 day.
    """
    with db() as s:
        anchor = s.get(Action, int(anchor_action_id))
        if not anchor:
            return 0
        seq_id = (anchor.sequence_id or "").strip()
        anchor_step = int(anchor.sequence_step or 0)
        if not seq_id or anchor_step <= 0:
            return 0
        rows = (
            s.query(Action)
            .filter(
                Action.session_id == anchor.session_id,
                Action.sequence_id == seq_id,
                Action.sequence_step > anchor_step,
            )
            .order_by(Action.sequence_step.asc())
            .all()
        )
        rows = [r for r in rows if (r.status or "").strip().lower() in {"planned", "sent", "in_progress"}]
        if not rows:
            return 0

        base_ts = interaction_ts.replace(tzinfo=None) if getattr(interaction_ts, "tzinfo", None) else interaction_ts
        next_at = base_ts + timedelta(days=1)

        deltas: list[timedelta] = []
        prev_sched = rows[0].scheduled_at or next_at
        for r in rows[1:]:
            curr = r.scheduled_at or (prev_sched + timedelta(days=2))
            d = curr - prev_sched
            if d.total_seconds() <= 0:
                d = timedelta(days=2)
            deltas.append(d)
            prev_sched = curr

        updated = 0
        running = next_at
        update_action(
            rows[0].id,
            scheduled_at=running,
            outreach_sync_meta={
                "source": "gmail_sync",
                "event_kind": "sequence_retime_after_interaction",
                "anchor_action_id": int(anchor_action_id),
                "gmail_message_id": gmail_message_id,
                "synced_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        updated += 1
        for idx, r in enumerate(rows[1:]):
            running = running + deltas[idx]
            update_action(
                r.id,
                scheduled_at=running,
                outreach_sync_meta={
                    "source": "gmail_sync",
                    "event_kind": "sequence_retime_after_interaction",
                    "anchor_action_id": int(anchor_action_id),
                    "gmail_message_id": gmail_message_id,
                    "synced_at": datetime.utcnow().isoformat() + "Z",
                },
            )
            updated += 1
        return updated


def correlate_outreach_from_gmail_message(
    *,
    gmail_account_id: int,
    gmail_message_id: str,
    user_id: int,
    account_email: str,
    from_email: str | None,
    to_emails: list | dict | None,
    subject: str | None,
    body_full: str | None,
    body_snippet: str | None,
    timestamp: datetime,
    is_sent: bool,
    thread_pk: int | None = None,
) -> dict[str, Any]:
    """
    Apply one idempotent correlation + optional classification for a stored inbox email row.
    Returns a small dict for sync diagnostics.
    """
    out: dict[str, Any] = {"mapped": False, "skipped": False, "reason": None}
    mid = (gmail_message_id or "").strip()
    if not mid:
        out["skipped"] = True
        out["reason"] = "empty_message_id"
        return out

    if gmail_outreach_sync_already_applied(gmail_account_id, mid):
        out["skipped"] = True
        out["reason"] = "already_applied"
        return out

    to_list: list[str] = []
    if isinstance(to_emails, list):
        to_list = [str(x) for x in to_emails]
    elif isinstance(to_emails, dict):
        to_list = list(to_emails.values())  # unlikely

    contact_addr = _participant_contact_email(
        from_email=from_email,
        to_emails=to_list,
        account_email=account_email,
        is_sent=is_sent,
    )
    if not contact_addr:
        out["skipped"] = True
        out["reason"] = "no_external_address"
        return out

    contact = get_contact_by_email(contact_addr)
    if not contact:
        out["skipped"] = True
        out["reason"] = "contact_not_in_db"
        return out

    sid = get_session_id_for_contact_owner(int(contact["id"]), int(user_id))
    if not sid:
        out["skipped"] = True
        out["reason"] = "no_session_for_owner"
        return out

    subj_n = normalize_subject(subject)

    if is_sent:
        act = _pick_action_outbound(sid, user_id, subj_n)
        if not act:
            out["skipped"] = True
            out["reason"] = "no_matching_outbound_action"
            return out
        with db() as s:
            row = s.get(Action, act.id)
            if not row:
                return out
            current = row.status
            new_status = resolve_action_status_for_patch(current, "sent")
            if not new_status:
                if canonical_outreach_status(current) == "sent":
                    record_gmail_outreach_sync_event(
                        gmail_account_id=gmail_account_id,
                        gmail_message_id=mid,
                        user_id=user_id,
                        action_id=row.id,
                        event_kind="sent_sync",
                        applied_status="sent",
                        outcome_reason="noop_already_sent",
                    )
                    out["mapped"] = True
                    out["reason"] = "noop_already_sent"
                    return out
                out["skipped"] = True
                out["reason"] = "transition_blocked_outbound"
                return out
            meta = {
                "source": "gmail_sync",
                "gmail_message_id": mid,
                "event_kind": "sent_sync",
                "synced_at": datetime.utcnow().isoformat() + "Z",
            }
            ts = timestamp.replace(tzinfo=None) if getattr(timestamp, "tzinfo", None) else timestamp
            update_action(
                row.id,
                status=new_status,
                completed_at=ts,
                outreach_sync_meta=meta,
                outcome_reason="correlation:outbound",
                content_sent=body_full[:50000] if body_full and not (row.content_sent or "").strip() else None,
            )
            retimed = _retime_following_sequence_steps(row.id, ts, mid)
            record_gmail_outreach_sync_event(
                gmail_account_id=gmail_account_id,
                gmail_message_id=mid,
                user_id=user_id,
                action_id=row.id,
                event_kind="sent_sync",
                applied_status=new_status,
                outcome_reason=f"correlation:outbound;retimed_following={retimed}",
            )
            out["mapped"] = True
            out["retimed_following"] = retimed
            return out

    # inbound: try strict per-email parent mapping first, then session-level fallback.
    act = _pick_action_inbound_by_parent_message(
        thread_pk=thread_pk,
        gmail_account_id=gmail_account_id,
        user_id=user_id,
        reply_ts=timestamp,
    )
    if not act:
        act = _pick_action_inbound(sid, user_id, timestamp)
    if not act:
        out["skipped"] = True
        out["reason"] = "no_inbound_action_target"
        return out

    blob = (body_full or body_snippet or "")[:80000]
    cleaned = _trim_reply_body(blob)
    clf = classify_inbound_reply(subject or "", cleaned)

    with db() as s:
        row = s.get(Action, act.id)
        if not row:
            return out
        new_status = resolve_action_status_for_patch(row.status, clf.action_status)
        if not new_status:
            out["skipped"] = True
            out["reason"] = "transition_blocked"
            return out
        meta = {
            "source": "gmail_sync",
            "gmail_message_id": mid,
            "event_kind": "reply_sync",
            "classifier": clf.response_type,
            "synced_at": datetime.utcnow().isoformat() + "Z",
        }
        update_action(
            row.id,
            status=new_status,
            response_content=cleaned[:50000] if cleaned else None,
            response_type=clf.response_type,
            outcome_reason=clf.outcome_reason,
            outreach_sync_meta=meta,
        )
        adapted_following = _adapt_following_sequence_steps_with_ai(row.id, cleaned, mid)
        retimed = _retime_following_sequence_steps(row.id, timestamp, mid)
        closed = _close_following_sequence_steps(row.id, new_status, mid)
        record_gmail_outreach_sync_event(
            gmail_account_id=gmail_account_id,
            gmail_message_id=mid,
            user_id=user_id,
            action_id=row.id,
            event_kind="reply_sync",
            applied_status=new_status,
            outcome_reason=f"{clf.outcome_reason};adapted_following={adapted_following};retimed_following={retimed};closed_following={closed}",
        )
        out["mapped"] = True
        out["adapted_following"] = adapted_following
        out["retimed_following"] = retimed
        out["closed_following"] = closed
        return out
