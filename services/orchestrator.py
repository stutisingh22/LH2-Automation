"""
Orchestrator - Lightweight Write Operation Wrapper

This module wraps database write operations with surgical cache invalidation.
It enforces the UI → Service → DB boundary without moving business logic.

Architecture:
- Wraps ONLY write operations (create, update, delete)
- Delegates to db_ops for actual database work
- Invalidates ONLY affected cache groups
- Does NOT contain business logic
- Does NOT wrap read operations (use ui_data_loader for reads)

Usage:
    # In UI files (apps.py, ui.py)
    from services.orchestrator import update_contact_status_cached
    
    # Write operation with automatic cache invalidation
    update_contact_status_cached(contact_id, "replied_positive")
    st.rerun()
"""

from services.cache_registry import invalidate_group, invalidate_multiple
import db_ops


def update_contact_status_cached(contact_id, status):
    """
    Update contact status and invalidate affected caches.
    
    Invalidates: contact, pipeline (status affects pipeline stats)
    """
    result = db_ops.update_contact_status(contact_id, status)
    invalidate_multiple("contact", "pipeline")
    return result


def upsert_company_cached(domain, **kwargs):
    """
    Create or update company and invalidate company caches.
    
    Invalidates: company
    """
    result = db_ops.upsert_company(domain, **kwargs)
    invalidate_group("company")
    return result


def upsert_contact_cached(company_id, name, **kwargs):
    """
    Create or update contact and invalidate contact caches.
    
    Invalidates: contact
    """
    result = db_ops.upsert_contact(company_id, name, **kwargs)
    invalidate_group("contact")
    return result


def upsert_spoc_contact_cached(company_id, spoc_contact_id, name, **kwargs):
    """
    Create or update SPOC contact and invalidate SPOC caches.
    
    Invalidates: spoc
    """
    result = db_ops.upsert_spoc_contact(company_id, spoc_contact_id, name, **kwargs)
    invalidate_group("spoc")
    return result


def delete_spoc_contact_cached(spoc_contact_id):
    """
    Delete SPOC contact and invalidate SPOC caches.
    
    Invalidates: spoc
    """
    result = db_ops.delete_spoc_contact(spoc_contact_id)
    invalidate_group("spoc")
    return result


def log_action_cached(session_id, user_id, action_type, channel, description, **kwargs):
    """
    Log action and invalidate action caches.
    
    Invalidates: action
    """
    result = db_ops.log_action(session_id, user_id, action_type, channel, description, **kwargs)
    invalidate_group("action")
    return result


def update_action_cached(action_id, **kwargs):
    """
    Update action and invalidate action caches.
    
    Invalidates: action
    """
    result = db_ops.update_action(action_id, **kwargs)
    invalidate_group("action")
    return result


def merge_company_extra_data_cached(company_id, patch):
    """
    Merge company extra_data and invalidate company/research caches.
    
    Invalidates: company, research (research stored in extra_data)
    """
    result = db_ops.merge_company_extra_data(company_id, patch)
    invalidate_multiple("company", "research")
    return result


def save_session_summary_cached(session_id, summary, msg_count=0):
    """
    Save session summary (research brief) and invalidate research caches.
    
    Invalidates: research
    """
    result = db_ops.save_session_summary(session_id, summary, msg_count)
    invalidate_group("research")
    return result


# Re-export read operations without caching (use ui_data_loader for cached reads)
# These are convenience exports for when you need direct DB access in orchestrator layer

get_company = db_ops.get_company
get_contact = db_ops.get_contact
get_all_companies = db_ops.get_all_companies
get_contacts = db_ops.get_contacts
get_spoc_contacts_by_company = db_ops.get_spoc_contacts_by_company
get_session = db_ops.get_session
get_session_actions = db_ops.get_session_actions
