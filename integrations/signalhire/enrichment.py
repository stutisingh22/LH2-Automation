"""
SignalHire API integration for contact enrichment.

Flow: Check DB first → if no verified email, offer "Run enrichment?" → SignalHire (or mock)
→ Store result → Display verified vs enriched, source, confidence → Add to company.
"""

import json
import logging
import time
from urllib.parse import urljoin

import requests

from config import SIGNALHIRE_API_KEY, SIGNALHIRE_CALLBACK_BASE_URL

log = logging.getLogger(__name__)

SIGNALHIRE_BASE = "https://www.signalhire.com/api/v1"
PERSON_SEARCH_URL = f"{SIGNALHIRE_BASE}/candidate/search"
CREDITS_URL = f"{SIGNALHIRE_BASE}/credits"


def get_credits():
    """Return remaining SignalHire credits or None if unavailable."""
    if not SIGNALHIRE_API_KEY:
        return None
    try:
        r = requests.get(CREDITS_URL, headers={"apikey": SIGNALHIRE_API_KEY}, timeout=10)
        if r.status_code == 200:
            return r.json().get("credits")
        return None
    except Exception as e:
        log.warning("SignalHire credits check failed: %s", e)
        return None


def _normalize_contact_list(contacts):
    """Turn SignalHire candidate.contacts into list of {type, value, rating, subType, source}."""
    out = []
    for c in (contacts or []):
        out.append({
            "type": c.get("type", "email"),
            "value": c.get("value", "").strip(),
            "rating": int(c.get("rating", 0)) if c.get("rating") else 0,
            "subType": c.get("subType", ""),
            "source": "SignalHire",
        })
    return out


