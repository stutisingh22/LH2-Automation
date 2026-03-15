"""
Search / discovery: LinkedIn lookup, ownership verifier. Implementation: .linkedin_lookup, .ownership_verifier
"""

from .linkedin_lookup import (
    text_search,
    extract_spoc_table_contacts,
    run_linkedin_lookup_for_contacts,
    get_linkedin_url_for_contact,
)
from .ownership_verifier import verify_parent_from_web

__all__ = [
    "text_search",
    "extract_spoc_table_contacts",
    "run_linkedin_lookup_for_contacts",
    "get_linkedin_url_for_contact",
    "verify_parent_from_web",
]
