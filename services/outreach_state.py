"""
Strict outreach status transitions for Gmail sync and manual updates.
Terminal user-set states are preserved unless an incoming signal strictly wins by policy.
"""

from __future__ import annotations

# Aliases → canonical internal status for transition graph
_STATUS_ALIASES: dict[str, str] = {
    "completed": "sent",
    "in_progress": "planned",
    "awaiting_reply": "sent",
}


def canonical_outreach_status(status: str | None) -> str:
    s = (status or "planned").strip().lower() or "planned"
    return _STATUS_ALIASES.get(s, s)


# Precedence rank: higher = stronger signal (used to avoid downgrades from Gmail sync)
_RANK: dict[str, int] = {
    "planned": 10,
    "sent": 20,
    "no_response": 25,
    "replied_negative": 35,
    "replied_positive": 40,
    "meeting_booked": 50,
    "deal_lost": 55,
    "deal_closed": 60,
}

# Allowed directed edges (canonical statuses)
_ALLOWED: dict[str, set[str]] = {
    # Rare: prospect replies before we mark sent in-app — still attach signal to the active step.
    "planned": {"sent", "deal_lost", "replied_positive", "replied_negative", "meeting_booked"},
    "sent": {"replied_positive", "replied_negative", "meeting_booked", "deal_lost", "no_response"},
    "no_response": {"sent", "replied_positive", "replied_negative", "meeting_booked", "deal_lost"},
    "replied_positive": {"meeting_booked", "deal_lost", "replied_negative", "deal_closed"},
    "replied_negative": {"deal_lost", "replied_positive", "meeting_booked"},  # rare correction path
    "meeting_booked": {"deal_closed", "deal_lost"},
    "deal_lost": set(),
    "deal_closed": set(),
}


def status_rank(status: str | None) -> int:
    return _RANK.get(canonical_outreach_status(status), 0)


def can_apply_gmail_transition(current: str | None, proposed: str) -> bool:
    """Whether proposed is a valid next state from current under the directed graph."""
    cur = canonical_outreach_status(current)
    prop = canonical_outreach_status(proposed)
    if cur == prop:
        return True
    return prop in _ALLOWED.get(cur, set())


def should_apply_sync_status(current: str | None, proposed: str, *, force: bool = False) -> bool:
    """
    Gmail sync should not downgrade a stronger confirmed state unless force (reprocess) — not used in prod.
    """
    if force:
        return True
    cur_c = canonical_outreach_status(current)
    prop_c = canonical_outreach_status(proposed)
    if cur_c == prop_c:
        return False  # no-op
    if not can_apply_gmail_transition(current, proposed):
        return False
    # Avoid replacing terminal negatives with soft positives accidentally — meeting_booked still allowed from sent
    if cur_c == "deal_lost" and prop_c != "deal_lost":
        return False
    if cur_c == "deal_closed":
        return False
    # Rank guard: don't go to a strictly weaker *reply* bucket if we already have meeting_booked
    if status_rank(current) > status_rank(proposed):
        return False
    return True


def resolve_action_status_for_patch(current: str | None, proposed: str) -> str | None:
    """
    Returns the status string to persist (canonical form for new states), or None if blocked.
    """
    if not should_apply_sync_status(current, proposed):
        return None
    prop = canonical_outreach_status(proposed)
    # Persist user-facing variants: keep 'sent' as sent; map is identity for new states
    return prop
