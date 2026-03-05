"""
Platform presence (MSN, Yahoo, NewsBreak) from tracker_sources.csv or from web search.
- CSV: match company domain vs CSV Website normalized; Type column = platform.
- Search: query "domain msn", "domain yahoo", "domain newsbreak"; first 3 results per query;
  presence = at least 2 of 3 results clearly show the outlet on that platform (URL + title/snippet).
"""

import csv
import re
import time
import random
import logging
from pathlib import Path
from collections import defaultdict

log = logging.getLogger(__name__)

# CSV path: same directory as this file
TRACKER_CSV = Path(__file__).resolve().parent / "tracker_sources.csv"

PLATFORMS = ("msn", "yahoo", "newsbreak")

# For search-based presence: URL must contain this host; at least 2 of 3 results must confirm
PLATFORM_HOSTS = {"msn": "msn.com", "yahoo": "yahoo.com", "newsbreak": "newsbreak.com"}
SEARCH_RESULTS_PER_PLATFORM = 3
MIN_RESULTS_TO_CONFIRM = 2  # stricter: at least 2 of 3 results must clearly show presence
DELAY_BETWEEN_COMPANIES_SEC = (1.0, 2.0)  # rate limit when running for all companies


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


def _result_confirms_presence(result: dict, platform: str, company_domain: str) -> bool:
    """
    True if this search result clearly shows the outlet (company_domain) on the platform.
    URL must contain platform host; title or body must reference the company domain.
    """
    host = PLATFORM_HOSTS.get(platform, "")
    if not host or not company_domain:
        return False
    href = (result.get("href") or "").lower()
    if host not in href:
        return False
    domain_lower = company_domain.strip().lower()
    # Strip www. for matching in snippets
    if domain_lower.startswith("www."):
        domain_lower = domain_lower[4:]
    text = f"{result.get('title') or ''} {result.get('body') or ''}".lower()
    return domain_lower in text or domain_lower.replace(".", "") in text.replace(".", "")


def search_platform_presence_for_domain(domain: str) -> tuple[dict[str, bool], dict[str, list]]:
    """
    Run web search for "domain msn", "domain yahoo", "domain newsbreak"; take first 3 results each.
    Presence = at least MIN_RESULTS_TO_CONFIRM of 3 results clearly show the outlet on that platform.
    Returns (platform_presence, platform_presence_search_results).
    """
    from linkedin_lookup import text_search

    domain = (domain or "").strip()
    if not domain:
        return {p: False for p in PLATFORMS}, {p: [] for p in PLATFORMS}

    presence = {}
    search_results = {}

    for platform in PLATFORMS:
        query = f"{domain} {platform}"
        try:
            raw = text_search(query, max_results=SEARCH_RESULTS_PER_PLATFORM)
        except Exception as e:
            log.warning("Platform presence search failed for %s %s: %s", domain, platform, e)
            raw = []
        results = [
            {"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
            for r in raw[:SEARCH_RESULTS_PER_PLATFORM]
        ]
        search_results[platform] = results
        confirming = sum(1 for r in results if _result_confirms_presence(r, platform, domain))
        presence[platform] = confirming >= MIN_RESULTS_TO_CONFIRM

    return presence, search_results


def update_all_companies_from_search(progress_callback=None) -> tuple[int, str]:
    """
    For each company, run search-based platform presence, save to Company.extra_data.
    progress_callback(current_index, total, company_domain) is called each company (e.g. for UI).
    Returns (updated_count, "" on success or error_message).
    """
    from db_ops import get_all_companies, merge_company_extra_data

    companies = get_all_companies(exclude_advisor_shadows=True)
    total = len(companies)
    updated = 0
    for i, c in enumerate(companies):
        domain = (c.get("domain") or "").strip()
        if progress_callback:
            progress_callback(i + 1, total, domain or c.get("name", "?"))
        if not domain:
            continue
        try:
            presence, search_results = search_platform_presence_for_domain(domain)
            patch = {
                "platform_presence": presence,
                "platform_presence_search_results": search_results,
            }
            if merge_company_extra_data(c["id"], patch):
                updated += 1
        except Exception as e:
            log.warning("Search platform presence failed for %s: %s", domain, e)
        if i < total - 1:
            time.sleep(random.uniform(*DELAY_BETWEEN_COMPANIES_SEC))
    return updated, ""


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


if __name__ == "__main__":
    """
    Run platform presence search for all companies and save to DB (SQLite or Postgres per keys.env).
    Usage: python tracker_sources.py
    """
    import sys
    # Ensure config/DB are loaded (same as app)
    _root = Path(__file__).resolve().parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from dotenv import load_dotenv
    load_dotenv(_root / "keys.env", override=True)
    load_dotenv(_root / ".env")

    print("Platform presence search — loading companies from DB...")
    updated, err = update_all_companies_from_search(
        progress_callback=lambda i, total, domain: print(f"  [{i}/{total}] {domain}")
    )
    if err:
        print("Error:", err)
        sys.exit(1)
    print(f"Done. Updated {updated} companies. Data saved to DB (app will show it on next load).")

    # Quick verification: show one company's saved presence so you can confirm it worked
    if updated > 0:
        from db_ops import get_all_companies, get_company
        companies = get_all_companies(exclude_advisor_shadows=True)
        for c in companies[:5]:
            ed = (c.get("extra_data") or {})
            if ed.get("platform_presence_search_results"):
                pp = ed.get("platform_presence") or {}
                print(f"\nSample (saved to DB): {c.get('domain')} -> MSN:{pp.get('msn')} Yahoo:{pp.get('yahoo')} NewsBreak:{pp.get('newsbreak')}")
                break
