"""
web_search.py — Web search for person/lead research (always from web, never Notion).

When TAVILY_API_KEY is set, person lookup and lead research use web search.
Used for: intelligence person lookup, SPOC briefs, auto_brief contact context,
and injecting real contact data into deep research (strategy/notion bot).
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

# Read from env so we don't depend on config.TAVILY_API_KEY (optional key)
TAVILY_API_KEY = (os.getenv("TAVILY_API_KEY") or "").strip()

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _tavily_search(query, max_results=8, search_depth="advanced", timeout=20):
    """Run a single Tavily search. Returns list of {title, content, url} or []."""
    if not TAVILY_API_KEY or not (query or "").strip():
        return []
    try:
        resp = requests.post(
            TAVILY_SEARCH_URL,
            json={
                "api_key": TAVILY_API_KEY,
                "query": query.strip(),
                "search_depth": search_depth,
                "max_results": max_results,
                "include_answer": False,
                "include_images": False,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        return [
            {
                "title": (r.get("title") or "").strip(),
                "content": (r.get("content") or "").strip(),
                "url": (r.get("url") or "").strip(),
            }
            for r in results
        ]
    except Exception as e:
        log.warning("Tavily search failed: %s", e)
        return []


def _format_results(results, max_items=8):
    """Format result dicts into a single string for prompts."""
    parts = []
    for r in results[:max_items]:
        if r.get("content"):
            url = r.get("url") or ""
            url_part = f" | LinkedIn: {url}" if url and "linkedin.com" in url else (f" (Source: {url})" if url else "")
            parts.append(
                f"[{r.get('title') or 'Source'}] {r['content']}{url_part}"
            )
    return "\n\n".join(parts) if parts else None


def _domain_to_brand(domain):
    """Convert domain to a likely brand name for better search (e.g. techcrunch.com -> TechCrunch)."""
    if not domain:
        return ""
    d = domain.strip().replace("www.", "").lower()
    # Remove TLD
    if "." in d:
        d = d.rsplit(".", 1)[0]
    # Replace hyphens with spaces and title-case
    name = d.replace("-", " ").strip()
    return name.title() if name else d


def search_web_for_person(linkedin_url=None, email=None, person_name=None, company_domain=None, max_results=8):
    """
    Search the web for information about a person. Used for person lookup
    and SPOC briefs — always from web, never from Notion/contacts.

    Args:
        linkedin_url: Optional LinkedIn profile URL.
        email: Optional email address.
        person_name: Optional name (e.g. "Mitch Williams" or "the editor").
        company_domain: Optional company domain for context.

    Returns:
        String of concatenated search result snippets, or None if no key or error.
    """
    if not TAVILY_API_KEY:
        return None

    query_parts = []
    if linkedin_url:
        query_parts.append(linkedin_url)
    if email:
        query_parts.append(email)
    if person_name and person_name.strip():
        query_parts.append(person_name.strip())
    if company_domain:
        query_parts.append(company_domain)
    if not query_parts:
        return None

    query = " ".join(query_parts)
    results = _tavily_search(query, max_results=max_results, search_depth="advanced")
    return _format_results(results, max_items=max_results)


def search_web_for_person_by_name(person_name, company_domain, max_results=10):
    """
    Search the web for a person by name and company. Use when the user types
    a name or role (e.g. "John Smith" or "research the editor") in intelligence.

    Returns:
        String of concatenated snippets (LinkedIn, company pages, press), or None.
    """
    if not TAVILY_API_KEY or not (person_name or "").strip():
        return None
    name = person_name.strip()
    domain = (company_domain or "").strip()
    # Prefer queries that surface LinkedIn and company pages
    queries = [
        f"{name} {domain} LinkedIn",
        f"{name} {domain}",
        f'"{name}" "{domain}"',
    ]
    if domain:
        queries.append(f"site:linkedin.com/in {domain} {name}")
    all_results = []
    seen_urls = set()
    for q in queries:
        results = _tavily_search(q, max_results=5, search_depth="advanced")
        for r in results:
            if r.get("url") and r["url"] not in seen_urls and r.get("content"):
                seen_urls.add(r["url"])
                all_results.append(r)
        if len(all_results) >= max_results:
            break
    return _format_results(all_results, max_items=max_results)


def power_scrape_company_spocs(domain, max_total_results=45):
    """
    Power scraping for a company: find C-level/executive SPOCs with real names and
    LinkedIn profiles (CEO, CFO, COO, founder, president, board). No editors.
    Uses both domain and brand name + explicit LinkedIn-focused queries so
    even well-known sites return concrete contacts and profile URLs.
    """
    if not TAVILY_API_KEY or not (domain or "").strip():
        return None
    d = domain.strip().replace("www.", "").lower()
    brand = _domain_to_brand(domain)
    # Mix of domain and brand: brand often works better for famous sites (e.g. "TechCrunch CEO")
    # Explicit LinkedIn queries to surface actual profile URLs and names
    queries = [
        f"{brand} CEO LinkedIn",
        f"{brand} CEO CFO LinkedIn profile",
        f'"{brand}" CEO OR CFO OR COO site:linkedin.com',
        f"{brand} founder president LinkedIn",
        f"{d} CEO founder LinkedIn",
        f"{brand} leadership team executives",
        f"{brand} C-suite board directors",
        f"site:linkedin.com/in {brand} CEO",
        f"site:linkedin.com/in {brand} CFO",
        f"{brand} chief executive chief financial officer",
        f"linkedin.com {brand} CEO",
        f"{brand} about us leadership team",
    ]
    all_results = []
    seen_urls = set()
    linkedin_first = []  # Prioritize LinkedIn profiles so they appear at top of context
    for q in queries:
        results = _tavily_search(q, max_results=10, search_depth="advanced", timeout=30)
        for r in results:
            if not r.get("content"):
                continue
            url = (r.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            if "linkedin.com" in url:
                linkedin_first.append(r)
            else:
                all_results.append(r)
        if len(linkedin_first) + len(all_results) >= max_total_results:
            break
    # Put LinkedIn results first so the model and brief get real profile links upfront
    combined = linkedin_first + all_results
    return _format_results(combined[:max_total_results], max_items=max_total_results)


def search_web_for_company_contacts(domain, max_results=30):
    """
    Search the web for C-level/executive contacts only (CEO, CFO, etc.). No editors.
    Never depend on Notion for SPOCs — research from web only.
    """
    return power_scrape_company_spocs(domain, max_total_results=max_results)
