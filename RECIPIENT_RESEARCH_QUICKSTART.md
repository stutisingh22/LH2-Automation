# Recipient Research - Quick Start Guide

## What is it?

The Recipient Research feature automatically gathers social media and online presence data for email recipients in the Strategist tab. This helps personalize outreach by understanding what topics the recipient is interested in, what they post about, and their recent professional activity.

## How to use it

### Automatic Research

1. Go to the **Strategist** tab
2. Select a company and contact (recipient must have an email)
3. The system automatically researches the recipient when you select them
4. A collapsible section appears: **"📊 Recipient Brief: [Name]"**
5. Click to expand and see:
   - Quick context summary
   - Recent LinkedIn topics
   - Twitter activity (if available)
   - Recent blog posts/articles

### Manual Refresh

If you want to update the research (e.g., after 7+ days):
1. Expand the Recipient Brief section
2. Click **"🔄 Refresh Research"**
3. Wait 8-12 seconds for fresh data

### AI Integration

The recipient research automatically feeds into:
- **Email drafts**: AI references their interests and recent activity
- **Outreach sequences**: Personalized based on their topics
- **Strategy suggestions**: Tailored to their professional focus

## What data is collected?

### LinkedIn
- Recent posts and topics they discuss
- Professional focus areas
- Engagement style (active/moderate/minimal)

### Twitter/X
- Twitter handle (if found)
- Topics they tweet about
- Activity level

### Blog Posts
- Articles they've authored
- Topics they write about
- Publication dates

## Privacy & Compliance

✅ **Only public information** - No private data scraping
✅ **AI-based inference** - Uses Anthropic Claude, not direct scraping
✅ **Cached for 7 days** - Reduces redundant requests
✅ **Professional focus only** - No personal/sensitive data

## Technical Details

### Storage
Research is saved in the database (`Contact.extra_data['recipient_research']`) with a 7-day cache TTL.

### Performance
- **Initial research**: 8-12 seconds
- **Cached results**: Instant
- **Refresh**: 8-12 seconds

### Caching Rules (following .cursorrules)
- ✅ Research function is **NEVER cached** (external AI calls)
- ✅ Uses `st.session_state` for progress tracking
- ✅ Only caches database reads of saved research
- ✅ Clears UI cache after saving new research

## Troubleshooting

### "No research available"
- **Cause**: Contact has no email address
- **Fix**: Add email to contact record, then click "🔍 Research Recipient"

### Research seems outdated
- **Cause**: Cached data older than 7 days but auto-refresh didn't trigger
- **Fix**: Click "🔄 Refresh Research" button

### Research failed
- **Cause**: AI API error or rate limiting
- **Fix**: Wait a moment and try "🔄 Refresh Research" again

## Example Output

```
📊 Recipient Brief: Jane Smith · Updated 2 days ago

Quick Context: VP of Marketing with 10+ years in B2B SaaS. 
Recently discussing AI adoption in content operations and 
scaling editorial teams.

Recent LinkedIn Focus:
• AI tools for content teams
• Scaling content operations without headcount
• Marketing automation and workflow optimization

Twitter (@janesmith): AI, SaaS marketing, content strategy

Recent Content:
• How We 3x'd Content Output with AI · Content Operations
• The Future of B2B Marketing · Thought Leadership
```

## Benefits

1. **Personalization**: Reference specific topics they care about
2. **Credibility**: Show you've done research on them specifically
3. **Relevance**: Connect LH2's offerings to their current challenges
4. **Higher response rates**: Personalized emails get 2-3x more replies
5. **Time savings**: Automated research vs. manual LinkedIn/Twitter stalking

## What's Next?

Future enhancements planned:
- Real-time job change detection
- Sentiment analysis of recent posts
- Mutual LinkedIn connections
- Company news relevant to the recipient
- Engagement tracking over time
