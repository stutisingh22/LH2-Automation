"""
Media-house-only social + platform presence CSV. No changes to existing files.
Uses advisor companies (parent + child), tracker_sources.csv for Yahoo/MSN/Newsbreak/Smartnews + MSN Followers,
and social_follower_research for Instagram, Facebook, YT. Output: social_from_search_media_houses.csv only (no DB insert).

Usage:
  python run_social_media_houses.py --step init   # build CSV from advisor companies + tracker_sources
  python run_social_media_houses.py --step msn-followers   # refresh MSN Followers column from tracker_sources.csv
  python run_social_media_houses.py              # fill Instagram, Facebook, YT via social_follower_research
  python run_social_media_houses.py --domain=example.com   # single domain, console only
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
from pathlib import Path

logging.getLogger("primp").setLevel(logging.ERROR)
logging.getLogger("ddgs").setLevel(logging.ERROR)

# Project root for imports and keys.env; standalone/social/ on path for social_follower_research
_root = Path(__file__).resolve().parent.parent.parent
_social_dir = Path(__file__).resolve().parent
for p in (_root, _social_dir):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from dotenv import load_dotenv
load_dotenv(str(_root / "keys.env"), override=True)
load_dotenv(str(_root / ".env"), override=True)

TRACKER_CSV = _root / "tracker_sources.csv"
OUT_CSV = _social_dir / "social_from_search_media_houses.csv"
PLATFORMS = ("msn", "yahoo", "newsbreak", "smartnews")

COLUMNS = [
    "Brand", "Parent", "Yahoo", "MSN", "MSN Followers", "Newsbreak", "Smartnews",
    "Instagram", "Facebook", "YT",
]


def _get_step():
    for i, arg in enumerate(sys.argv):
        if arg == "--step" and i + 1 < len(sys.argv):
            val = sys.argv[i + 1].strip().lower()
            if val == "init":
                return "init"
            if val == "msn-followers":
                return "msn-followers"
            return None
    return None


def _get_domain_filter():
    for arg in sys.argv:
        if arg.startswith("--domain="):
            val = arg.split("=", 1)[1].strip()
            if val:
                return val
    return os.getenv("RUN_SOCIAL_MEDIA_HOUSES_DOMAIN", "").strip() or None


def _normalize_domain(url_or_domain: str) -> str | None:
    if not url_or_domain or not isinstance(url_or_domain, str):
        return None
    s = (url_or_domain or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"^https?://", "", s).split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip(".") or None


def _brand_url(domain: str) -> str:
    if not domain:
        return "—"
    d = domain.strip().lower()
    if not d.startswith("http"):
        d = "https://www." + d if not d.startswith("www.") else "https://" + d
    return d if d.endswith("/") else d + "/"


def _format_follower(n: int | None) -> str:
    if n is None or n <= 0:
        return "NA"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"~{n / 1_000:.1f}K".replace(".0", "")
    return str(n)


def _load_tracker_presence_and_msn_followers():
    from integrations.analytics import load_domain_to_platforms, load_domain_to_msn_follower_count
    domain_to_platforms = load_domain_to_platforms(TRACKER_CSV)
    domain_to_msn_followers = load_domain_to_msn_follower_count(TRACKER_CSV)
    return domain_to_platforms, domain_to_msn_followers


def _run_init():
    """Build CSV from advisor companies (media house children) + tracker_sources. No DB write."""
    if not TRACKER_CSV.exists():
        print(f"Error: {TRACKER_CSV} not found.")
        sys.exit(1)
    try:
        from db_ops import get_all_advisor_companies
        all_advisor = get_all_advisor_companies()
    except Exception as e:
        print(f"Error: could not load advisor companies: {e}")
        sys.exit(1)

    # Only child companies (exclude __parent_* rows)
    to_process = [
        c for c in all_advisor
        if (c.get("domain") or "").strip()
        and not (c.get("domain") or "").strip().startswith("__parent_")
    ]
    if not to_process:
        print("No media house child companies to process.")
        sys.exit(0)

    domain_to_platforms, domain_to_msn_followers = _load_tracker_presence_and_msn_followers()
    rows_to_write = []

    for c in to_process:
        domain = (c.get("domain") or "").strip()
        parent = (c.get("parent_name") or "").strip() or "—"
        norm = _normalize_domain(domain)
        if not norm:
            continue
        platforms_set = domain_to_platforms.get(norm, set())
        presence = {p: (p in platforms_set) for p in PLATFORMS}
        msn_count = domain_to_msn_followers.get(norm)

        rows_to_write.append({
            "Brand": _brand_url(domain),
            "Parent": parent,
            "Yahoo": "Yes" if presence.get("yahoo") else "No",
            "MSN": "Yes" if presence.get("msn") else "No",
            "MSN Followers": _format_follower(msn_count),
            "Newsbreak": "Yes" if presence.get("newsbreak") else "No",
            "Smartnews": "Yes" if presence.get("smartnews") else "No",
            "Instagram": "NA",
            "Facebook": "NA",
            "YT": "NA",
        })

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows_to_write)
    print(f"Done. Wrote {len(rows_to_write)} rows to {OUT_CSV}.")


def _load_rows_from_csv():
    if not OUT_CSV.exists():
        return []
    with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _run_msn_followers_only():
    """Update only MSN Followers column from tracker_sources.csv."""
    if not TRACKER_CSV.exists():
        print(f"Error: {TRACKER_CSV} not found.")
        sys.exit(1)
    rows = _load_rows_from_csv()
    if not rows:
        print(f"Error: {OUT_CSV} not found or empty. Run --step init first.")
        sys.exit(1)
    from integrations.analytics import load_domain_to_msn_follower_count
    domain_to_msn_followers = load_domain_to_msn_follower_count(TRACKER_CSV)
    updated = 0
    for row in rows:
        norm = _normalize_domain(row.get("Brand") or "")
        if not norm:
            continue
        count = domain_to_msn_followers.get(norm)
        new_val = _format_follower(count)
        if new_val != (row.get("MSN Followers") or "").strip():
            updated += 1
        row["MSN Followers"] = new_val
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"Done. Updated MSN Followers for {len(rows)} rows ({updated} changed). Wrote {OUT_CSV}.")


def _domain_from_brand(brand: str) -> str:
    """Brand is URL like https://www.example.com/; return normalized domain."""
    norm = _normalize_domain(brand or "")
    return norm or ""


