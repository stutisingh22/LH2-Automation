"""
Deterministic reply classification (keyword / intent rules). No LLM in the critical path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Normalized labels stored on Action.response_type for sync-derived replies
RT_MEETING = "outreach:meeting_intent"
RT_POSITIVE = "outreach:positive"
RT_NEGATIVE = "outreach:negative"
RT_DEFER = "outreach:defer"
RT_NEUTRAL = "outreach:neutral"

MEETING_STRONG_PATTERNS = [
    r"\bcalendar\b",
    r"\bcalendly\b",
    r"\bzoom\b",
    r"\bmeeting\b",
    r"\bbook\b.*\btime\b",
    r"\bschedule\b",
    r"\btime slot\b",
    r"\b(book|schedule).*\b(call|meeting)\b",
]

MEETING_WEAK_PATTERNS = [
    r"\bcall\b",
    r"\bmeet\b",
    r"\bavailab(le|ility)\b",
    r"\bnext week\b",
    r"\b\d{1,2}:\d{2}\s*(am|pm)?\b",
]

STRONG_LOST_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\bnot for sale\b",
    r"\bwe are not (for sale|selling)\b",
    r"\bnever contact\b",
]

NEGATIVE_PATTERNS = [
    r"\bnot interested\b",
    r"\bno thank\b",
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\bstop (contacting|emailing)\b",
    r"\bhard no\b",
    r"\bwe are not (looking|interested|for sale)\b",
    r"\bno plans to sell\b",
    r"\bnot for sale\b",
    r"\bgoing to pass\b",
    r"\brespectfully decline\b",
    r"\bplease delete\b",
]

POSITIVE_PATTERNS = [
    r"\byes\b",
    r"\bsounds good\b",
    r"\blove to\b",
    r"\bhappy to\b",
    r"\binterested\b",
    r"\btell me more\b",
    r"\bopen to\b",
    r"\bgood time\b",
]

DEFER_PATTERNS = [
    r"\b(check|follow) back\b",
    r"\bmonth\b",
    r"\bnext quarter\b",
    r"\btouch base\b",
    r"\btoo busy\b",
    r"\bget back to you\b",
    r"\bcircle back\b",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    t = (text or "").lower()
    for p in patterns:
        if re.search(p, t, re.I):
            return True
    return False


def _count_matches(text: str, patterns: list[str]) -> int:
    t = (text or "").lower()
    count = 0
    for p in patterns:
        if re.search(p, t, re.I):
            count += 1
    return count


@dataclass
class ClassifyResult:
    action_status: str  # replied_positive | replied_negative | meeting_booked | planned (neutral)
    response_type: str
    outcome_reason: str


def classify_inbound_reply(subject: str, body: str) -> ClassifyResult:
    """
    Map free text → outreach Action.status + response_type + short machine-readable reason.
    Priority: meeting > negative > defer > positive > neutral.
    """
    blob = f"{subject or ''}\n{body or ''}".strip()
    reasons: list[str] = []

    if _matches_any(blob, STRONG_LOST_PATTERNS):
        reasons.append("rule:strong_lost_keywords")
        return ClassifyResult(
            action_status="deal_lost",
            response_type=RT_NEGATIVE,
            outcome_reason=";".join(reasons),
        )

    strong_meeting = _matches_any(blob, MEETING_STRONG_PATTERNS)
    weak_meeting_count = _count_matches(blob, MEETING_WEAK_PATTERNS)
    if strong_meeting or weak_meeting_count >= 2:
        reasons.append("rule:meeting_keywords")
        return ClassifyResult(
            action_status="meeting_booked",
            response_type=RT_MEETING,
            outcome_reason=";".join(reasons),
        )

    if _matches_any(blob, NEGATIVE_PATTERNS):
        reasons.append("rule:negative_keywords")
        return ClassifyResult(
            action_status="replied_negative",
            response_type=RT_NEGATIVE,
            outcome_reason=";".join(reasons),
        )

    if _matches_any(blob, DEFER_PATTERNS):
        reasons.append("rule:defer_keywords")
        return ClassifyResult(
            action_status="replied_positive",  # still engagement; not lost
            response_type=RT_DEFER,
            outcome_reason=";".join(reasons),
        )

    if _matches_any(blob, POSITIVE_PATTERNS):
        reasons.append("rule:positive_keywords")
        return ClassifyResult(
            action_status="replied_positive",
            response_type=RT_POSITIVE,
            outcome_reason=";".join(reasons),
        )

    reasons.append("rule:neutral_default")
    return ClassifyResult(
        action_status="replied_positive",
        response_type=RT_NEUTRAL,
        outcome_reason=";".join(reasons),
    )
