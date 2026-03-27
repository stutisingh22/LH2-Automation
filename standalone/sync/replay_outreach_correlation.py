#!/usr/bin/env python3
"""
Replay Outreach correlation for already-synced Gmail messages.

Use when correlation logic is improved and existing UI state must be recomputed.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / "keys.env")
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from db_ops import db, init_db, list_gmail_accounts
from models import Action, GmailAccount, GmailOutreachSyncEvent, InboxEmail
from services.outreach_correlator import correlate_outreach_from_gmail_message

log = logging.getLogger("replay_outreach_correlation")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def _reset_for_account(account_id: int) -> dict:
    with db() as s:
        evs = s.query(GmailOutreachSyncEvent).filter_by(gmail_account_id=int(account_id)).all()
        action_ids = {int(e.action_id) for e in evs if e.action_id}
        deleted_events = len(evs)
        s.query(GmailOutreachSyncEvent).filter_by(gmail_account_id=int(account_id)).delete()

        reset_actions = 0
        if action_ids:
            rows = s.query(Action).filter(Action.id.in_(list(action_ids))).all()
            for a in rows:
                meta = getattr(a, "outreach_sync_meta", None) or {}
                source = meta.get("source") if isinstance(meta, dict) else None
                if source != "gmail_sync":
                    continue
                a.status = "planned"
                a.completed_at = None
                a.response_content = None
                a.response_type = None
                if hasattr(a, "outcome_reason"):
                    a.outcome_reason = None
                if hasattr(a, "outreach_sync_meta"):
                    a.outreach_sync_meta = None
                reset_actions += 1

        return {
            "deleted_events": deleted_events,
            "candidate_actions": len(action_ids),
            "reset_actions": reset_actions,
        }


def _replay_for_account(account_id: int) -> dict:
    with db() as s:
        acc = s.get(GmailAccount, int(account_id))
        if not acc:
            return {"ok": False, "error": "account_not_found"}
        account_email = (acc.email or "").strip().lower()
        user_id = int(acc.user_id)
        msgs = (
            s.query(InboxEmail)
            .filter_by(gmail_account_id=int(account_id))
            .order_by(InboxEmail.timestamp.asc(), InboxEmail.id.asc())
            .all()
        )

    mapped = 0
    skipped = 0
    errors = 0
    examined = 0
    for m in msgs:
        examined += 1
        try:
            out = correlate_outreach_from_gmail_message(
                gmail_account_id=int(account_id),
                gmail_message_id=str(m.gmail_message_id or ""),
                user_id=user_id,
                account_email=account_email,
                from_email=m.from_email,
                to_emails=(m.to_emails or []),
                subject=m.subject,
                body_full=m.body_full,
                body_snippet=m.body_snippet,
                timestamp=m.timestamp,
                is_sent=bool(m.is_sent),
                thread_pk=int(m.thread_id) if m.thread_id else None,
            )
            if out.get("mapped"):
                mapped += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            log.warning("replay failed mid=%s: %s", m.gmail_message_id, e)

    return {
        "ok": True,
        "account_id": int(account_id),
        "examined": examined,
        "mapped": mapped,
        "skipped": skipped,
        "errors": errors,
    }


def _resolve_accounts(account_id: int | None, account_email: str | None) -> list[int]:
    rows = list_gmail_accounts()
    if account_id is not None:
        return [int(account_id)]
    if account_email:
        ae = account_email.strip().lower()
        return [int(r["id"]) for r in rows if str(r.get("email") or "").strip().lower() == ae]
    return [int(r["id"]) for r in rows]


def main() -> None:
    p = argparse.ArgumentParser(description="Replay Outreach correlation for existing synced Gmail messages.")
    p.add_argument("--account-id", type=int, default=None)
    p.add_argument("--account-email", type=str, default=None)
    p.add_argument("--dry-run", action="store_true", help="Only show target accounts; do not mutate.")
    args = p.parse_args()

    init_db()
    targets = _resolve_accounts(args.account_id, args.account_email)
    if not targets:
        log.info("No matching Gmail accounts found.")
        return
    log.info("Targets: %s", targets)
    if args.dry_run:
        return

    for aid in targets:
        reset_stats = _reset_for_account(aid)
        replay_stats = _replay_for_account(aid)
        log.info("account=%s reset=%s replay=%s", aid, reset_stats, replay_stats)


if __name__ == "__main__":
    main()
