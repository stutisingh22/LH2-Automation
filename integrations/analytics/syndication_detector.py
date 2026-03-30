from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MAX_SEARCH_RESULTS = 6
SEARCH_BACKENDS_DEFAULT = ("duckduckgo", "yahoo", "google")


class _TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = max(0.05, float(rate))
        self.burst = max(1, int(burst))
        self.tokens = float(self.burst)
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last
                self.last = now
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                missing = 1.0 - self.tokens
                wait_s = max(0.01, missing / self.rate)
            time.sleep(wait_s)


_SCRAPE_BUCKET = _TokenBucket(rate=0.6, burst=2)
_DDGS_BUCKET = _TokenBucket(rate=0.7, burst=2)


def normalize_domain(url_or_domain: str) -> str:
    s = (url_or_domain or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    return s.strip(".")


def _domain_root(domain: str) -> str:
    base = normalize_domain(domain)
    return (base.split(".")[0] if "." in base else base).strip()


def _tokens_from_text(s: str) -> list[str]:
    txt = re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return [x for x in txt.split() if len(x) >= 2]


def _publisher_tokens(domain: str, publisher_name: str | None = None) -> list[str]:
    toks = _tokens_from_text(_domain_root(domain))
    if publisher_name:
        toks.extend(_tokens_from_text(publisher_name))
    # stable unique
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _name_match_strict(provider_title: str, tokens: list[str]) -> bool:
    t = set(_tokens_from_text(provider_title))
    if not t or not tokens:
        return False
    overlap = [x for x in tokens if x in t]
    if len(overlap) >= 2:
        return True
    root = tokens[0]
    # small tolerance: leading "the", acronym-ish merge.
    compact_title = "".join(_tokens_from_text(provider_title))
    if root and (root == compact_title or compact_title.startswith("the" + root)):
        return True
    return False


def _safe_get(url: str, timeout: int = 15) -> requests.Response | None:
    _SCRAPE_BUCKET.acquire()
    try:
        return requests.get(url, timeout=timeout, headers={"user-agent": UA})
    except Exception:
        return None


def _ddgs_search(query: str, max_results: int = MAX_SEARCH_RESULTS) -> list[dict[str, str]]:
    try:
        from ddgs import DDGS
    except Exception:
        return []

    out: list[dict[str, str]] = []
    for backend in SEARCH_BACKENDS_DEFAULT:
        try:
            _DDGS_BUCKET.acquire()
            with DDGS(timeout=20) as ddgs:
                rows = list(
                    ddgs.text(
                        query,
                        max_results=max_results,
                        region="us-en",
                        safesearch="off",
                        backend=backend,
                    )
                )
            if rows:
                out.extend(
                    {
                        "title": (r.get("title") or "").strip(),
                        "body": (r.get("body") or "").strip(),
                        "href": (r.get("href") or "").strip(),
                        "source": f"ddgs:{backend}",
                    }
                    for r in rows[:max_results]
                    if isinstance(r, dict)
                )
                break
        except Exception:
            continue
    return out


def _bing_html_search(query: str, max_results: int = MAX_SEARCH_RESULTS) -> list[dict[str, str]]:
    _SCRAPE_BUCKET.acquire()
    try:
        r = requests.get(
            f"https://www.bing.com/search?q={quote_plus(query)}",
            timeout=15,
            headers={"user-agent": UA},
        )
    except Exception:
        return []
    if not r or r.status_code != 200:
        return []
    html = r.text
    links = re.findall(r'<a href="(https?://[^"]+)"', html, flags=re.IGNORECASE)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for href in links:
        h = href.strip()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append({"title": "", "body": "", "href": h, "source": "bing_html"})
        if len(out) >= max_results:
            break
    return out


def _search_with_fallbacks(queries: list[str], max_results: int = MAX_SEARCH_RESULTS) -> list[dict[str, str]]:
    all_rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for q in queries:
        for row in (_ddgs_search(q, max_results=max_results) + _bing_html_search(q, max_results=max_results)):
            href = (row.get("href") or "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            all_rows.append(row)
    return all_rows


def _msn_provider_api_query(publisher_query: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://cdn.query.prod.cms.msn.com/cms/api/amp/search?q={quote_plus(publisher_query)}&$top=15"
    r = _safe_get(url, timeout=20)
    if not r:
        return [], "request_failed"
    if r.status_code != 200:
        return [], f"http_{r.status_code}"
    try:
        data = r.json()
    except Exception:
        return [], "invalid_json"

    providers: list[dict[str, Any]] = []
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if str(cur.get("_type") or "").lower() == "provider":
                providers.append(cur)
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return providers, None


def _extract_msn_source_slug(url: str) -> str:
    try:
        p = urlparse(url)
        m = re.search(r"/channel/source/([^/?#]+)", p.path, flags=re.IGNORECASE)
        return (m.group(1) if m else "").strip().lower()
    except Exception:
        return ""


def _slug_matches_tokens(slug: str, tokens: list[str]) -> bool:
    if not slug or not tokens:
        return False
    s = re.sub(r"[^a-z0-9]+", "", slug.lower())
    if not s:
        return False
    root = re.sub(r"[^a-z0-9]+", "", tokens[0].lower())
    if not root:
        return False
    if s == root or s.startswith(root) or root.startswith(s):
        return True
    if s.startswith("the" + root):
        return True
    # Acronym-ish tolerance: "nypost" vs "newyorkpost"
    compact_tokens = "".join(re.sub(r"[^a-z0-9]+", "", t) for t in tokens)
    if compact_tokens and (s in compact_tokens or compact_tokens in s):
        return True
    return False


def _generic_page_looks_weak(html: str) -> bool:
    x = (html or "").lower()
    weak = ("search results", "/search?", "rss", "feed", "no results", "subscribe to feeds")
    return any(w in x for w in weak)


def _parse_followers(text: str) -> int | None:
    if not text:
        return None
    pats = [
        r'([\d,.]+)\s*([kmb]?)\s*followers',
        r'"followers"\s*:\s*"([^"]+)"',
    ]
    for pat in pats:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if not m:
            continue
        if pat.startswith('"followers"'):
            raw = (m.group(1) or "").strip()
            m2 = re.search(r"([\d,.]+)\s*([kmb]?)", raw, flags=re.IGNORECASE)
            if not m2:
                continue
            num = m2.group(1)
            suf = m2.group(2)
        else:
            num = m.group(1)
            suf = m.group(2)
        try:
            val = float((num or "0").replace(",", ""))
        except Exception:
            continue
        mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
        return int(val * mult.get((suf or "").lower(), 1))
    return None


@dataclass
class PlatformDecision:
    present: bool
    url: str | None
    method: str
    reason: str
    evidence: list[str]
    followers: int | None = None
    error: str | None = None


def _detect_msn(domain: str, publisher_name: str | None = None) -> PlatformDecision:
    tokens = _publisher_tokens(domain, publisher_name)
    pub_q = " ".join(tokens) or _domain_root(domain)

    providers, api_err = _msn_provider_api_query(pub_q)
    matched = []
    for p in providers:
        title = str(p.get("title") or p.get("name") or "")
        if not _name_match_strict(title, tokens):
            continue
        cand_url = str(
            p.get("url")
            or p.get("canonicalUrl")
            or p.get("link")
            or p.get("webUrl")
            or ""
        ).strip()
        if cand_url:
            matched.append((title, cand_url))
    if len(matched) == 1:
        url = matched[0][1]
        followers = None
        r = _safe_get(url)
        if r and r.status_code == 200:
            followers = _parse_followers(r.text)
        return PlatformDecision(
            present=True,
            url=url,
            method="msn_amp_api_provider",
            reason="single_strict_provider_match",
            evidence=[url],
            followers=followers,
        )
    if len(matched) > 1:
        return PlatformDecision(
            present=False,
            url=None,
            method="msn_amp_api_provider",
            reason="ambiguous_provider_matches",
            evidence=[u for _, u in matched],
        )

    # Step B fallback search
    queries = [f"{_domain_root(domain)} msn", f"{normalize_domain(domain)} msn"]
    rows = _search_with_fallbacks(queries)
    candidates: list[str] = []
    for r in rows:
        href = (r.get("href") or "").strip()
        if "/channel/source/" not in href.lower():
            continue
        rr = _safe_get(href)
        if not rr or rr.status_code != 200:
            continue
        if _generic_page_looks_weak(rr.text):
            continue
        slug = _extract_msn_source_slug(href)
        if not _slug_matches_tokens(slug, tokens):
            continue
        candidates.append(href)
    dedup = list(dict.fromkeys(candidates))
    if len(dedup) == 1:
        url = dedup[0]
        rr = _safe_get(url)
        followers = _parse_followers(rr.text) if rr and rr.status_code == 200 else None
        return PlatformDecision(
            present=True,
            url=url,
            method="msn_search_slug_validated",
            reason="single_unambiguous_source_slug",
            evidence=dedup,
            followers=followers,
        )
    if len(dedup) > 1:
        return PlatformDecision(
            present=False,
            url=None,
            method="msn_search_slug_validated",
            reason="ambiguous_source_slugs",
            evidence=dedup,
        )
    return PlatformDecision(
        present=False,
        url=None,
        method="msn_search_slug_validated",
        reason=f"no_valid_source_slug (api_err={api_err or 'none'})",
        evidence=[],
    )


def _domain_name_consistent(href: str, title: str, body: str, domain: str) -> bool:
    root = _domain_root(domain)
    txt = f"{href} {title} {body}".lower()
    return bool(root and root in re.sub(r"[^a-z0-9]+", "", txt))


def _detect_search_profile_platform(
    domain: str,
    platform: str,
    host_token: str,
    profile_path_rules: tuple[str, ...],
    reject_path_rules: tuple[str, ...],
) -> PlatformDecision:
    queries = [f"{_domain_root(domain)} {platform}", f"{normalize_domain(domain)} {platform}"]
    rows = _search_with_fallbacks(queries)
    candidates: list[str] = []
    for r in rows[: max(4, MAX_SEARCH_RESULTS)]:
        href = (r.get("href") or "").strip()
        if host_token not in href.lower():
            continue
        p = urlparse(href).path.lower()
        if reject_path_rules and any(x in p for x in reject_path_rules):
            continue
        if profile_path_rules and not any(x in p for x in profile_path_rules):
            continue
        if not _domain_name_consistent(href, r.get("title", ""), r.get("body", ""), domain):
            continue
        candidates.append(href)
    dedup = list(dict.fromkeys(candidates))
    if len(dedup) == 1:
        return PlatformDecision(
            present=True,
            url=dedup[0],
            method=f"{platform}_search_profile_validated",
            reason="single_validated_profile_url",
            evidence=dedup,
        )
    if len(dedup) > 1:
        return PlatformDecision(
            present=False,
            url=None,
            method=f"{platform}_search_profile_validated",
            reason="ambiguous_multiple_profile_urls",
            evidence=dedup,
        )
    return PlatformDecision(
        present=False,
        url=None,
        method=f"{platform}_search_profile_validated",
        reason="no_validated_profile_url",
        evidence=[],
    )


def detect_syndication_for_domain(domain: str, publisher_name: str | None = None) -> dict[str, Any]:
    """
    Conservative syndication detection with evidence/method logging.
    Returns:
      {
        "platform_presence": {...},
        "platform_presence_details": {
           "msn|yahoo|newsbreak|smartnews": {
             present, url, method, reason, evidence_urls, followers, error
           }
        }
      }
    """
    dom = normalize_domain(domain)
    if not dom:
        return {
            "platform_presence": {"msn": False, "yahoo": False, "newsbreak": False, "smartnews": False},
            "platform_presence_details": {},
        }

    details: dict[str, dict[str, Any]] = {}

    # MSN (strictest)
    try:
        msn = _detect_msn(dom, publisher_name=publisher_name)
        details["msn"] = {
            "present": bool(msn.present),
            "url": msn.url,
            "method": msn.method,
            "reason": msn.reason,
            "evidence_urls": msn.evidence[:6],
            "followers": msn.followers,
            "error": msn.error,
        }
    except Exception as e:
        details["msn"] = {
            "present": False,
            "url": None,
            "method": "msn_error",
            "reason": "platform_error",
            "evidence_urls": [],
            "followers": None,
            "error": str(e),
        }

    # Yahoo
    try:
        yahoo = _detect_search_profile_platform(
            dom,
            platform="yahoo",
            host_token="yahoo.com",
            profile_path_rules=("/author/", "/publisher/", "/news/topics/"),
            reject_path_rules=("/search", "/video/", "/finance/quote", "/news/live/"),
        )
        details["yahoo"] = {
            "present": bool(yahoo.present),
            "url": yahoo.url,
            "method": yahoo.method,
            "reason": yahoo.reason,
            "evidence_urls": yahoo.evidence[:6],
            "error": yahoo.error,
        }
    except Exception as e:
        details["yahoo"] = {
            "present": False,
            "url": None,
            "method": "yahoo_error",
            "reason": "platform_error",
            "evidence_urls": [],
            "error": str(e),
        }

    # NewsBreak
    try:
        newsbreak = _detect_search_profile_platform(
            dom,
            platform="newsbreak",
            host_token="newsbreak.com",
            profile_path_rules=("/publisher/", "/channel/", "/u/", "/@"),
            reject_path_rules=("/news/", "/article/", "/video/", "/topic/"),
        )
        details["newsbreak"] = {
            "present": bool(newsbreak.present),
            "url": newsbreak.url,
            "method": newsbreak.method,
            "reason": newsbreak.reason,
            "evidence_urls": newsbreak.evidence[:6],
            "error": newsbreak.error,
        }
    except Exception as e:
        details["newsbreak"] = {
            "present": False,
            "url": None,
            "method": "newsbreak_error",
            "reason": "platform_error",
            "evidence_urls": [],
            "error": str(e),
        }

    # SmartNews
    try:
        smartnews = _detect_search_profile_platform(
            dom,
            platform="smartnews",
            host_token="smartnews.com",
            profile_path_rules=("/publisher/", "/en/", "/source/"),
            reject_path_rules=("/article/", "/video/", "/search"),
        )
        details["smartnews"] = {
            "present": bool(smartnews.present),
            "url": smartnews.url,
            "method": smartnews.method,
            "reason": smartnews.reason,
            "evidence_urls": smartnews.evidence[:6],
            "error": smartnews.error,
        }
    except Exception as e:
        details["smartnews"] = {
            "present": False,
            "url": None,
            "method": "smartnews_error",
            "reason": "platform_error",
            "evidence_urls": [],
            "error": str(e),
        }

    presence = {k: bool((details.get(k) or {}).get("present")) for k in ("msn", "yahoo", "newsbreak", "smartnews")}
    return {
        "platform_presence": presence,
        "platform_presence_details": details,
    }

