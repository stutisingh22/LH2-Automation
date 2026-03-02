"""
Cache Registry - Surgical Cache Invalidation for LH2 Platform

This module provides targeted cache invalidation grouped by domain,
avoiding global cache clearing that affects unrelated UI components.

Architecture Principle:
- External APIs = NEVER cached
- Database reads = cached with TTL
- Writes = invalidate ONLY affected cache groups

Cache Groups:
- spoc: SPOC contacts and tracking
- research: Research briefs, strategic summaries
- pipeline: Pipeline statistics, contact status aggregates
- company: Company profiles and metadata
- contact: Contact lists and details
- action: Action history and timelines
"""

# Cache groups: domain-based organization for surgical invalidation
CACHE_GROUPS = {
    "spoc": [],       # SPOC tracking and contacts
    "research": [],   # Strategic briefs, research snapshots
    "pipeline": [],   # Pipeline stats, status aggregates
    "company": [],    # Company profiles and lists
    "contact": [],    # Contact lists and details
    "action": [],     # Action history and timelines
}


def register_cache(group_name, func):
    """
    Register a cached function to a cache group.
    
    This allows targeted invalidation when data in that domain changes,
    instead of clearing all caches globally.
    
    Args:
        group_name: One of CACHE_GROUPS keys
        func: Cached function with .clear() method
        
    Usage:
        @st.cache_data(ttl=60)
        def load_companies():
            return get_all_companies()
        
        register_cache("company", load_companies)
    """
    if group_name not in CACHE_GROUPS:
        raise ValueError(f"Unknown cache group: {group_name}. Valid groups: {list(CACHE_GROUPS.keys())}")
    
    if func not in CACHE_GROUPS[group_name]:
        CACHE_GROUPS[group_name].append(func)


def invalidate_group(group_name):
    """
    Invalidate all caches in a specific group.
    
    Use this instead of clear_ui_cache() or st.cache_data.clear()
    for surgical invalidation that doesn't affect unrelated UI.
    
    Args:
        group_name: Cache group to invalidate
        
    Usage:
        # After updating contact status
        update_contact_status(contact_id, "replied_positive")
        invalidate_group("contact")
        invalidate_group("pipeline")  # Status affects pipeline stats
    """
    if group_name not in CACHE_GROUPS:
        return  # Silently ignore unknown groups
    
    for func in CACHE_GROUPS[group_name]:
        try:
            func.clear()
        except Exception:
            # Ignore errors (function might not have .clear() or already cleared)
            pass


def invalidate_multiple(*group_names):
    """
    Invalidate multiple cache groups at once.
    
    Use when a write operation affects multiple domains.
    
    Args:
        *group_names: Variable number of group names
        
    Usage:
        # Contact status update affects both contact and pipeline
        invalidate_multiple("contact", "pipeline")
    """
    for group_name in group_names:
        invalidate_group(group_name)


def get_registered_caches(group_name):
    """
    Get list of registered cache functions for a group.
    
    Useful for debugging and verification.
    
    Args:
        group_name: Cache group name
        
    Returns:
        List of cached function objects
    """
    return CACHE_GROUPS.get(group_name, [])


def clear_all_caches():
    """
    Nuclear option: clear ALL registered caches.
    
    ⚠️ Use sparingly - prefer invalidate_group() for surgical invalidation.
    Only use after migrations, schema changes, or system-wide updates.
    """
    for group_name in CACHE_GROUPS:
        invalidate_group(group_name)
    
    # Also refresh PostgreSQL materialized views if available
    try:
        from db_ops import refresh_pipeline_stats
        refresh_pipeline_stats()
    except Exception:
        pass
