"""
SignalHire enrichment service — production-ready.

Enrich LinkedIn profile URLs with email and phone via SignalHire API.
No notebook, webhook.site, or Google Sheets; sync or callback-based flow.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

SIGNALHIRE_CREDITS_URL = "https://www.signalhire.com/api/v1/credits"
SIGNALHIRE_SEARCH_URL = "https://www.signalhire.com/api/v1/candidate/search"
BATCH_SIZE = 50
REQUEST_TIMEOUT = 60
POLL_INTERVAL = 10
MAX_RETRIES = 5
BASE_BACKOFF = 2


# Regex for valid linkedin.com/in/<profile> (profile: letters, digits, -, _, %)
_LINKEDIN_IN_PATTERN = re.compile(r"linkedin\.com/in/[A-Za-z0-9\-_%]+", re.IGNORECASE)


def clean_linkedin_url(url: str) -> str | None:
    """
    Normalize LinkedIn URLs: remove query params, trailing slash.
    Return None if not a valid linkedin.com/in/<profile> URL.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not _LINKEDIN_IN_PATTERN.search(url):
        return None
    url = re.sub(r"\?.*$", "", url)
    url = re.sub(r"/overlay/.*$", "", url)
    url = url.rstrip("/")
    return url if url else None


def check_signalhire_credits(api_key: str) -> int | None:
    """GET credits; return remaining count or None on error."""
    if not (api_key or "").strip():
        return None
    try:
        r = requests.get(
            SIGNALHIRE_CREDITS_URL,
            headers={"apikey": api_key.strip()},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            return int(data.get("credits", 0))
        log.warning("SignalHire credits: HTTP %s %s", r.status_code, r.text[:200])
        return None
    except Exception as e:
        log.warning("SignalHire credits check failed: %s", e)
        return None


def send_signalhire_request(api_key: str, linkedin_urls: list[str], callback_url: str | None = None) -> dict[str, Any]:
    """
    POST to candidate/search with items. Handles 201 success, 429 rate limit with exponential backoff.
    Returns response dict (e.g. {'requestId': ...}) on 201; raises or returns error dict on failure.
    """
    if not (api_key or "").strip():
        return {"error": "Missing API key"}
    payload_items = [u for u in linkedin_urls if u]
    if not payload_items:
        return {"error": "No valid LinkedIn URLs"}
    payload: dict[str, Any] = {"items": payload_items}
    if callback_url and callback_url.strip().startswith("http"):
        payload["callbackUrl"] = callback_url.strip()
    headers = {"apikey": api_key.strip(), "Content-Type": "application/json"}
    log.info("Sending batch request to SignalHire for %d URL(s)", len(payload_items))
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                SIGNALHIRE_SEARCH_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 201:
                data = r.json() if r.text else {}
                request_id = data.get("requestId") or data.get("request_id")
                if request_id:
                    log.info("SignalHire request accepted, requestId=%s", request_id)
                return data
            if r.status_code == 429:
                wait = BASE_BACKOFF ** (attempt + 1)
                log.warning("SignalHire 429 rate limit; waiting %ss before retry %s/%s", wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                continue
            last_error = {"error": (r.text or f"HTTP {r.status_code}").strip() or f"HTTP {r.status_code}"}
            if attempt < MAX_RETRIES - 1:
                time.sleep(BASE_BACKOFF ** (attempt + 1))
                continue
            return last_error
        except requests.exceptions.Timeout as e:
            last_error = {"error": str(e)}
            if attempt < MAX_RETRIES - 1:
                time.sleep(BASE_BACKOFF ** (attempt + 1))
                continue
            return last_error
        except Exception as e:
            last_error = {"error": str(e)}
            if attempt < MAX_RETRIES - 1:
                time.sleep(BASE_BACKOFF ** (attempt + 1))
                continue
            return last_error
    return last_error or {"error": "Max retries reached"}


def parse_candidate_data(candidate_data: dict) -> dict[str, Any]:
    """
    Parse one candidate from SignalHire response.
    Returns { "full_name": str, "emails": list[str], "phones": list[str] }.
    """
    out: dict[str, Any] = {"full_name": "", "emails": [], "phones": []}
    if not candidate_data or not isinstance(candidate_data, dict):
        return out
    out["full_name"] = (candidate_data.get("fullName") or "").strip()
    contacts = candidate_data.get("contacts") or []
    for c in contacts:
        if not isinstance(c, dict):
            continue
        val = (c.get("value") or "").strip()
        if not val:
            continue
        typ = (c.get("type") or "").lower()
        if typ == "email":
            out["emails"].append(val)
        elif typ == "phone":
            out["phones"].append(val)
    out["emails"] = list(dict.fromkeys(out["emails"]))
    out["phones"] = list(dict.fromkeys(out["phones"]))
    return out


def _normalize_polled_result(parsed: dict) -> dict[str, Any]:
    """Build { name, emails, phones } from either raw candidate or already-parsed result_json."""
    if not parsed or not isinstance(parsed, dict):
        return {"name": "", "emails": [], "phones": []}
    if isinstance(parsed.get("emails"), list) and (not parsed.get("emails") or isinstance(parsed["emails"][0], str)):
        return {
            "name": (parsed.get("full_name") or parsed.get("fullName") or "").strip(),
            "emails": list(parsed.get("emails") or []),
            "phones": list(parsed.get("phones") or []),
        }
    # DB result_json: emails/phones are list of {value, source, confidence}
    emails = parsed.get("emails") or []
    phones = parsed.get("phones") or []
    if isinstance(emails, list) and emails and isinstance(emails[0], dict):
        emails = [e.get("value", "") for e in emails if (e.get("value") or "").strip()]
    if isinstance(phones, list) and phones and isinstance(phones[0], dict):
        phones = [p.get("value", "") for p in phones if (p.get("value") or "").strip()]
    if isinstance(emails, list) or isinstance(phones, list):
        return {
            "name": (parsed.get("full_name") or parsed.get("fullName") or "").strip(),
            "emails": list(emails) if isinstance(emails, list) else [],
            "phones": list(phones) if isinstance(phones, list) else [],
        }
    return parse_candidate_data(parsed)


def enrich_linkedin_batch(
    api_key: str,
    linkedin_urls: list[str],
    callback_url: str | None = None,
    poll_result_fn: Callable[[str, int], list] | None = None,
    save_pending_fn: Callable[[str, list[str]], None] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """
    Clean URLs, dedupe, send in batches of 50, optionally wait for results via poll_result_fn.

    poll_result_fn(request_id: str, timeout: int) -> list of (linkedin_url, result_dict).
    save_pending_fn(request_id: str, urls: list[str]) called after each POST so caller can persist pending rows.

    Returns:
      - If poll_result_fn provided and results received: { linkedin_url: { "name", "emails", "phones" } }
      - If no poll_result_fn or no results: { "request_ids": [...], "pending": True }
      - On error: { "error": str }
    """
    if not (api_key or "").strip():
        return {"error": "Missing API key"}
    cleaned = []
    seen = set()
    for u in linkedin_urls:
        c = clean_linkedin_url(u)
        if c and c not in seen:
            cleaned.append(c)
            seen.add(c)
    if not cleaned:
        return {"error": "No valid LinkedIn URLs"}
    log.info("Enriching %d LinkedIn URL(s) (deduplicated)", len(cleaned))
    result_by_url: dict[str, dict[str, Any]] = {}
    request_ids: list[str] = []
    for i in range(0, len(cleaned), BATCH_SIZE):
        batch = cleaned[i : i + BATCH_SIZE]
        resp = send_signalhire_request(api_key, batch, callback_url=callback_url)
        if "error" in resp and "requestId" not in resp and "request_id" not in resp:
            return resp
        request_id = resp.get("requestId") or resp.get("request_id")
        if request_id:
            request_ids.append(str(request_id))
            if save_pending_fn:
                try:
                    save_pending_fn(str(request_id), list(batch))
                except Exception as e:
                    log.warning("save_pending_fn failed: %s", e)
        if poll_result_fn and request_id:
            try:
                log.info("Polling for results, requestId=%s", request_id)
                polled = poll_result_fn(str(request_id), timeout=timeout)
                if isinstance(polled, list):
                    for entry in polled:
                        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                            url, parsed = entry[0], entry[1]
                            url = (url or "").strip()
                            if url:
                                result_by_url[url] = _normalize_polled_result(parsed if isinstance(parsed, dict) else {})
                        elif isinstance(entry, dict):
                            url = (entry.get("item") or entry.get("identifier") or "").strip()
                            cand = entry.get("candidate") or entry.get("data") or entry
                            if url:
                                result_by_url[url] = _normalize_polled_result(cand)
                    if polled:
                        log.info("Parsed %d result(s) for requestId=%s", len(polled), request_id)
            except Exception as e:
                log.warning("Poll failed for request_id=%s: %s", request_id, e)
    if result_by_url:
        return result_by_url
    return {"request_ids": request_ids, "pending": True}
