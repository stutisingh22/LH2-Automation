"""
Gmail Sent Sync — When a Command Center user sends an email to a contact in the DB,
record it as a completed action and update timeline / contact status.

Run periodically (cron or scheduler) or once (from project root):
  python standalone/sync/gmail_sent_sync.py

Requires:
- credentials.json in project folder (Google OAuth client; one file for all users).
- Same DATABASE_URL (e.g. Supabase) on every system so data is shared.
- Each user in the DB must have email set (users.email). Each user authorizes Gmail once
  via "Connect Gmail" in the app or on first run of this script (browser opens).

Flow:
1. List all users with email from the DB.
2. For each user, use Gmail API (read scope) via google_auth tokens.
3. For each sent message, if recipient is a contact in the DB, call record_sent_email().
4. record_sent_email() creates/gets session, logs action, updates contact status; idempotent by Gmail message ID.
"""

import os
import re
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

# Project root so imports and credentials work (standalone/sync/ -> parent.parent.parent)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from dotenv import load_dotenv
load_dotenv(_ROOT / "keys.env")
load_dotenv(_ROOT / ".env")

from db_ops import (
    get_contact_by_email,
    get_or_create_session,
    get_action_by_id,
    get_all_users,
    get_session_actions,
    get_sequence_actions,
    get_user_by_email,
    record_sent_email,
    update_action,
)
from integrations.gmail import get_google_credentials, log_user
from integrations.ai.ai_engine import adapt_following_sequence_drafts

log = logging.getLogger("gmail_sent_sync")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(h)


def _parse_email_addresses(header_value):
    """Extract email addresses from a header like 'Name <a@b.com>' or 'a@b.com'."""
    if not header_value:
        return []
    emails = re.findall(r"[\w.-]+@[\w.-]+\.\w+", header_value)
    return [e.lower().strip() for e in emails]


def _get_header(headers, name):
    for h in headers:
        if (h.get("name") or "").strip().lower() == name.strip().lower():
            return (h.get("value") or "").strip()
    return ""


def fetch_sent_messages(service, newer_than_hours=24, max_results=50):
    """List sent messages. Returns list of dicts: id, from, to_emails, subject, body_snippet, internal_date."""
    try:
        query = f"in:sent newer_than:{newer_than_hours}h"
        response = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        msg_ids = [m["id"] for m in response.get("messages", [])]
    except Exception as e:
        log.warning("List sent failed: %s", e)
        return []

    out = []
    for mid in msg_ids:
        try:
            msg = service.users().messages().get(
                userId="me", id=mid, format="full"
            ).execute()
            payload = msg.get("payload") or {}
            headers = payload.get("headers") or []
            from_val = _get_header(headers, "From")
            to_val = _get_header(headers, "To")
            subject = _get_header(headers, "Subject")
            to_emails = _parse_email_addresses(to_val)
            from_emails = _parse_email_addresses(from_val)

            body = ""
            if "body" in payload and payload["body"].get("data"):
                import base64
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
            else:
                for part in payload.get("parts") or []:
                    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                        import base64
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                        break

            internal_date = msg.get("internalDate")
            try:
                sent_at = datetime.fromtimestamp(int(internal_date) / 1000, timezone.utc) if internal_date else None
            except Exception:
                sent_at = None

            out.append({
                "id": mid,
                "thread_id": msg.get("threadId"),
                "from_emails": from_emails,
                "to_emails": to_emails,
                "subject": subject,
                "body": (body or "")[:15000],
                "sent_at": sent_at,
            })
        except Exception as e:
            log.debug("Get message %s failed: %s", mid, e)
    return out


def _extract_body_from_payload(payload):
    """Get plain-text body from a Gmail message payload."""
    import base64
    body = ""
    if payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    else:
        for part in payload.get("parts") or []:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                break
    return body


