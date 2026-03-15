"""
auto_similarweb_on_import.py

Standalone helper to auto-fetch Similarweb visits right after importing domains.

Use case:
  - You bulk import domains (Independent or Media House children)
  - Immediately call run_similarweb_for_domains(domains)
  - It writes:
      - Company.monthly_visits
      - Company.extra_data["platform_presence_analytics"]["similarweb"]

DB backend:
  - Uses the same DB as the app (Postgres if DATABASE_URL set; else local SQLite).
  - Reads SIMILARWEB_COOKIES / SIMWEB_COOKIES from env / keys.env (same as run_similarweb_independent.py).

CLI usage examples:
  python auto_similarweb_on_import.py --domains "13wham.com,411mania.com"
  python auto_similarweb_on_import.py --csv social_from_search.csv --brand-col Brand
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

# Project root for imports and keys.env
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root / "keys.env", override=True)
load_dotenv(_root / ".env")

from config import normalize_domain
from db_ops import get_company_by_domain, get_company, merge_company_extra_data, upsert_company

# Reuse Similarweb fetch/snapshot logic from platform_analytics (cookie + public fallback).
from integrations.analytics.platform_analytics import _similarweb_monthly_visits, _similarweb_snapshot_from_fetch


def _load_similarweb_cookies_into_env() -> None:
    """
    Match run_similarweb_independent.py behavior:
    - Prefer SIMILARWEB_COOKIES / SIMWEB_COOKIES
    - Else read SIMILARWEB_COOKIES_FILE (defaults to similarweb_cookies.txt)
    """
    cookies = (os.environ.get("SIMILARWEB_COOKIES") or os.environ.get("SIMWEB_COOKIES") or "").strip()
    if cookies:
        os.environ["SIMILARWEB_COOKIES"] = cookies
        return
    cookie_file = os.environ.get("SIMILARWEB_COOKIES_FILE", str(_root / "similarweb_cookies.txt"))
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = "".join(line for line in f if line.strip() and not line.strip().startswith("#")).strip()
    if cookies:
        os.environ["SIMILARWEB_COOKIES"] = cookies


def _should_skip_company(comp: dict, freshness_days: int) -> bool:
    """
    Skip if we already have a non-zero monthly_visits and it was updated recently in platform_presence_analytics.
    """
    if not comp:
        return False
    mv = comp.get("monthly_visits")
    if not isinstance(mv, (int, float)) or mv <= 0:
        return False
    ed = comp.get("extra_data") or {}
    if not isinstance(ed, dict):
        return False
    ppa = ed.get("platform_presence_analytics") or {}
    if not isinstance(ppa, dict):
        return False
    updated_at = (ppa.get("updated_at") or "").strip()
    if not updated_at:
        return False
    try:
        # tolerate "2026-03-12T..." strings
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except Exception:
        return False
    return dt >= (datetime.utcnow() - timedelta(days=freshness_days))


def run_similarweb_for_domains(
    domains: Iterable[str],
    *,
    force: bool = False,
    freshness_days: int = 30,
    min_delay_sec: float = 2.0,
    max_delay_sec: float = 5.0,
    progress_callback=None,
) -> tuple[int, int, str]:
    """
    Fetch Similarweb visits for specific domains and write to DB.

    Returns (updated_count, skipped_count, err_msg).
    """
    import random
    import time

    _load_similarweb_cookies_into_env()

    updated = 0
    skipped = 0

    normed = []
    seen = set()
    for d in domains:
        nd = normalize_domain(d)
        if not nd or nd in seen or nd.startswith("__advisor_"):
            continue
        seen.add(nd)
        normed.append(nd)

    for i, dom in enumerate(normed):
        comp = get_company_by_domain(dom)
        if not comp:
            skipped += 1
            continue
        if (not force) and _should_skip_company(comp, freshness_days=freshness_days):
            skipped += 1
            continue
        if progress_callback:
            progress_callback(i + 1, len(normed), dom)

        sw = _similarweb_monthly_visits(dom)
        if sw.get("error") == "cookie_expired":
            return updated, skipped, "Similarweb cookie expired. Update SIMILARWEB_COOKIES and re-run."

        visits = sw.get("visits")
        cid = comp.get("id")
        existing = (get_company(cid) or {}).get("extra_data") or {}
        if not isinstance(existing, dict):
            existing = {}
        analytics = dict(existing.get("platform_presence_analytics") or {})
        if not isinstance(analytics, dict):
            analytics = {}
        analytics["similarweb"] = _similarweb_snapshot_from_fetch(sw)
        analytics["updated_at"] = datetime.utcnow().isoformat()
        analytics["status"] = "ok" if visits is not None else "no_data"

        if merge_company_extra_data(cid, {"platform_presence_analytics": analytics}):
            updated += 1
        if visits is not None:
            upsert_company(comp.get("domain") or dom, monthly_visits=visits)

        if i < len(normed) - 1:
            time.sleep(random.uniform(min_delay_sec, max_delay_sec))

    return updated, skipped, ""


def _read_domains_from_csv(path: Path, col: str) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out.append((row.get(col) or "").strip())
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--domains", help="Comma-separated domains", default="")
    p.add_argument("--csv", help="CSV file to read domains from", default="")
    p.add_argument("--brand-col", help="CSV column name (default Brand)", default="Brand")
    p.add_argument("--force", action="store_true", help="Force refetch even if already fresh")
    p.add_argument("--freshness-days", type=int, default=30, help="Skip if updated within N days (default 30)")
    args = p.parse_args()

    domains: list[str] = []
    if args.domains.strip():
        domains.extend([x.strip() for x in args.domains.split(",") if x.strip()])
    if args.csv.strip():
        domains.extend(_read_domains_from_csv(Path(args.csv), args.brand_col))

    if not domains:
        print("No domains provided. Use --domains or --csv.")
        sys.exit(1)

    def prog(i, total, dom):
        print(f"  [{i}/{total}] {dom}", end=" ... ", flush=True)

    print(f"Auto Similarweb: processing {len(domains)} input domains (deduped after normalization).")
    updated, skipped, err = run_similarweb_for_domains(
        domains,
        force=bool(args.force),
        freshness_days=int(args.freshness_days),
        progress_callback=prog,
    )
    if err:
        print("\n" + err)
        sys.exit(1)
    print(f"\nDone. Updated {updated}, skipped {skipped}.")


if __name__ == "__main__":
    main()

