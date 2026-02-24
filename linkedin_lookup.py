"""
linkedin_lookup.py — Find LinkedIn profile URLs from names + company using search (no LinkedIn API).
Used by Strategist "Extract contacts & find LinkedIn URLs" from brief/research.
Does not modify research.py or any existing research logic.
"""

from __future__ import annotations

import re
import time
import random
import logging
from urllib.parse import urlparse, urlunparse

log = logging.getLogger(__name__)

# Rate limiting: delay between search calls (seconds)
DELAY_BETWEEN_LOOKUPS = (1.5, 3.5)
MAX_RESULTS_PER_QUERY = 10


def normalize_linkedin_url(url: str) -> str:
    """
    Keep only canonical public LinkedIn profile URLs (linkedin.com/in/...),
    strip query params/fragments, normalize scheme/hostname.
    """
    if not url or not url.strip():
        return ""
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    host = (parsed.netloc or "").lower()
    if not host.endswith("linkedin.com"):
        return ""
    path = parsed.path or ""
    if not path.startswith("/in/"):
        return ""
    if any(seg in path for seg in ("/jobs/", "/company/", "/school/", "/posts/", "/feed/", "/mwlite/")):
        return ""
    cleaned = parsed._replace(scheme="https", netloc="www.linkedin.com", params="", query="", fragment="")
    return urlunparse(cleaned)


def _ddg_search(query: str, max_results: int = MAX_RESULTS_PER_QUERY) -> list[dict]:
    """
    Run DuckDuckGo (or fallback backend) text search. Returns list of {title, body, href}.
    """
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
        except Exception as e:
            raise RuntimeError(
                "LinkedIn lookup needs ddgs. Install: pip install ddgs"
            ) from e

    backends = ["bing", "duckduckgo", "brave"]
    last_err = None
    for backend in backends:
        try:
            out = []
            with DDGS(timeout=20) as ddgs:
                for r in ddgs.text(
                    query,
                    max_results=max_results,
                    region="us-en",
                    safesearch="off",
                    backend=backend,
                ):
                    out.append({
                        "title": (r.get("title") or "").strip(),
                        "body": (r.get("body") or "").strip(),
                        "href": (r.get("href") or "").strip(),
                    })
            if out:
                return out
        except Exception as e:
            last_err = e
            if "429" in str(e) or "Too Many Requests" in str(e):
                raise
            continue
    if last_err:
        raise last_err
    return []


def _extract_linkedin_urls_from_result(title: str, body: str, href: str) -> list[str]:
    """Extract and normalize linkedin.com/in URLs from a single search result."""
    candidates = [href]
    text = f"{title}\n{body}\n{href}"
    for m in re.finditer(
        r"https?://(?:[\w.-]+\.)?linkedin\.com/in/[^\s)\"'>]+",
        text,
        flags=re.IGNORECASE,
    ):
        candidates.append(m.group(0))
    seen = set()
    out = []
    for c in candidates:
        u = normalize_linkedin_url(c)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _name_parts(name: str) -> tuple[str, str]:
    """Return (first_name, last_name) for scoring; last may be empty."""
    parts = (name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _score_result_for_person(title: str, body: str, name: str) -> int:
    """
    Score how well a search result (title/snippet) matches the person.
    Higher = better. Prefer results that contain the full name or last name.
    """
    text = f"{title} {body}".lower()
    name_lower = (name or "").strip().lower()
    first, last = _name_parts(name)
    score = 0
    if name_lower and name_lower in text:
        score += 10
    if last and last.lower() in text:
        score += 5
    if first and first.lower() in text:
        score += 2
    return score


def get_linkedin_url_for_contact(name: str, company: str) -> str | None:
    """
    Given a person's name and company, search for their LinkedIn profile and return the best match.
    Tries name+company first, then name-only; prefers results where title/snippet contains the name.
    """
    name = (name or "").strip()
    company = (company or "").strip()
    if not name:
        return None

    def _search_and_pick(query: str) -> str | None:
        try:
            results = _ddg_search(query, max_results=MAX_RESULTS_PER_QUERY)
        except Exception:
            return None
        best_url = None
        best_score = -1
        for r in results:
            urls = _extract_linkedin_urls_from_result(
                r.get("title", ""),
                r.get("body", ""),
                r.get("href", ""),
            )
            if not urls:
                continue
            score = _score_result_for_person(
                r.get("title", ""),
                r.get("body", ""),
                name,
            )
            if score > best_score:
                best_score = score
                best_url = urls[0]
        return best_url

    # Prefer query with company for relevance
    if company:
        query_with_company = f'site:linkedin.com/in "{name}" {company}'
        url = _search_and_pick(query_with_company)
        if url:
            return url
    query_name_only = f'site:linkedin.com/in "{name}"'
    return _search_and_pick(query_name_only)


def extract_contacts_from_brief_research(
    brief_text: str,
    research_text: str,
    company_name: str,
) -> list[dict]:
    """
    Use AI to extract (name, role, company) from brief + research. Company defaults to company_name when not stated.
    Returns list of {"name": str, "role": str, "company": str}.
    """
    from config import ANTHROPIC_API_KEY
    from anthropic import Anthropic

    combined = f"=== BRIEF ===\n{(brief_text or '').strip()}\n\n=== RESEARCH ===\n{(research_text or '').strip()}"
    if not combined.strip():
        return []

    if not ANTHROPIC_API_KEY:
        log.warning("No ANTHROPIC_API_KEY for contact extraction")
        return []

    company_name = (company_name or "this company").strip()
    prompt = f"""From the brief and research below, list every person (SPOC, contact, or named individual) mentioned.
For each person provide: their full name, their role/title (if stated, else empty string), and the company they belong to (if not stated, use "{company_name}").
Output valid JSON only, no markdown: [{{"name": "...", "role": "...", "company": "..."}}, ...]
Deduplicate by name; if the same person appears twice, keep one entry with the most complete role/company.
If no people are mentioned, return []."""

    try:
        from config import AI_MODEL
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        r = client.messages.create(
            model=AI_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": f"{prompt}\n\n{combined[:50000]}"}],
        )
        text = (r.content[0].text if r.content else "").strip()
        # Strip markdown code block if present
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text).rstrip("`").strip()
        import json
        data = json.loads(text)
        if not isinstance(data, list):
            return []
        out = []
        seen_names = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            out.append({
                "name": name,
                "role": (item.get("role") or "").strip(),
                "company": (item.get("company") or company_name).strip() or company_name,
            })
        return out
    except Exception as e:
        log.warning("Contact extraction failed: %s", e)
        return []


def run_linkedin_lookup_for_contacts(
    contacts: list[dict],
    progress_callback=None,
) -> list[dict]:
    """
    For each contact {"name", "role", "company"}, run get_linkedin_url_for_contact and add "linkedin_url" key.
    progress_callback(current_index, total, name) optional. Adds small delay between lookups.
    """
    result = []
    total = len(contacts)
    for i, c in enumerate(contacts):
        name = c.get("name") or ""
        company = c.get("company") or ""
        if progress_callback:
            progress_callback(i, total, name)
        url = get_linkedin_url_for_contact(name, company)
        result.append({
            **c,
            "linkedin_url": url or "",
        })
        if i < total - 1:
            time.sleep(random.uniform(*DELAY_BETWEEN_LOOKUPS))
    return result
