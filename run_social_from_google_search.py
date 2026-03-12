"""
Search-based social follower counts. No Scrapy, no AI.

init: Build CSV from DB — all domains (Brand) + Yahoo/MSN/Newsbreak/Smartnews from tracker_sources (and DB platform_presence when present); IG/FB/YT empty.
Run: For each row, search per platform; get count from first result snippet. If no count in snippet, open first result URL and read count from page.

Read rows from CSV written by init (no DB). Use --multi for multithreaded run.

Output: social_from_search.csv — Brand, Yahoo, MSN, MSN Followers, Newsbreak, Smartnews, Instagram, Facebook, YT.

Usage:
  python run_social_from_google_search.py --step init   # create/update CSV (Yahoo, MSN, Newsbreak from tracker)
  python run_social_from_google_search.py --step msn-followers   # update only MSN Followers column from tracker_sources.csv
  python run_social_from_google_search.py              # run (search + fetch first URL if no count), single-threaded
  python run_social_from_google_search.py --multi
  python run_social_from_google_search.py --debug
  python run_social_from_google_search.py --domain=411mania.com   # one domain, console only (no file write)
  python run_social_from_google_search.py --ig   # all domains: fetch only Instagram (other columns kept from CSV)
  python run_social_from_google_search.py --fb   # all domains: fetch only Facebook
  python run_social_from_google_search.py --yt   # all domains: fetch only YouTube
  python run_social_from_google_search.py --ig=20k   # re-run IG on NA or where value <= 20K (fix low/wrong values)
  python run_social_from_google_search.py --ig --ig=20k   # same: only IG, and only rows with NA or IG <= 20k
  python run_social_from_google_search.py --ordered --ig --yt --fb   # run all domains IG, then all YT, then all FB (not per-domain)
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import defaultdict

# Suppress ddgs/primp "Impersonate '...' does not exist" so script doesn't look stuck
logging.getLogger("primp").setLevel(logging.ERROR)
logging.getLogger("ddgs").setLevel(logging.ERROR)

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(str(_root / "keys.env"), override=True)
load_dotenv(str(_root / ".env"), override=True)

TRACKER_CSV = _root / "tracker_sources.csv"
PLATFORMS = ("msn", "yahoo", "newsbreak", "smartnews")
OUT_CSV = _root / "social_from_search.csv"
SEARCH_DELAY_SEC = 0.5
FETCH_DELAY_SEC = 0.4
SEARCH_RETRY_DELAY_SEC = 2.0  # delay before retry on timeout/failure
RETRY_NA_DELAY_SEC = 3.0  # delay before retry when we got NA (retry up to 2 more times to get a value)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# When --multi: max concurrent search/fetch ops across workers (rate limit to avoid blocking)
_RATE_LIMIT_SEMAPHORE: threading.Semaphore | None = None
MAX_CONCURRENT_SEARCH = 3


def _get_step():
    """Return 'init', 'msn-followers', or 0. init = build/update CSV. msn-followers = update only MSN Followers from tracker_sources. 0 = run search."""
    for i, arg in enumerate(sys.argv):
        if arg == "--step" and i + 1 < len(sys.argv):
            val = sys.argv[i + 1].strip().lower()
            if val == "init":
                return "init"
            if val == "msn-followers":
                return "msn-followers"
            return 0
    env = os.getenv("RUN_SOCIAL_STEP", "0").strip().lower()
    if env == "init":
        return "init"
    if env == "msn-followers":
        return "msn-followers"
    return 0


def _get_multi() -> bool:
    """True if --multi or RUN_SOCIAL_MULTI=1."""
    if "--multi" in sys.argv:
        return True
    return os.getenv("RUN_SOCIAL_MULTI", "").strip() in ("1", "true", "yes")


def _get_ordered() -> bool:
    """True if --ordered: run all domains for IG, then all for YT, then all for FB (instead of per-domain IG+YT+FB)."""
    if "--ordered" in sys.argv:
        return True
    return os.getenv("RUN_SOCIAL_ORDERED", "").strip() in ("1", "true", "yes")


# With --ordered: process in this order (all domains for one platform, then next platform)
PLATFORM_ORDER = ("instagram", "youtube", "facebook")


def _get_debug() -> bool:
    """True if --debug or RUN_SOCIAL_DEBUG=1. Print intermediate search results and parsed text."""
    if "--debug" in sys.argv:
        return True
    return os.getenv("RUN_SOCIAL_DEBUG", "").strip() in ("1", "true", "yes")


def _get_platform_filter() -> set[str] | None:
    """If --ig/--fb/--yt or --ig=/--fb=/--yt= are set, return set of platforms to fetch; else None (fetch all)."""
    platforms = set()
    for arg in sys.argv:
        if arg == "--ig" or arg.startswith("--ig="):
            platforms.add("instagram")
            break
    for arg in sys.argv:
        if arg == "--fb" or arg.startswith("--fb="):
            platforms.add("facebook")
            break
    for arg in sys.argv:
        if arg == "--yt" or arg.startswith("--yt="):
            platforms.add("youtube")
            break
    return platforms if platforms else None


def _get_thresholds() -> dict[str, int]:
    """Parse --ig=20k, --fb=50k, --yt=10k (re-run when value is NA or <= threshold). Returns e.g. {'instagram': 20000}."""
    thresholds = {}
    for arg in sys.argv:
        if arg.startswith("--ig="):
            raw = arg.split("=", 1)[1].strip()
            n = _parse_count_to_int(raw) if raw else None
            if n is not None:
                thresholds["instagram"] = n
        elif arg.startswith("--fb="):
            raw = arg.split("=", 1)[1].strip()
            n = _parse_count_to_int(raw) if raw else None
            if n is not None:
                thresholds["facebook"] = n
        elif arg.startswith("--yt="):
            raw = arg.split("=", 1)[1].strip()
            n = _parse_count_to_int(raw) if raw else None
            if n is not None:
                thresholds["youtube"] = n
    return thresholds


def _should_rerun_by_threshold(cell_value: str, threshold: int | None) -> bool:
    """True if threshold is set and cell value parses to a number <= threshold (re-fetch low values)."""
    if threshold is None:
        return False
    parsed = _parse_count_to_int(cell_value)
    if parsed is None:
        return False
    return parsed <= threshold


_PLATFORM_COLUMN = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YT"}


def _log_threshold_changes(row_old: dict, row_new: dict, thresholds: dict[str, int]) -> None:
    """When thresholds are set, log old --> new for each platform so we know what changed or stayed correct."""
    if not thresholds:
        return
    for plat in sorted(thresholds.keys()):
        col = _PLATFORM_COLUMN.get(plat, plat)
        old_val = (row_old.get(col) or "").strip() or "NA"
        new_val = (row_new.get(col) or "").strip() or "NA"
        if old_val != new_val:
            print(f"    {col}: {old_val} --> {new_val}")
        else:
            print(f"    {col}: {old_val} --> {new_val} (unchanged)")


def _get_domain_filter() -> str | None:
    """If --domain=example.com or RUN_SOCIAL_DOMAIN=example.com, return that domain; else None. No file write, console only."""
    for arg in sys.argv:
        if arg.startswith("--domain="):
            val = arg.split("=", 1)[1].strip()
            if val:
                return val
    val = os.getenv("RUN_SOCIAL_DOMAIN", "").strip()
    return val or None


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


def _row_key(row: dict, *keys: str) -> str:
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            return v
    return ""


def _load_tracker_presence_and_msn_followers() -> tuple[dict[str, set[str]], dict[str, int]]:
    """Load MSN/Yahoo/Newsbreak/Smartnews presence and MSN follower counts from tracker_sources (CSV)."""
    from tracker_sources import load_domain_to_platforms, load_domain_to_msn_follower_count
    domain_to_platforms = load_domain_to_platforms(TRACKER_CSV)
    domain_to_msn_followers = load_domain_to_msn_follower_count(TRACKER_CSV)
    return domain_to_platforms, domain_to_msn_followers


def _format_follower(n: int | None) -> str:
    if n is None or n <= 0:
        return "NA"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"~{n / 1_000:.1f}K".replace(".0", "")
    return str(n)


def _brand_url(domain: str) -> str:
    if not domain:
        return "—"
    d = domain.strip().lower()
    if not d.startswith("http"):
        d = "https://www." + d if not d.startswith("www.") else "https://" + d
    return d if d.endswith("/") else d + "/"


def _parse_count_to_int(raw: str) -> int | None:
    """Parse '1234', '12.5K', '1.2M', '30.5K+', '27L' (lakh=100K) to integer. Returns None if not parseable."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.replace(",", "").replace(" ", "").strip().rstrip("+")
    if not s:
        return None
    s_upper = s.upper()
    mult = 1
    if s_upper.endswith("K"):
        mult = 1_000
        s = s[:-1]
    elif s_upper.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    elif s_upper.endswith("B"):
        mult = 1_000_000_000
        s = s[:-1]
    elif s_upper.endswith("L"):
        mult = 100_000  # lakh (Indian)
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except (ValueError, TypeError):
        return None


