"""
Gmail Draft integration (OAuth + drafts.create).

Uses .auth for tokens: tokens/{email}_gmail_compose.json.
Scope: gmail.compose only.
"""

import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .auth import get_google_credentials

log = logging.getLogger(__name__)


def parse_email_content(content):
    """
    Parse 'Subject: ...\\nbody' or plain content into (subject, body).
    Returns (subject, body) as strings.
    """
    if not (content or "").strip():
        return "", ""
    lines = (content or "").strip().split("\n")
    subject = ""
    body_lines = []
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("subject:"):
            subject = line.split(":", 1)[-1].strip()
            body_lines = lines[i + 1:]
            break
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    return subject, body


def create_gmail_draft(user_email, to_email, subject, body, body_html=None, allow_interactive=True):
    """
    Create a Gmail draft for the given account. Does not send the email.

    Args:
        user_email: account key (email or username) for token lookup.
        to_email: recipient address.
        subject: draft subject line.
        body: draft body (plain text).
        body_html: optional HTML body; if set, message is multipart/alternative (plain + HTML).

    Returns:
        dict: {"success": bool, "draft_id": str|None, "error": str|None}
    """
    result = {"success": False, "draft_id": None, "error": None}

    try:
        creds = get_google_credentials(
            user_email, scope_set="gmail_compose", allow_interactive=allow_interactive
        )
        if not creds:
            result["error"] = "Authentication required."
            return result

        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)

        if body_html and (body_html or "").strip():
            message = MIMEMultipart("alternative")
            message["to"] = to_email or ""
            message["subject"] = subject or "(No subject)"
            message.attach(MIMEText(body or "", "plain", "utf-8"))
            message.attach(MIMEText(body_html.strip(), "html", "utf-8"))
        else:
            message = MIMEText(body or "", "plain", "utf-8")
            message["to"] = to_email or ""
            message["subject"] = subject or "(No subject)"

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body_param = {"message": {"raw": raw}}

        draft = service.users().drafts().create(userId="me", body=body_param).execute()
        result["success"] = True
        result["draft_id"] = draft.get("id")
        log.info("Gmail draft created for %s (draft_id=%s)", user_email, result["draft_id"])
    except Exception as e:
        log.exception("Gmail draft create failed")
        result["error"] = str(e) if str(e) else "Failed to create draft."
    return result
