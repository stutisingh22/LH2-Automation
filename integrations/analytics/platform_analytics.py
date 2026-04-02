"""
Platform analytics collector for deep research.

Collector flow:
- Similarweb Pro endpoint (cookie-based) -> AvgMonthVisits
- Platform presence search (MSN / Yahoo / NewsBreak)
- Social footprint scrape from publisher homepage
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import requests

from .tracker_sources import normalize_domain, search_platform_presence_for_domain
from integrations.search import text_search

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SIMWEB_API_URL = "https://pro.similarweb.com/widgetApi/WebsiteOverview/EngagementOverview/Table"
SEARCH_RESULTS_PER_SOCIAL = 3
MIN_SOCIAL_RESULTS_TO_CONFIRM = 2

# Worldwide (999) to match SimilarWeb Pro UI; use "840" for US-only
SIMWEB_COUNTRY_CODE = "999"


def _get_similarweb_date_range_single_month() -> tuple[str, str]:
    """
    Return (from_str, to_str) for the last complete month.
    Format: YYYY|MM|DD as expected by SimilarWeb Pro API (e.g. 2026|02|01 to 2026|02|28).
    """
    today = datetime.utcnow()
    first_of_this_month = today.replace(day=1)
    end_date = first_of_this_month - timedelta(days=1)  # last day of previous month
    start_date = end_date.replace(day=1)
    from_str = start_date.strftime("%Y|%m|01")
    to_str = end_date.strftime(f"%Y|%m|{end_date.day:02d}")
    return from_str, to_str


def _get_similarweb_date_range() -> tuple[str, str]:
    """
    Return (from_str, to_str) for the most recent full 3-month window.
    Format: YYYY|MM|DD as expected by SimilarWeb Pro API.
    Always uses completed months — never the current partial month.
    """
    today = datetime.utcnow()
    first_of_this_month = today.replace(day=1)
    end_date = first_of_this_month - timedelta(days=1)
    start_month = end_date.month - 2
    start_year = end_date.year
    if start_month <= 0:
        start_month += 12
        start_year -= 1
    start_date = end_date.replace(year=start_year, month=start_month, day=1)
    from_str = start_date.strftime("%Y|%m|01")
    to_str = end_date.strftime(f"%Y|%m|{end_date.day:02d}")
    return from_str, to_str


def _similarweb_monthly_visits(domain: str) -> dict[str, Any]:
    """
    Fetch AvgMonthVisits from Similarweb Pro API using stored cookies.
    Falls back to public page scrape if cookie is missing or expired.
    Uses worldwide traffic (country 999) by default.
    """
    cookies = (os.getenv("SIMILARWEB_COOKIES") or os.getenv("SIMWEB_COOKIES") or "").strip()
    if cookies:
        result = _similarweb_pro_fetch(domain, cookies)
        if result.get("visits"):
            return result
        # Cookie present but returned no data — fall through to public scrape
        log.warning("SimilarWeb Pro returned no data for %s (%s), trying public fallback", domain, result.get("error"))

    return _similarweb_public_fallback(domain)


def _similarweb_pro_fetch(domain: str, cookies: str) -> dict[str, Any]:
    """Hit the authenticated SimilarWeb Pro API endpoint (worldwide, last complete month)."""
    from_str, to_str = _get_similarweb_date_range_single_month()

    params = {
        "country": SIMWEB_COUNTRY_CODE,
        "iso": "[object Object]",
        "to": to_str,
        "from": from_str,
        "isWindow": "false",
        "webSource": "Total",
        "ignoreFilterConsistency": "false",
        "includeSubDomains": "true",
        "timeGranularity": "Monthly",
        "keys": domain,
        "ShouldGetVerifiedData": "false",
    }

    headers = {
        "accept": "application/json",
        "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
        "content-type": "application/json; charset=utf-8",
        "referer": "https://pro.similarweb.com/",
        "user-agent": USER_AGENT,
        "x-requested-with": "XMLHttpRequest",
        "Cookie": cookies,
        "x-sw-page": (
            f"https://pro.similarweb.com/#/digitalsuite/websiteanalysis/overview/"
            f"website-performance/*/999/3m?webSource=Total&key={domain}"
        ),
    }

    try:
        r = requests.get(SIMWEB_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code in (401, 403):
            return {"visits": None, "error": "cookie_expired"}
        if r.status_code != 200:
            return {"visits": None, "error": f"http_{r.status_code}"}
        data = r.json()
        items = data.get("Data") or []
        if items:
            visits = int(items[0].get("AvgMonthVisits") or 0)
            return {
                "visits": visits if visits > 0 else None,
                "error": None,
                "date_range": f"{from_str} to {to_str}",
                "country": "999",
                "source": "similarweb_pro",
            }
    except requests.exceptions.Timeout:
        return {"visits": None, "error": "timeout"}
    except requests.exceptions.ConnectionError:
        return {"visits": None, "error": "connection_error"}
    except ValueError:
        return {"visits": None, "error": "invalid_json"}
    except Exception as e:
        return {"visits": None, "error": str(e)}

    return {"visits": None, "error": "no_data"}


def _similarweb_public_fallback(domain: str) -> dict[str, Any]:
    """
    Scrape the public SimilarWeb website page for monthly visits estimate.
    Extracts the visits figure shown on https://www.similarweb.com/website/<domain>/
    Returns total visits (not US-only — noted in source field).
    """
    url = f"https://www.similarweb.com/website/{domain}/"
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": USER_AGENT,
    }
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return {"visits": None, "error": f"public_page_http_{r.status_code}", "source": "similarweb_public"}

        text = r.text

        # Pattern 1: JSON-LD / inline data — look for "totalVisits" or "Visits" numeric values
        import re
        patterns = [
            r'"totalVisits"\s*:\s*([\d.]+)',
            r'"Total Visits"\s*:\s*"?([\d,.]+[KMB]?)"?',
            r'class="[^"]*engagement[^"]*"[^>]*>[^<]*?([\d,.]+[KMB]?)\s*(?:visits|Visits)',
            r'>([\d,.]+[KMB]?)\s*</span>\s*<[^>]+>\s*(?:Total )?Visits',
        ]

        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                raw = m.group(1).replace(",", "").strip()
                visits = _parse_visit_shorthand(raw)
                if visits and visits > 1000:
                    return {
                        "visits": visits,
                        "error": None,
                        "source": "similarweb_public",
                        "note": "total traffic (not US-only) — set SIMILARWEB_COOKIES for US-specific data",
                    }

        # Pattern 2: search snippet fallback via text_search
        return _traffic_estimate_from_search(domain)

    except requests.exceptions.Timeout:
        return _traffic_estimate_from_search(domain)
    except Exception as e:
        log.warning("SimilarWeb public scrape failed for %s: %s", domain, e)
        return _traffic_estimate_from_search(domain)


def _parse_visit_shorthand(raw: str) -> int | None:
    """Convert '3.1M', '450K', '1.2B', '3100000' to int."""
    import re
    raw = raw.strip().upper()
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", raw)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "": 1}
    try:
        return int(num * multipliers.get(suffix, 1))
    except (ValueError, OverflowError):
        return None


def _traffic_estimate_from_search(domain: str) -> dict[str, Any]:
    """
    Last-resort: search for SimilarWeb traffic data for the domain
    and parse any visit estimates from snippets.
    """
    import re
    brand = domain.split(".")[0] if "." in domain else domain
    queries = [
        f"site:similarweb.com {domain}",
        f"{domain} monthly visits similarweb",
        f"{brand} website traffic statistics",
    ]
    for query in queries:
        try:
            rows = text_search(query, max_results=3)
            for r in (rows or []):
                if not isinstance(r, dict):
                    continue
                snippet = f"{r.get('title', '')} {r.get('body', '')}".lower()
                # Look for patterns like "3.1m monthly visits" or "3,100,000 visits/month"
                m = re.search(
                    r"([\d,.]+\s*[kmb]?)\s*(?:monthly\s*)?(?:visits|pageviews|traffic)",
                    snippet,
                    re.IGNORECASE,
                )
                if m:
                    raw = m.group(1).replace(",", "").replace(" ", "").upper()
                    visits = _parse_visit_shorthand(raw)
                    if visits and visits > 1000:
                        return {
                            "visits": visits,
                            "error": None,
                            "source": "search_snippet_estimate",
                            "note": "estimated from search snippets — set SIMILARWEB_COOKIES for precise data",
                            "evidence": snippet[:200],
                        }
        except Exception:
            continue

    return {"visits": None, "error": "missing_cookie — no public fallback succeeded", "source": "none"}


def _scrape_social_footprint(domain: str) -> list[dict[str, Any]]:
    """Backward-compatible alias: now uses web-search confirmation."""
    details = _search_social_presence(domain)
    return [{"network": d["network"], "share": None} for d in details if d.get("present")]


def _result_confirms_social_presence(
    result: dict[str, Any], domain: str, network_hosts: tuple[str, ...]
) -> bool:
    href = (result.get("href") or "").lower()
    if not href or not any(h in href for h in network_hosts):
        return False

    text = f"{result.get('title') or ''} {result.get('body') or ''}".lower()
    dom = (domain or "").lower().strip()
    dom_nodot = dom.replace(".", "")

    # Direct domain mention in snippet
    if dom in text or dom_nodot in text.replace(".", ""):
        return True

    # Brand root token (min 4 chars) appears in snippet
    root = dom.split(".")[0] if "." in dom else dom
    if root and len(root) >= 4 and root in text:
        return True

    # Profile URL directly contains the brand root
    if root and len(root) >= 4 and root in href:
        return True

    return False


def _search_social_presence(domain: str) -> list[dict[str, Any]]:
    """
    Determine social presence via search queries.
    Query: "<domain> <network> site:<network_domain>"
    Confirms when at least MIN_SOCIAL_RESULTS_TO_CONFIRM results clearly match.
    """
    networks = {
        "facebook": ("facebook.com",),
        "twitter": ("twitter.com", "x.com"),
        "instagram": ("instagram.com",),
        "youtube": ("youtube.com", "youtu.be"),
        "linkedin": ("linkedin.com",),
        "tiktok": ("tiktok.com",),
        "pinterest": ("pinterest.com",),
    }

    # Brand root for query (e.g. "411mania" from "411mania.com")
    brand = domain.split(".")[0] if "." in domain else domain

    out = []
    for network, hosts in networks.items():
        primary_host = hosts[0].replace(".com", "").replace(".be", "")
        # More targeted query: include site: operator to focus results
        query = f"{brand} {primary_host} site:{hosts[0]}"
        try:
            rows = text_search(query, max_results=SEARCH_RESULTS_PER_SOCIAL)
        except Exception as e:
            # Fallback: try without site: operator
            try:
                query_fallback = f"{domain} {network}"
                rows = text_search(query_fallback, max_results=SEARCH_RESULTS_PER_SOCIAL)
            except Exception:
                out.append({
                    "network": network,
                    "present": False,
                    "confidence": "low",
                    "evidence_urls": [],
                    "error": str(e),
                })
                continue

        evidence_urls = []
        confirming = 0
        for r in rows[:SEARCH_RESULTS_PER_SOCIAL]:
            if not isinstance(r, dict):
                continue
            if _result_confirms_social_presence(r, domain, hosts):
                confirming += 1
                href = (r.get("href") or "").strip()
                if href:
                    evidence_urls.append(href)

        present = confirming >= 1  # 1 confirmed match is enough given targeted site: query
        confidence = "high" if confirming >= 2 else "medium" if confirming == 1 else "low"
        out.append({
            "network": network,
            "present": present,
            "confidence": confidence,
            "evidence_urls": evidence_urls[:3],
        })

    return out


def _simple_platform_presence_check(brand: str, platform: str, host: str) -> dict[str, Any]:
    """
    Search "{brand} {platform}" and check if any of the top 3 results
    have the platform host in their href. Fast, simple, accurate.
    If present, return the matching results for summarization.
    """
    query = f"{brand} {platform}"
    try:
        rows = text_search(query, max_results=3)
    except Exception as e:
        log.warning("Presence check failed for %s/%s: %s", brand, platform, e)
        return {"present": False, "results": []}

    matching = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        href = (r.get("href") or "").lower()
        if host in href:
            matching.append({
                "title": (r.get("title") or "").strip(),
                "body": (r.get("body") or "").strip(),
                "href": href,
            })

    return {"present": bool(matching), "results": matching}


def _platform_positioning_from_search(
    domain: str, presence_results: dict[str, list]
) -> dict[str, dict[str, Any]]:
    """
    Simple, fast, accurate platform presence and positioning.
    Step 1: Search "{brand} {platform}" — check top 3 results for platform host in href.
    Step 2: If present, summarize those results via Anthropic API.
    """
    brand = domain.split(".")[0] if "." in domain else domain

    platforms = {
        "msn": "msn.com",
        "yahoo": "yahoo.com",
        "newsbreak": "newsbreak.com",
    }

    positioning = {}
    for platform, host in platforms.items():
        check = _simple_platform_presence_check(brand, platform, host)
        present = check["present"]
        results = check["results"]

        if present:
            summary = _summarize_platform_presence(domain, platform, results)
            evidence_urls = [r["href"] for r in results if r["href"]][:3]
            mode = "present"
        else:
            summary = f"{brand} does not appear on {host} in top search results."
            evidence_urls = []
            mode = "not_found"

        positioning[platform] = {
            "mode": mode,
            "present": present,
            "summary": summary,
            "evidence_urls": evidence_urls,
            "result_count": len(results),
        }

    return positioning



def _summarize_platform_presence(
    domain: str, platform: str, results: list[dict[str, Any]]
) -> str:
    """
    Use the Anthropic API to summarize what content from this domain
    is appearing on the platform, what topics it covers, and recency signals.
    Falls back to plain text if API key is unavailable.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    snippets_text = "\n".join(
        f"- {r.get('title', '(no title)')} | {(r.get('body') or '')[:200]}\n  URL: {r.get('href', '')}"
        for r in results[:6]
    )

    if not api_key:
        titles = [r["title"] for r in results if r.get("title")]
        return f"Found {len(results)} article(s) on {platform}: {' | '.join(titles[:3])}"

    prompt = f"""You are analyzing syndication data for a publisher research tool used by a media holding company.

Publisher domain being researched: {domain}
Syndication platform: {platform}

Search results showing content from {domain} appearing on {platform} (last ~30 days where visible):

{snippets_text}

Write 2-3 tight sentences covering:
1. What content types or topics from {domain} are appearing on {platform}
2. What verticals or subject areas dominate
3. Any signal about volume, recency, or consistency of syndication

Be specific and factual. Do not pad. Do not start with "Based on". Do not reference "the results" — just state what you observe."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = (data.get("content") or [{}])[0].get("text", "").strip()
            if text:
                return text
        log.warning("Anthropic summarization HTTP %s for %s/%s", resp.status_code, domain, platform)
    except Exception as e:
        log.warning("Anthropic summarization error for %s/%s: %s", domain, platform, e)

    # Fallback: plain titles
    titles = [r["title"] for r in results if r.get("title")]
    return f"Found {len(results)} article(s) on {platform}: {' | '.join(titles[:3])}"




def build_platform_analytics_for_domain(
    domain: str, platform_presence: dict[str, bool] | None = None
) -> dict[str, Any]:
    """
    Return normalized analytics snapshot for deep research expander.
    Targets USA traffic. External API call: Similarweb (live fetch).
    """
    dom = normalize_domain(domain or "")
    if not dom:
        return {
            "status": "error",
            "error": "missing_domain",
            "updated_at": datetime.utcnow().isoformat(),
        }

    presence_search_results: dict[str, list] = {}
    if platform_presence is None:
        try:
            platform_presence, presence_search_results = search_platform_presence_for_domain(dom)
        except Exception as e:
            log.warning("Platform presence search failed for %s: %s", dom, e)
            platform_presence = {"msn": False, "yahoo": False, "newsbreak": False}
            presence_search_results = {}

    result: dict[str, Any] = {
        "status": "ok",
        "domain": dom,
        "country": "US",
        "updated_at": datetime.utcnow().isoformat(),
        "source": "similarweb-pro-cookie+presence+social",
        "monthly_visits": None,
        "monthly_visits_date_range": None,
        "social_presence": [],
        "msn_presence": bool(platform_presence.get("msn", False)),
        "yahoo_presence": bool(platform_presence.get("yahoo", False)),
        "newsbreak_presence": bool(platform_presence.get("newsbreak", False)),
        "platform_presence": {
            "msn": bool(platform_presence.get("msn", False)),
            "yahoo": bool(platform_presence.get("yahoo", False)),
            "newsbreak": bool(platform_presence.get("newsbreak", False)),
        },
        "similarweb": {
            "available": False,
            "errors": [],
            "latest_monthly_visits": None,
            "latest_visits_month": None,
            "date_range": None,
            "country": "US",
            "channel_shares": {},
            "top_social_networks": [],
            "top_referral_domains": [],
        },
        "presence_search_results": presence_search_results,
        "platform_positioning": {},
        "social_presence_details": [],
        "platform_details": [],
    }

    # SimilarWeb fetch (USA preferred, public fallback if no cookie)
    sw = _similarweb_monthly_visits(dom)
    visits = sw.get("visits")
    result["similarweb"]["latest_monthly_visits"] = visits
    result["similarweb"]["available"] = bool(visits)
    result["similarweb"]["date_range"] = sw.get("date_range")
    result["similarweb"]["traffic_source"] = sw.get("source", "unknown")
    result["similarweb"]["traffic_note"] = sw.get("note", "")
    result["monthly_visits"] = visits
    result["monthly_visits_date_range"] = sw.get("date_range")
    result["monthly_visits_source"] = sw.get("source", "unknown")
    if sw.get("note"):
        result["monthly_visits_note"] = sw["note"]
    if sw.get("error"):
        result["similarweb"]["errors"].append(sw["error"])
        log.warning("SimilarWeb error for %s: %s", dom, sw["error"])

    # Social presence
    social_details = _search_social_presence(dom)
    result["social_presence_details"] = social_details
    result["social_presence"] = [d["network"] for d in social_details if d.get("present")]
    social_rows = [
        {"network": d["network"], "share": None}
        for d in social_details
        if d.get("present")
    ]
    result["similarweb"]["top_social_networks"] = social_rows
    result["similarweb"]["top_referral_domains"] = []

    # Platform positioning — runs its own domain-specific searches
    result["platform_positioning"] = _platform_positioning_from_search(dom, presence_search_results)

    # Override platform_presence flags based on what positioning actually found
    # (more reliable than search_platform_presence_for_domain which can false-positive)
    for p in ("msn", "yahoo", "newsbreak"):
        ppos = result["platform_positioning"].get(p) or {}
        confirmed = ppos.get("result_count", 0) > 0
        result["platform_presence"][p] = confirmed
        result[f"{p}_presence"] = confirmed

    # Flatten platform details
    for p in ("msn", "yahoo", "newsbreak"):
        ppos = result["platform_positioning"].get(p) or {}
        result["platform_details"].append({
            "platform": p,
            "present": bool(result["platform_presence"].get(p)),
            "site_monthly_visits_us": visits,
            "social_top_networks": [x.get("network") for x in social_rows[:3]],
            "positioning_summary": ppos.get("summary", ""),
            "positioning_evidence_urls": ppos.get("evidence_urls") or [],
        })

    return result


def _similarweb_snapshot_from_fetch(sw: dict[str, Any]) -> dict[str, Any]:
    """Build the similarweb dict for platform_presence_analytics from _similarweb_monthly_visits result."""
    visits = sw.get("visits")
    date_range = sw.get("date_range") or ""
    # Parse "YYYY|MM|01 to YYYY|MM|DD" -> "Jan 2026"
    latest_visits_month = "latest"
    if date_range:
        parts = date_range.split(" to ")
        if len(parts) >= 2:
            to_part = parts[-1].strip()
            if "|" in to_part:
                ymd = to_part.split("|")
                if len(ymd) >= 2:
                    try:
                        from datetime import datetime
                        dt = datetime(int(ymd[0]), int(ymd[1]), 1)
                        latest_visits_month = dt.strftime("%b %Y")
                    except (ValueError, IndexError):
                        pass
    errors = []
    if sw.get("error"):
        errors.append(sw["error"])
    return {
        "available": bool(visits),
        "errors": errors,
        "latest_monthly_visits": visits,
        "latest_visits_month": latest_visits_month,
        "date_range": date_range,
        "country": sw.get("country", "US"),
        "channel_shares": {},
        "top_social_networks": [],
        "top_referral_domains": [],
        "traffic_source": sw.get("source", "unknown"),
        "traffic_note": sw.get("note", ""),
    }


def apply_similarweb_traffic_to_company(company_id: int, domain: str) -> tuple[bool, str | None]:
    """
    Fetch Similarweb visits for one independent company and persist to
    Company.monthly_visits and extra_data.platform_presence_analytics.similarweb.
    Uses SIMILARWEB_COOKIES from the environment when set (e.g. keys.env).
    Returns (ok, None) or (False, "cookie_expired") without writing on cookie expiry.
    """
    from db_ops import get_company, merge_company_extra_data, upsert_company

    dom = normalize_domain(domain or "")
    if not dom or not company_id:
        return False, None
    sw = _similarweb_monthly_visits(dom)
    if sw.get("error") == "cookie_expired":
        log.warning(
            "Similarweb cookie expired during traffic refresh for company %s (%s)",
            company_id,
            dom,
        )
        return False, "cookie_expired"
    visits = sw.get("visits")
    existing = (get_company(int(company_id)) or {}).get("extra_data") or {}
    if not isinstance(existing, dict):
        existing = {}
    analytics = dict(existing.get("platform_presence_analytics") or {})
    if not isinstance(analytics, dict):
        analytics = {}
    analytics["similarweb"] = _similarweb_snapshot_from_fetch(sw)
    analytics["updated_at"] = datetime.utcnow().isoformat()
    analytics["status"] = "ok" if visits is not None else "no_data"
    ok = bool(merge_company_extra_data(int(company_id), {"platform_presence_analytics": analytics}))
    if visits is not None:
        upsert_company(dom, monthly_visits=visits)
    return ok, None


def apply_similarweb_traffic_to_advisor_company(advisor_company_id: int, domain: str) -> tuple[bool, str | None]:
    """
    Same as apply_similarweb_traffic_to_company for a media-house child row.
    AdvisorCompany has no monthly_visits column; traffic lives in extra_data only.
    """
    from db_ops import get_advisor_company, merge_advisor_company_extra_data

    dom = normalize_domain(domain or "")
    if not dom or not advisor_company_id:
        return False, None
    sw = _similarweb_monthly_visits(dom)
    if sw.get("error") == "cookie_expired":
        log.warning(
            "Similarweb cookie expired during traffic refresh for advisor company %s (%s)",
            advisor_company_id,
            dom,
        )
        return False, "cookie_expired"
    visits = sw.get("visits")
    existing = (get_advisor_company(int(advisor_company_id)) or {}).get("extra_data") or {}
    if not isinstance(existing, dict):
        existing = {}
    analytics = dict(existing.get("platform_presence_analytics") or {})
    if not isinstance(analytics, dict):
        analytics = {}
    analytics["similarweb"] = _similarweb_snapshot_from_fetch(sw)
    analytics["updated_at"] = datetime.utcnow().isoformat()
    analytics["status"] = "ok" if visits is not None else "no_data"
    if visits is not None:
        analytics["monthly_visits"] = int(visits)
    ok = bool(merge_advisor_company_extra_data(int(advisor_company_id), {"platform_presence_analytics": analytics}))
    return ok, None


def run_similarweb_for_independent_companies(
    progress_callback=None,
    min_delay_sec: float = 2.0,
    max_delay_sec: float = 5.0,
) -> tuple[int, str]:
    """
    Fetch Similarweb AvgMonthVisits for all independent companies and merge into
    Company.extra_data["platform_presence_analytics"]["similarweb"] only. Existing
    platform_presence, platform_details, etc. are left unchanged.

    Expects SIMILARWEB_COOKIES in environment (e.g. from keys.env). Stops on cookie expiry.
    Returns (updated_count, "" on success or error_message).
    """
    import random
    import time
    from db_ops import get_all_companies, get_company, merge_company_extra_data, upsert_company

    companies = get_all_companies(exclude_advisor_shadows=True)
    to_process = [
        c for c in companies
        if (c.get("domain") or "").strip()
        and not (c.get("domain") or "").strip().startswith("__advisor_")
    ]
    updated = 0
    for i, c in enumerate(to_process):
        cid = c["id"]
        domain = normalize_domain((c.get("domain") or "").strip())
        if not domain:
            continue
        if progress_callback:
            progress_callback(i + 1, len(to_process), domain)
        sw = _similarweb_monthly_visits(domain)
        if sw.get("error") == "cookie_expired":
            return updated, "Similarweb cookie expired. Update SIMILARWEB_COOKIES in keys.env and re-run."
        visits = sw.get("visits")
        existing = (get_company(cid) or {}).get("extra_data") or {}
        if not isinstance(existing, dict):
            existing = {}
        analytics = dict(existing.get("platform_presence_analytics") or {})
        if not isinstance(analytics, dict):
            analytics = {}
        analytics["similarweb"] = _similarweb_snapshot_from_fetch(sw)
        analytics["updated_at"] = datetime.utcnow().isoformat()
        analytics["status"] = "ok"
        if merge_company_extra_data(cid, {"platform_presence_analytics": analytics}):
            updated += 1
        if visits is not None:
            upsert_company(c["domain"], monthly_visits=visits)
        if i < len(to_process) - 1:
            time.sleep(random.uniform(min_delay_sec, max_delay_sec))
    return updated, ""