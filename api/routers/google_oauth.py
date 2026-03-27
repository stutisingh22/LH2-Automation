"""
Google OAuth for Gmail (readonly). Callback is unauthenticated; state embeds app user id (JWT).
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from api.deps import get_current_user
from db_ops import upsert_gmail_account
from services.gmail_service import (
    FRONTEND_URL,
    GMAIL_READONLY_SCOPE,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    build_authorization_url,
    create_oauth_state,
    decode_oauth_state,
    encrypt_secret,
    exchange_code_for_tokens,
    oauth_config_debug,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["google-oauth"])

# Same handler as /api/auth/google/callback — Google Cloud often uses /api/gmail/oauth/callback
gmail_callback_router = APIRouter(tags=["google-oauth"])


def _handle_oauth_callback(code: str | None, state: str | None, error: str | None) -> RedirectResponse:
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/gmail-inbox?error={quote(error)}")
    uid = decode_oauth_state(state or "")
    if uid is None or not code:
        return RedirectResponse(f"{FRONTEND_URL}/gmail-inbox?error=invalid_oauth_state")
    try:
        tokens = exchange_code_for_tokens(code)
    except Exception as e:
        log.exception("exchange_code: %s", e)
        detail = str(e).strip() or "token_exchange_failed"
        # Bubble up a compact reason to unblock local OAuth debugging.
        return RedirectResponse(f"{FRONTEND_URL}/gmail-inbox?error={quote(f'token_exchange_failed:{detail[:160]}')}")

    refresh_plain = (tokens.get("refresh_token") or "").strip()
    access_plain = tokens.get("access_token") or ""
    expiry = tokens.get("expiry")

    try:
        creds = Credentials(
            token=access_plain,
            refresh_token=refresh_plain or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID or None,
            client_secret=GOOGLE_CLIENT_SECRET or None,
            scopes=[GMAIL_READONLY_SCOPE],
        )
        if expiry:
            creds.expiry = expiry
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        prof = service.users().getProfile(userId="me").execute()
        email = (prof.get("emailAddress") or "").strip().lower()
    except Exception as e:
        log.exception("gmail profile: %s", e)
        return RedirectResponse(f"{FRONTEND_URL}/gmail-inbox?error={quote('profile_failed')}")

    if not email:
        return RedirectResponse(f"{FRONTEND_URL}/gmail-inbox?error=no_email")

    access_enc = encrypt_secret(access_plain)
    refresh_enc = encrypt_secret(refresh_plain) if refresh_plain else None
    try:
        upsert_gmail_account(uid, email, access_enc, refresh_enc, expiry)
    except Exception as e:
        log.exception("upsert gmail account: %s", e)
        return RedirectResponse(f"{FRONTEND_URL}/gmail-inbox?error={quote('save_failed')}")

    return RedirectResponse(f"{FRONTEND_URL}/gmail-inbox?connected=1")


@router.get("/google/login")
def google_login(user: dict = Depends(get_current_user)):
    state = create_oauth_state(user["id"])
    try:
        url = build_authorization_url(state)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "url": url,
        # Useful in dev to verify callback target isn't production.
        "redirect_uri": GOOGLE_REDIRECT_URI,
    }


@router.get("/google/debug")
def google_debug(user: dict = Depends(get_current_user)):
    del user
    return oauth_config_debug()


@router.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    return _handle_oauth_callback(code, state, error)


@gmail_callback_router.get("/gmail/oauth/callback")
def google_callback_gmail_path(code: str | None = None, state: str | None = None, error: str | None = None):
    return _handle_oauth_callback(code, state, error)
