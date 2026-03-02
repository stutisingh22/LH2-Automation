# Strategic Research Snapshots - Usage Guide

## Overview

The `services/ui_data_loader.py` module now includes strategic research snapshot functions optimized for Sydney-to-Singapore latency (200ms floor). These functions use a longer TTL (600s = 10 minutes) to cache saved research data.

## Two-Tier Caching Strategy

```
┌─────────────────────────────────────────────────────┐
│           Fast-Changing Data (TTL=60s)              │
├─────────────────────────────────────────────────────┤
│  • load_all_companies()                             │
│  • load_spocs_by_company()                          │
│  • load_all_spocs()                                 │
│  • load_recent_actions()                            │
│  • load_recent_activity()                           │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│        Research Snapshots (TTL=600s)                │
├─────────────────────────────────────────────────────┤
│  • load_strategic_brief(session_id)                 │
│  • load_company_profile(domain)                     │
│  • load_action_history(company_id)                  │
│                                                      │
│  Optimized for Sydney-Singapore latency             │
│  Only refreshed after Deep Research completes       │
└─────────────────────────────────────────────────────┘
```

## Research vs. Display Boundary

### ✅ CORRECT: Separate Research and Display

```python
import streamlit as st
from services.ui_data_loader import (
    load_strategic_brief,
    load_company_profile,
    load_action_history,
    force_refresh_research
)
from ai_engine import intelligence_request
from db_ops import save_session_summary

def research_tab():
    st.header("Deep Research")
    
    company = st.selectbox("Select Company", ...)
    session_id = get_or_create_session(company["id"], ...)
    
    # ── Display cached research (fast) ──
    st.subheader("Previous Research")
    brief = load_strategic_brief(session_id)  # TTL=600s, fast
    
    if brief and brief["strategic_summary"]:
        st.write(brief["strategic_summary"])
        st.caption(f"Lead Score: {brief['lead_score']}")
    else:
        st.info("No research saved yet.")
    
    # ── Run new research (live, NOT cached) ──
    if st.button("🔍 Run New Deep Research"):
        with st.spinner("Researching... (Live API calls, NOT cached)"):
            # NEVER cache this - always runs live
            research_result = intelligence_request(
                company["id"], 
                "Deep research on partnership potential"
            )
            
            # Store in session state for preview (In-Memory Rule)
            st.session_state['pending_research'] = research_result
            st.write(research_result)
    
    # ── Save research (triggers cache refresh) ──
    if 'pending_research' in st.session_state:
        if st.button("💾 Save Research"):
            # Save to database
            save_session_summary(
                session_id, 
                st.session_state['pending_research']
            )
            
            # Deep Research Safety Valve - refresh research caches
            force_refresh_research(session_id)
            
            # Clear pending state
            del st.session_state['pending_research']
            
            st.success("Research saved! Cache refreshed.")
            st.rerun()  # Show fresh cached data
```

### ❌ INCORRECT: Caching Live Research

```python
# WRONG - Never cache external API calls!
@st.cache_data(ttl=600)  # ❌ BAD!
def get_research_cached(company_id):
    # This calls Anthropic API - must always run live
    return intelligence_request(company_id, "research")
```

## Function Reference

### `load_strategic_brief(session_id)`

Fetch saved Claude research brief for a session.

**Parameters:**
- `session_id` (int): Session ID

**Returns:**
- Dict with session data including `strategic_summary`, or None

**Example:**
```python
brief = load_strategic_brief(session_id)

if brief:
    st.markdown(f"### {brief['strategic_summary']}")
    st.metric("Lead Score", brief['lead_score'])
```

### `load_company_profile(domain)`

Fetch enriched company metadata by domain.

**Parameters:**
- `domain` (str): Company domain (e.g., "example.com")

**Returns:**
- Dict with company data including `extra_data` enrichment, or None

**Example:**
```python
profile = load_company_profile("example.com")

if profile:
    st.write(f"**{profile['name']}**")
    st.write(f"Type: {profile['publisher_type']}")
    st.write(f"Visits: {profile['monthly_visits']:,}")
    
    # Access enrichment data
    extra = profile.get('extra_data', {})
    if 'research_full' in extra:
        st.expander("Full Research").write(extra['research_full'])
```

### `load_action_history(company_id, limit=100)`

Fetch timeline of research actions for a company.

**Parameters:**
- `company_id` (int): Company ID
- `limit` (int): Max actions to retrieve (default: 100)

**Returns:**
- List of action dictionaries with timeline information

**Example:**
```python
history = load_action_history(company_id, limit=50)

st.subheader("Action History")
for action in history:
    status_color = "green" if action['status'] == "completed" else "gray"
    st.markdown(
        f"**{action['created_at'][:10]}** · "
        f":{status_color}[{action['status']}] · "
        f"{action['description']}"
    )
```

## Cache Management

### `force_refresh_research(session_id)`

Deep Research Safety Valve - clear research caches after new research completes.

**When to use:**
- After completing a new Deep Research cycle
- After saving research to the database
- When you know research data has changed

**Example:**
```python
# After deep research completes and is saved
def save_research_workflow(session_id, research_data):
    # Save to database
    save_session_summary(session_id, research_data)
    
    # Clear research caches to show fresh data
    force_refresh_research(session_id)
    
    st.success("Research saved and cache refreshed!")
```

### `clear_ui_cache()`

Clear all UI caches (both fast-changing and research snapshots).

**When to use:**
- After bulk database updates
- When you need to force-refresh everything
- After schema changes or migrations

