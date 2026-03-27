"""
Gmail OAuth + token crypto + API helpers for multi-user inbox sync.
Never exposes tokens to the frontend; use only server-side.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from cryptography.fernet import Fernet
from jose import JWTError, jwt

from api.settings import JWT_ALGORITHM, JWT_SECRET
from config import _config_dir

log = logging.getLogger(__name__)

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def _parse_downloaded_client_json(path: Path) -> dict[str, Any]:
    """Parse Google Cloud 'Download JSON' client file (web or installed)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    web = data.get("web") or data.get("installed")
    if not web:
        return {}
    return {
        "client_id": (web.get("client_id") or "").strip(),
        "client_secret": (web.get("client_secret") or "").strip(),
        "redirect_uris": [u for u in (web.get("redirect_uris") or []) if u],
    }


def _pick_among_client_secret_json(matches: list[Path]) -> Path | None:
    """When several `client_secret*.json` exist, disambiguate without deleting files."""
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    matches = sorted(matches, key=lambda p: p.name.lower())
    redirect_hint = (
        os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or os.getenv("GOOGLE_REDIRECT_URI") or ""
    ).strip()
    if redirect_hint:
        for p in matches:
            try:
                meta = _parse_downloaded_client_json(p)
                if redirect_hint in (meta.get("redirect_uris") or []):
                    log.info("Using %s (redirect URIs include GOOGLE_OAUTH_REDIRECT_URI)", p.name)
                    return p
            except Exception:
                continue
        log.warning(
            "GOOGLE_OAUTH_REDIRECT_URI=%s not listed in any client_secret*.json — using %s",
            redirect_hint,
            matches[0].name,
        )
        return matches[0]
    # No env hint: prefer the file that lists the most redirect URIs (usually the newest download).
    best: Path | None = None
    best_n = -1
    for p in matches:
        try:
            meta = _parse_downloaded_client_json(p)
            n = len(meta.get("redirect_uris") or [])
            if n > best_n:
                best_n = n
                best = p
        except Exception:
            continue
    if best is not None:
        log.info(
            "Multiple client_secret*.json files; using %s (%s redirect URIs). "
            "Set GOOGLE_OAUTH_CREDENTIALS_PATH or GOOGLE_OAUTH_REDIRECT_URI to pin one file.",
            best.name,
            best_n,
        )
        return best
    return matches[0]


def _credentials_json_path() -> Path | None:
    explicit = (os.getenv("GOOGLE_OAUTH_CREDENTIALS_PATH") or "").strip()
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = _config_dir / p
        return p if p.is_file() else None
    matches = list(_config_dir.glob("client_secret*.json"))
    return _pick_among_client_secret_json(matches)


_creds_file = _credentials_json_path()
_oauth_from_file: dict[str, Any] = {}
if _creds_file:
    try:
        _oauth_from_file = _parse_downloaded_client_json(_creds_file)
        log.info("Loaded Google OAuth client from %s", _creds_file.name)
    except Exception as e:
        log.warning("Could not read %s: %s", _creds_file, e)