def fetch_full_thread(service, thread_id, max_chars=45000):
    """
    Fetch the full thread (all messages in the conversation) and return a single
    string: each message with From / To / Date and body, chronological order.
    """
    if not thread_id:
        return ""
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
    except Exception as e:
        log.debug("Thread get failed %s: %s", thread_id, e)
        return ""

    parts = []
    total = 0
    messages = thread.get("messages") or []
    # Gmail returns thread messages in chronological order
    for i, msg in enumerate(messages):
        if total >= max_chars:
            parts.append("\n\n[... thread truncated ...]")
            break
        payload = msg.get("payload") or {}
        headers = payload.get("headers") or []
        from_val = _get_header(headers, "From")
        to_val = _get_header(headers, "To")
        date_val = _get_header(headers, "Date")
        body = _extract_body_from_payload(payload)
        internal_date = msg.get("internalDate")
        try:
            ts = datetime.fromtimestamp(int(internal_date) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if internal_date else date_val or "—"
        except Exception:
            ts = date_val or "—"
        block = f"--- Message {i + 1} | From: {from_val} | To: {to_val} | {ts} ---\n\n{(body or '').strip()}"
        if total + len(block) > max_chars:
            block = block[: max_chars - total - 20] + "\n...[truncated]"
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts)


def fetch_thread_messages(service, thread_id):
    """Structured thread messages for reply detection."""
    if not thread_id:
        return []
    try:
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    except Exception as e:
        log.debug("Thread get failed %s: %s", thread_id, e)
        return []
    out = []
    for msg in thread.get("messages") or []:
        payload = msg.get("payload") or {}
        headers = payload.get("headers") or []
        from_val = _get_header(headers, "From")
        to_val = _get_header(headers, "To")
        body = _extract_body_from_payload(payload)
        from_emails = _parse_email_addresses(from_val)
        to_emails = _parse_email_addresses(to_val)
        internal_date = msg.get("internalDate")
        try:
            sent_at = datetime.fromtimestamp(int(internal_date) / 1000, timezone.utc) if internal_date else None
        except Exception:
            sent_at = None
        out.append(
            {
                "id": msg.get("id"),
                "thread_id": thread_id,
                "from_emails": [e.lower().strip() for e in from_emails],
                "to_emails": [e.lower().strip() for e in to_emails],
                "body": (body or "").strip(),
                "sent_at": sent_at,
            }
        )
    return out


def _latest_inbound_reply(thread_messages, sender_email, recipient_email, outbound_message_id=None):
    """
    Latest inbound reply corresponding to a specific outbound message.
    We only consider replies after that outbound and before the next outbound
    from the same sender in the same thread (if any).
    """
    se = (sender_email or "").strip().lower()
    re_email = (recipient_email or "").strip().lower()
    ordered = list(thread_messages or [])
    ordered.sort(key=lambda x: (x.get("sent_at") or datetime.min.replace(tzinfo=timezone.utc)))

    outbound_idx = -1
    if outbound_message_id:
        for i, m in enumerate(ordered):
            if str(m.get("id") or "") == str(outbound_message_id):
                outbound_idx = i
                break

    # If we couldn't locate outbound by id, keep prior behavior but still
    # avoid selecting before the first outbound from sender->recipient.
    if outbound_idx < 0:
        for i, m in enumerate(ordered):
            frm = set(m.get("from_emails") or [])
            to = set(m.get("to_emails") or [])
            if se in frm and re_email in to:
                outbound_idx = i
                break
        if outbound_idx < 0:
            return None

    # Bound search until next outbound sender->recipient message.
    next_outbound_idx = len(ordered)
    for j in range(outbound_idx + 1, len(ordered)):
        frm = set(ordered[j].get("from_emails") or [])
        to = set(ordered[j].get("to_emails") or [])
        if se in frm and re_email in to:
            next_outbound_idx = j
            break

    candidates = []
    for m in ordered[outbound_idx + 1: next_outbound_idx]:
        frm = set(m.get("from_emails") or [])
        to = set(m.get("to_emails") or [])
        if re_email in frm and se in to and (m.get("body") or "").strip():
            candidates.append(m)
    if not candidates:
        return None
    return candidates[-1]


