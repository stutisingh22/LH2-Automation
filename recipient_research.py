"""
recipient_research.py — Social research for email recipients

Automatically gathers online presence data (LinkedIn, Twitter, blog posts) 
for recipients to inform email personalization in the Strategist tab.

Research is conducted by Anthropic Claude only; no external scraping APIs.
Results are cached in Contact.extra_data with 7-day TTL.
"""

import json
import logging
from datetime import datetime, timedelta
from anthropic import Anthropic

from anthropic_limiter import create_with_retry
from config import ANTHROPIC_API_KEY
from db_ops import get_contact, update_contact_extra_data

log = logging.getLogger(__name__)

# Research model
RESEARCH_MODEL = "claude-sonnet-4-5"
RESEARCH_MAX_TOKENS = 4000
RESEARCH_CACHE_TTL_DAYS = 7


# ══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def research_recipient(contact_id, force_refresh=False):
    """
    Main entry point. Retrieves recipient research from cache or runs fresh research.
    
    IMPORTANT: This function calls external AI APIs and must NEVER be cached with @st.cache_data.
    Use st.session_state for progress tracking only.
    
    Args:
        contact_id: Database ID of the contact
        force_refresh: If True, bypass cache and run fresh research
    
    Returns:
        dict with keys: linkedin, twitter, blog_posts, brief, last_updated, status
        Returns None if contact not found or research fails
    """
    contact = get_contact(contact_id)
    if not contact:
        log.warning("Contact %s not found for recipient research", contact_id)
        return None
    
    # Check if we should refresh
    if not force_refresh:
        existing = _get_cached_research(contact)
        if existing and not _should_refresh_research(existing):
            log.info("Using cached recipient research for contact %s", contact_id)
            return existing
    
    # Run fresh research
    log.info("Running fresh recipient research for contact %s", contact_id)
    research_data = _run_recipient_research_ai(contact)
    
    if research_data:
        # Save to database
        _save_recipient_research(contact_id, research_data)
        return research_data
    else:
        log.error("Recipient research failed for contact %s", contact_id)
        return None


# ══════════════════════════════════════════════════════════════
# CACHE MANAGEMENT
# ══════════════════════════════════════════════════════════════

def _get_cached_research(contact):
    """Extract cached research from contact's extra_data."""
    extra_data = contact.get("extra_data") or {}
    return extra_data.get("recipient_research")


def _should_refresh_research(research_data):
    """Check if research is stale (>7 days old) or missing key fields."""
    if not research_data:
        return True
    
    # Check timestamp
    last_updated = research_data.get("last_updated")
    if not last_updated:
        return True
    
    try:
        updated_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        age = datetime.utcnow() - updated_dt.replace(tzinfo=None)
        if age > timedelta(days=RESEARCH_CACHE_TTL_DAYS):
            log.info("Research is %d days old, refreshing", age.days)
            return True
    except (ValueError, AttributeError) as e:
        log.warning("Could not parse last_updated timestamp: %s", e)
        return True
    
    # Check if research has minimum required data
    if not research_data.get("brief"):
        return True
    
    return False


def _save_recipient_research(contact_id, research_data):
    """Save research data to contact's extra_data field."""
    try:
        contact = get_contact(contact_id)
        if not contact:
            log.error("Cannot save research: contact %s not found", contact_id)
            return False
        
        extra_data = contact.get("extra_data") or {}
        extra_data["recipient_research"] = research_data
        
        update_contact_extra_data(contact_id, extra_data)
        log.info("Saved recipient research for contact %s", contact_id)
        
        # Clear UI cache (following .cursorrules)
        try:
            from services.ui_data_loader import clear_ui_cache
            clear_ui_cache()
        except ImportError:
            pass  # ui_data_loader may not exist yet
        
        return True
    except Exception as e:
        log.error("Failed to save recipient research: %s", e)
        return False


# ══════════════════════════════════════════════════════════════
# AI RESEARCH
# ══════════════════════════════════════════════════════════════

