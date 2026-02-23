"""
web_search.py — Stub for person/lead research.

Web research is done by Anthropic (Claude) only; no external search API.
These helpers return None so callers pass no pre-fetched results to the AI.
"""


def search_web_for_person(linkedin_url=None, email=None, company_domain=None, max_results=6):
    """No-op: person research is done by Anthropic. Returns None."""
    return None


def search_web_for_person_by_name(person_name, company_domain=None, company_name=None, max_results=10):
    """No-op: person research is done by Anthropic. Returns None."""
    return None


def research_person_from_web(name=None, linkedin_url=None, email=None, company_domain=None, company_name=None, max_results=10):
    """No-op: person research is done by Anthropic. Returns None."""
    return None
