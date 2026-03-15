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
from datetime import datetime
from pathlib import Path

# Project root so imports and credentials work (standalone/sync/ -> parent.parent.parent)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from dotenv import load_dotenv
load_dotenv(_ROOT / "keys.env")
load_dotenv(_ROOT / ".env")

from db_ops import get_all_users, record_sent_email
from integrations.gmail import get_google_credentials, log_user

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
                sent_at = datetime.utcfromtimestamp(int(internal_date) / 1000) if internal_date else None
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
            ts = datetime.utcfromtimestamp(int(internal_date) / 1000).strftime("%Y-%m-%d %H:%M UTC") if internal_date else date_val or "—"
        except Exception:
            ts = date_val or "—"
        block = f"--- Message {i + 1} | From: {from_val} | To: {to_val} | {ts} ---\n\n{(body or '').strip()}"
        if total + len(block) > max_chars:
            block = block[: max_chars - total - 20] + "\n...[truncated]"
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts)


# Domains we ignore when matching recipient (internal)
IGNORE_DOMAINS = {"lh2holdings.com", "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"}


def run_sync(newer_than_hours=24, max_messages_per_user=50):
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

            to_emails = [e for e in (sm.get("to_emails") or []) if e.split("@")[-1].lower() not in IGNORE_DOMAINS]
            if not to_emails:
                skipped += 1
                continue

            # First external recipient — fetch full thread then record
            recipient = to_emails[0]
            thread_id = sm.get("thread_id")
            if thread_id:
                body_for_record = fetch_full_thread(service, thread_id)
                if not body_for_record.strip():
                    body_for_record = sm.get("body") or ""
            else:
                body_for_record = sm.get("body") or ""

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
            elif err:
                if err in ("sender_not_found", "recipient_not_found"):
                    skipped += 1
                else:
                    log_user(log, uid, email, f"record_sent_email failed: {err}", level="warning")
                    errors += 1
            else:
                processed += 1
                log_user(log, uid, email, "Recorded -> %s (action id %s)" % (recipient, aid))

    return {"processed": processed, "skipped": skipped, "errors": errors}


def main():
    import argparse
    p = argparse.ArgumentParser(description="Sync Gmail sent mail to Command Center timeline/actions.")
    p.add_argument("--hours", type=int, default=24, help="Look at sent mail from last N hours (default 24)")
    p.add_argument("--max", type=int, default=50, help="Max sent messages per user (default 50)")
    p.add_argument("--once", action="store_true", help="Run once and exit (default: run once)")
    p.add_argument("--loop", type=int, default=0, metavar="MINUTES", help="Run every N minutes (0 = run once)")
    args = p.parse_args()

    log.info("Gmail Sent Sync — checking last %s hours, max %s messages per user", args.hours, args.max)

    if args.loop and args.loop > 0:
        import time
        while True:
            result = run_sync(newer_than_hours=args.hours, max_messages_per_user=args.max)
            log.info("Cycle: processed=%s skipped=%s errors=%s", result["processed"], result["skipped"], result["errors"])
            time.sleep(args.loop * 60)
    else:
        result = run_sync(newer_than_hours=args.hours, max_messages_per_user=args.max)
        log.info("Done: processed=%s skipped=%s errors=%s", result["processed"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
