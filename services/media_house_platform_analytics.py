"""
Media house (advisor) platform analytics refresh.

Uses the same logic as independent companies (run_social_from_google_search + sync_social_from_search_to_db):
- Tracker CSV for Yahoo, MSN, MSN Followers, Newsbreak, Smartnews (presence Yes/No and MSN follower count).
- Search + extract for Instagram, Facebook, YT (same _search_one_platform flow).

Only difference: runs on click (Refresh platform analytics) for one domain instead of in bulk.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Same as independent script
PLATFORMS = ("msn", "yahoo", "newsbreak", "smartnews")


def normalize_media_house_domain(domain: str) -> str:
    """
    Normalize domain for tracker lookup. Same idea as run_social_from_google_search._normalize_domain
    and tracker_sources.normalize_domain: strip protocol, www, path, query, port.
    """
    if not domain or not isinstance(domain, str):
        return ""
    s = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    if s.startswith("www."):
        s = s[4:].strip()
    s = s.split("/")[0].split("?")[0].strip().rstrip(".")
    if ":" in s:
        host, port = s.rsplit(":", 1)
        if port.isdigit():
            s = host
    return s or ""


def _candidate_domains_for_tracker(domain: str, norm: str) -> list[str]:
    """Domain keys to try for tracker CSV lookup (same normalization as independent)."""
    from integrations.analytics.tracker_sources import normalize_domain

    candidates = []
    seen = set()
    for raw in (norm, domain or ""):
        if not raw or raw in seen:
            continue
        seen.add(raw)
        n = normalize_domain(raw) if raw else None
        if n and n not in seen:
            candidates.append(n)
            seen.add(n)
    if norm and norm not in seen:
        candidates.append(norm)
    return candidates


def _format_follower(n: int | None) -> str:
    """Same as run_social_from_google_search._format_follower — independent uses 'NA' for none."""
    if n is None or n <= 0:
        return "NA"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"~{n / 1_000:.1f}K".replace(".0", "")
    return str(n)


def build_media_house_refresh_payload(
    domain: str,
    fresh_analytics: dict[str, Any],
) -> dict[str, Any]:
    """
    Build platform_presence and social_from_search for one media house using the same logic
    as independent: tracker for all presence + MSN followers, search for IG/FB/YT.
    Returns same shape as sync_social_from_search_to_db writes (platform_presence, social_from_search).
    """
    from integrations.analytics.tracker_sources import (
        normalize_domain,
        load_domain_to_platforms,
        load_domain_to_msn_follower_count,
    )
    from integrations.analytics.social_follower_counts import get_social_counts_for_domain

    norm = normalize_media_house_domain(domain or "")
    if not norm and domain:
        _nd = normalize_domain(domain or "")
        norm = (_nd or "") if _nd else ""

    tracker_candidates = _candidate_domains_for_tracker(domain or "", norm)

    # Same as independent: load tracker once, lookup by normalized domain
    domain_to_platforms = load_domain_to_platforms()
    domain_to_msn_followers = load_domain_to_msn_follower_count()

    # Platform presence: only from tracker (same as independent init) — try each candidate
    platform_presence = {"msn": False, "yahoo": False, "newsbreak": False, "smartnews": False}
    for cand in tracker_candidates:
        platforms_set = domain_to_platforms.get(cand, set())
        if platforms_set:
            for p in PLATFORMS:
                if p in platforms_set:
                    platform_presence[p] = True
            break

    # MSN followers: same as independent (_format_follower; "NA" when none)
    msn_followers_str = "NA"
    for cand in tracker_candidates:
        n = domain_to_msn_followers.get(cand)
        if n is not None and n > 0:
            msn_followers_str = _format_follower(n)
            break

    # Instagram, Facebook, YT: same as independent (_process_one_row → _search_one_platform + _normalize_follower_display)
    social_counts = get_social_counts_for_domain(norm or domain or "")
    updated_at = (fresh_analytics.get("updated_at") or "").strip() or datetime.utcnow().isoformat()

    def _cell(val: str) -> str:
        """Same as sync_social_from_search_to_db: non-empty or 'NA'."""
        v = (val or "").strip()
        return v if v and v != "—" else "NA"

    social_from_search = {
        "msn_followers": msn_followers_str,
        "instagram": _cell(social_counts.get("instagram") or ""),
        "facebook": _cell(social_counts.get("facebook") or ""),
        "yt": _cell(social_counts.get("yt") or ""),
        "updated_at": updated_at,
    }

    return {
        "platform_presence": platform_presence,
        "social_from_search": social_from_search,
    }
