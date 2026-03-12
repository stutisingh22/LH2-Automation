"""
Sync social_from_search.csv into the database (SQLite or Postgres per keys.env).

Reads each row from social_from_search.csv, looks up the company by domain (Brand column),
and merges into company.extra_data:
  - platform_presence: { msn, yahoo, newsbreak, smartnews } (booleans from Yes/No)
  - social_from_search: { msn_followers, instagram, facebook, yt, updated_at }

Platform presence in Intelligence will show MSN/Yahoo/NewsBreak and the full social counts
when this data is present.

Usage:
  python sync_social_from_search_to_db.py
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(str(_root / "keys.env"), override=True)
load_dotenv(str(_root / ".env"), override=True)

from config import normalize_domain
from db_ops import get_company_by_domain, merge_company_extra_data

OUT_CSV = _root / "social_from_search.csv"
COLUMNS = [
    "Brand", "Yahoo", "MSN", "MSN Followers", "Newsbreak", "Smartnews",
    "Instagram", "Facebook", "YT",
]


def _yes_no_to_bool(val: str) -> bool:
    return (val or "").strip().lower() in ("yes", "1", "true")


def run() -> None:
    if not OUT_CSV.exists():
        print(f"Error: {OUT_CSV} not found. Run run_social_from_google_search.py --step init first.")
        sys.exit(1)

    rows = []
    with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: (row.get(k) or "").strip() for k in COLUMNS})

    if not rows:
        print("CSV is empty. Nothing to sync.")
        sys.exit(0)

    updated = 0
    skipped = 0
    skipped_domains = []
    for row in rows:
        brand = row.get("Brand") or ""
        domain_norm = normalize_domain(brand)
        if not domain_norm:
            skipped += 1
            skipped_domains.append((brand or "(empty)")[:50])
            continue
        company = get_company_by_domain(domain_norm)
        if not company:
            skipped += 1
            skipped_domains.append(domain_norm)
            continue
        cid = company.get("id")
        if not cid:
            skipped += 1
            skipped_domains.append(domain_norm)
            continue

        platform_presence = {
            "msn": _yes_no_to_bool(row.get("MSN")),
            "yahoo": _yes_no_to_bool(row.get("Yahoo")),
            "newsbreak": _yes_no_to_bool(row.get("Newsbreak")),
            "smartnews": _yes_no_to_bool(row.get("Smartnews")),
        }
        def _cell(val: str) -> str:
            v = (val or "").strip()
            return v if v else "NA"

        social_from_search = {
            "msn_followers": _cell(row.get("MSN Followers")),
            "instagram": _cell(row.get("Instagram")),
            "facebook": _cell(row.get("Facebook")),
            "yt": _cell(row.get("YT")),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        patch = {
            "platform_presence": platform_presence,
            "social_from_search": social_from_search,
        }
        if merge_company_extra_data(cid, patch):
            updated += 1

    print(f"Done. Updated {updated} companies, skipped {skipped} rows (no match or no domain).")
    if skipped_domains and skipped <= 20:
        print("Skipped domains:", ", ".join(skipped_domains[:20]))
    elif skipped_domains:
        print("First 10 skipped domains:", ", ".join(skipped_domains[:10]))
    print("Platform presence and social counts are in company.extra_data; visible in Intelligence (and for Media Houses, data is read from main company by domain).")


if __name__ == "__main__":
    run()