# Sanity cap: social follower counts above this are likely misparsed (e.g. "2856" + "M" from another word)
_MAX_SANE_FOLLOWERS = 500_000_000

def _normalize_follower_display(raw: str) -> str:
    """Format raw count (e.g. '1234', '12.5K', '1.2M') to consistent K/M display."""
    if _is_empty_or_na(raw):
        return "NA"
    n = _parse_count_to_int(raw)
    if n is None or n <= 0 or n > _MAX_SANE_FOLLOWERS:
        return "NA"
    return _format_follower(n)


def _extract_count_from_snippet(text: str) -> str:
    """Parse follower/subscriber count from search snippet: '32K followers', '1.2M likes', '950 Followers'."""
    if not text:
        return "NA"
    # e.g. 32K followers, 1.2M Followers, 27L+ followers (L=lakh), 950 likes
    m = re.search(r"([\d,.]+\s*[KkMmBbLl]?\+?)\s*(?:followers?|likes?|subscribers?)", text, re.I)
    if not m:
        m = re.search(r"([\d,.]+\s*[KkMmBbLl])\s", text)
    if not m:
        return "NA"
    raw = m.group(1).replace(",", "").replace(" ", "").strip()
    return raw.upper() if raw else "NA"


def _extract_fb_likes_from_snippet(text: str) -> str:
    """Facebook: snippet has 'X followers' or 'X likes' ( = page followers). Extract number."""
    if not text:
        return "NA"
    # Google first result: "30.5K+ followers", "27L+ followers" (L=lakh=100K), or "30536 likes"
    m = re.search(r"([\d,.]+\s*[KkMmBbLl]?\+?)\s*followers?", text, re.I)
    if m:
        raw = m.group(1).replace(",", "").replace(" ", "").strip().rstrip("+")
        if raw:
            return raw.upper()
    m = re.search(r"([\d,.]+\s*[KkMmBbLl]?\+?)\s*likes?", text, re.I)
    if m:
        raw = m.group(1).replace(",", "").replace(" ", "").strip().rstrip("+")
        if raw:
            return raw.upper()
    m = re.search(r"([\d,.]+\s*[KkMmBbLl]?)\s*likes?", text, re.I)
    if m:
        raw = m.group(1).replace(",", "").replace(" ", "").strip()
        if raw:
            return raw.upper()
    m = re.search(r"likes?\s*[·:\s]+\s*([\d,.]+\s*[KkMmBbLl]?)", text, re.I)
    if m:
        raw = m.group(1).replace(",", "").replace(" ", "").strip()
        if raw:
            return raw.upper()
    return "NA"


