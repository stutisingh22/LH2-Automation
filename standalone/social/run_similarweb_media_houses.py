"""
Run Similarweb for media houses: same logic as run_similarweb_independent / auto_similarweb_on_import,
but reads from social_from_search_media_houses.csv and updates both the CSV and (when present) the main DB.

- Child companies: domains from the Brand column (each row = one child site).
- Parent companies: advisor parent rows use __parent_<slug> (not real domains); we only fetch for real
  domains. If you add a "Parent Domain" column with real domains, those will be fetched and stored too.

Reads SIMILARWEB_COOKIES from keys.env. Writes:
  - CSV: adds/updates only the "Similarweb Visits" (and optionally "Parent Similarweb Visits") column;
    all other columns and row data are preserved (read full file, update in memory, write back).
  - DB: for rows whose domain exists in the main companies table, updates
    Company.extra_data["platform_presence_analytics"]["similarweb"] and Company.monthly_visits

Usage:
  python run_similarweb_media_houses.py
  python run_similarweb_media_houses.py --force
  python run_similarweb_media_houses.py --domain=example.com   # single domain only
"""

from __future__ import annotations

import csv
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# Project root for imports, keys.env; standalone/social/ for CSV
_root = Path(__file__).resolve().parent.parent.parent
_social_dir = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root / "keys.env", override=True)
load_dotenv(_root / ".env")

# Cookie setup (same as run_similarweb_independent)
cookies = (os.environ.get("SIMILARWEB_COOKIES") or os.environ.get("SIMWEB_COOKIES") or "").strip()
if not cookies:
    cookie_file = os.environ.get("SIMILARWEB_COOKIES_FILE", str(_root / "similarweb_cookies.txt"))
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = "".join(
                line for line in f
                if line.strip() and not line.strip().startswith("#")
            ).strip()
if not cookies:
    print("Add SIMILARWEB_COOKIES to keys.env (or create similarweb_cookies.txt with the cookie string).")
    sys.exit(1)
os.environ["SIMILARWEB_COOKIES"] = cookies

from config import normalize_domain
from integrations.analytics.platform_analytics import _similarweb_monthly_visits, _similarweb_snapshot_from_fetch

from sqlalchemy import or_, func

from db_ops import db
from models import AdvisorCompany, Company

try:
    from db_ops import get_company_by_domain, get_company, merge_company_extra_data, upsert_company
except Exception:
    get_company_by_domain = get_company = merge_company_extra_data = upsert_company = None

OUT_CSV = _social_dir / "social_from_search_media_houses.csv"
SIMILARWEB_COL = "Similarweb Visits"
MIN_DELAY_SEC = 2.0
MAX_DELAY_SEC = 5.0


def _domain_from_brand(brand: str) -> str:
    """Brand is URL like https://www.example.com/; return normalized domain."""
    return (normalize_domain(brand or "") or "").strip()


def _load_rows():
    if not OUT_CSV.exists():
        return []
    with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _get_domain_filter():
    for arg in sys.argv:
        if arg.startswith("--domain="):
            val = arg.split("=", 1)[1].strip()
            if val:
                return normalize_domain(val) or None
    return None


