# Recipient Research Feature - Technical Specification

## Overview
This feature adds automatic social research for email recipients in the Strategist tab. When a user selects a recipient, the system automatically gathers their online presence data (LinkedIn posts, Twitter activity, company blog posts) and presents it as a brief profile to inform email strategy.

## Feature Requirements

### Trigger & Input
- **Automatic trigger**: Research begins when recipient is selected in the TO dropdown
- **Input method**: Recipient email from the contacts database
- **Backtracking**: From email, extract name, company, and designation from existing contact record

### Data Sources (Priority Order)
1. **LinkedIn**: Posts, activity, about section, recent engagement
2. **Twitter/X**: Recent tweets, interests, engagement patterns
3. **Company blog posts**: Articles authored by the recipient (if available)

### Storage
- Research results saved to database (`Contact.extra_data['recipient_research']`)
- Cached for reuse across sessions
- Timestamp tracking for refresh logic (7-day TTL)

### UI/UX
- **Display location**: Collapsible section above email composition area
- **Design principle**: Clean, minimal, non-intrusive
- **Structure**:
  - Compact header with name, role, last updated timestamp
  - Expandable brief (3-5 key insights)
  - Recent activity highlights (max 3-4 items)
  - Manual refresh button for forced update

## Technical Architecture

### 1. Database Schema Extension

#### Contact Model (`models.py`)
The `Contact.extra_data` JSON field will store:
```json
{
  "recipient_research": {
    "last_updated": "2026-03-02T10:30:00Z",
    "linkedin": {
      "profile_url": "https://linkedin.com/in/...",
      "headline": "VP of Marketing at Acme Corp",
      "recent_posts": [
        {
          "date": "2026-02-28",
          "content": "Excited to announce...",
          "engagement": "120 likes, 15 comments"
        }
      ],
      "interests": ["B2B SaaS", "Content Marketing", "AI"]
    },
    "twitter": {
      "handle": "@username",
      "recent_tweets": [
        {
          "date": "2026-03-01",
          "content": "Just read an interesting piece on...",
          "engagement": "45 likes, 8 retweets"
        }
      ],
      "topics": ["Marketing", "AI", "Podcasts"]
    },
    "blog_posts": [
      {
        "title": "How We Scaled Content Production",
        "url": "https://...",
        "date": "2026-01-15"
      }
    ],
    "brief": "VP of Marketing with 10+ years in B2B SaaS. Active on LinkedIn discussing AI and content strategy. Recently focused on scaling content operations and AI adoption in marketing workflows."
  }
}
```

### 2. New Service: `recipient_research.py`

**Core Functions:**

```python
def research_recipient(contact_id: int, force_refresh: bool = False) -> dict:
    """
    Main entry point. Retrieves recipient research from cache or runs fresh research.
    
    Args:
        contact_id: Database ID of the contact
        force_refresh: If True, bypass cache and run fresh research
    
    Returns:
        dict with keys: linkedin, twitter, blog_posts, brief, last_updated
    """

def _should_refresh_research(contact: dict) -> bool:
    """Check if research is stale (>7 days old) or missing."""

def _scrape_linkedin_data(linkedin_url: str, name: str) -> dict:
    """
    Scrape LinkedIn data using Anthropic AI with web search capability.
    Returns: {profile_url, headline, recent_posts, interests}
    """

def _scrape_twitter_data(name: str, company_domain: str) -> dict:
    """
    Scrape Twitter/X data using Anthropic AI.
    Returns: {handle, recent_tweets, topics}
    """

def _find_blog_posts(name: str, company_domain: str) -> list:
    """
    Search for blog posts authored by the recipient.
    Returns: List of {title, url, date}
    """

def _generate_recipient_brief(research_data: dict) -> str:
    """
    Use Anthropic AI to synthesize research into a 2-3 sentence brief.
    """
```

**Implementation Strategy:**

Since the codebase uses **Anthropic Claude for all research** (no external search APIs), the recipient research will leverage Claude's web knowledge and reasoning:

1. **LinkedIn Research**: Pass the recipient's LinkedIn URL (if available) or name+company to Claude with a prompt requesting:
   - Recent posts and themes
   - Professional interests
   - Headline and current role focus

2. **Twitter Research**: Pass name and company to Claude requesting:
   - Likely Twitter handle
   - Recent tweet themes (based on public knowledge)
   - Professional topics of interest

3. **Blog Post Research**: Pass name and company domain to Claude requesting:
   - Articles likely authored by this person
   - Key topics they write about

4. **Brief Generation**: Combine all research data and ask Claude to synthesize into a 2-3 sentence actionable brief for email personalization.

**Research Prompt Template:**