# URL patterns for profile links in search results
_IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+/?")
_FB_RE = re.compile(r"https?://(?:www\.)?(?:fb\.com|facebook\.com)/[a-zA-Z0-9_.-]+/?")
# YouTube: channel URLs can be /c/name, /channel/id, /@handle, /user/name, or bare /handle (e.g. /13WHAMTVNews/featured)
_YT_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:(?:c/|channel/|@|user/)[a-zA-Z0-9_-]+|(?!watch|playlist|embed|results)[a-zA-Z0-9_-]+)(?:/|$)?|youtu\.be/[a-zA-Z0-9_-]+)"
)


def _extract_url_and_count_from_results(
    results: list[dict], platform: str, debug: bool
) -> tuple[str | None, str]:
    """Parse results list (title/body/href) and return (profile_url_or_None, count_str)."""
    if platform.lower() == "instagram":
        pattern = _IG_RE
    elif platform.lower() == "facebook":
        pattern = _FB_RE
    else:
        pattern = _YT_RE
    url_found = None
    count_str = "NA"
    for idx, item in enumerate(results):
        href = (item.get("href") or "").strip()
        body = (item.get("body") or "").strip()
        title = (item.get("title") or "").strip()
        this_has_url = False
        for text in (href, body, title):
            m = pattern.search(text)
            if m:
                u = m.group(0).rstrip("/")
                if not u.startswith("http"):
                    u = "https://" + u
                if not url_found:
                    url_found = u
                this_has_url = True
                break
        if this_has_url:
            if debug:
                combined = f"{title} {body}".strip()
                if len(combined) > 500:
                    combined_preview = combined[:500] + "..."
                else:
                    combined_preview = combined
                print(f"[DEBUG] Using result index {idx} (first with platform URL)")
                print(f"[DEBUG] URL found: {url_found}")
                print(f"[DEBUG] Text we parse for count (title + body): {combined_preview!r}")
            if platform.lower() == "facebook":
                for text in (f"{title} {body}".strip(), body, title):
                    c = _extract_fb_likes_from_snippet(text)
                    if c != "NA":
                        count_str = c
                        if debug:
                            print(f"[DEBUG] Extracted (fb_likes): {c!r}")
                        break
            if count_str == "NA":
                for text in (body, title):
                    c = _extract_count_from_snippet(text)
                    if c != "NA":
                        count_str = c
                        if debug:
                            print(f"[DEBUG] Extracted (generic): {c!r}")
                        break
            if debug:
                normalized = _normalize_follower_display(count_str)
                print(f"[DEBUG] Raw count_str: {count_str!r} -> normalized: {normalized!r}")
            break
    return url_found, count_str


