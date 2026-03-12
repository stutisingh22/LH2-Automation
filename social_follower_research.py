"""
Social follower research: sitemap for articles/day; flexible mix of URL fetch + AI.
Try: find profile URLs → fetch HTML → parse counts. If any platform still NA, use
Claude + web_search with flexible queries to extract counts from search results.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 10) -> str | None:
    """Fetch a URL and return text content, or None on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _extract_meta(html: str, property_or_name: str) -> str | None:
    """Extract og: or meta name content from HTML."""
    pattern = rf'<meta[^>]+(?:property|name)=["\']?{re.escape(property_or_name)}["\']?[^>]+content=["\']([^"\']+)["\']'
    m = re.search(pattern, html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    pattern2 = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']?{re.escape(property_or_name)}["\']?'
    m2 = re.search(pattern2, html, re.IGNORECASE)
    return m2.group(1).strip() if m2 else None


def _parse_follower_string(text: str | None) -> str:
    """Turn '857K Followers...' or '1,200,000 followers' → '857K' / '1.2M'."""
    if not text:
        return "NA"
    m = re.search(r"([\d,\.]+\s*[KkMmBb]?)\s*[Ff]ollower", text)
    if not m:
        m = re.search(r"([\d,\.]+\s*[KkMmBb])", text)
    if not m:
        return "NA"
    raw = m.group(1).replace(",", "").replace(" ", "")
    return raw.upper()


# ── articles/day via sitemap ────────────────────────────────────────────────────

def _articles_per_day_from_sitemap(domain: str) -> str:
    """
    Fetch sitemap, count articles published in last 30 days, divide by 30.
    Returns e.g. '3.2' or 'NA'.
    """
    # Normalize: strip protocol and path
    domain = (domain or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0]
    if domain.startswith("www."):
        domain = domain[4:]

    sitemap_candidates = [
        f"https://{domain}/sitemap.xml",
        f"https://{domain}/sitemap_index.xml",
        f"https://www.{domain}/sitemap.xml",
        f"https://www.{domain}/sitemap_index.xml",
        f"https://{domain}/news-sitemap.xml",
    ]

    now = datetime.now(tz=timezone.utc)
    total_recent = 0
    found_any = False

    for sitemap_url in sitemap_candidates:
        xml_text = _fetch_url(sitemap_url, timeout=12)
        if not xml_text:
            continue
        found_any = True

        # If sitemap index, drill into sub-sitemaps (first 3 only)
        sub_urls = re.findall(r"<loc>\s*(https?://[^\s<]+sitemap[^\s<]*)\s*</loc>", xml_text)
        sources = [xml_text]
        for sub in sub_urls[:3]:
            sub_text = _fetch_url(sub, timeout=10)
            if sub_text:
                sources.append(sub_text)
            time.sleep(0.2)

        for src in sources:
            dates = re.findall(
                r"<lastmod>\s*(\d{4}-\d{2}-\d{2}[T\s][^<]*|\d{4}-\d{2}-\d{2})\s*</lastmod>",
                src,
            )
            for d in dates:
                try:
                    parsed = datetime.fromisoformat(d.strip().replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    if (now - parsed).days <= 30:
                        total_recent += 1
                except Exception:
                    continue
        break  # stop after first working sitemap

    if not found_any or total_recent == 0:
        return "NA"

    per_day = round(total_recent / 30, 1)
    return str(per_day) if per_day > 0 else "NA"


# ── find social profile URLs via Claude + web_search ──────────────────────────

def _find_social_urls(client, domain: str, brand: str) -> dict:
    """
    Use Claude + web_search to find Instagram, Facebook, YouTube profile URLs.
    Flexible: try multiple query phrasings (brand name, domain, site:).
    """
    prompt = f"""Find the official social media profile URLs for the publisher "{brand}" (website: {domain}).

Use web_search with flexible queries — try different phrasings for each platform:
- Instagram: "{brand} instagram", "{brand} instagram.com", "site:instagram.com {brand}", "{domain} Instagram"
- Facebook: "{brand} facebook", "{brand} facebook.com", "site:facebook.com {brand}", "{domain} Facebook"
- YouTube: "{brand} youtube channel", "{brand} youtube.com", "site:youtube.com {brand}", "{domain} YouTube"

Return the official/brand account URLs when you find them (null if not found). Return ONLY a JSON object, no markdown:
{{"instagram_url": "<full URL or null>", "facebook_url": "<full URL or null>", "youtube_url": "<full URL or null>"}}"""

    try:
        from anthropic_limiter import stream_slot
    except Exception:
        from contextlib import contextmanager
        @contextmanager
        def _noop():
            yield
        stream_slot = _noop

    with stream_slot():
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

    result_text = ""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            result_text = (getattr(block, "text", "") or "").strip()
        elif isinstance(block, dict) and block.get("type") == "text":
            result_text = (block.get("text") or "").strip()

    clean = re.sub(r"```(?:json)?|```", "", result_text).strip()
    try:
        return json.loads(clean)
    except Exception:
        return {"instagram_url": None, "facebook_url": None, "youtube_url": None}


# ── extract follower counts from fetched HTML ──────────────────────────────────

def _get_instagram_followers(url: str | None) -> str:
    if not url:
        return "NA"
    html = _fetch_url(url)
    if not html:
        return "NA"
    # Instagram puts follower count in og:description: "857K Followers, 260 Following..."
    desc = _extract_meta(html, "og:description")
    return _parse_follower_string(desc)


def _get_facebook_followers(url: str | None) -> str:
    if not url:
        return "NA"
    html = _fetch_url(url)
    if not html:
        return "NA"
    desc = _extract_meta(html, "og:description")
    count = _parse_follower_string(desc)
    if count != "NA":
        return count
    m = re.search(r"([\d,\.]+[KkMm]?)\s*[Ff]ollowers", html)
    if m:
        return m.group(1).replace(",", "")
    return "NA"


def _get_youtube_subscribers(url: str | None) -> str:
    if not url:
        return "NA"
    html = _fetch_url(url)
    if not html:
        return "NA"
    # YouTube embeds subscriber count in JSON blob
    m = re.search(r'"subscriberCountText"[^}]*?"simpleText":"([^"]+)"', html)
    if m:
        return m.group(1).strip()
    # fallback: meta description
    desc = _extract_meta(html, "og:description")
    return _parse_follower_string(desc)


# ── flexible AI fallback: extract counts from search when fetch returns NA ───────

def _ai_extract_follower_counts(client, domain: str, brand: str) -> dict:
    """
    Claude + web_search with flexible queries to get IG/FB/YT counts from search results.
    Use when URL fetch fails (login walls, etc.). Returns dict with instagram, facebook, youtube.
    """
    prompt = f"""You are helping get social media follower/subscriber counts for the publisher "{brand}" (website: {domain}).

Use the web_search tool with flexible, varied queries — don't rely on one exact phrase. Try for each platform:
- Instagram: "{brand} Instagram followers", "{brand} Instagram", "site:instagram.com {brand}", "{domain} Instagram followers"
- Facebook: "{brand} Facebook page likes", "{brand} Facebook followers", "site:facebook.com {brand}", "{domain} Facebook"
- YouTube: "{brand} YouTube subscribers", "{brand} YouTube channel", "site:youtube.com {brand}", "{domain} YouTube subscribers"

Extract the follower/subscriber/likes count from whatever you find in search results. Use short form: 94K, 1.5M, 3.4K. If you cannot find a number for a platform, use NA.
Return ONLY a JSON object, no other text or markdown:
{{"instagram": "<value or NA>", "facebook": "<value or NA>", "youtube": "<value or NA>"}}"""

    try:
        from anthropic_limiter import stream_slot
    except Exception:
        from contextlib import contextmanager
        @contextmanager
        def _noop():
            yield
        stream_slot = _noop

    with stream_slot():
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

    result_text = ""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            result_text = (getattr(block, "text", "") or "").strip()
        elif isinstance(block, dict) and block.get("type") == "text":
            result_text = (block.get("text") or "").strip()

    clean = re.sub(r"```(?:json)?\s*|```", "", result_text).strip()
    try:
        out = json.loads(clean)
        return {
            "instagram": str(out.get("instagram") or "NA").strip() or "NA",
            "facebook": str(out.get("facebook") or "NA").strip() or "NA",
            "youtube": str(out.get("youtube") or "NA").strip() or "NA",
        }
    except Exception:
        return {"instagram": "NA", "facebook": "NA", "youtube": "NA"}


# ── main entry point ───────────────────────────────────────────────────────────

def run_social_follower_research(domain: str, brand: str) -> dict:
    """
    Multi-step research:
      Step 1 — Fetch sitemap directly → articles/day (no AI)
      Step 2 — Claude + web_search → find exact profile URLs
      Step 3 — Fetch each profile page directly → extract counts from HTML

    Returns dict: instagram, facebook, youtube, articles_per_day
    All values are strings. Returns "NA" for anything not found with confidence.
    """
    result = {
        "instagram": "NA",
        "facebook": "NA",
        "youtube": "NA",
        "articles_per_day": "NA",
    }

    # Step 1: sitemap crawl — no AI involved
    print(f"    [sitemap] {domain}")
    result["articles_per_day"] = _articles_per_day_from_sitemap(domain)
    print(f"    [sitemap] → {result['articles_per_day']}")

    # Step 2: find profile URLs via Claude + web_search (skip if no API key)
    try:
        from config import ANTHROPIC_API_KEY
    except Exception:
        ANTHROPIC_API_KEY = None

    if not ANTHROPIC_API_KEY:
        print("    [url-search] skipped (no ANTHROPIC_API_KEY)")
        # Matrix expects "—" for missing articles_per_day
        if result["articles_per_day"] == "NA":
            result["articles_per_day"] = "—"
        return result

    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    print(f"    [url-search] {brand}")
    urls = _find_social_urls(client, domain, brand)
    print(f"    [url-search] → {urls}")

    time.sleep(0.5)

    # Step 3: fetch each page directly and parse HTML
    print(f"    [fetch-ig] {urls.get('instagram_url')}")
    result["instagram"] = _get_instagram_followers(urls.get("instagram_url"))

    print(f"    [fetch-fb] {urls.get('facebook_url')}")
    result["facebook"] = _get_facebook_followers(urls.get("facebook_url"))

    print(f"    [fetch-yt] {urls.get('youtube_url')}")
    result["youtube"] = _get_youtube_subscribers(urls.get("youtube_url"))

    # Flexible AI fallback: if any platform still NA, use Claude + web_search to extract from search results
    if result["instagram"] == "NA" or result["facebook"] == "NA" or result["youtube"] == "NA":
        print(f"    [ai-fallback] filling NA via flexible search for {brand}")
        fallback = _ai_extract_follower_counts(client, domain, brand)
        for key in ("instagram", "facebook", "youtube"):
            if result.get(key) == "NA" and fallback.get(key) and str(fallback.get(key)).strip().upper() != "NA":
                result[key] = str(fallback[key]).strip()

    # Matrix uses "—" for missing articles_per_day
    if result["articles_per_day"] == "NA":
        result["articles_per_day"] = "—"

    print(f"    [result] {domain} → {result}")
    return result