def _run_recipient_research_ai(contact):
    """
    Run AI-powered recipient research.
    
    NEVER cache this function - it makes external API calls.
    
    Returns dict with research data or None on failure.
    """
    if not ANTHROPIC_API_KEY:
        log.warning("No Anthropic API key, skipping recipient research")
        return None
    
    # Extract contact details (use "or ''" so None never gets .strip())
    name = (contact.get("name") or "").strip()
    email = (contact.get("email") or "").strip()
    role = (contact.get("role") or "").strip()
    linkedin_url = (contact.get("linkedin") or "").strip()
    
    # Get company details
    from db_ops import get_company
    company_id = contact.get("company_id")
    company = get_company(company_id) if company_id else None
    company_name = (company.get("name") or "") if company else ""
    company_domain = (company.get("domain") or "") if company else ""
    
    if not name:
        log.warning("Contact has no name, cannot research")
        return None
    
    # Build research prompt
    prompt = _build_research_prompt(
        name=name,
        email=email,
        role=role,
        linkedin_url=linkedin_url,
        company_name=company_name,
        company_domain=company_domain,
    )
    
    try:
        # Call Anthropic AI
        response = create_with_retry(
            model=RESEARCH_MODEL,
            max_tokens=RESEARCH_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        
        result_text = (response.content[0].text or "").strip()
        
        # Parse JSON response
        research_data = _parse_research_response(result_text)
        
        if research_data:
            research_data["last_updated"] = datetime.utcnow().isoformat() + "Z"
            research_data["status"] = "completed"
            log.info("Recipient research completed for %s", name)
            return research_data
        else:
            log.error("Failed to parse research response for %s", name)
            return None
            
    except Exception as e:
        log.error("AI research failed for %s: %s", name, e)
        return None


def _build_research_prompt(name, email, role, linkedin_url, company_name, company_domain):
    """Build the AI research prompt. Demands real post-level data or minimal honest output."""
    return f"""You are researching an email recipient. Your output must be EITHER (A) specific post/tweet/article content you actually know, OR (B) minimal honest output when you only know name/role/company.

RECIPIENT:
Name: {name}
Role: {role or "Unknown"}
Company: {company_name} ({company_domain})
LinkedIn: {linkedin_url or "Not provided"}
Email: {email or "Not provided"}

CRITICAL RULE — No job-title filler:
- If you know SPECIFIC posts, tweets, or articles by this person (what they said, wrote, or shared): include them as snippets in recent_posts / recent_tweets / blog_posts and write a brief that cites that activity.
- If you ONLY know their name, role, and company (no specific posts/tweets/articles): you MUST output the MINIMAL template below. Do NOT invent "themes" or "priorities" from their job title. Do NOT write "likely prioritizes...", "would be most receptive to...", "focuses on internet culture/digital media trends/online communities" or similar. That is forbidden when you have no actual post-level data.

When you have NO specific posts/tweets/articles, your output MUST be:
- linkedin.recent_posts: []
- linkedin.recent_activity: []   (do NOT list "internet culture", "digital media trends", "online communities" or other job-inferred topics)
- linkedin.professional_focus: null
- twitter.handle: null
- twitter.recent_tweets: []
- twitter.topics: []
- blog_posts: []
- brief: "No specific recent posts found. " + one short factual sentence only, e.g. "Editor at Daily Dot." or "Works at {company_name}." No pitches, no "prioritizes", no "receptive to".

When you HAVE specific content: put real snippets in recent_posts/recent_tweets, real themes tied to those in recent_activity/topics, and a brief that leads with that content.

OUTPUT FORMAT — Return ONLY valid JSON, no markdown or code fences:

{{
  "linkedin": {{
    "recent_posts": [{{"snippet": "quote or close paraphrase of actual post", "date_if_known": "e.g. last week or null"}}],
    "recent_activity": [],
    "professional_focus": null,
    "engagement_style": "active/moderate/minimal"
  }},
  "twitter": {{
    "handle": null,
    "recent_tweets": [{{"snippet": "actual tweet or paraphrase", "date_if_known": "or null"}}],
    "topics": [],
    "activity_level": "active/moderate/minimal"
  }},
  "blog_posts": [{{"title": "Exact title", "topic": "theme", "date": null, "url": null}}],
  "brief": "No specific recent posts found. One short factual sentence only."
}}

Fill arrays and brief only when you have real content. Otherwise leave arrays empty and use the exact brief format above. Return ONLY the JSON object."""
    

def _parse_research_response(response_text):
    """Parse AI response into structured data."""
    try:
        # Try direct JSON parse first
        data = json.loads(response_text)
        
        # Validate structure
        required_keys = ["linkedin", "twitter", "blog_posts", "brief"]
        if all(key in data for key in required_keys):
            return data
        else:
            log.warning("Research response missing required keys")
            return None
            
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code blocks
        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return data
            except json.JSONDecodeError:
                pass
        
        # Try to find any JSON object in the response
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                return data
            except json.JSONDecodeError:
                pass
        
        log.error("Could not parse JSON from research response")
        return None


# ══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════

def get_research_age_days(research_data):
    """Get the age of research data in days."""
    if not research_data:
        return None
    
    last_updated = research_data.get("last_updated")
    if not last_updated:
        return None
    
    try:
        updated_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        age = datetime.utcnow() - updated_dt.replace(tzinfo=None)
        return age.days
    except (ValueError, AttributeError):
        return None


def format_time_ago(research_data):
    """Format last_updated as human-readable time ago."""
    age_days = get_research_age_days(research_data)
    
    if age_days is None:
        return "Unknown"
    elif age_days == 0:
        return "Today"
    elif age_days == 1:
        return "Yesterday"
    elif age_days < 7:
        return f"{age_days} days ago"
    elif age_days < 30:
        weeks = age_days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    else:
        months = age_days // 30
        return f"{months} month{'s' if months > 1 else ''} ago"
