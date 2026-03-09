"""
Deterministic outreach strategy extraction from research context.

This module does not call external APIs. It converts existing research text
into a compact strategy profile that the LLM can follow while keeping voice
and email structure rules unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _iter_note_texts(research_notes: Any) -> Iterable[str]:
    if not isinstance(research_notes, list):
        return []
    out = []
    for note in research_notes:
        if isinstance(note, dict):
            txt = (note.get("content") or "").strip()
            if txt:
                out.append(txt)
    return out


def _recipient_research_text(recipient_research: Any) -> str:
    if not isinstance(recipient_research, dict):
        return ""
    parts = []
    brief = (recipient_research.get("brief") or "").strip()
    if brief:
        parts.append(brief)
    linkedin = recipient_research.get("linkedin") or {}
    for p in (linkedin.get("recent_posts") or [])[:5]:
        snip = (p.get("snippet") or "").strip()
        if snip:
            parts.append(snip)
    twitter = recipient_research.get("twitter") or {}
    for t in (twitter.get("recent_tweets") or [])[:5]:
        snip = (t.get("snippet") or "").strip()
        if snip:
            parts.append(snip)
    return "\n".join(parts)


def _contains_any(text: str, words: Iterable[str]) -> bool:
    return any(w in text for w in words)


def _first_hits(text: str, words: Iterable[str], limit: int = 3) -> list[str]:
    hits = []
    for w in words:
        if w in text and w not in hits:
            hits.append(w)
        if len(hits) >= limit:
            break
    return hits


def build_strategy_profile(
    comp: dict,
    ct: dict,
    research_notes: Any = None,
    recipient_research: Any = None,
    research_context: str = "",
    drive_context: str = "",
) -> Dict[str, Any]:
    monthly_visits = _safe_int((comp or {}).get("monthly_visits"))
    publisher_type = ((comp or {}).get("publisher_type") or "").strip()
    content_type = ((comp or {}).get("content_type") or "").strip()
    contact_role = ((ct or {}).get("role") or "").strip()

    text_parts = [
        (research_context or ""),
        (drive_context or ""),
        "\n".join(_iter_note_texts(research_notes)),
        _recipient_research_text(recipient_research),
        publisher_type,
        content_type,
    ]
    corpus = "\n".join([p for p in text_parts if p]).lower()

    syndication_terms = ("msn", "newsbreak", "yahoo", "syndication", "syndicated")
    seo_terms = ("seo", "discover", "organic", "search traffic")
    ops_terms = ("small team", "lean team", "editorial team", "newsroom", "workflow")
    monetization_terms = ("programmatic", "ad revenue", "rpm", "affiliate", "commerce")
    growth_terms = ("growing", "growth", "up", "increase", "momentum")

    has_syndication = _contains_any(corpus, syndication_terms)
    has_seo = _contains_any(corpus, seo_terms)
    has_ops = _contains_any(corpus, ops_terms)
    has_monetization = _contains_any(corpus, monetization_terms)

    archetype = "hybrid publisher"
    if _contains_any(corpus, ("entertainment", "sports", "politics", "celebrity", "breaking news")):
        archetype = "broad-reach news/entertainment publisher"
    elif _contains_any(corpus, ("b2b", "niche", "vertical", "saas", "technology", "health", "finance")):
        archetype = "niche/vertical publisher"

    if monthly_visits >= 15_000_000:
        scale_band = "enterprise-scale"
    elif monthly_visits >= 1_000_000:
        scale_band = "mid-large scale"
    elif monthly_visits > 0:
        scale_band = "emerging scale"
    else:
        scale_band = "unknown scale"

    route = "accelerate"
    route_reason = "default route for most targets."
    if monthly_visits and monthly_visits < 200_000 and has_syndication:
        route = "acquisition_candidate"
        route_reason = "low traffic plus syndication footprint can indicate micro-publisher acquisition fit."
    elif monthly_visits >= 20_000_000:
        route = "mediaos_door_opener_candidate"
        route_reason = "very large scale can benefit from a tooling-first opener before full pod discussion."

    distribution_focus = "mixed distribution strategy"
    if has_syndication and not has_seo:
        distribution_focus = "syndication-heavy distribution strategy"
    elif has_seo and not has_syndication:
        distribution_focus = "SEO/Discover-heavy distribution strategy"
    elif has_syndication and has_seo:
        distribution_focus = "blended distribution strategy"

    primary_hook = "start from one concrete research-backed business observation."
    if has_monetization:
        primary_hook = "start from a monetization gap/opportunity found in research."
    elif has_ops:
        primary_hook = "start from an operating-model gap (team/workflow/content velocity) from research."

    evidence_terms = []
    evidence_terms.extend(_first_hits(corpus, syndication_terms, limit=2))
    evidence_terms.extend(_first_hits(corpus, seo_terms, limit=2))
    evidence_terms.extend(_first_hits(corpus, monetization_terms, limit=2))
    evidence_terms.extend(_first_hits(corpus, growth_terms, limit=1))
    evidence_terms = evidence_terms[:5]

    risk_flags = []
    if not has_syndication and not has_seo:
        risk_flags.append("weak channel evidence in research; keep recommendations conservative.")
    if not evidence_terms:
        risk_flags.append("few explicit data signals detected; avoid specific numeric claims.")
    if not contact_role:
        risk_flags.append("contact role missing; avoid role-dependent assumptions.")

    return {
        "route": route,
        "route_reason": route_reason,
        "archetype": archetype,
        "scale_band": scale_band,
        "distribution_focus": distribution_focus,
        "primary_hook": primary_hook,
        "evidence_terms": evidence_terms,
        "risk_flags": risk_flags,
        "has_recipient_research": bool(_recipient_research_text(recipient_research).strip()),
    }


def format_strategy_profile_block(profile: Dict[str, Any]) -> str:
    if not isinstance(profile, dict) or not profile:
        return "(No strategy profile available.)"

    evidence_terms = ", ".join(profile.get("evidence_terms") or []) or "(none detected)"
    risk_flags = profile.get("risk_flags") or []
    risk_line = "; ".join(risk_flags) if risk_flags else "(none)"

    return (
        f"- Route Recommendation: {profile.get('route', 'accelerate')} ({profile.get('route_reason', '')})\n"
        f"- Publisher Archetype: {profile.get('archetype', 'unknown')}\n"
        f"- Scale Band: {profile.get('scale_band', 'unknown')}\n"
        f"- Distribution Focus: {profile.get('distribution_focus', 'mixed distribution strategy')}\n"
        f"- Primary Hook: {profile.get('primary_hook', '')}\n"
        f"- Evidence Terms Detected: {evidence_terms}\n"
        f"- Recipient Social Research Available: {'yes' if profile.get('has_recipient_research') else 'no'}\n"
        f"- Risk Flags: {risk_line}"
    )