def _clean_reply_body(text: str) -> str:
    """Keep only the new reply content; strip quoted history blocks."""
    t = (text or "").strip()
    if not t:
        return ""
    # Common Gmail quoted-thread marker.
    m = re.search(r"\nOn .+ wrote:\s*$", t, flags=re.IGNORECASE | re.MULTILINE)
    if m:
        t = t[: m.start()].strip()
    # Secondary hard separators in some clients.
    for marker in ("\n--- Message ", "\nFrom: ", "\n> "):
        idx = t.find(marker)
        if idx > 0:
            t = t[:idx].strip()
    return t


def _clean_outbound_body(text: str) -> str:
    """Keep only newly written outbound message text for action logging."""
    t = (text or "").strip()
    if not t:
        return ""
    # Common quoted-history marker in replies/forwards.
    m = re.search(r"\nOn .+ wrote:\s*$", t, flags=re.IGNORECASE | re.MULTILINE)
    if m:
        t = t[: m.start()].strip()
    # Remove quote-prefix lines from copied context.
    lines = []
    for ln in t.splitlines():
        if re.match(r"^\s*>", ln):
            continue
        lines.append(ln)
    t = "\n".join(lines).strip()
    return t


def _adapt_following_for_reply(action_id: int, reply_text: str):
    """If action belongs to a sequence, rewrite following drafts from reply signal."""
    act = get_action_by_id(action_id)
    if not act:
        return 0
    seq_id = (act.get("sequence_id") or "").strip()
    sid = act.get("session_id")
    anchor_step = int(act.get("sequence_step") or 0)
    if not seq_id or not sid or anchor_step <= 0:
        return 0
    following_raw = [
        s for s in (get_sequence_actions(int(sid), seq_id) or [])
        if int(s.get("sequence_step") or 0) > anchor_step
    ]
    if not following_raw:
        return 0
    anchor_text = (act.get("content_sent") or "").strip()
    if not anchor_text:
        return 0
    results = adapt_following_sequence_drafts(
        anchor_step,
        anchor_text,
        following_raw,
        user_note=f"Prospect replied on email thread:\n{(reply_text or '').strip()[:3000]}",
    )
    count = 0
    for row in results or []:
        aid = row.get("id")
        rev = (row.get("revised_content") or "").strip()
        if not aid or not rev:
            continue
        update_action(
            int(aid),
            content_sent=rev,
            draft_revisions=[],
            draft_original="",
        )
        count += 1
    return count


def _find_best_existing_action_for_sent(sender_email: str, recipient_email: str, subject: str):
    """
    Prefer linking Gmail send events to existing planned sequence step instead of creating
    a brand-new standalone action.
    """
    user = get_user_by_email(sender_email)
    contact = get_contact_by_email(recipient_email)
    if not user or not contact:
        return None
    sid = get_or_create_session(contact["company_id"], contact["id"], user["id"], score=0)
    actions = get_session_actions(sid) or []
    subject_l = (subject or "").strip().lower()

    # Prefer pending sequence email steps first (oldest sequence step first).
    seq_pending = [
        a for a in actions
        if (a.get("sequence_id") or "").strip()
        and "email" in str(a.get("channel") or "").lower()
        and str(a.get("status") or "").lower() in ("planned", "sent", "in_progress")
    ]
    seq_pending.sort(key=lambda a: int(a.get("sequence_step") or 0))
    if seq_pending:
        return seq_pending[0]

    # Fallback: pending standalone email actions.
    standalone_pending = [
        a for a in actions
        if not (a.get("sequence_id") or "").strip()
        and "email" in str(a.get("channel") or "").lower()
        and str(a.get("status") or "").lower() in ("planned", "sent", "in_progress")
    ]
    if standalone_pending:
        return standalone_pending[0]

    # Last resort: most recent completed with same subject line.
    if subject_l:
        for a in actions:
            cs = str(a.get("content_sent") or "")
            if f"subject: {subject_l}" in cs.lower():
                return a
    return None


