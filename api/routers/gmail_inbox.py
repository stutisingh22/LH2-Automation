"""
REST API for synced Gmail threads/messages (tokens never returned).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_current_user
from db_ops import (
    get_known_contact_emails,
    get_gmail_account_by_id,
    get_inbox_thread_detail,
    get_user,
    list_gmail_accounts_for_user,
    list_inbox_emails_for_user,
    list_inbox_threads_for_user,
    update_gmail_account_sync_run,
)
from services.gmail_sync_service import sync_all_accounts, sync_gmail_account

log = logging.getLogger(__name__)

router = APIRouter(prefix="/gmail", tags=["gmail-inbox"])


class SyncBody(BaseModel):
    user_id: int | None = None
    account_id: int | None = None
    newer_than_days: int = 14
    max_messages: int = 200
    only_known_contacts: bool = True


def _effective_user_id(current: dict, user_id: int | None) -> int:
    if user_id is None or user_id == current["id"]:
        return current["id"]
    if (current.get("role") or "").strip() != "admin":
        raise HTTPException(status_code=403, detail="Only admins may filter by user_id")
    if not get_user(user_id):
        raise HTTPException(status_code=404, detail="user not found")
    return user_id


def _account_ids_for_sync(current: dict, body: SyncBody) -> list[int]:
    target = _effective_user_id(current, body.user_id)
    if body.account_id is not None:
        acc = get_gmail_account_by_id(body.account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="account not found")
        if acc["user_id"] != target:
            raise HTTPException(status_code=400, detail="account does not belong to selected user")
        if acc["user_id"] != current["id"] and (current.get("role") or "").strip() != "admin":
            raise HTTPException(status_code=403, detail="cannot sync another user's mailbox")
        return [body.account_id]
    return [a["id"] for a in list_gmail_accounts_for_user(target)]


@router.get("/connections")
def gmail_connections(
    user_id: int | None = None,
    user: dict = Depends(get_current_user),
):
    uid = _effective_user_id(user, user_id)
    return {"accounts": list_gmail_accounts_for_user(uid)}


@router.get("/diagnostics")
def gmail_diagnostics(
    user_id: int | None = None,
    user: dict = Depends(get_current_user),
):
    """
    Operational snapshot: last sync time, errors, and counters (inserted/mapped/errors) per connected account.
    """
    uid = _effective_user_id(user, user_id)
    accounts = list_gmail_accounts_for_user(uid)
    return {"user_id": uid, "accounts": accounts}


@router.post("/poll-sync")
def poll_sync_all(
    user: dict = Depends(get_current_user),
    newer_than_days: int = Query(14, ge=1, le=90),
    max_messages: int = Query(200, ge=1, le=500),
):
    """
    Idempotent full sync for all accounts the current user can access (own accounts).
    Intended for cron or a 5–10 minute poller; same code path as POST /gmail/sync.
    """
    if (user.get("role") or "").strip() != "admin":
        # non-admin: only their accounts via sync endpoint pattern
        ids = [a["id"] for a in list_gmail_accounts_for_user(user["id"])]
        if not ids:
            return {"ok": True, "message": "no_connected_accounts", "results": []}
        results = []
        for aid in ids:
            try:
                r = sync_gmail_account(
                    aid,
                    max_messages=max_messages,
                    newer_than_days=newer_than_days,
                    only_known_contacts=True,
                )
                results.append({"account_id": aid, **r})
            except Exception as e:
                log.exception("poll sync %s: %s", aid, e)
                err = str(e)
                update_gmail_account_sync_run(aid, ok=False, error=err, stats={"phase": "poll_sync"})
                results.append({"account_id": aid, "ok": False, "error": err})
        return {"ok": True, "results": results}
    results = sync_all_accounts(
        max_messages=max_messages,
        newer_than_days=newer_than_days,
        only_known_contacts=True,
    )
    return {"ok": True, "results": results}


@router.get("/threads")
def list_threads(
    user_id: int | None = None,
    limit: int = Query(200, ge=1, le=500),
    only_known_contacts: bool = Query(True, description="Only emails tied to contact emails in DB"),
    user: dict = Depends(get_current_user),
):
    uid = _effective_user_id(user, user_id)
    known = get_known_contact_emails() if only_known_contacts else None
    return {"threads": list_inbox_threads_for_user(uid, limit=limit, known_emails=known)}


@router.get("/emails")
def list_emails(
    user_id: int | None = None,
    thread_id: int | None = Query(None, description="Internal email_threads.id"),
    limit: int = Query(300, ge=1, le=1000),
    only_known_contacts: bool = Query(True, description="Only emails tied to contact emails in DB"),
    user: dict = Depends(get_current_user),
):
    uid = _effective_user_id(user, user_id)
    known = get_known_contact_emails() if only_known_contacts else None
    return {"emails": list_inbox_emails_for_user(uid, limit=limit, thread_pk=thread_id, known_emails=known)}


@router.get("/threads/{thread_pk}")
def thread_detail(
    thread_pk: int,
    user_id: int | None = None,
    only_known_contacts: bool = Query(True, description="Only emails tied to contact emails in DB"),
    user: dict = Depends(get_current_user),
):
    uid = _effective_user_id(user, user_id)
    known = get_known_contact_emails() if only_known_contacts else None
    detail = get_inbox_thread_detail(thread_pk, uid, known_emails=known)
    if not detail:
        raise HTTPException(status_code=404, detail="thread not found")
    return detail


@router.post("/sync")
def trigger_sync(body: SyncBody, user: dict = Depends(get_current_user)):
    ids = _account_ids_for_sync(user, body)
    if not ids:
        return {"ok": True, "message": "no_connected_accounts", "results": []}
    results = []
    for aid in ids:
        try:
            r = sync_gmail_account(
                aid,
                max_messages=body.max_messages,
                newer_than_days=body.newer_than_days,
                only_known_contacts=body.only_known_contacts,
            )
            results.append({"account_id": aid, **r})
        except Exception as e:
            log.exception("sync account %s: %s", aid, e)
            err = str(e)
            update_gmail_account_sync_run(aid, ok=False, error=err, stats={"phase": "manual_sync"})
            results.append({"account_id": aid, "ok": False, "error": err})
    return {"ok": True, "results": results}