def _search_one_platform(brand: str, platform: str, search_query: str | None = None) -> tuple[str | None, str]:
    """
    Run one query; return (profile_url_or_None, follower_count_from_snippet).
    Retries once on timeout/failure; if no count from first search, retries search once.
    When --multi, uses _RATE_LIMIT_SEMAPHORE to limit concurrent search/fetch across workers.
    """
    sem = _RATE_LIMIT_SEMAPHORE
    if sem:
        sem.acquire()
    try:
        return _search_one_platform_impl(brand, platform, search_query)
    finally:
        if sem:
            sem.release()


def _search_one_platform_impl(brand: str, platform: str, search_query: str | None = None) -> tuple[str | None, str]:
    if search_query:
        q = search_query.strip()
    else:
        if not (brand or "").strip():
            return None, "NA"
        q = f"{brand.strip()} {platform}"
    if not q:
        return None, "NA"
    debug = _get_debug()
    try:
        from linkedin_lookup import text_search
    except Exception:
        return None, "NA"

    # Fetch results with retry once on timeout/failure
    results = None
    for attempt in range(2):
        try:
            results = text_search(q, max_results=8, backend_prefer="bing")
            break
        except Exception as e:
            if debug:
                print(f"[DEBUG] Search failed (attempt {attempt + 1}): {e}")
            if attempt == 0:
                time.sleep(SEARCH_RETRY_DELAY_SEC)
                continue
            return None, "NA"
    if not results:
        return None, "NA"

    if debug:
        print(f"\n[DEBUG] === {platform} ===")
        print(f"[DEBUG] Query: {q!r}")
        print(f"[DEBUG] Backend: bing only")
        print(f"[DEBUG] Results returned: {len(results)}")
        for i, item in enumerate(results):
            t = (item.get("title") or "").strip()
            b = (item.get("body") or "").strip()
            h = (item.get("href") or "").strip()
            if len(b) > 400:
                b = b[:400] + "..."
            if len(t) > 120:
                t = t[:120] + "..."
            print(f"[DEBUG]   [{i}] title={t!r}")
            print(f"[DEBUG]       body={b!r}")
            print(f"[DEBUG]       href={h!r}")

    url_found, count_str = _extract_url_and_count_from_results(results, platform, debug)

    # If we didn't get a count, retry search once (second attempt may return data)
    if _is_empty_or_na(count_str):
        time.sleep(SEARCH_DELAY_SEC)
        try:
            results2 = text_search(q, max_results=8, backend_prefer="bing")
            if results2:
                url_found2, count_str2 = _extract_url_and_count_from_results(results2, platform, False)
                if not _is_empty_or_na(count_str2):
                    count_str = count_str2
                    if url_found2:
                        url_found = url_found2
                    if debug:
                        print(f"[DEBUG] Retry search got count: {count_str2!r}")
        except Exception as e:
            if debug:
                print(f"[DEBUG] Retry search failed: {e}")

    # If still no count but we have the first result URL, open it and try to read count from page
    if _is_empty_or_na(count_str) and url_found:
        if debug:
            print(f"[DEBUG] No count in snippet; fetching page: {url_found!r}")
        time.sleep(FETCH_DELAY_SEC)
        fetched = _fetch_count_from_page(url_found, platform)
        if not _is_empty_or_na(fetched):
            count_str = fetched
            if debug:
                print(f"[DEBUG] Fetched count from page: {fetched!r}")
        elif debug:
            print(f"[DEBUG] Fetched page but could not extract count (got NA)")

    return url_found, count_str


