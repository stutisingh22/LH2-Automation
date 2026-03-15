"""
Unified Google OAuth token handling for all Gmail (and optional Drive) flows.

Token layout:
  - Directory: tokens/ (project root).
  - Naming: {sanitized_email}_gmail_compose.json, {sanitized_email}_gmail_read.json
  - One credentials.json in project root; all users authorize through it; tokens are per-account.
"""

import re
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Project root so tokens/ and credentials.json stay at repo root
_APP_DIR = Path(__file__).resolve().parent.parent.parent
_TOKEN_DIR = _APP_DIR / "tokens"
_CREDS_FILE = _APP_DIR / "credentials.json"

SCOPES = {
    "gmail_compose": ["https://www.googleapis.com/auth/gmail.compose"],
    "gmail_read": ["https://www.googleapis.com/auth/gmail.readonly"],
}


def _sanitize_account_key(email_or_username):
    """Safe filename: replace invalid chars with underscore."""
    if not email_or_username:
        return ""
    s = str(email_or_username).strip().lower()
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    return s or "default"


def _token_path(account_email, scope_set):
    """Path to token file for account and scope set. scope_set in ('gmail_compose', 'gmail_read')."""
    key = _sanitize_account_key(account_email)
    if scope_set not in SCOPES:
        raise ValueError(f"scope_set must be one of {list(SCOPES)}")
    suffix = "gmail_compose" if scope_set == "gmail_compose" else "gmail_read"
    _TOKEN_DIR.mkdir(exist_ok=True)
    return _TOKEN_DIR / f"{key}_{suffix}.json"


def _migrate_old_token_if_exists(account_email, scope_set):
    """One-time: rename old token files to new naming so existing users don't re-auth."""
    key = _sanitize_account_key(account_email)
    new_path = _token_path(account_email, scope_set)
    if new_path.exists():
        return
    if scope_set == "gmail_compose":
        old_path = _TOKEN_DIR / f"{key}_token.json"
    else:
        old_path = _TOKEN_DIR / f"{key}_read_token.json"
    if old_path.exists():
        try:
            old_path.rename(new_path)
            log.info("Migrated token %s -> %s", old_path.name, new_path.name)
        except Exception as e:
            log.warning("Token migration failed %s -> %s: %s", old_path, new_path, e)


def get_google_credentials(account_email, scope_set="gmail_compose"):
    """
    Load or obtain OAuth credentials for the given account and scope set.

    Args:
        account_email: User email (or username fallback) used for token file path.
        scope_set: "gmail_compose" or "gmail_read".

    Returns:
        Google credentials object or None on failure.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        log.warning("Google auth packages not installed (google-auth, google-auth-oauthlib)")
        return None

    _migrate_old_token_if_exists(account_email, scope_set)
    path = _token_path(account_email, scope_set)
    scopes = SCOPES[scope_set]
    creds = None

    if path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(path), scopes)
        except Exception as e:
            log.warning("Could not load token %s: %s", path, e)

    if creds and creds.expired and getattr(creds, "refresh_token", None):
        try:
            creds.refresh(Request())
            path.write_text(creds.to_json())
        except Exception as e:
            log.warning("Token refresh failed: %s", e)
            creds = None

    if (not creds or not creds.valid) and _CREDS_FILE.exists():
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_FILE), scopes)
            creds = flow.run_local_server(port=0)
            path.write_text(creds.to_json())
            log.info("Saved %s token for %s", scope_set, account_email)
        except Exception as e:
            log.warning("OAuth flow failed: %s", e)
            return None

    return creds


def log_user(logger, user_id, email, message, level="info"):
    """Log with user_id and account (email) prefix for filtering. level: info, warning, error, debug."""
    prefix = f"[user_id={user_id}|{email or '?'}] "
    getattr(logger, level)(prefix + str(message))
