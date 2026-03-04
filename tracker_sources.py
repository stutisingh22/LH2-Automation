"""
Platform presence (MSN, Yahoo, NewsBreak) from tracker_sources.csv.
Match: company string (as displayed in Companies tab) normalized vs CSV Website normalized.
E.g. 13wham.com in app vs https://13wham.com/ in CSV → both become 13wham.com → match.
Type column = platform (msn, yahoo, newsbreak).
"""

import csv
import re
from pathlib import Path
from collections import defaultdict

# CSV path: same directory as this file
TRACKER_CSV = Path(__file__).resolve().parent / "tracker_sources.csv"

PLATFORMS = ("msn", "yahoo", "newsbreak")


def _row_key(row: dict, *keys: str) -> str:
    """First non-empty value from row for given keys (e.g. 'Type' or 'type')."""
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            return v
    return ""


def normalize_domain(url_or_domain: str) -> str | None:
    """
    Normalize for comparison: strip protocol, www, path, query; lower case.
    Same normalization for CSV Website (https://13wham.com/) and app company string (13wham.com).
    """
    if not url_or_domain or not isinstance(url_or_domain, str):
        return None
    s = (url_or_domain or "").strip().lower()
    if not s:
        return None
    # Strip protocol
    s = re.sub(r"^https?://", "", s)
    # Strip path and query (take host only)
    s = s.split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    s = s.rstrip(".")
    return s if s else None


def load_domain_to_platforms(csv_path: Path | None = None) -> dict[str, set[str]]:
    """
    Build map: normalized Website (from CSV) -> set of platform types (Type column).
    Only CSV Website column is used; company string is compared after same normalization.
    """
    path = csv_path or TRACKER_CSV
    key_to_platforms: dict[str, set[str]] = defaultdict(set)
    if not path.exists():
        return dict(key_to_platforms)
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return dict(key_to_platforms)
            for row in reader:
                raw_type = _row_key(row, "Type", "type").lower()
                if raw_type not in PLATFORMS:
                    continue
                website = _row_key(row, "Website", "website")
                domain = normalize_domain(website)
                if domain:
                    key_to_platforms[domain].add(raw_type)
    except (OSError, csv.Error):
        return dict(key_to_platforms)
    return dict(key_to_platforms)


def get_presence_for_domain(company_display_string: str, domain_to_platforms: dict[str, set[str]] | None = None) -> dict[str, bool]:
    """
    Company string = exactly as displayed in Companies tab (e.g. 13wham.com).
    Same normalization is applied then compared to normalized Website from CSV.
    """
    if domain_to_platforms is None:
        domain_to_platforms = load_domain_to_platforms()
    norm = normalize_domain(company_display_string)
    platforms_set = domain_to_platforms.get(norm, set()) if norm else set()
    return {p: (p in platforms_set) for p in PLATFORMS}


def update_all_companies_from_csv(csv_path: Path | None = None) -> tuple[int, str]:
    """
    Load CSV, compute presence per company by domain, write to Company.extra_data["platform_presence"].
    Returns (updated_count, "" on success or error_message).
    """
    from db_ops import get_all_companies, merge_company_extra_data

    domain_to_platforms = load_domain_to_platforms(csv_path)
    companies = get_all_companies(exclude_advisor_shadows=True)
    updated = 0
    for c in companies:
        domain = (c.get("domain") or "").strip()
        presence = get_presence_for_domain(domain, domain_to_platforms)
        patch = {"platform_presence": presence}
        if merge_company_extra_data(c["id"], patch):
            updated += 1
    return updated, ""