GOOGLE_CLIENT_ID = (
    (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    or _oauth_from_file.get("client_id", "")
)
GOOGLE_CLIENT_SECRET = (
    (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    or _oauth_from_file.get("client_secret", "")
)

_redirect_env = (
    os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or os.getenv("GOOGLE_REDIRECT_URI") or ""
).strip()
_file_redirects = _oauth_from_file.get("redirect_uris") or []
_default_from_file = (_file_redirects[0] or "").strip() if _file_redirects else ""


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


_profile = (os.getenv("LH2_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
_is_dev_profile = _profile in ("development", "dev", "local") or _env_truthy("LH2_LOCAL_GOOGLE_OAUTH")

_api_port = (os.getenv("API_PORT") or os.getenv("UVICORN_PORT") or "8000").strip()
_local_api_base = (os.getenv("LH2_LOCAL_API_BASE") or "").strip().rstrip("/")
if not _local_api_base:
    _local_api_base = f"http://127.0.0.1:{_api_port}"
# Canonical callback path for dev/prod consistency.
_local_oauth_callback = f"{_local_api_base}/api/gmail/oauth/callback"

# Web redirect must hit the API (backend receives the authorization code).
# In dev, never take production imperium URLs from downloaded JSON unless explicitly overridden.
if _redirect_env:
    GOOGLE_REDIRECT_URI = _redirect_env
elif _is_dev_profile:
    GOOGLE_REDIRECT_URI = _local_oauth_callback
    log.info(
        "Dev OAuth: GOOGLE_REDIRECT_URI=%s (authorize this exact URI in Google Cloud → Credentials)",
        GOOGLE_REDIRECT_URI,
    )
elif _default_from_file:
    GOOGLE_REDIRECT_URI = _default_from_file
else:
    GOOGLE_REDIRECT_URI = "http://127.0.0.1:8000/api/auth/google/callback"

GOOGLE_REDIRECT_URI = GOOGLE_REDIRECT_URI.strip()

_frontend_default = "http://localhost:3002" if _is_dev_profile else "http://localhost:5173"
FRONTEND_URL = (os.getenv("FRONTEND_URL") or _frontend_default).strip().rstrip("/")

CREDENTIALS_PATH = _config_dir / "credentials.json"


def oauth_config_debug() -> dict[str, Any]:
    """Safe debug snapshot to confirm active OAuth config at runtime."""
    secret = GOOGLE_CLIENT_SECRET or ""
    secret_fp = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12] if secret else ""
    return {
        "client_id_tail": GOOGLE_CLIENT_ID[-14:] if GOOGLE_CLIENT_ID else "",
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "frontend_url": FRONTEND_URL,
        "creds_file": str(_creds_file) if _creds_file else None,
        "from_env_client_id": bool((os.getenv("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()),
        "from_env_client_secret": bool((os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()),
        "secret_fingerprint": secret_fp,
    }


def _fernet() -> Fernet:
    raw = (os.getenv("GMAIL_TOKEN_FERNET_KEY") or "").strip()
    if raw:
        return Fernet(raw.encode() if len(raw) == 44 else base64.urlsafe_b64decode(raw))
    key = base64.urlsafe_b64encode(hashlib.sha256(JWT_SECRET.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(blob: str | None) -> str:
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob.encode("utf-8")).decode("utf-8")
    except Exception as e:
        log.warning("Token decrypt failed: %s", e)
        return ""


def create_oauth_state(user_id: int, ttl_seconds: int = 1800) -> str:
    now = int(time.time())
    payload = {"uid": user_id, "purpose": "gmail_oauth", "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_oauth_state(state: str) -> int | None:
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("purpose") != "gmail_oauth":
            return None
        return int(payload["uid"])
    except (JWTError, TypeError, ValueError, KeyError):
        return None


def build_authorization_url(state: str) -> str:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "Set GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET in keys.env, "
            "or add a single client_secret*.json from Google Cloud in the project root."
        )
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": GMAIL_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth client not configured")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        },
        scopes=[GMAIL_READONLY_SCOPE],
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    flow.fetch_token(code=code)
    creds: Credentials = flow.credentials
    return {
        "access_token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None) or "",
        "expiry": creds.expiry,
    }


def credentials_from_stored(
    access_token: str,
    refresh_token: str,
    expiry: datetime | None,
) -> Credentials | None:
    try:
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID or None,
            client_secret=GOOGLE_CLIENT_SECRET or None,
            scopes=[GMAIL_READONLY_SCOPE],
        )
        if expiry:
            creds.expiry = to_naive_utc(expiry)
        return creds
    except Exception as e:
        log.warning("credentials_from_stored: %s", e)
        return None


def refresh_if_needed(
    access_token: str,
    refresh_token: str,
    expiry: datetime | None,
) -> tuple[str, str, datetime | None]:
    """Return updated (access_token, refresh_token, expiry)."""
    creds = credentials_from_stored(access_token, refresh_token, expiry)
    if not creds:
        return access_token, refresh_token, expiry
    exp = to_naive_utc(getattr(creds, "expiry", None) or expiry)
    should_refresh = bool(creds.refresh_token and exp and exp <= (datetime.utcnow() + timedelta(seconds=30)))
    if should_refresh:
        try:
            creds.refresh(Request())
            return creds.token, creds.refresh_token or refresh_token, to_naive_utc(creds.expiry)
        except Exception as e:
            log.warning("Gmail token refresh failed: %s", e)
    return access_token, refresh_token, to_naive_utc(expiry)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalize datetime for DB/storage + comparisons (UTC naive)."""
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_addresses(header_value: str) -> list[str]:
    if not header_value:
        return []
    emails = re.findall(r"[\w.\-+]+@[\w.\-]+\.\w+", header_value)
    return [e.lower().strip() for e in emails]


def get_header(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if (h.get("name") or "").strip().lower() == name.strip().lower():
            return (h.get("value") or "").strip()
    return ""


def extract_plain_body(payload: dict) -> str:
    import base64

    def _decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return ""

    if payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    parts = payload.get("parts") or []
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _decode(part["body"]["data"])
    for part in parts:
        if part.get("mimeType", "").startswith("multipart/"):
            inner = extract_plain_body(part)
            if inner:
                return inner
    return ""
