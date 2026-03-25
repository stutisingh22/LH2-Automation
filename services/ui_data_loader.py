"""
UI Data Loader - Performance Buffer for LH2 Platform UI

This module provides cached wrapper functions for database operations
to improve UI performance by avoiding direct db_ops calls in render loops.

Two caching tiers:
1. Fast-changing data (TTL=60s): Lists, stats, recent activity
2. Research snapshots (TTL=600s): Strategic briefs, company profiles, action history
   - Longer TTL optimized for Sydney-to-Singapore latency (200ms floor)
   - Only refreshed after explicit Deep Research cycles

Architecture Constraint: UI files should call these cached functions
instead of calling db_ops directly, especially in Streamlit loops.

Research vs. Display Boundary:
- These functions cache SAVED research (Display Rule ✅)
- NEVER cache live API calls like ai_engine.*, notion_sync.* (Active Rule ❌)
"""

import sys

# FastAPI/uvicorn entrypoints import this module at server startup. Streamlit's
# cache runtime is not available there and produces noisy warnings. When running
# under uvicorn we stub `st.cache_data` with a no-op decorator.
if any("uvicorn" in (a or "").lower() for a in sys.argv):
    def _cache_data(ttl=None, **kwargs):
        def decorator(fn):
            # Provide a .clear() method so cache invalidation code doesn't fail.
            if not hasattr(fn, "clear"):
                fn.clear = lambda: None  # type: ignore[attr-defined]
            return fn

        return decorator

    class _StStub:
        cache_data = staticmethod(_cache_data)

    st = _StStub()  # type: ignore[assignment]
else:
    import streamlit as st
from db_ops import (
    get_all_companies,
    get_company,
    get_spoc_contacts_by_company,
    get_recent_activity,
    get_all_actions,
    get_session,
    get_timeline_actions,
    search_companies,
)
from services.cache_registry import register_cache


# ── Cached Data Loaders ──────────────────────────────────────────

@st.cache_data(ttl=60)
def load_all_companies(exclude_advisor_shadows=True):
    """
    Load all companies with caching.
    
    Args:
        exclude_advisor_shadows: When True, exclude shadow companies for Media Houses children
        
    Returns:
        List of company dictionaries
    """
    return get_all_companies(exclude_advisor_shadows=exclude_advisor_shadows)

register_cache("company", load_all_companies)


@st.cache_data(ttl=60)
def load_spocs_by_company(company_id):
    """
    Load all SPOC-tagged contacts for a specific company with caching.
    
    Args:
        company_id: The company ID to fetch SPOC contacts for
        
    Returns:
        List of SPOC contact dictionaries
    """
    return get_spoc_contacts_by_company(company_id)

register_cache("spoc", load_spocs_by_company)


@st.cache_data(ttl=60)
def load_all_spocs():
    """
    Load all SPOC contacts across all companies with caching.
    
    This aggregates SPOC contacts from all companies to provide a
    comprehensive view without requiring per-company queries in loops.
    
    Returns:
        List of SPOC contact dictionaries with company information
    """
    companies = get_all_companies(exclude_advisor_shadows=True)
    all_spocs = []
    
    for company in companies:
        spocs = get_spoc_contacts_by_company(company["id"])
        # Enrich with company name for easier UI display
        for spoc in spocs:
            spoc["company_name"] = company["name"]
            all_spocs.append(spoc)
    
    return all_spocs

register_cache("spoc", load_all_spocs)


@st.cache_data(ttl=60)
def load_recent_actions(limit=200):
    """
    Load recent actions with caching.
    
    Uses get_all_actions which provides shared visibility and includes
    username information for better UI display.
    
    Args:
        limit: Maximum number of actions to retrieve (default: 200)
        
    Returns:
        List of action dictionaries with username information
    """
    return get_all_actions(limit=limit)

register_cache("action", load_recent_actions)


@st.cache_data(ttl=60)
def load_recent_activity(limit=20):
    """
    Load recent activity with caching (lighter-weight alternative).
    
    Args:
        limit: Maximum number of activities to retrieve (default: 20)
        
    Returns:
        List of recent activity dictionaries
    """
    return get_recent_activity(limit=limit)

register_cache("action", load_recent_activity)


# ── Media Houses (Advisor) cached loaders ─────────────────────────
# Reduces lag: use these instead of direct db_ops advisor calls in UI.

@st.cache_data(ttl=60)
def load_all_advisor_companies():
    """Cached list of all advisor (Media Houses) companies. Invalidate 'advisor' on sync/write."""
    from db_ops import get_all_advisor_companies
    return get_all_advisor_companies()


@st.cache_data(ttl=60)
def load_advisor_parents_with_children():
    """Cached parent→children groups for Media Houses. Invalidate 'advisor' on sync/write."""
    from db_ops import get_advisor_parents_with_children
    return get_advisor_parents_with_children()


@st.cache_data(ttl=60)
def load_advisor_company(advisor_id):
    """Cached single advisor company by id. Invalidate 'advisor' after merge_advisor_company_extra_data."""
    if advisor_id is None:
        return None
    from db_ops import get_advisor_company
    return get_advisor_company(advisor_id)


def _register_advisor_caches():
    register_cache("advisor", load_all_advisor_companies)
    register_cache("advisor", load_advisor_parents_with_children)
    register_cache("advisor", load_advisor_company)


_register_advisor_caches()


# ── Strategic Research Snapshots (TTL=600s for Sydney-Singapore latency) ──

