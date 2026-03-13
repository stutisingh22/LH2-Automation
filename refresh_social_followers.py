"""
Standalone module: fetch YouTube, Facebook, and Instagram follower counts for a domain/company.

Use from UI (e.g. refresh button) or CLI for testing.

  from refresh_social_followers import get_social_followers
  data = get_social_followers("411mania.com")  # -> {"youtube": "1.2M", "facebook": "32K", "instagram": "NA"}

  python refresh_social_followers.py 411mania.com
  python refresh_social_followers.py --domain=example.com
"""

from __future__ import annotations

import os
import re
import sys
import time
import urllib.request
from pathlib import Path

# Optional: load .env next to this file (for API keys if needed)
_root = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(_root / "keys.env", override=True)
    load_dotenv(_root / ".env", override=True)
except Exception:
    pass

# --- Constants ---
SEARCH_DELAY_SEC = 0.5
FETCH_DELAY_SEC = 0.4
SEARCH_RETRY_DELAY_SEC = 2.0
RETRY_NA_DELAY_SEC = 3.0
# Timeouts: generous so YT/FB/IG fetches don't fail due to timeout
FETCH_PAGE_TIMEOUT_SEC = 60  # urllib page fetch (profile page)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_MAX_SANE_FOLLOWERS = 500_000_000

# URL patterns for profile links in search results
_IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+/?")
_FB_RE = re.compile(r"https?://(?:www\.)?(?:fb\.com|facebook\.com)/[a-zA-Z0-9_.-]+/?")
_YT_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:(?:c/|channel/|@|user/)[a-zA-Z0-9_-]+|(?!watch|playlist|embed|results)[a-zA-Z0-9_-]+)(?:/|$)?|youtu\.be/[a-zA-Z0-9_-]+)"
)

# Search timeout (ddgs); no imports from other project files
_SEARCH_TIMEOUT_SEC = 60


def _text_search(query: str, max_results: int = 8, backend: str = "bing") -> list[dict]:
    """
    Run text search via ddgs/duckduckgo_search. Returns list of {title, body, href}.
    Standalone: no project imports. Requires: pip install ddgs (or duckduckgo-search).
    """
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
        except Exception as e:
            raise RuntimeError("Search requires ddgs. Install: pip install ddgs") from e
    out = []
    with DDGS(timeout=_SEARCH_TIMEOUT_SEC) as ddgs:
        for r in ddgs.text(
            query,
            max_results=max_results,
            region="us-en",
            safesearch="off",
            backend=backend,
        ):
            out.append({
                "title": (r.get("title") or "").strip(),
                "body": (r.get("body") or "").strip(),
                "href": (r.get("href") or "").strip(),
            })
    return out


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


def _format_follower(n: int | None) -> str:
    if n is None or n <= 0:
        return "NA"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"~{n / 1_000:.1f}K".replace(".0", "")
    return str(n)


def _parse_count_to_int(raw: str) -> int | None:
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
        mult = 100_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except (ValueError, TypeError):
        return None


def _is_empty_or_na(val: str) -> bool:
    v = (val or "").strip()
    return not v or v.upper() == "NA"


def _normalize_follower_display(raw: str) -> str:
    if _is_empty_or_na(raw):
        return "NA"
    n = _parse_count_to_int(raw)
    if n is None or n <= 0 or n > _MAX_SANE_FOLLOWERS:
        return "NA"
    return _format_follower(n)


def _extract_count_from_snippet(text: str) -> str:
    if not text:
        return "NA"
    m = re.search(r"([\d,.]+\s*[KkMmBbLl]?\+?)\s*(?:followers?|likes?|subscribers?)", text, re.I)
    if not m:
        m = re.search(r"([\d,.]+\s*[KkMmBbLl])\s", text)
    if not m:
        return "NA"
    raw = m.group(1).replace(",", "").replace(" ", "").strip()
    return raw.upper() if raw else "NA"


def _extract_fb_likes_from_snippet(text: str) -> str:
    if not text:
        return "NA"
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


def _extract_url_and_count_from_results(
    results: list[dict], platform: str
) -> tuple[str | None, str]:
    if platform.lower() == "instagram":
        pattern = _IG_RE
    elif platform.lower() == "facebook":
        pattern = _FB_RE
    else:
        pattern = _YT_RE
    url_found = None
    count_str = "NA"
    for item in results:
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
            if platform.lower() == "facebook":
                for text in (f"{title} {body}".strip(), body, title):
                    c = _extract_fb_likes_from_snippet(text)
                    if c != "NA":
                        count_str = c
                        break
            if count_str == "NA":
                for text in (body, title):
                    c = _extract_count_from_snippet(text)
                    if c != "NA":
                        count_str = c
                        break
            break
    return url_found, count_str