def _run_social_followers(domain_filter: str | None = None):
    """Fill only Instagram, Facebook, YT (same search+fetch as Independent). Keep Yahoo, MSN, MSN Followers, Newsbreak, Smartnews unchanged."""
    try:
        from run_social_from_google_search import _process_one_row
    except ImportError as e:
        print(f"Error: could not import run_social_from_google_search: {e}")
        sys.exit(1)

    all_rows = _load_rows_from_csv()
    if not all_rows:
        print(f"Error: {OUT_CSV} not found or empty. Run --step init first.")
        sys.exit(1)

    if domain_filter:
        norm_filter = _normalize_domain(domain_filter)
        to_process = [r for r in all_rows if _normalize_domain(r.get("Brand") or "") == norm_filter]
        if not to_process:
            print(f"No row found for domain {domain_filter}.")
            sys.exit(1)
    else:
        to_process = all_rows

    for i, row in enumerate(to_process):
        brand_url = (row.get("Brand") or "").strip()
        domain = _domain_from_brand(brand_url)
        parent = (row.get("Parent") or "").strip()

        print(f"[{i + 1}/{len(to_process)}] {domain} ({parent})")
        # Minimal row for Independent's _process_one_row (same search + snippet + fetch)
        row_in = {
            "Brand": brand_url,
            "Instagram": row.get("Instagram") or "NA",
            "Facebook": row.get("Facebook") or "NA",
            "YT": row.get("YT") or "NA",
        }
        _, row_out = _process_one_row(row_in, i)
        # Only update IG/FB/YT; keep existing Yahoo, MSN, MSN Followers, Newsbreak, Smartnews
        row["Instagram"] = row_out.get("Instagram") or "NA"
        row["Facebook"] = row_out.get("Facebook") or "NA"
        row["YT"] = row_out.get("YT") or "NA"
        print(f"    IG: {row['Instagram']}  FB: {row['Facebook']}  YT: {row['YT']}")

    # Write full CSV: all rows with existing platform columns unchanged, only IG/FB/YT updated
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"Done. Wrote {len(all_rows)} rows to {OUT_CSV} (Yahoo, MSN, Newsbreak, Smartnews unchanged; only Instagram, Facebook, YT updated).")


def main():
    step = _get_step()
    domain_filter = _get_domain_filter()

    if step == "init":
        _run_init()
        return
    if step == "msn-followers":
        _run_msn_followers_only()
        return

    _run_social_followers(domain_filter=domain_filter)


if __name__ == "__main__":
    main()