```python
RECIPIENT_RESEARCH_PROMPT = """You are conducting social research on an email recipient to help personalize outreach.

RECIPIENT DETAILS:
Name: {name}
Role: {role}
Company: {company_name} ({company_domain})
LinkedIn: {linkedin_url}
Email: {email}

RESEARCH OBJECTIVES:
1. LinkedIn Activity: Find recent posts, comments, or engagement patterns. What topics do they discuss?
2. Professional Interests: Based on their role and company, what are their likely priorities and pain points?
3. Twitter/X Presence: Do they have a public Twitter account? If so, what do they tweet about?
4. Content Authorship: Have they written blog posts, articles, or thought leadership pieces?

OUTPUT FORMAT:
Return a JSON object with this structure:
{{
  "linkedin": {{
    "recent_activity": ["topic 1", "topic 2", "topic 3"],
    "professional_focus": "1-2 sentence summary",
    "engagement_style": "active/moderate/minimal"
  }},
  "twitter": {{
    "handle": "@username or null",
    "topics": ["topic 1", "topic 2"],
    "activity_level": "active/moderate/minimal"
  }},
  "blog_posts": [
    {{"title": "Article title", "topic": "Main theme", "date": "approx date if known"}}
  ],
  "brief": "2-3 sentence synthesis for email personalization"
}}

Important:
- Base insights on your knowledge of publicly available information
- If specific data is unavailable, infer likely interests based on role and industry
- Focus on actionable insights for email personalization
- Be concise but specific
"""
```

### 3. Strategist UI Updates (`pages/strategist.py`)

**Integration Points:**

1. **After recipient selection** (line ~130):
   - Trigger `research_recipient(contact_id)` 
   - Store result in `st.session_state.recipient_research`

2. **Display section** (insert after line ~186, before quick actions):

```python
# ── Recipient Research Brief ──
st.markdown("---")
if st.session_state.get("recipient_research"):
    research = st.session_state.recipient_research
    
    with st.expander(
        f"📊 Recipient Brief: {ct.get('name', 'Contact')} · "
        f"Updated {_format_time_ago(research.get('last_updated'))}",
        expanded=False
    ):
        brief = research.get("brief", "")
        if brief:
            st.markdown(f"**Quick Context:** {brief}")
        
        # LinkedIn section
        linkedin_data = research.get("linkedin", {})
        if linkedin_data.get("recent_activity"):
            st.markdown("**Recent LinkedIn Focus:**")
            for topic in linkedin_data["recent_activity"][:3]:
                st.markdown(f"• {topic}")
        
        # Twitter section
        twitter_data = research.get("twitter", {})
        if twitter_data.get("handle") and twitter_data.get("topics"):
            st.markdown(f"**Twitter ({twitter_data['handle']}):**")
            st.markdown(f"Discusses: {', '.join(twitter_data['topics'][:3])}")
        
        # Blog posts
        blog_posts = research.get("blog_posts", [])
        if blog_posts:
            st.markdown("**Recent Content:**")
            for post in blog_posts[:2]:
                st.markdown(f"• [{post['title']}]({post.get('url', '#')}) · {post.get('topic', '')}")
        
        # Refresh button
        if st.button("🔄 Refresh Research", key="refresh_recipient_research"):
            with st.spinner("Researching recipient..."):
                fresh_research = research_recipient(ctid, force_refresh=True)
                st.session_state.recipient_research = fresh_research
                st.rerun()

# ── Quick actions + Sequence builder ──
# (existing code continues...)
```

**Loading State:**

```python
# When recipient changes (line ~130)
if to_id != ctid:
    select_contact(cid, to_id)
    # Trigger research in background
    if "recipient_research_loading" not in st.session_state:
        st.session_state.recipient_research_loading = True
    st.rerun()

# After rerun, load research
if st.session_state.get("recipient_research_loading"):
    with st.spinner("Researching recipient..."):
        st.session_state.recipient_research = research_recipient(ctid)
        st.session_state.recipient_research_loading = False
```

### 4. AI Context Integration

Update `get_strategist_response_stream()` in `ai_engine.py` to include recipient research in the system context:

```python
def get_strategist_response_stream(comp, ct, messages, prompt, user_id, sid, research_notes, sender_email=None, product_key=None, recipient_research=None):
    """
    Added parameter: recipient_research (dict)
    """
    # ... existing code ...
    
    # Add recipient context to system message
    if recipient_research:
        brief = recipient_research.get("brief", "")
        linkedin_topics = recipient_research.get("linkedin", {}).get("recent_activity", [])
        twitter_topics = recipient_research.get("twitter", {}).get("topics", [])
        
        context_parts.append(f"""
RECIPIENT SOCIAL RESEARCH:
{ct.get('name', 'Contact')} recently engaged with: {', '.join(linkedin_topics[:3])}.
Brief: {brief}
Twitter interests: {', '.join(twitter_topics[:2]) if twitter_topics else 'N/A'}

Use this context to personalize your email suggestions and outreach strategy.
""")
    
    # ... rest of function ...
```