def _fetch_count_from_page(url: str | None, platform: str) -> str:
    if not url or not url.strip().startswith("http"):
        return "NA"
    try:
        req = urllib.request.Request(url.strip(), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=FETCH_PAGE_TIMEOUT_SEC) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception:
        return "NA"
    if not html:
        return "NA"
    m = re.search(r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:description["\']', html, re.I)
    if m:
        c = _extract_count_from_snippet(m.group(1))
        if c != "NA":
            return c
    if platform.lower() == "youtube":
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
        m = re.search(r"([\d,.]+\s*[KkMmBb]?)\s*subscribers?", html, re.I)
        if m:
            raw = m.group(1).replace(",", "").replace(" ", "").strip()
            return raw.upper() if raw else "NA"
    m = re.search(r"([\d,.]+\s*[KkMmBbLl]?\+?)\s*(?:followers?|likes?|subscribers?)", html, re.I)
    if m:
        raw = m.group(1).replace(",", "").replace(" ", "").strip().rstrip("+")
        return raw.upper() if raw else "NA"
    return "NA"


def _search_one_platform(brand: str, domain: str, platform: str) -> tuple[str | None, str]:
    if not (brand or "").strip() and not (domain or "").strip():
        return None, "NA"
    if platform.lower() == "instagram":
        q = f"{brand.strip()} Instagram"
    elif platform.lower() == "facebook":
        q = f"{domain} facebook followers"
    else:
        q = f"{domain} youtube"
    if not q.strip():
        return None, "NA"
    results = None
    for attempt in range(2):
        try:
            results = _text_search(q, max_results=8, backend="bing")
            break
        except Exception:
            if attempt == 0:
                time.sleep(SEARCH_RETRY_DELAY_SEC)
                continue
            return None, "NA"
    if not results:
        return None, "NA"
    url_found, count_str = _extract_url_and_count_from_results(results, platform)
    if _is_empty_or_na(count_str):
        time.sleep(SEARCH_DELAY_SEC)
        try:
            results2 = _text_search(q, max_results=8, backend="bing")
            if results2:
                url_found2, count_str2 = _extract_url_and_count_from_results(results2, platform)
                if not _is_empty_or_na(count_str2):
                    count_str = count_str2
                    if url_found2:
                        url_found = url_found2
        except Exception:
            pass
    if _is_empty_or_na(count_str) and url_found:
        time.sleep(FETCH_DELAY_SEC)
        fetched = _fetch_count_from_page(url_found, platform)
        if not _is_empty_or_na(fetched):
            count_str = fetched
    return url_found, count_str


def get_social_followers(domain_or_company: str) -> dict[str, str]:
    """
    Fetch YouTube, Facebook, and Instagram follower counts for a domain or company name.

    Args:
        domain_or_company: e.g. "411mania.com" or "Nike" (used for search queries).

    Returns:
        Dict with keys "youtube", "facebook", "instagram". Values are display strings
        like "1.2M", "32K", or "NA" if not found / error.
    """
    domain = _normalize_domain(domain_or_company) or (domain_or_company or "").strip()
    brand = domain.split(".")[0] if domain and "." in domain else (domain or domain_or_company or "").strip()
    if not domain and not brand:
        return {"youtube": "NA", "facebook": "NA", "instagram": "NA"}

    out: dict[str, str] = {"youtube": "NA", "facebook": "NA", "instagram": "NA"}

    for attempt in range(3):
        _, ig = _search_one_platform(brand, domain, "Instagram")
        out["instagram"] = _normalize_follower_display(ig)
        if not _is_empty_or_na(out["instagram"]):
            break
        if attempt < 2:
            time.sleep(RETRY_NA_DELAY_SEC)
    time.sleep(SEARCH_DELAY_SEC)

    for attempt in range(3):
        _, fb = _search_one_platform(brand, domain, "Facebook")
        out["facebook"] = _normalize_follower_display(fb)
        if not _is_empty_or_na(out["facebook"]):
            break
        if attempt < 2:
            time.sleep(RETRY_NA_DELAY_SEC)
    time.sleep(SEARCH_DELAY_SEC)

    for attempt in range(3):
        _, yt = _search_one_platform(brand, domain, "YouTube")
        out["youtube"] = _normalize_follower_display(yt)
        if not _is_empty_or_na(out["youtube"]):
            break
        if attempt < 2:
            time.sleep(RETRY_NA_DELAY_SEC)

    return out


def _main() -> None:
    domain = None
    for arg in sys.argv[1:]:
        if arg.startswith("--domain="):
            domain = arg.split("=", 1)[1].strip()
            break
        if not arg.startswith("-"):
            domain = arg.strip()
            break
    if not domain:
        domain = os.getenv("REFRESH_SOCIAL_DOMAIN", "").strip()
    if not domain:
        print("Usage: python refresh_social_followers.py <domain_or_company>")
        print("   or: python refresh_social_followers.py --domain=example.com")
        print("   or: REFRESH_SOCIAL_DOMAIN=example.com python refresh_social_followers.py")
        sys.exit(1)
    print(f"Fetching YT / FB / IG followers for: {domain}")
    result = get_social_followers(domain)
    print("youtube:", result["youtube"])
    print("facebook:", result["facebook"])
    print("instagram:", result["instagram"])


if __name__ == "__main__":
    _main()
