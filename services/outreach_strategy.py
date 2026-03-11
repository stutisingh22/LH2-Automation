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


def _pct_range_midpoint(raw: str) -> float | None:
    """
    Extract a midpoint from a percentage like '75-85%' or '10%'.
    Returns None when no percentage is found.
    """
    if not raw:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*%", raw)
    if m:
        lo = float(m.group(1))
        hi = float(m.group(2))
        return (lo + hi) / 2.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", raw)
    if m:
        return float(m.group(1))
    return None


def _infer_seo_vs_syndication_ratio_from_research(corpus: str) -> float | None:
    """
    Best-effort deterministic extraction of an organic/SEO-dominant vs syndication-dominant mix
    from research text that includes business model tables.

    Returns ratio = (seo-ish weight) / (syndication-ish weight) using midpoints.
    If either side can't be extracted, returns None.
    """
    if not corpus:
        return None

    lines = [ln.strip() for ln in corpus.splitlines() if ln.strip()]
    programmatic_weight = None
    seo_weight = None
    syndication_weight = None

    for ln in lines:
        mid = _pct_range_midpoint(ln)
        if mid is not None:
            if syndication_weight is None and re.search(r"\b(syndication|wire\s+service|rev-?share|revenue-?share)\b", ln, re.IGNORECASE):
                syndication_weight = mid
            if programmatic_weight is None and re.search(r"\bprogrammatic\b", ln, re.IGNORECASE):
                programmatic_weight = mid
            if seo_weight is None and re.search(r"\b(organic|seo|search)\s+(traffic|revenue)?\b", ln, re.IGNORECASE):
                seo_weight = mid

    # If SEO isn't explicitly labeled as a revenue line item, programmatic is usually the SEO-driven core.
    seoish = seo_weight if seo_weight is not None else programmatic_weight

    if seoish is not None and syndication_weight is not None and syndication_weight > 0:
        return float(seoish) / float(syndication_weight)

    # Fallback: research often has "Programmatic ... 75-85%" and "Syndication ... 5-10%" in table form.
    # If both high programmatic % and low syndication % appear in corpus, treat as programmatic-dominant.
    if re.search(r"75\s*-\s*85\s*%|70\s*-\s*90\s*%", corpus) and re.search(r"\bprogrammatic\b", corpus, re.IGNORECASE):
        if re.search(r"5\s*-\s*10\s*%|1\s*-\s*15\s*%", corpus) and re.search(
            r"\b(syndication|revenue-?\s*share|wire\s+service)\b", corpus, re.IGNORECASE
        ):
            return 10.0  # programmatic dominant
    return None


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

    # If a business-model breakdown exists, prefer it over simple keyword signals.
    ratio = _infer_seo_vs_syndication_ratio_from_research(corpus)
    revenue_mix_focus = "unknown"
    if ratio is not None:
        if ratio > 4.0:
            distribution_focus = "SEO/Discover-heavy distribution strategy"
            revenue_mix_focus = "seo_dominant"
        elif ratio < 0.25:
            distribution_focus = "syndication-heavy distribution strategy"
            revenue_mix_focus = "syndication_dominant"
        else:
            distribution_focus = "blended distribution strategy"
            revenue_mix_focus = "blended"
        # Make the presence of deterministic mix extraction visible to the LLM.
        risk_flags.append("revenue mix extracted from research; do not lead with secondary channel.")

    return {
        "route": route,
        "route_reason": route_reason,
        "archetype": archetype,
        "scale_band": scale_band,
        "distribution_focus": distribution_focus,
        "revenue_mix_focus": revenue_mix_focus,
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
    revenue_mix = (profile.get("revenue_mix_focus") or "").strip() or "unknown"
    dist_focus = profile.get("distribution_focus", "mixed distribution strategy") or ""

    # Explicit NARRATIVE LEAD so the model cannot lead with the wrong channel.
    if revenue_mix == "seo_dominant" or "SEO" in dist_focus and "syndication-heavy" not in dist_focus.lower():
        narrative_lead = (
            "NARRATIVE LEAD (mandatory for ALL emails): Lead with programmatic revenue, content volume, "
            "and search/organic economics. Do NOT lead with syndication or MSN. Mention syndication only as "
            "secondary or supporting distribution. The prospect's main revenue driver is not syndication."
        )
    elif revenue_mix == "syndication_dominant" or "syndication-heavy" in dist_focus.lower():
        narrative_lead = (
            "NARRATIVE LEAD (mandatory for ALL emails): Lead with syndication (MSN/Yahoo/NewsBreak) and "
            "platform distribution; programmatic/SEO is secondary."
        )
    else:
        narrative_lead = (
            "NARRATIVE LEAD (mandatory for ALL emails): Read the RESEARCH section for revenue breakdown. "
            "If programmatic/core revenue is much larger than syndication (e.g. 75-85% vs 5-10%), lead with "
            "programmatic, content volume, and search economics; do NOT lead with syndication or MSN. "
            "When unclear, lead with content volume and programmatic; mention syndication only as secondary."
        )

    return (
        f"- Route Recommendation: {profile.get('route', 'accelerate')} ({profile.get('route_reason', '')})\n"
        f"- Publisher Archetype: {profile.get('archetype', 'unknown')}\n"
        f"- Scale Band: {profile.get('scale_band', 'unknown')}\n"
        f"- Distribution Focus: {dist_focus}\n"
        f"- Revenue Mix (from research): {revenue_mix}\n"
        f"- {narrative_lead}\n"
        f"- Primary Hook: {profile.get('primary_hook', '')}\n"
        f"- Evidence Terms Detected: {evidence_terms}\n"
        f"- Recipient Social Research Available: {'yes' if profile.get('has_recipient_research') else 'no'}\n"
        f"- Risk Flags: {risk_line}"
    )