def _is_empty_or_na(val: str) -> bool:
    v = (val or "").strip()
    return not v or v.upper() == "NA"


def _fetch_count_from_page(url: str | None, platform: str) -> str:
    """
    Open the profile URL and try to read follower count from the page.
    Uses og:description, meta tags, or body text. Returns "NA" if not found or fetch fails.
    """
    if not url or not url.strip().startswith("http"):
        return "NA"
    try:
        import urllib.request
        req = urllib.request.Request(url.strip(), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception:
        return "NA"
    if not html:
        return "NA"
    # og:description often has "857K Followers" or "32K likes"
    m = re.search(r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:description["\']', html, re.I)
    if m:
        c = _extract_count_from_snippet(m.group(1))
        if c != "NA":
            return c
    if platform.lower() == "youtube":
        # YouTube: subscriberCountText in embedded JSON (multiple possible structures)
        m = re.search(r'"subscriberCountText"[^}]*?"simpleText"\s*:\s*"([^"]+)"', html)
        if m:
            return (m.group(1) or "").strip() or "NA"
        m = re.search(r'"subscriberCountText"[^}]*?"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"', html)
        if m:
            return (m.group(1) or "").strip() or "NA"
        m = re.search(r'"label"\s*:\s*"([^"]*\d[\d,.]*\s*[KkMm]?\s*subscribers?)"', html)
        if m:
            c = _extract_count_from_snippet(m.group(1))
            if c != "NA":
                return c
        # Fallback: any "X subscribers" or "X subscriber" in page
        m = re.search(r"([\d,.]+\s*[KkMmBb]?)\s*subscribers?", html, re.I)
        if m:
            raw = m.group(1).replace(",", "").replace(" ", "").strip()
            return raw.upper() if raw else "NA"
    # Fallback: number + "followers" in page
    m = re.search(r"([\d,.]+\s*[KkMmBbLl]?\+?)\s*(?:followers?|likes?|subscribers?)", html, re.I)
    if m:
        raw = m.group(1).replace(",", "").replace(" ", "").strip().rstrip("+")
        return raw.upper() if raw else "NA"
    return "NA"


COLUMNS = [
    "Brand", "Yahoo", "MSN", "MSN Followers", "Newsbreak", "Smartnews",
    "Instagram", "Facebook", "YT",
]


def _run_init() -> None:
    """Update only Yahoo, MSN, MSN Followers, Newsbreak from tracker_sources (+ DB). Leave Brand, Smartnews, Instagram, Facebook, YT unchanged."""
    from db_ops import get_all_companies
    if not TRACKER_CSV.exists():
        print(f"Error: tracker_sources.csv not found at {TRACKER_CSV}")
        sys.exit(1)
    try:
        companies = get_all_companies(exclude_advisor_shadows=True)
    except Exception as e:
        print(f"Error: DB connection failed. {e}")
        sys.exit(1)
    to_process = [
        c for c in companies
        if (c.get("domain") or "").strip() and not (c.get("domain") or "").strip().startswith("__advisor_")
    ]
    if not to_process:
        print("No companies to process.")
        sys.exit(0)
    domain_to_platforms, domain_to_msn_followers = _load_tracker_presence_and_msn_followers()
    companies_by_norm = {_normalize_domain((c.get("domain") or "").strip()): c for c in to_process}

    # Only Yahoo, MSN, MSN Followers, Newsbreak — NOT Smartnews
    def _tracker_row(c: dict, norm: str) -> dict:
        pp = (c.get("extra_data") or {}).get("platform_presence") or {}
        def _has(p: str) -> bool:
            if p in pp and isinstance(pp[p], bool):
                return pp[p]
            return p in domain_to_platforms.get(norm, set())
        presence = {p: _has(p) for p in PLATFORMS}
        msn_follower = domain_to_msn_followers.get(norm)
        return {
            "Yahoo": "Yes" if presence.get("yahoo") else "No",
            "MSN": "Yes" if presence.get("msn") else "No",
            "MSN Followers": _format_follower(msn_follower),
            "Newsbreak": "Yes" if presence.get("newsbreak") else "No",
        }

    existing = _load_rows_from_csv()
    if existing:
        print("Step init: updating only Yahoo, MSN, MSN Followers, Newsbreak (keeping Brand, Smartnews, IG, FB, YT as-is).")
        norms_in_csv = {_normalize_domain(r.get("Brand") or "") for r in existing}
        for row in existing:
            norm = _normalize_domain(row.get("Brand") or "")
            c = companies_by_norm.get(norm)
            if c:
                tracker = _tracker_row(c, norm)
                row["Yahoo"] = tracker["Yahoo"]
                row["MSN"] = tracker["MSN"]
                row["MSN Followers"] = tracker["MSN Followers"]
                row["Newsbreak"] = tracker["Newsbreak"]
        new_rows = []
        for c in to_process:
            norm = _normalize_domain((c.get("domain") or "").strip())
            if not norm or norm in norms_in_csv:
                continue
            norms_in_csv.add(norm)
            domain = (c.get("domain") or "").strip()
            tracker = _tracker_row(c, norm)
            new_rows.append({
                "Brand": _brand_url(domain),
                "Yahoo": tracker["Yahoo"],
                "MSN": tracker["MSN"],
                "MSN Followers": tracker["MSN Followers"],
                "Newsbreak": tracker["Newsbreak"],
                "Smartnews": "No",  # new row default; init does not set from tracker
                "Instagram": "NA",
                "Facebook": "NA",
                "YT": "NA",
            })
        rows_to_write = existing + new_rows
    else:
        print("Step init: building CSV from DB (all domains + Yahoo, MSN, Newsbreak from tracker_sources).")
        rows_to_write = []
        for c in to_process:
            domain = (c.get("domain") or "").strip()
            norm = _normalize_domain(domain)
            if not norm:
                continue
            tracker = _tracker_row(c, norm)
            rows_to_write.append({
                "Brand": _brand_url(domain),
                "Yahoo": tracker["Yahoo"],
                "MSN": tracker["MSN"],
                "MSN Followers": tracker["MSN Followers"],
                "Newsbreak": tracker["Newsbreak"],
                "Smartnews": "No",  # init does not set Smartnews from tracker
                "Instagram": "NA",
                "Facebook": "NA",
                "YT": "NA",
            })

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows_to_write)
    print(f"Done. Wrote {len(rows_to_write)} rows to {OUT_CSV}. IG/FB/YT unchanged (or NA for new rows).")


def _run_msn_followers_only() -> None:
    """Update only MSN Followers column from tracker_sources.csv. All other columns unchanged."""
    if not TRACKER_CSV.exists():
        print(f"Error: tracker_sources.csv not found at {TRACKER_CSV}")
        sys.exit(1)
    rows = _load_rows_from_csv()
    if not rows:
        print(f"Error: {OUT_CSV} not found or empty. Run --step init first.")
        sys.exit(1)
    from tracker_sources import load_domain_to_msn_follower_count
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


def _load_rows_from_csv() -> list[dict]:
    """Load rows from CSV written by init. Returns list of row dicts with keys in COLUMNS."""
    if not OUT_CSV.exists():
        return []
    rows = []
    with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: (row.get(k) or "").strip() for k in COLUMNS})
    return rows