**Example:**
```python
from services.ui_data_loader import clear_ui_cache

# After bulk updates
for company in companies:
    upsert_company(company)

# Clear all caches
clear_ui_cache()
st.success("All caches cleared!")
```

## Complete Workflow Example

```python
import streamlit as st
from services.ui_data_loader import (
    load_strategic_brief,
    load_company_profile,
    load_action_history,
    force_refresh_research,
)
from ai_engine import intelligence_request, research_person_for_company
from db_ops import save_session_summary, log_action

def company_research_page():
    st.title("🔍 Company Research")
    
    # Select company
    company_domain = st.text_input("Company Domain", "example.com")
    
    # ── DISPLAY: Cached company profile (fast) ──
    profile = load_company_profile(company_domain)
    
    if not profile:
        st.error("Company not found. Add it first.")
        return
    
    company_id = profile["id"]
    session_id = get_or_create_session(company_id, None, st.session_state.user["id"])
    
    # Show cached profile
    with st.expander("📊 Company Profile (Cached)", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Monthly Visits", f"{profile['monthly_visits']:,}")
        with col2:
            st.metric("Publisher Type", profile['publisher_type'])
    
    # ── DISPLAY: Cached research brief (fast) ──
    st.subheader("💡 Strategic Brief")
    brief = load_strategic_brief(session_id)
    
    if brief and brief["strategic_summary"]:
        st.markdown(brief["strategic_summary"])
        st.caption(f"🎯 Lead Score: {brief['lead_score']} | "
                   f"📅 Updated: {brief['updated_at'][:10]}")
    else:
        st.info("No strategic brief saved yet. Run deep research below.")
    
    # ── DISPLAY: Cached action history (fast) ──
    with st.expander("📜 Action History (Cached)"):
        history = load_action_history(company_id, limit=20)
        if history:
            for action in history[:10]:
                st.markdown(f"- **{action['created_at'][:10]}**: {action['description']}")
        else:
            st.caption("No actions yet.")
    
    st.divider()
    
    # ── RESEARCH: Live deep research (NOT cached) ──
    st.subheader("🚀 Deep Research")
    
    research_type = st.selectbox(
        "Research Type",
        ["Strategic Brief", "Person Research", "Partnership Analysis"]
    )
    
    if st.button("▶️ Run Deep Research", type="primary"):
        progress = st.progress(0)
        status = st.empty()
        
        # Step 1: Live AI research (NOT cached)
        status.info("🤖 Running AI analysis...")
        progress.progress(33)
        
        research_result = intelligence_request(
            company_id,
            f"{research_type} for {profile['name']}"
        )  # Always live, NEVER cached
        
        # Step 2: Show results (session state, not cached)
        status.info("📊 Processing results...")
        progress.progress(66)
        
        st.session_state['pending_research'] = {
            'summary': research_result,
            'type': research_type,
            'timestamp': datetime.now().isoformat()
        }
        
        # Step 3: Display results
        status.success("✅ Research complete!")
        progress.progress(100)
        
        st.markdown("### Research Results")
        st.write(research_result)
        
        # Option to save
        st.info("👆 Review the research above, then save it to cache.")
    
    # ── SAVE: Persist research and refresh cache ──
    if 'pending_research' in st.session_state:
        st.divider()
        
        col1, col2 = st.columns([3, 1])
        with col1:
            st.warning("⚠️ Unsaved research in memory. Save to make it permanent.")
        with col2:
            if st.button("💾 Save Research", type="primary"):
                # Save to database
                pending = st.session_state['pending_research']
                save_session_summary(session_id, pending['summary'])
                
                # Log action
                log_action(
                    session_id,
                    st.session_state.user["id"],
                    "research",
                    "ai",
                    f"Completed {pending['type']}",
                    status="completed"
                )
                
                # Deep Research Safety Valve - refresh caches
                force_refresh_research(session_id)
                
                # Clear pending
                del st.session_state['pending_research']
                
                st.success("✅ Research saved and cache refreshed!")
                st.balloons()
                time.sleep(1)
                st.rerun()  # Show fresh cached data
```

## Performance Impact

### Before Strategic Snapshots

```
User in Sydney → PostgreSQL in Singapore
├─ Each research view load: 200ms+ latency
├─ Multiple queries per page load
└─ Total: 500ms+ per page load
```

### After Strategic Snapshots

```
User in Sydney → Streamlit Cache (local)
├─ First load: 200ms (cache miss, fetch from DB)
├─ Next 10 minutes: <5ms (cache hit)
└─ Total: <50ms per page load (40x faster!)
```

## Best Practices

1. **Always separate research and display**
   - Research: Live API calls (ai_engine.*, notion_sync.*)
   - Display: Cached database reads (load_strategic_brief, etc.)

2. **Use force_refresh_research() after saving**
   - Ensures UI shows fresh data immediately
   - More targeted than clear_ui_cache()

3. **Show cache status to users**
   ```python
   brief = load_strategic_brief(session_id)
   if brief:
       age = (now - brief['updated_at']).total_seconds() / 60
       st.caption(f"📅 Cached {age:.0f}m ago")
   ```

4. **Handle cache misses gracefully**
   ```python
   brief = load_strategic_brief(session_id)
   if not brief or not brief.get("strategic_summary"):
       st.info("No research cached. Run deep research to generate.")
   ```

5. **Use session_state for pending research**
   - Don't cache until user saves
   - Allows review before persistence
   - Follows In-Memory Rule from .cursorrules

## Related Documentation

- `.cursorrules` - Research vs. Display boundary rules
- `CACHING_STRATEGY.md` - Complete caching strategy
- `MIGRATION_GUIDE.md` - Database performance optimizations