@st.cache_data(ttl=600)
def load_strategic_brief(session_id):
    """
    Fetch the saved Claude research brief for a session.
    
    This is a 'Display' operation - loads SAVED research from the database.
    Longer TTL (10 minutes) optimized for Sydney-to-Singapore latency.
    
    Architecture: NEVER cache the live AI research itself (ai_engine.py functions).
    This only caches the SAVED results after research completes.
    
    Args:
        session_id: The session ID to fetch strategic summary for
        
    Returns:
        Dict with session data including strategic_summary, or None if not found
        
    Usage:
        # After running live research (NOT cached):
        result = intelligence_request(company_id, "deep research")
        
        # User saves it to database
        save_session_summary(session_id, result)
        
        # Now safe to display from cache:
        brief = load_strategic_brief(session_id)  # Fast cached read
    """
    session = get_session(session_id)
    if not session:
        return None
    
    return {
        "session_id": session.get("id"),
        "company_id": session.get("company_id"),
        "contact_id": session.get("contact_id"),
        "strategic_summary": session.get("strategic_summary"),
        "lead_score": session.get("lead_score"),
        "summary_msg_count": session.get("summary_msg_count"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
    }

register_cache("research", load_strategic_brief)


@st.cache_data(ttl=600)
def load_company_profile(domain):
    """
    Fetch enriched company metadata by domain.
    
    This is a 'Display' operation - loads SAVED enrichment data from the database.
    Longer TTL (10 minutes) for cross-region performance.
    
    Architecture: This caches saved company data. NEVER cache live enrichment
    calls like signalhire_enrichment.request_enrichment() or notion_sync.*.
    
    Args:
        domain: Company domain (e.g., "example.com")
        
    Returns:
        Dict with company data including extra_data enrichment, or None if not found
        
    Usage:
        # After live enrichment (NOT cached):
        enrichment = request_enrichment(linkedin_url)  # Live API call
        
        # Save to database
        merge_company_extra_data(company_id, enrichment)
        
        # Now safe to display from cache:
        profile = load_company_profile(domain)  # Fast cached read
    """
    # Search by domain (exact match preferred, but search handles variations)
    companies = search_companies(domain, exclude_advisor_shadows=True)
    
    if not companies:
        return None
    
    # Prefer exact domain match, fall back to first result
    company = None
    for c in companies:
        if c.get("domain", "").lower() == domain.lower():
            company = c
            break
    
    if not company:
        company = companies[0]
    
    return {
        "id": company.get("id"),
        "name": company.get("name"),
        "domain": company.get("domain"),
        "publisher_type": company.get("publisher_type"),
        "content_type": company.get("content_type"),
        "monthly_visits": company.get("monthly_visits"),
        "notion_page_id": company.get("notion_page_id"),
        "research_status": company.get("research_status"),
        "extra_data": company.get("extra_data", {}),
        "created_at": company.get("created_at"),
        "updated_at": company.get("updated_at"),
    }

register_cache("company", load_company_profile)


@st.cache_data(ttl=600)
def load_action_history(company_id, limit=100):
    """
    Fetch the timeline of research actions for a company.
    
    This is a 'Display' operation - loads SAVED action records from the database.
    Longer TTL (10 minutes) for cross-region performance.
    
    Architecture: This caches saved action history. NEVER cache live Gmail/Notion
    operations that create new actions.
    
    Args:
        company_id: The company ID to fetch action history for
        limit: Maximum number of actions to retrieve (default: 100)
        
    Returns:
        List of action dictionaries with timeline information
        
    Usage:
        # After completing actions (writes, NOT cached):
        log_action(session_id, user_id, "email", "gmail", "Sent intro")
        
        # Display from cache:
        history = load_action_history(company_id)  # Fast cached read
    """
    return get_timeline_actions(company_id=company_id, limit=limit)

register_cache("action", load_action_history)


# ── Cache Management ──────────────────────────────────────────────

def clear_ui_cache():
    """
    ⚠️ DEPRECATED: Use invalidate_group() for surgical invalidation.
    
    This function clears ALL caches and should only be used for:
    - Database migrations
    - Schema changes
    - System-wide updates
    
    For normal operations, use targeted invalidation:
    - After company update: invalidate_group("company")
    - After SPOC update: invalidate_group("spoc")
    - After research save: invalidate_group("research")
    
    Usage (new way):
        from services.cache_registry import invalidate_group
        
        # After updating company
        upsert_company(...)
        invalidate_group("company")  # Only affects company caches
    """
    from services.cache_registry import clear_all_caches
    clear_all_caches()


def force_refresh_research(session_id):
    """
    Deep Research Safety Valve - Clear research caches after Deep Research completes.
    
    This function should be called ONLY after a new Deep Research cycle completes
    to ensure the UI displays fresh results.
    
    Architecture: Use this after LIVE research (ai_engine.*, notion_sync.*, etc.)
    has completed and been SAVED to the database. Never cache the live research
    itself - only the saved results.
    
    Args:
        session_id: The session ID that just completed deep research
        
    Usage:
        # Step 1: Run live deep research (NOT cached)
        research_result = intelligence_request(company_id, "deep research")
        
        # Step 2: User reviews and saves
        if st.button("Save Research"):
            save_session_summary(session_id, research_result)
            
            # Step 3: Clear research caches so UI shows fresh data
            force_refresh_research(session_id)
            st.success("Research saved and cache refreshed!")
    """
    from services.cache_registry import invalidate_group
    
    # Surgical invalidation: only clear research caches
    invalidate_group("research")
    
    # Also invalidate action history (research creates actions)
    invalidate_group("action")
    
    # Optionally refresh pipeline stats if research affected contact statuses
    try:
        from db_ops import refresh_pipeline_stats
        refresh_pipeline_stats()
    except Exception:
        pass