def _process_one_row(row: dict, idx: int, platform_filter: set[str] | None = None, thresholds: dict[str, int] | None = None) -> tuple[int, dict]:
    """Process a single row: search per platform; if no count in snippet, fetch first result URL for count.
    platform_filter: if set, only fetch these platforms; others keep row value.
    thresholds: e.g. {'instagram': 20000} — also re-run when value is <= threshold (for low/wrong values).
    """
    brand_url = (row.get("Brand") or "").strip()
    domain = _normalize_domain(brand_url)
    if not domain:
        return idx, row
    brand = domain.split(".")[0] if domain else ""
    out = dict(row)

    run_all = platform_filter is None
    thresholds = thresholds or {}

    if run_all or "instagram" in (platform_filter or set()):
        cur_ig = row.get("Instagram") or ""
        if _is_empty_or_na(cur_ig) or _should_rerun_by_threshold(cur_ig, thresholds.get("instagram")):
            new_ig = "NA"
            for attempt in range(3):
                ig_url, instagram = _search_one_platform(brand, "Instagram")
                new_ig = _normalize_follower_display(instagram)
                if not _is_empty_or_na(new_ig):
                    break
                if attempt < 2:
                    time.sleep(RETRY_NA_DELAY_SEC)
            out["Instagram"] = new_ig if not _is_empty_or_na(new_ig) else (row.get("Instagram") or "NA")
            time.sleep(SEARCH_DELAY_SEC)
        else:
            out["Instagram"] = cur_ig or "NA"
    else:
        out["Instagram"] = row.get("Instagram") or "NA"

    if run_all or "facebook" in (platform_filter or set()):
        cur_fb = row.get("Facebook") or ""
        if _is_empty_or_na(cur_fb) or _should_rerun_by_threshold(cur_fb, thresholds.get("facebook")):
            new_fb = "NA"
            for attempt in range(3):
                fb_url, facebook = _search_one_platform(brand, "Facebook", search_query=f"{domain} facebook followers")
                new_fb = _normalize_follower_display(facebook)
                if not _is_empty_or_na(new_fb):
                    break
                if attempt < 2:
                    time.sleep(RETRY_NA_DELAY_SEC)
            out["Facebook"] = new_fb if not _is_empty_or_na(new_fb) else (row.get("Facebook") or "NA")
            time.sleep(SEARCH_DELAY_SEC)
        else:
            out["Facebook"] = cur_fb or "NA"
    else:
        out["Facebook"] = row.get("Facebook") or "NA"

    if run_all or "youtube" in (platform_filter or set()):
        cur_yt = row.get("YT") or ""
        if _is_empty_or_na(cur_yt) or _should_rerun_by_threshold(cur_yt, thresholds.get("youtube")):
            new_yt = "NA"
            for attempt in range(3):
                yt_url, yt = _search_one_platform(brand, "YouTube", search_query=f"{domain} youtube")
                new_yt = _normalize_follower_display(yt)
                if not _is_empty_or_na(new_yt):
                    break
                if attempt < 2:
                    time.sleep(RETRY_NA_DELAY_SEC)
            out["YT"] = new_yt if not _is_empty_or_na(new_yt) else (row.get("YT") or "NA")
        else:
            out["YT"] = cur_yt or "NA"
    else:
        out["YT"] = row.get("YT") or "NA"

    return idx, out


