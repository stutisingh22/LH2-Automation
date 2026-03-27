#!/usr/bin/env python3
"""
Periodic Gmail inbox+sent sync with Outreach correlation (idempotent).

Run from project root (same as other standalone sync scripts):
  python standalone/sync/outreach_poll.py
  python standalone/sync/outreach_poll.py --loop 10   # every 10 minutes

Uses keys.env / DATABASE_URL — pulls all connected gmail_accounts.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os

os.chdir(_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / "keys.env")
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("outreach_poll")


def main():
    p = argparse.ArgumentParser(description="Poll Gmail + correlate outreach actions.")
    p.add_argument("--days", type=int, default=14, help="newer_than:Nd for Gmail query")
    p.add_argument("--max-messages", type=int, default=200, help="Per-account budget (inbox + sent)")
    p.add_argument("--loop", type=int, default=0, metavar="MINUTES", help="Repeat every N minutes (0 = once)")
    args = p.parse_args()

    from db_ops import init_db

    init_db()

    from services.gmail_sync_service import sync_all_accounts

    def cycle():
        results = sync_all_accounts(
            max_messages=args.max_messages,
            newer_than_days=args.days,
            only_known_contacts=True,
        )
        log.info("accounts=%s results=%s", len(results), results)
        return results

    if args.loop > 0:
        while True:
            cycle()
            time.sleep(args.loop * 60)
    else:
        cycle()


if __name__ == "__main__":
    main()
