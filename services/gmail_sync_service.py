"""
Background/manual Gmail sync: pull recent messages into email_threads / emails tables.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from googleapiclient.discovery import build

from db_ops import (
    get_inbox_email_by_gmail_id,
    upsert_email_thread,
    upsert_inbox_email,
    update_gmail_account_tokens,
    update_gmail_account_sync_run,
    list_gmail_accounts,
    get_gmail_account_by_id,
)
from services.outreach_correlator import correlate_outreach_from_gmail_message
from services.gmail_service import (
    decrypt_secret,
    encrypt_secret,
    extract_plain_body,
    get_header,
    parse_addresses,
    refresh_if_needed,
    to_naive_utc,
)

log = logging.getLogger(__name__)


def _parse_internal_date(ms: str | None) -> datetime | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except Exception:
        return None


def sync_gmail_account(
    account_id: int,
    *,
    max_messages: int = 200,
    newer_than_days: int = 14,
    only_known_contacts: bool = True,
) -> dict:
    """
    Fetch recent inbox + sent for one connected account; upsert threads/messages.
    """
    row = get_gmail_account_by_id(account_id)
    if not row:
        return {"ok": False, "error": "account_not_found", "inserted": 0}

    access = decrypt_secret(row["access_token_enc"])
    refresh = decrypt_secret(row["refresh_token_enc"] or "")
    expiry = row["token_expiry"]
    if isinstance(expiry, str):
        try:
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except Exception:
            expiry = None

    access, refresh, expiry = refresh_if_needed(access, refresh, expiry)
    expiry = to_naive_utc(expiry)
    update_gmail_account_tokens(
        account_id,
        encrypt_secret(access),
        encrypt_secret(refresh) if refresh else None,
        expiry,
    )

    try:
        from google.oauth2.credentials import Credentials
        from services.gmail_service import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GMAIL_READONLY_SCOPE

        creds = Credentials(
            token=access,
            refresh_token=refresh or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID or None,
            client_secret=GOOGLE_CLIENT_SECRET or None,
            scopes=[GMAIL_READONLY_SCOPE],
        )
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        log.exception("Gmail build failed: %s", e)
        err = str(e)
        update_gmail_account_sync_run(account_id, ok=False, error=err, stats={"phase": "gmail_build"})
        return {"ok": False, "error": err, "inserted": 0}

    user_id = row["user_id"]
    account_email = (row["email"] or "").strip().lower()
    query = f"newer_than:{newer_than_days}d"
    inserted = 0
    examined_total = 0
    mapped_outreach = 0
    skipped_correlation = 0
    correlation_errors = 0
    hydrated_threads: set[str] = set()

    def ingest_full(full: dict) -> None:
        nonlocal inserted, mapped_outreach, skipped_correlation, correlation_errors
        mid = (full.get("id") or "").strip()
        if not mid:
            return
        if get_inbox_email_by_gmail_id(account_id, mid):
            return
        payload = full.get("payload") or {}
        headers = payload.get("headers") or []
        frm = get_header(headers, "From")
        to_h = get_header(headers, "To")
        subj = get_header(headers, "Subject")
        label_ids = full.get("labelIds") or []
        is_sent = "SENT" in label_ids
        from_list = parse_addresses(frm)
        to_list = parse_addresses(to_h)
        from_email = from_list[0] if from_list else ""
        ts = _parse_internal_date(full.get("internalDate"))
        if not ts:
            ts = datetime.now(timezone.utc)
        ts = to_naive_utc(ts) or datetime.utcnow()
        body_plain = extract_plain_body(payload) or ""
        snippet = (full.get("snippet") or "").strip()
        gmail_thread_id = full.get("threadId") or ""

        thread_pk = upsert_email_thread(
            user_id=user_id,
            gmail_account_id=account_id,
            gmail_thread_id=gmail_thread_id,
            subject=subj,
            snippet=snippet,
            last_message_at=ts,
        )
        thread_label = "SENT" if is_sent else "RECEIVED"
        if not is_sent and account_email and from_email and from_email != account_email:
            thread_label = "RECEIVED"
        elif is_sent:
            thread_label = "SENT"

        upsert_inbox_email(
            gmail_message_id=mid,
            thread_pk=thread_pk,
            user_id=user_id,
            gmail_account_id=account_id,
            from_email=from_email,
            to_emails=to_list,
            subject=subj,
            body_snippet=snippet,
            body_full=body_plain[:500000] if body_plain else None,
            timestamp=ts,
            is_sent=is_sent,
            thread_label=thread_label,
        )
        inserted += 1
        try:
            cor = correlate_outreach_from_gmail_message(
                gmail_account_id=account_id,
                gmail_message_id=mid,
                user_id=user_id,
                account_email=account_email or row["email"] or "",
                from_email=from_email,
                to_emails=to_list,
                subject=subj,
                body_full=body_plain,
                body_snippet=snippet,
                timestamp=ts,
                is_sent=is_sent,
                thread_pk=thread_pk,
            )
            if cor.get("mapped"):
                mapped_outreach += 1
            elif cor.get("skipped"):
                skipped_correlation += 1
        except Exception:
            log.exception("outreach correlate failed for %s", mid)
            correlation_errors += 1

    def process_list(list_kwargs: dict, *, budget: int) -> int:
        nonlocal inserted, examined_total
        examined = 0
        page_token = None
        while examined < budget:
            req = dict(list_kwargs)
            if page_token:
                req["pageToken"] = page_token
            try:
                resp = service.users().messages().list(userId="me", **req).execute()
            except Exception as e:
                log.warning("messages.list failed: %s", e)
                return examined
            for m in resp.get("messages") or []:
                mid = m.get("id")
                if not mid:
                    continue
                if examined >= budget:
                    return examined
                examined += 1
                examined_total += 1
                if get_inbox_email_by_gmail_id(account_id, mid):
                    continue
                try:
                    full = service.users().messages().get(userId="me", id=mid, format="full").execute()
                except Exception as e:
                    log.debug("get message %s: %s", mid, e)
                    continue
                gmail_thread_id = (full.get("threadId") or "").strip()
                if gmail_thread_id and gmail_thread_id not in hydrated_threads:
                    hydrated_threads.add(gmail_thread_id)
                    try:
                        thread_data = service.users().threads().get(userId="me", id=gmail_thread_id, format="full").execute()
                        msgs = thread_data.get("messages") or []
                        # Keep deterministic order from oldest -> newest.
                        msgs = sorted(msgs, key=lambda x: int((x or {}).get("internalDate") or 0))
                        for tm in msgs:
                            ingest_full(tm or {})
                    except Exception as e:
                        log.debug("get thread %s: %s", gmail_thread_id, e)
                        ingest_full(full)
                else:
                    ingest_full(full)
            page_token = resp.get("nextPageToken")
            if not page_token:
                return examined
        return examined

    # Keep separate budgets so inbox volume doesn't starve sent-message sync.
    inbox_examined = process_list(
        {"q": f"in:inbox {query}", "maxResults": min(100, max_messages)},
        budget=max_messages,
    )
    sent_examined = process_list(
        {"q": f"in:sent {query}", "maxResults": min(100, max_messages)},
        budget=max_messages,
    )

    out = {
        "ok": True,
        "inserted": inserted,
        "examined": examined_total,
        "examined_inbox": inbox_examined,
        "examined_sent": sent_examined,
        "mapped": mapped_outreach,
        "correlation_skipped": skipped_correlation,
        "correlation_errors": correlation_errors,
        "filtered_out": 0,
        "only_known_contacts": only_known_contacts,
    }
    update_gmail_account_sync_run(account_id, ok=True, stats=out)
    return out


def sync_all_accounts(
    *,
    max_messages: int = 200,
    newer_than_days: int = 14,
    only_known_contacts: bool = True,
) -> list[dict]:
    out = []
    for acc in list_gmail_accounts():
        r = sync_gmail_account(
            acc["id"],
            max_messages=max_messages,
            newer_than_days=newer_than_days,
            only_known_contacts=only_known_contacts,
        )
        out.append({"account_id": acc["id"], **r})
    return out