def _capture_reply_and_adapt_for_action(
    service,
    thread_id: str,
    action_id: int,
    sender_email: str,
    recipient_email: str,
    outbound_message_id: str,
    uid: int,
):
    """Capture reply for an action and adapt following sequence drafts."""
    if not action_id or not thread_id:
        return
    thread_msgs = fetch_thread_messages(service, thread_id)
    latest_reply = _latest_inbound_reply(
        thread_msgs,
        sender_email,
        recipient_email,
        outbound_message_id=outbound_message_id,
    )
    if not latest_reply:
        return
    clean_reply = _clean_reply_body(latest_reply.get("body") or "")
    if not clean_reply:
        return
    act = get_action_by_id(action_id)
    marker = f"gmail_reply:{latest_reply.get('id')}"
    if not act:
        return
    # Update if marker changed OR captured text differs.
    if (act.get("response_type") or "") == marker and (act.get("response_content") or "").strip() == clean_reply:
        return
    update_action(
        action_id,
        response_content=clean_reply[:50000],
        response_type=marker,
    )
    adapted = _adapt_following_for_reply(action_id, clean_reply)
    if adapted > 0:
        log_user(
            log,
            uid,
            sender_email,
            f"Reply captured for {recipient_email}; adapted {adapted} following steps (action id {action_id})",
        )


# Domains we ignore when matching recipient by default.
# Keep personal inbox domains excluded in normal operation to avoid logging internal tests/noise.
IGNORE_DOMAINS = {"lh2holdings.com", "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"}


def run_sync(newer_than_hours=24, max_messages_per_user=50, allow_personal_recipients=False):
    """
    For each Command Center user with an email, list their sent mail and record any
    email sent to a contact in the DB. Returns counts: processed, skipped, errors.
    """
    from googleapiclient.discovery import build

    users = [u for u in get_all_users() if (u.get("email") or "").strip()]
    if not users:
        log.warning("No users with email in DB. Set users.email for users who send outreach.")
        return {"processed": 0, "skipped": 0, "errors": 0}

    processed = 0
    skipped = 0
    errors = 0

    for user in users:
        email = (user.get("email") or "").strip().lower()
        uid = user.get("id")
        if not email:
            continue
        creds = get_google_credentials(email, scope_set="gmail_read")
        if not creds:
            log_user(log, uid, email, "No Gmail read credentials — run once to authorize (or use Connect Gmail in app).", level="warning")
            errors += 1
            continue

        try:
            service = build("gmail", "v1", credentials=creds)
        except Exception as e:
            log_user(log, uid, email, f"Gmail build failed: {e}", level="warning")
            errors += 1
            continue

        sent_list = fetch_sent_messages(service, newer_than_hours=newer_than_hours, max_results=max_messages_per_user)
        for sm in sent_list:
            # Sender should be this user's email
            from_emails = sm.get("from_emails") or []
            if email not in from_emails:
                skipped += 1
                continue

            ignored_domains = {"lh2holdings.com"} if allow_personal_recipients else IGNORE_DOMAINS
            to_emails = [e for e in (sm.get("to_emails") or []) if e.split("@")[-1].lower() not in ignored_domains]
            if not to_emails:
                skipped += 1
                continue

            # First external recipient — record only this outbound message body.
            recipient = to_emails[0]
            thread_id = sm.get("thread_id")
            body_for_record = _clean_outbound_body(sm.get("body") or "")

            aid, err = record_sent_email(
                sender_email=email,
                recipient_email=recipient,
                subject=sm.get("subject") or "",
                body=body_for_record,
                gmail_message_id=sm["id"],
                sent_at=sm.get("sent_at"),
            )
            if err == "already_processed":
                skipped += 1
                # Already logged send event: still try to refresh latest reply + sequence adaptation.
                try:
                    existing = _find_best_existing_action_for_sent(email, recipient, sm.get("subject") or "")
                    if existing and existing.get("id"):
                        _capture_reply_and_adapt_for_action(
                            service=service,
                            thread_id=thread_id,
                            action_id=int(existing["id"]),
                            sender_email=email,
                            recipient_email=recipient,
                            outbound_message_id=sm.get("id"),
                            uid=uid,
                        )
                except Exception as e:
                    log_user(log, uid, email, f"Reply refresh (already_processed) failed: {e}", level="warning")
            elif err:
                if err in ("sender_not_found", "recipient_not_found"):
                    skipped += 1
                else:
                    log_user(log, uid, email, f"record_sent_email failed: {err}", level="warning")
                    errors += 1
            else:
                processed += 1
                log_user(log, uid, email, "Recorded -> %s (action id %s)" % (recipient, aid))
                try:
                    # Prefer mapping this send to an existing planned sequence step.
                    existing = _find_best_existing_action_for_sent(email, recipient, sm.get("subject") or "")
                    target_action_id = int(existing["id"]) if existing and existing.get("id") else int(aid)
                    if existing and int(existing["id"]) != int(aid):
                        update_action(
                            int(existing["id"]),
                            status="completed",
                            content_sent=(body_for_record[:50000] if body_for_record else None),
                        )
                    _capture_reply_and_adapt_for_action(
                        service=service,
                        thread_id=thread_id,
                        action_id=target_action_id,
                        sender_email=email,
                        recipient_email=recipient,
                        outbound_message_id=sm.get("id"),
                        uid=uid,
                    )
                except Exception as e:
                    log_user(log, uid, email, f"Reply capture/adapt failed: {e}", level="warning")

    return {"processed": processed, "skipped": skipped, "errors": errors}