### 5. Caching Strategy Compliance

**Following `.cursorrules`:**

✅ **NEVER cache the research function** (`research_recipient()`) - it calls external AI APIs
✅ **Use `st.session_state`** for progress tracking during research
✅ **Only cache database reads** - when fetching saved research from `Contact.extra_data`
✅ **Clear cache after writes** - call `clear_ui_cache()` after saving research

```python
# CORRECT pattern
def load_recipient_research_from_db(contact_id):
    """Fetch saved research from database - safe to cache."""
    @st.cache_data(ttl=60)
    def _cached_load(cid):
        contact = get_contact(cid)
        return contact.get("extra_data", {}).get("recipient_research")
    return _cached_load(contact_id)

# LIVE research - NOT cached
def research_recipient(contact_id, force_refresh=False):
    """Live AI research - NEVER cached."""
    # Check if refresh needed
    if not force_refresh:
        existing = load_recipient_research_from_db(contact_id)
        if existing and not _should_refresh_research(existing):
            return existing
    
    # Run fresh research (AI calls - always live)
    contact = get_contact(contact_id)
    research_data = _run_recipient_research_ai(contact)
    
    # Save to database
    save_recipient_research(contact_id, research_data)
    clear_ui_cache()  # Ensure cached reads are refreshed
    
    return research_data
```

## Implementation Checklist

### Phase 1: Core Infrastructure
- [ ] Create `recipient_research.py` with core functions
- [ ] Add database helper functions in `db_ops.py`:
  - `get_recipient_research(contact_id)`
  - `save_recipient_research(contact_id, research_data)`
- [ ] Write unit tests for research data structure

### Phase 2: AI Research Logic
- [ ] Implement `_run_recipient_research_ai()` with Anthropic
- [ ] Create research prompt template
- [ ] Test research quality with sample contacts
- [ ] Add error handling and fallbacks

### Phase 3: UI Integration
- [ ] Update `pages/strategist.py` with research section
- [ ] Add loading state and progress indicators
- [ ] Style the expander section for minimal UI weight
- [ ] Add manual refresh button

### Phase 4: Context Integration
- [ ] Update `get_strategist_response_stream()` to accept recipient research
- [ ] Pass recipient research from UI to AI engine
- [ ] Test personalization improvements in email generation

### Phase 5: Polish & Testing
- [ ] Add time-ago formatting helper
- [ ] Implement 7-day TTL refresh logic
- [ ] Test with various recipient profiles
- [ ] Add error states for missing data
- [ ] Performance testing (research should complete in <10s)

## UI Design Mockup

```
┌─────────────────────────────────────────────────────────┐
│ FROM: sender@lh2.com  TO: recipient@company.com        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ ▶ 📊 Recipient Brief: John Smith · Updated 2 days ago   │
│   [When expanded:]                                       │
│   Quick Context: VP of Marketing with 10+ years in B2B  │
│   SaaS. Recently discussing AI adoption in content ops. │
│                                                          │
│   Recent LinkedIn Focus:                                 │
│   • AI tools for content teams                          │
│   • Scaling content operations                          │
│   • Marketing automation trends                         │
│                                                          │
│   Twitter (@johnsmith): AI, SaaS marketing, podcasts    │
│                                                          │
│   Recent Content:                                        │
│   • How We Scaled Content Production · Marketing        │
│                                                          │
│   [🔄 Refresh Research]                                 │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ [💬 LinkedIn] [📧 Email] [⏰ Timing] [📋 Sequence]     │
└─────────────────────────────────────────────────────────┘
```

## Performance Considerations

1. **Research Duration**: 8-12 seconds typical (AI inference time)
2. **Caching**: 7-day TTL reduces redundant research
3. **Async Loading**: Research loads in background while user views other data
4. **Graceful Degradation**: If research fails, strategist still functions normally

## Privacy & Compliance

- Only scrape **publicly available** information
- No storage of sensitive personal data
- Research focuses on professional activity only
- Users can refresh or clear research data at any time
- Complies with LinkedIn, Twitter TOS (no direct scraping, AI-based inference only)

## Future Enhancements

1. **Real-time signals**: Track recipient job changes, company news
2. **Sentiment analysis**: Analyze tone of recent posts
3. **Mutual connections**: Find shared LinkedIn connections
4. **Content recommendations**: Suggest articles recipient would find relevant
5. **Engagement tracking**: Track recipient's response to previous outreach

## Success Metrics

- **Research completion rate**: >95% of recipients successfully researched
- **Email personalization improvement**: Track mention of recipient-specific topics
- **User adoption**: % of strategists using recipient research in sessions
- **Response rate improvement**: A/B test emails with/without recipient research context