def main():
    step = _get_step()
    multi = _get_multi()

    if step == "init":
        _run_init()
        return
    if step == "msn-followers":
        _run_msn_followers_only()
        return

    # Step 0: load rows from CSV (from init); no DB
    rows = _load_rows_from_csv()
    if not rows:
        print(f"Error: {OUT_CSV} not found or empty. Run --step init first.")
        sys.exit(1)

    domain_filter = _get_domain_filter()
    if domain_filter:
        norm_filter = _normalize_domain(domain_filter)
        rows = [r for r in rows if _normalize_domain(r.get("Brand") or "") == norm_filter]
        if not rows:
            print(f"No row found for domain: {domain_filter!r}")
            sys.exit(1)
        print(f"Domain test only: {domain_filter!r} (no file write, console only)\n")

    print(f"Run: search + fetch first URL if no count. Multi: {multi}. Rows: {len(rows)} (from CSV, no DB).")
    platform_filter = _get_platform_filter()
    thresholds = _get_thresholds()
    if platform_filter:
        print(f"Only fetching: {', '.join(sorted(platform_filter))}. Other columns kept from CSV.")
    if thresholds:
        parts = []
        for plat, n in sorted(thresholds.items()):
            if n >= 1_000_000:
                parts.append(f"{plat}<={n / 1_000_000:.1f}M")
            elif n >= 1_000:
                parts.append(f"{plat}<={n / 1_000:.1f}K".rstrip("0").rstrip(".") + "K")
            else:
                parts.append(f"{plat}<={n}")
        print(f"Threshold: also re-run where value <= ({', '.join(parts)}).")
    if platform_filter or thresholds:
        print()
    if not domain_filter:
        print(f"Output: {OUT_CSV}\n")

    global _RATE_LIMIT_SEMAPHORE
    if multi:
        _RATE_LIMIT_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_SEARCH)
        print(f"Rate limit: max {MAX_CONCURRENT_SEARCH} concurrent search/fetch.\n")
    else:
        _RATE_LIMIT_SEMAPHORE = None
        n_platforms = 3 if not platform_filter else len(platform_filter)
        if n_platforms == 1:
            print("Starting... first row may take 10-30s (1 search + optional fetch).\n")
        else:
            print(f"Starting... first row may take 30-60s ({n_platforms} searches + optional fetches).\n")
        print("Search: Bing only (no DuckDuckGo/Brave fallback). 429 = rate limited.\n")

    if domain_filter:
        # Single domain: process and log to console only, no CSV write
        for i, row in enumerate(rows):
            _, updated = _process_one_row(row, i, platform_filter, thresholds)
            domain = _normalize_domain((updated.get("Brand") or ""))
            print(f"\n--- Result for {domain} ---")
            for col in COLUMNS:
                print(f"  {col}: {updated.get(col, '')}")
            if thresholds:
                _log_threshold_changes(row, updated, thresholds)
            print("---")
        print("\nDomain test done. No file written.")
        return

    ordered = _get_ordered()
    if ordered:
        # Run all domains for one platform, then all for the next (IG -> YT -> FB)
        platforms_to_run = [p for p in PLATFORM_ORDER if not platform_filter or p in platform_filter]
        if not platforms_to_run:
            platforms_to_run = list(PLATFORM_ORDER)
        print(f"Ordered: {', '.join(_PLATFORM_COLUMN.get(p, p) for p in platforms_to_run)}\n")

        results = list(rows)
        write_lock = threading.Lock() if multi else None

        def write_full_csv():
            with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS)
                w.writeheader()
                w.writerows(results)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

        for platform in platforms_to_run:
            pf = {platform}
            col_name = _PLATFORM_COLUMN.get(platform, platform)
            print(f"--- Pass: {col_name} ---")

            if multi:
                def write_csv_realtime():
                    with write_lock:
                        write_full_csv()

                with ThreadPoolExecutor(max_workers=min(8, len(rows))) as ex:
                    futures = {ex.submit(_process_one_row, results[i], i, pf, thresholds): i for i in range(len(rows))}
                    for fut in as_completed(futures):
                        idx, updated = fut.result()
                        old_row = results[idx]
                        results[idx] = updated
                        write_csv_realtime()
                        domain = _normalize_domain((updated.get("Brand") or ""))
                        print(f"  [{idx + 1}/{len(rows)}] {domain}  {col_name}={updated.get(col_name)}")
                        if thresholds and platform in thresholds:
                            _log_threshold_changes(old_row, updated, {platform: thresholds[platform]})
            else:
                for i in range(len(rows)):
                    original = results[i]
                    _, updated = _process_one_row(original, i, pf, thresholds)
                    results[i] = updated
                    write_full_csv()
                    domain = _normalize_domain((updated.get("Brand") or ""))
                    print(f"[{i + 1}/{len(rows)}] {domain}  {col_name}={updated.get(col_name)}")
                    if thresholds and platform in thresholds:
                        _log_threshold_changes(original, updated, {platform: thresholds[platform]})

        print(f"\nDone. Wrote {OUT_CSV}")
        return

    if multi:
        max_workers = min(8, len(rows))
        print(f"Workers: {max_workers}\n")
        results = [None] * len(rows)
        write_lock = threading.Lock()

        def write_csv_realtime():
            with write_lock:
                with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=COLUMNS)
                    w.writeheader()
                    for i in range(len(rows)):
                        row_to_write = results[i] if results[i] is not None else rows[i]
                        w.writerow(row_to_write)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_process_one_row, row, i, platform_filter, thresholds): i for i, row in enumerate(rows)}
            for fut in as_completed(futures):
                idx, updated = fut.result()
                results[idx] = updated
                write_csv_realtime()
                domain = _normalize_domain((updated.get("Brand") or ""))
                print(f"  [{idx + 1}/{len(rows)}] {domain}  IG={updated.get('Instagram')} FB={updated.get('Facebook')} YT={updated.get('YT')}")
                if thresholds:
                    _log_threshold_changes(rows[idx], updated, thresholds)
    else:
        # Update init CSV one row at a time: keep full state in memory, write full file after each row (no override of other rows/columns)
        results = list(rows)
        for i in range(len(rows)):
            original = results[i]
            _, updated = _process_one_row(original, i, platform_filter, thresholds)
            results[i] = updated
            with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS)
                w.writeheader()
                w.writerows(results)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            domain = _normalize_domain((updated.get("Brand") or ""))
            print(f"[{i + 1}/{len(rows)}] {domain}  IG={updated.get('Instagram')} FB={updated.get('Facebook')} YT={updated.get('YT')}")
            if thresholds:
                _log_threshold_changes(original, updated, thresholds)

    print(f"\nDone. Wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