def run_similarweb_media_houses(
    *,
    force: bool = False,
    domain_filter: str | None = None,
    progress_callback=None,
) -> tuple[int, int, str]:
    """
    For each row in social_from_search_media_houses.csv, fetch Similarweb for the Brand domain,
    update the row with Similarweb Visits, and (if company exists in main DB) update DB.
    Returns (updated_csv_count, updated_db_count, error_message).
    """
    rows = _load_rows()
    if not rows:
        print(f"No rows in {OUT_CSV}. Run run_social_media_houses.py --step init first.")
        return 0, 0, ""

    # Domains that already have Similarweb data in the CSV (skip unless --force)
    parent_domain_col = "Parent Domain"
    parent_visits_col = "Parent Similarweb Visits"
    already_fetched: set[str] = set()
    if not force:
        for row in rows:
            sw_val = (row.get(SIMILARWEB_COL) or "").strip()
            if sw_val and sw_val != "NA":
                d = _domain_from_brand(row.get("Brand") or "")
                if d:
                    already_fetched.add(d)
            p_visits = (row.get(parent_visits_col) or "").strip()
            if p_visits and p_visits != "NA":
                p_raw = (row.get(parent_domain_col) or "").strip()
                if p_raw:
                    pd = (normalize_domain(p_raw) or "").strip()
                    if pd:
                        already_fetched.add(pd)

    # Collect unique domains from Brand (child) and optionally "Parent Domain" column; dedupe; skip already-fetched unless --force
    to_fetch: list[str] = []
    seen_domains: set[str] = set()
    for row in rows:
        for raw in (row.get("Brand") or "", row.get(parent_domain_col) or ""):
            domain = (normalize_domain(raw) or "").strip() if raw else ""
            if not domain or domain.startswith("__parent_"):
                continue
            if domain_filter and domain != domain_filter:
                continue
            if domain in seen_domains:
                continue
            if (not force) and domain in already_fetched:
                continue
            seen_domains.add(domain)
            to_fetch.append(domain)

    if progress_callback and not force and already_fetched:
        # Log skip count when continuing from previous run
        total_domains = len(seen_domains) + len(already_fetched)
        print(f"Skipping {len(already_fetched)} domain(s) that already have Similarweb data; fetching {len(to_fetch)} remaining.")

    if not to_fetch:
        csv_with_data = len([r for r in rows if (r.get(SIMILARWEB_COL) or "").strip() and r.get(SIMILARWEB_COL) != "NA"])
        if progress_callback:
            print(f"All domains already have Similarweb data ({csv_with_data} rows). Use --force to re-fetch.")
        return csv_with_data, 0, ""

    # Domain -> (visits, sw_result) for applying to all rows with that domain
    cache: dict[str, tuple[int | None, dict]] = {}
    updated_db = 0

    for idx, dom in enumerate(to_fetch):
        if progress_callback:
            progress_callback(idx + 1, len(to_fetch), dom)

        sw = _similarweb_monthly_visits(dom)
        if sw.get("error") == "cookie_expired":
            return 0, updated_db, "Similarweb cookie expired. Update SIMILARWEB_COOKIES in keys.env and re-run."

        visits = sw.get("visits")
        cache[dom] = (visits, sw)

        if get_company_by_domain and get_company and merge_company_extra_data and upsert_company:
            comp = get_company_by_domain(dom)
            if comp:
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
                    updated_db += 1
                if visits is not None:
                    upsert_company(comp.get("domain") or dom, monthly_visits=visits)
            else:
                # Media Houses dashboard uses shadow companies created via:
                #   domain = "__advisor_<advisor_company_id>"
                # but this script previously only updated real domains.
                # If the real domain row doesn't exist, write monthly_visits into the shadow company.
                norm_dom = normalize_domain(dom) or dom
                www_dom = f"www.{norm_dom}"
                with db() as s:
                    adv = (
                        s.query(AdvisorCompany)
                        .filter(
                            or_(
                                AdvisorCompany.domain == norm_dom,
                                AdvisorCompany.domain == www_dom,
                                func.lower(AdvisorCompany.domain) == norm_dom,
                                func.lower(AdvisorCompany.domain) == www_dom,
                            )
                        )
                        .first()
                    )
                    if adv:
                        shadow_domain = f"__advisor_{adv.id}"
                        shadow = s.query(Company).filter(Company.domain == shadow_domain).first()
                        if shadow and visits is not None:
                            shadow.monthly_visits = visits
                            shadow.updated_at = datetime.utcnow()
                            s.flush()
                            updated_db += 1

        if idx < len(to_fetch) - 1:
            time.sleep(random.uniform(MIN_DELAY_SEC, MAX_DELAY_SEC))

    # Apply cached results to all rows: only set Similarweb columns; leave all other columns unchanged
    parent_domain_col = "Parent Domain"
    parent_visits_col = "Parent Similarweb Visits"
    for row in rows:
        brand_domain = _domain_from_brand(row.get("Brand") or "")
        if brand_domain and brand_domain in cache:
            visits, _ = cache[brand_domain]
            row[SIMILARWEB_COL] = str(visits) if visits is not None else "NA"
        parent_raw = (row.get(parent_domain_col) or "").strip() if parent_domain_col in row else ""
        if parent_raw:
            parent_dom = (normalize_domain(parent_raw) or "").strip()
            if parent_dom and parent_dom in cache:
                p_visits, _ = cache[parent_dom]
                row[parent_visits_col] = str(p_visits) if p_visits is not None else "NA"

    # Preserve every column already in the CSV; only add Similarweb column(s) if missing (never remove existing columns)
    all_keys: set[str] = set()
    for row in rows:
        all_keys.update(row.keys())
    fieldnames = list(rows[0].keys()) if rows else []  # keep original column order
    for k in (SIMILARWEB_COL, parent_visits_col):
        if k not in fieldnames:
            fieldnames.append(k)
    # Ensure any column that appeared in any row is in fieldnames (no column dropped)
    for k in sorted(all_keys):
        if k not in fieldnames:
            fieldnames.append(k)
    for row in rows:
        if SIMILARWEB_COL not in row:
            row[SIMILARWEB_COL] = ""
        if parent_visits_col in fieldnames and parent_visits_col not in row:
            row[parent_visits_col] = ""

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    return len([r for r in rows if SIMILARWEB_COL in r and str(r.get(SIMILARWEB_COL)).strip() and r.get(SIMILARWEB_COL) != "NA"]), updated_db, ""


def main():
    force = "--force" in sys.argv
    domain_filter = _get_domain_filter()

    def prog(i, total, dom):
        print(f"  [{i}/{total}] {dom}", end=" ... ", flush=True)

    print("Similarweb for media houses: reading", OUT_CSV.name)
    updated_csv, updated_db, err = run_similarweb_media_houses(
        force=force,
        domain_filter=domain_filter,
        progress_callback=prog,
    )
    if err:
        print("\n" + err)
        sys.exit(1)
    print(f"\nDone. CSV: {updated_csv} rows with Similarweb data; DB: {updated_db} companies updated.")


if __name__ == "__main__":
    main()