def normalize_candidate_to_result(candidate, identifier):
    """
    Normalize SignalHire candidate object to our display format.
    Returns dict: fullName, headline, emails[], phones[], linkedin, source, confidence.
    Emails/phones include: value, source (Verified | Enriched), confidence (0-100).
    """
    contacts = candidate.get("contacts") or []
    emails = []
    phones = []
    for c in contacts:
        val = (c.get("value") or "").strip()
        if not val:
            continue
        rating = int(c.get("rating", 0)) if c.get("rating") else 0
        confidence = min(100, max(0, rating))
        sub = (c.get("subType") or "").lower()
        source_label = "Verified" if confidence >= 90 else "Enriched"
        item = {"value": val, "source": source_label, "confidence": confidence}
        if c.get("type") == "phone":
            phones.append(item)
        else:
            emails.append(item)

    linkedin = ""
    for s in (candidate.get("social") or []):
        if (s.get("type") or "").lower() == "li":
            linkedin = (s.get("link") or "").strip()
            break

    return {
        "fullName": (candidate.get("fullName") or "").strip(),
        "headline": (candidate.get("headline") or "").strip(),
        "emails": emails,
        "phones": phones,
        "linkedin": linkedin or identifier if _is_linkedin_url(identifier) else "",
        "source": "SignalHire",
        "confidence": min(100, max(0, sum(e.get("confidence", 0) for e in emails) // max(len(emails), 1))),
        "locations": [loc.get("name") for loc in (candidate.get("locations") or []) if loc.get("name")],
    }


def _is_linkedin_url(s):
    return s and "linkedin.com" in str(s).lower()


def request_enrichment(identifier, user_id, callback_url=None):
    """
    Run enrichment for one identifier (LinkedIn URL or email).
    If SIGNALHIRE_CALLBACK_BASE_URL is set and callback_url is provided, POST to SignalHire
    and return (record_id, request_id, status='pending').
    Otherwise use mock and return (record_id, None, status='completed') with mock result.
    """
    from db_ops import save_enrichment_pending, save_enrichment_result_direct

    identifier = (identifier or "").strip()
    if not identifier:
        return None, None, "failed"

    use_callback = bool(
        SIGNALHIRE_API_KEY
        and (callback_url or SIGNALHIRE_CALLBACK_BASE_URL)
        and (callback_url or SIGNALHIRE_CALLBACK_BASE_URL).strip().lower().startswith("http")
    )
    cb = (callback_url or (SIGNALHIRE_CALLBACK_BASE_URL + "/api/signalhire-callback")).strip()

    if use_callback:
        try:
            log.info("SignalHire request: callbackUrl=%s (verify this matches your ngrok URL)", cb)
            r = requests.post(
                PERSON_SEARCH_URL,
                headers={"apikey": SIGNALHIRE_API_KEY, "Content-Type": "application/json"},
                json={"items": [identifier], "callbackUrl": cb},
                timeout=15,
            )
            if r.status_code in (200, 201):
                j = r.json()
                req_id = j.get("requestId") or j.get("request_id")
                if req_id is not None:
                    req_id = str(req_id)
                    record_id = save_enrichment_pending(user_id, req_id, identifier)
                    log.info("SignalHire accepted: requestId=%s — wait for callback to %s", req_id, cb)
                    return record_id, req_id, "pending"
            log.warning("SignalHire request failed: %s %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("SignalHire request error: %s", e)

    try:
        time.sleep(1.2)
        mock_candidate = _mock_candidate(identifier)
        result = normalize_candidate_to_result(mock_candidate, identifier)
        record_id = save_enrichment_result_direct(user_id, identifier, result, status="completed")
        return record_id, None, "completed"
    except Exception as e:
        log.exception("Enrichment mock/save failed: %s", e)
        return None, None, "failed"


def _linkedin_slug_to_name(identifier):
    """Extract a display name from LinkedIn URL slug."""
    if not identifier or "linkedin.com" not in identifier.lower():
        return ""
    try:
        parts = identifier.rstrip("/").split("/in/")
        if len(parts) < 2:
            return ""
        slug = parts[-1].split("?")[0].strip()
        if not slug:
            return ""
        name_part = slug.rsplit("-", 1)[0] if slug[-1].isdigit() else slug
        return name_part.replace("-", " ").title()
    except Exception:
        return ""


def _mock_candidate(identifier):
    """Generate a mock SignalHire candidate."""
    identifier = (identifier or "").strip()
    contacts = []
    linkedin = ""

    if _is_linkedin_url(identifier):
        linkedin = identifier
        name = _linkedin_slug_to_name(identifier) or "Contact (from LinkedIn)"
        headline = "Professional"
        contacts = []
    elif "@" in identifier:
        name = identifier.split("@")[0].replace(".", " ").title()
        headline = "Professional"
        contacts = [
            {"type": "email", "value": identifier, "rating": "95", "subType": "work"},
        ]
    else:
        name = "Contact"
        headline = ""

    return {
        "fullName": name,
        "headline": headline,
        "contacts": contacts,
        "social": [{"type": "li", "link": linkedin, "rating": "100"}] if linkedin else [],
        "locations": [],
    }


def has_verified_email(contact_dict):
    """True if contact has an email we consider verified (from DB, not just enrichment)."""
    email = (contact_dict.get("email") or "").strip()
    if not email:
        return False
    extra = contact_dict.get("extra_data") or {}
    enrichment = extra.get("enrichment") or {}
    return bool(email)


def parse_callback_payload(body, request_id_from_header=None):
    """
    Parse SignalHire callback POST body. Returns list of (request_id, identifier, result_json) to update.
    """
    out = []
    try:
        data = body if isinstance(body, (dict, list)) else (json.loads(body) if body else {})
    except Exception:
        data = {}
    request_id = None
    results = []
    if isinstance(data, list):
        results = data
        request_id = (request_id_from_header or "").strip() or None
    else:
        request_id = data.get("requestId") or data.get("request_id") or (request_id_from_header or "").strip() or None
        if request_id is not None:
            request_id = str(request_id)
        results = data.get("results") or data.get("data") or data.get("items") or []
    if not isinstance(results, list):
        results = []
    for item in results:
        if not isinstance(item, dict):
            continue
        status = (item.get("status") or item.get("result") or "").lower()
        candidate = item.get("candidate") or item.get("data") or {}
        identifier = (item.get("item") or item.get("identifier") or item.get("linkedin") or "").strip()
        if status in ("success", "completed", "ok") and candidate:
            result_json = normalize_candidate_to_result(candidate, identifier)
            out.append((str(request_id) if request_id else None, identifier, result_json))
        elif status in ("success", "completed", "ok") and request_id:
            out.append((str(request_id), identifier, {"fullName": "", "emails": [], "phones": [], "source": "SignalHire"}))
    if not out and request_id:
        out.append((str(request_id), "", {"fullName": "", "emails": [], "phones": [], "source": "SignalHire"}))
    return out
