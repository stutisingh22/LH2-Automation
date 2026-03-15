"""
STANDALONE script: builds Brands/Traffic/... matrix and saves to DB.

- Traffic: read from DB (Company.monthly_visits / platform_presence_analytics.similarweb).
- Yahoo/MSN/Newsbreak/Smartnews + MSN Followers: from tracker_sources.csv.
- Articles/Day + Instagram/Facebook/YT: social_follower_research.run_social_follower_research
  (sitemap + Claude URL search + fetch profile pages; no hallucination).
- Writes each row to Company.extra_data["brands_traffic_matrix"] and to brands_traffic_matrix.csv.

Usage:
  Similarweb in DB (run_similarweb_independent.py). tracker_sources.csv present.
  ANTHROPIC_API_KEY in keys.env for social URL lookup.
  python run_brands_traffic_matrix.py
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from pathlib import Path
from collections import defaultdict

# Project root for imports, keys.env; standalone/social/ for CSV and social_follower_research
_root = Path(__file__).resolve().parent.parent.parent
_social_dir = Path(__file__).resolve().parent
for p in (_root, _social_dir):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from dotenv import load_dotenv
load_dotenv(str(_root / "keys.env"), override=True)
load_dotenv(str(_root / ".env"), override=True)
print("Anthropic key loaded:", bool(os.getenv("ANTHROPIC_API_KEY")))

# --- Config (all in this file) ---
TRACKER_CSV = _root / "tracker_sources.csv"
PLATFORMS = ("msn", "yahoo", "newsbreak", "smartnews")
DELAY_AFTER_AI_SEC = 1.5

MATRIX_COLUMNS = [
    "Brands", "Traffic", "Articles/Day", "Yahoo", "MSN", "MSN Followers",
    "Newsbreak", "Smartnews", "Instagram", "Facebook", "YT",
]


# --- Domain normalize ---
def _normalize_domain(url_or_domain: str) -> str | None:
    if not url_or_domain or not isinstance(url_or_domain, str):
        return None
    s = (url_or_domain or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    s = s.rstrip(".")
    return s if s else None


# --- Traffic from DB ---
def _traffic_from_company(c: dict) -> int | None:
    n = c.get("monthly_visits")
    if n is not None:
        return int(n) if n else None
    ed = c.get("extra_data") or {}
    if not isinstance(ed, dict):
        return None
    pa = ed.get("platform_presence_analytics") or {}
    if not isinstance(pa, dict):
        return None
    sw = pa.get("similarweb") or {}
    if not isinstance(sw, dict):
        return None
    n = sw.get("latest_monthly_visits")
    return int(n) if n is not None else None


# --- Tracker CSV: presence + MSN followers ---
def _row_key(row: dict, *keys: str) -> str:
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            return v
    return ""


def _load_domain_to_platforms() -> dict[str, set[str]]:
    out = defaultdict(set)
    if not TRACKER_CSV.exists():
        return dict(out)
    with open(TRACKER_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw_type = _row_key(row, "Type", "type").lower()
            if raw_type not in PLATFORMS:
                continue
            website = _row_key(row, "Website", "website")
            domain = _normalize_domain(website)
            if domain:
                out[domain].add(raw_type)
    return dict(out)


def _load_domain_to_msn_followers() -> dict[str, int]:
    out = {}
    if not TRACKER_CSV.exists():
        return out
    with open(TRACKER_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if _row_key(row, "Type", "type").lower() != "msn":
                continue
            if _row_key(row, "Status", "status").lower() != "active":
                continue
            website = _row_key(row, "Website", "website")
            domain = _normalize_domain(website)
            if not domain:
                continue
            raw = (row.get("Follower Count") or row.get("follower_count") or "").strip()
            try:
                count = int(raw) if raw else 0
            except ValueError:
                count = 0
            if count > 0:
                out[domain] = count
    return out


# --- Formatting ---
def _format_traffic(n: int | None) -> str:
    if n is None or n <= 0:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".rstrip("0").rstrip(".")
    return str(n)


def _format_follower(n: int | None) -> str:
    if n is None or n <= 0:
        return "NA"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"~{n / 1_000:.1f}K".replace(".0", "")
    return str(n)


def _brands_url(domain: str) -> str:
    if not domain or domain.startswith("__advisor_"):
        return "—"
    d = domain.strip().lower()
    if not d.startswith("http"):
        d = "https://www." + d if not d.startswith("www.") else "https://" + d
    return d if d.endswith("/") else d + "/"


def main():
    from db_ops import get_all_companies, merge_company_extra_data
    from social_follower_research import run_social_follower_research

    companies = get_all_companies(exclude_advisor_shadows=True)
    to_process = [
        c for c in companies
        if (c.get("domain") or "").strip() and not (c.get("domain") or "").strip().startswith("__advisor_")
    ]
    if not to_process:
        print("No companies found.")
        sys.exit(0)

    print("Loading tracker_sources.csv...")
    domain_to_platforms = _load_domain_to_platforms()
    domain_to_msn_followers = _load_domain_to_msn_followers()
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    print(f"Companies to process: {len(to_process)}")
    print("Anthropic enabled:", has_anthropic)

    all_rows = []
    updated = 0
    for i, c in enumerate(to_process):
        domain = (c.get("domain") or "").strip()
        norm = _normalize_domain(domain)
        if not norm:
            continue

        visits = _traffic_from_company(c)
        traffic_str = _format_traffic(visits)

        platforms_set = domain_to_platforms.get(norm, set())
        presence = {p: (p in platforms_set) for p in PLATFORMS}
        msn_follower = domain_to_msn_followers.get(norm)

        brand = (c.get("name") or "").strip() or norm.split(".")[0]
        articles_day = "—"
        instagram = facebook = yt = "NA"
        if has_anthropic:
            try:
                extracted = run_social_follower_research(domain, brand) or {}
                print("  AI RESULT:", extracted)
                articles_day = extracted.get("articles_per_day") or "—"
                instagram = extracted.get("instagram") or "NA"
                facebook = extracted.get("facebook") or "NA"
                yt = extracted.get("youtube") or "NA"
                time.sleep(DELAY_AFTER_AI_SEC)
            except Exception as e:
                print("  AI ERROR:", e)

        row = {
            "Brands": _brands_url(domain),
            "Traffic": traffic_str,
            "Articles/Day": articles_day,
            "Yahoo": "Yes" if presence.get("yahoo") else "No",
            "MSN": "Yes" if presence.get("msn") else "No",
            "MSN Followers": _format_follower(msn_follower),
            "Newsbreak": "Yes" if presence.get("newsbreak") else "No",
            "Smartnews": "Yes" if presence.get("smartnews") else "No",
            "Instagram": instagram,
            "Facebook": facebook,
            "YT": yt,
        }
        all_rows.append(row)

        payload = {"brands_traffic_matrix": dict(row)}
        try:
            ok = merge_company_extra_data(c["id"], payload)
            if ok:
                updated += 1
        except Exception as e:
            print("  DB SAVE ERROR:", e)

        print(f"[{i + 1}/{len(to_process)}] {domain}  Traffic={traffic_str}  IG={instagram}  FB={facebook}  YT={yt}")

    out_csv = _social_dir / "brands_traffic_matrix.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MATRIX_COLUMNS)
        w.writeheader()
        w.writerows(all_rows)
    print("\nDone.")
    print("Companies updated:", updated)
    print("CSV:", out_csv)


if __name__ == "__main__":
    main()
