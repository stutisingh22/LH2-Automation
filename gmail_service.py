"""
gmail_service.py — Gmail Draft integration (OAuth + drafts.create)

- Token storage: tokens/{user_email}_token.json (per-account).
- Scope: gmail.compose only.
- No Streamlit UI in this module.
"""

import base64
import json
import os
import re
import logging
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_CREDS_FILE = os.path.join(_APP_DIR, "credentials.json")
_TOKEN_DIR = os.path.join(_APP_DIR, "tokens")

GMAIL_SCOPE = ["https://www.googleapis.com/auth/gmail.compose"]


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


def _sanitize_account_key(email_or_username):
    """Safe filename: replace invalid chars with underscore."""
    if not email_or_username:
        return ""
    s = str(email_or_username).strip().lower()
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    return s or "default"


def _token_path(account_key):
    key = _sanitize_account_key(account_key)
    os.makedirs(_TOKEN_DIR, exist_ok=True)
    return os.path.join(_TOKEN_DIR, f"{key}_token.json")


def get_credentials(account_key):
    """
    Load or obtain OAuth credentials for the given account.
    account_key: user email or username (used for token file path).
    Returns credentials object or None on failure.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        log.warning("Google auth packages not installed")
        return None

    path = _token_path(account_key)
    creds = None

    if os.path.exists(path):
        try:
            creds = Credentials.from_authorized_user_file(path, GMAIL_SCOPE)
        except Exception as e:
            log.warning("Could not load token %s: %s", path, e)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(path, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            log.warning("Token refresh failed: %s", e)
            creds = None

    if not creds and os.path.exists(_CREDS_FILE):
        try:
            flow = InstalledAppFlow.from_client_secrets_file(_CREDS_FILE, GMAIL_SCOPE)
            creds = flow.run_local_server(port=0)
            with open(path, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            log.warning("OAuth flow failed: %s", e)
            return None

    return creds


def create_gmail_draft(user_email, to_email, subject, body):
    """
    Create a Gmail draft for the given account. Does not send the email.

    Args:
        user_email: account key (email or username) for token lookup.
        to_email: recipient address.
        subject: draft subject line.
        body: draft body (plain text).

    Returns:
        dict: {"success": bool, "draft_id": str|None, "error": str|None}
    """
    result = {"success": False, "draft_id": None, "error": None}

    try:
        creds = get_credentials(user_email)
        if not creds:
            result["error"] = "Authentication required."
            return result

        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)

        message = MIMEText(body or "", "plain", "utf-8")
        message["to"] = to_email or ""
        message["subject"] = subject or "(No subject)"
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body_param = {"message": {"raw": raw}}

        draft = service.users().drafts().create(userId="me", body=body_param).execute()
        result["success"] = True
        result["draft_id"] = draft.get("id")
    except Exception as e:
        log.exception("Gmail draft create failed")
        result["error"] = str(e) if str(e) else "Failed to create draft."
    return result
