"""
Social follower counts using the same logic as run_social_from_google_search:
search + extract from snippet, optional fetch from page. No CSV reads.
Used by the app when refreshing platform analytics for media houses (advisor).
"""

from __future__ import annotations

import re
import time
import urllib.request
from typing import Any

from .tracker_sources import normalize_domain, load_domain_to_msn_follower_count

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
SEARCH_DELAY_SEC = 0.5
FETCH_DELAY_SEC = 0.4
SEARCH_RETRY_DELAY_SEC = 2.0
_MAX_SANE_FOLLOWERS = 500_000_000

_IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+/?")
_FB_RE = re.compile(r"https?://(?:www\.)?(?:fb\.com|facebook\.com)/[a-zA-Z0-9_.-]+/?")
_YT_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:(?:c/|channel/|@|user/)[a-zA-Z0-9_-]+|(?!watch|playlist|embed|results)[a-zA-Z0-9_-]+)(?:/|$)?|youtu\.be/[a-zA-Z0-9_-]+)"
)


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


def _extract_url_and_count_from_results(results: list[dict], platform: str) -> tuple[str | None, str]:
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
        for text in (href, body, title):
            m = pattern.search(text)
            if m:
                u = m.group(0).rstrip("/")
                if not u.startswith("http"):
                    u = "https://" + u
                if not url_found:
                    url_found = u
                if platform.lower() == "facebook":
                    for t in (f"{title} {body}".strip(), body, title):
                        c = _extract_fb_likes_from_snippet(t)
                        if c != "NA":
                            count_str = c
                            break
                if count_str == "NA":
                    for t in (body, title):
                        c = _extract_count_from_snippet(t)
                        if c != "NA":
                            count_str = c
                            break
                return url_found, count_str
    return url_found, count_str


def _fetch_count_from_page(url: str | None, platform: str) -> str:
    if not url or not (url or "").strip().startswith("http"):
        return "NA"
    try:
        req = urllib.request.Request((url or "").strip(), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
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


def _search_one_platform(brand: str, domain: str, platform: str, search_query: str | None = None) -> tuple[str | None, str]:
    """Same logic as run_social_from_google_search: search, extract from snippet, optionally fetch page. Returns (url, count_str)."""
    from integrations.search import text_search

    q = (search_query or "").strip() if search_query else f"{(brand or '').strip()} {platform}"
    if not q:
        return None, "NA"
    results = None
    for attempt in range(2):
        try:
            results = text_search(q, max_results=8, backend_prefer="bing")
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
            results2 = text_search(q, max_results=8, backend_prefer="bing")
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


def get_msn_follower_display(domain: str) -> str:
    """MSN follower count from tracker (same logic as standalone). Returns formatted string (e.g. ~1.3K) or '—' if none.
    Media house names may be stored as full URLs; we strip https/http and www so lookup matches tracker_sources.csv."""
    raw = (domain or "").strip()
    if not raw:
        return "—"
    # Strip protocol and www so media house URLs match CSV Website column (e.g. https://www.1105media.com -> 1105media.com)
    s = raw.lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    if s.startswith("www."):
        s = s[4:].strip()
    s = s.split("/")[0].split("?")[0].strip().rstrip(".")
    dom = normalize_domain(s) if s else None
    if not dom:
        return "—"
    m = load_domain_to_msn_follower_count()
    n = m.get(dom)
    if n is None or n <= 0:
        return "—"
    return _format_follower(n)


def get_social_counts_for_domain(domain: str) -> dict[str, str]:
    """
    Fetch Instagram, Facebook, YT follower counts using the same search+extract+fetch logic
    as run_social_from_google_search. No CSV. Returns {"instagram": "...", "facebook": "...", "yt": "..."}.
    """
    dom = normalize_domain(domain or "")
    brand = dom.split(".")[0] if dom else ""
    out: dict[str, str] = {"instagram": "—", "facebook": "—", "yt": "—"}

    def _display_val(raw: str) -> str:
        v = _normalize_follower_display(raw) if not _is_empty_or_na(raw) else ""
        return v if v and v != "NA" else "—"

    _, ig = _search_one_platform(brand, dom, "Instagram")
    out["instagram"] = _display_val(ig)
    time.sleep(SEARCH_DELAY_SEC)

    _, fb = _search_one_platform(brand, dom, "Facebook", search_query=f"{dom} facebook followers")
    out["facebook"] = _display_val(fb)
    time.sleep(SEARCH_DELAY_SEC)

    _, yt = _search_one_platform(brand, dom, "YouTube", search_query=f"{dom} youtube")
    out["yt"] = _display_val(yt)

    return out