def main():
    import argparse
    p = argparse.ArgumentParser(description="Sync Gmail sent mail to Command Center timeline/actions.")
    p.add_argument("--hours", type=int, default=24, help="Look at sent mail from last N hours (default 24)")
    p.add_argument("--max", type=int, default=50, help="Max sent messages per user (default 50)")
    p.add_argument("--once", action="store_true", help="Run once and exit (default: run once)")
    p.add_argument("--loop", type=int, default=0, metavar="MINUTES", help="Run every N minutes (0 = run once)")
    p.add_argument(
        "--allow-personal-recipients",
        action="store_true",
        help="Include personal inbox domains (gmail/outlook/yahoo/etc.) for local testing.",
    )
    p.add_argument(
        "--inbox-sync",
        action="store_true",
        help="After sent sync, run full Gmail inbox+sent import + outreach correlation (same as outreach_poll).",
    )
    args = p.parse_args()

    log.info(
        "Gmail Sent Sync — checking last %s hours, max %s messages per user (allow_personal_recipients=%s)",
        args.hours,
        args.max,
        bool(args.allow_personal_recipients),
    )

    if args.loop and args.loop > 0:
        import time
        while True:
            result = run_sync(
                newer_than_hours=args.hours,
                max_messages_per_user=args.max,
                allow_personal_recipients=args.allow_personal_recipients,
            )
            log.info("Cycle: processed=%s skipped=%s errors=%s", result["processed"], result["skipped"], result["errors"])
            time.sleep(args.loop * 60)
    else:
        result = run_sync(
            newer_than_hours=args.hours,
            max_messages_per_user=args.max,
            allow_personal_recipients=args.allow_personal_recipients,
        )
        log.info("Done: processed=%s skipped=%s errors=%s", result["processed"], result["skipped"], result["errors"])
        if args.inbox_sync:
            try:
                from db_ops import init_db

                init_db()
                from services.gmail_sync_service import sync_all_accounts

                newer_days = max(1, int((int(args.hours) + 23) / 24))
                inbox_res = sync_all_accounts(
                    max_messages=max(50, int(args.max) * 4),
                    newer_than_days=newer_days,
                    only_known_contacts=True,
                )
                log.info("Inbox+correlation sync: %s accounts", len(inbox_res))
            except Exception as e:
                log.warning("inbox-sync failed: %s", e)


if __name__ == "__main__":
    main()
