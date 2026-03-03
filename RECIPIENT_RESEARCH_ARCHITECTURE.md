# Recipient Research - System Architecture Diagram

## Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        STRATEGIST TAB (UI)                      │
│                     pages/strategist.py                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ 1. User selects recipient
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    RECIPIENT RESEARCH SERVICE                   │
│                     recipient_research.py                       │
│                                                                 │
│  research_recipient(contact_id, force_refresh=False)           │
│    ├─ Check cache (7-day TTL)                                  │
│    ├─ If stale/missing → Run fresh research                    │
│    └─ Return research data                                     │
└─────────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┴─────────────────┐
            │                                   │
            ↓ (if fresh research needed)        ↓ (if cached)
┌──────────────────────────┐         ┌──────────────────────────┐
│   ANTHROPIC CLAUDE API   │         │      DATABASE READ       │
│                          │         │       db_ops.py          │
│ _run_recipient_research  │         │                          │
│    ↓                     │         │  get_contact(id)         │
│ LinkedIn analysis        │         │  extra_data[            │
│ Twitter discovery        │         │   'recipient_research'] │
│ Blog post search         │         │                          │
│ Brief synthesis          │         │  ✅ CACHED (60s)        │
│                          │         │                          │
│ ❌ NEVER CACHED          │         └──────────────────────────┘
└──────────────────────────┘                      │
            │                                     │
            │ 2. Research complete (JSON)         │
            ↓                                     │
┌──────────────────────────────────────────────────┐            │
│            DATABASE WRITE                        │            │
│            db_ops.py                             │            │
│                                                  │            │
│  update_contact_extra_data(contact_id, data)    │            │
│    └─ Save to Contact.extra_data                │            │
│    └─ Clear UI cache                            │            │
└──────────────────────────────────────────────────┘            │
                              │                                  │
                              └──────────────┬───────────────────┘
                                             │
                              3. Display research
                                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                      STRATEGIST UI SECTION                      │
│                                                                 │
│  📊 Recipient Brief: Jane Smith · Updated 2 days ago           │
│  ▼ [Expanded]                                                  │
│                                                                 │
│  Quick Context: VP of Marketing with 10+ years in B2B SaaS...  │
│                                                                 │
│  Recent LinkedIn Focus:                                        │
│  • AI tools for content teams                                  │
│  • Scaling content operations                                  │
│  • Marketing automation workflows                              │
│                                                                 │
│  Twitter (@janesmith): AI, SaaS marketing, content strategy    │
│                                                                 │
│  Recent Content:                                               │
│  • How We 3x'd Content Output · Content Operations             │
│                                                                 │
│  [🔄 Refresh Research]                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ 4. User generates email/sequence
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    AI STRATEGIST ENGINE                         │
│                     ai_engine.py                                │
│                                                                 │
│  get_strategist_response_stream(                               │
│    comp, ct, messages, prompt,                                 │
│    recipient_research=research_data  ◄── CONTEXT INJECTION     │
│  )                                                             │
│    ↓                                                           │
│  _build_system_prompt(                                         │
│    ..., recipient_research=research_data                       │
│  )                                                             │
│    ↓                                                           │
│  System prompt includes:                                       │
│  ┌──────────────────────────────────────────────┐            │
│  │ == RECIPIENT SOCIAL RESEARCH ==              │            │
│  │ Brief: VP of Marketing focusing on AI...     │            │
│  │ LinkedIn: AI tools, content scaling...       │            │
│  │ Twitter: SaaS marketing, AI, podcasts        │            │
│  │ USE THIS: Reference these topics when        │            │
│  │ drafting messages.                           │            │
│  └──────────────────────────────────────────────┘            │
│    ↓                                                           │
│  Anthropic Claude generates personalized email                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ 5. AI output
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    PERSONALIZED EMAIL DRAFT                     │
│                                                                 │
│  Subject: Scaling content operations — familiar challenge?     │
│                                                                 │
│  Hi Jane,                                                      │
│                                                                 │
│  Saw your recent post on scaling content operations with AI    │
│  — that gap between demand and capacity is exactly what we've  │
│  helped publishers solve.                                      │
│                                                                 │
│  At LH2 we've averaged 2.5-3x revenue growth in year one       │
│  across live partnerships, no upfront fees...                  │
│                                                                 │
│  ✅ PERSONALIZED using recipient research                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Interaction Flow

```
┌──────────────┐
│              │
│  Strategist  │ ◄─── User interacts here
│     UI       │
│              │
└──────┬───────┘
       │
       │ calls
       │
       ↓
┌─────────────────────┐
│                     │
│  recipient_research │ ◄─── Service layer (no caching)
│      .py            │
│                     │
└──────┬──────────────┘
       │
       │ uses
       │
       ↓
┌─────────────────────┐
│                     │
│    db_ops.py        │ ◄─── Database operations
│                     │
└─────────────────────┘
       │
       │ reads/writes
       │
       ↓
┌─────────────────────┐
│                     │
│   Contact.extra     │ ◄─── PostgreSQL/SQLite
│      _data          │
│                     │
└─────────────────────┘


┌──────────────┐
│              │
│  ai_engine   │ ◄─── AI context building
│     .py      │
│              │
└──────┬───────┘
       │
       │ calls
       │
       ↓
┌─────────────────────┐
│                     │
│   Anthropic API     │ ◄─── External AI service
│   (Claude)          │
│                     │
└─────────────────────┘
```

---

## Caching Strategy (Compliant with .cursorrules)

```
┌──────────────────────────────────────────────────────────────┐
│                    RESEARCH FUNCTION                         │
│             research_recipient(contact_id)                   │
│                                                              │
│  ❌ NEVER CACHED with @st.cache_data                        │
│  ✅ Uses st.session_state for progress tracking             │
│                                                              │
│  Flow:                                                       │
│  1. Check st.session_state[f"recipient_research_{id}"]      │
│  2. If missing → Load from database (cached read)           │
│  3. If stale (>7 days) → Run fresh AI research (not cached) │
│  4. Save to database (write operation)                      │
│  5. Clear UI cache (ensure fresh display)                   │
└──────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
                ↓                           ↓
┌───────────────────────────┐   ┌───────────────────────────┐
│   DATABASE READ           │   │   AI RESEARCH             │
│   (SAFE TO CACHE)         │   │   (NEVER CACHE)           │
│                           │   │                           │
│  get_contact(id)          │   │  _run_recipient_research  │
│    .extra_data            │   │    ↓                      │
│                           │   │  Anthropic API call       │
│  ✅ @st.cache_data(60)    │   │  ❌ External API          │
│                           │   │  ❌ Must run live         │
└───────────────────────────┘   └───────────────────────────┘
```

---

## State Management

```
st.session_state Structure:
├─ recipient_research_{contact_id}     ← Research data per contact
├─ recipient_research_loading          ← Loading state flag
├─ research_cache                      ← Company research cache
├─ selected_sender                     ← Sender email
├─ selected_product                    ← Product key
└─ chat_messages                       ← Chat history

Contact.extra_data Structure:
{
  "recipient_research": {
    "last_updated": "ISO timestamp",
    "status": "completed",
    "linkedin": {...},
    "twitter": {...},
    "blog_posts": [...],
    "brief": "..."
  },
  "enrichment": {...},        ← Existing fields
  "research_notes": [...],    ← Existing fields
  ...
}
```

---

## Error Handling Flow

```
┌────────────────────────┐
│ User selects recipient │
└───────────┬────────────┘
            │
            ↓
┌────────────────────────┐
│ research_recipient()   │
└───────────┬────────────┘
            │
    ┌───────┴────────┐
    │                │
    ↓                ↓
┌─────────┐    ┌──────────────┐
│ No email│    │ Has email    │
└────┬────┘    └──────┬───────┘
     │                │
     ↓                ↓
┌─────────────────────────────┐
│ Show manual trigger button  │
│ "🔍 Research Recipient"     │
└─────────────────────────────┘
                    │
                    ↓
            ┌───────┴────────┐
            │ API call fails? │
            └───────┬────────┘
                    │
         ┌──────────┴──────────┐
         │                     │
         ↓ Yes                 ↓ No
┌─────────────────┐    ┌──────────────┐
│ Return None     │    │ Return data  │
│ Log error       │    │ Save to DB   │
│ UI still works  │    │ Display brief│
└─────────────────┘    └──────────────┘
```

---

## Performance Optimization

```
Request Timeline:

0ms    ─┐
        │ User selects recipient
100ms   ├─ Load from st.session_state (if exists)
        │    ↓ If missing
200ms   ├─ Check database (cached read, <100ms)
        │    ↓ If stale or missing
300ms   ├─ Show spinner: "Researching recipient..."
        │    ↓
8000ms  ├─ Anthropic API call (8-12 seconds)
        │    ├─ LinkedIn analysis
        │    ├─ Twitter discovery  
        │    ├─ Blog post search
        │    └─ Brief synthesis
        │    ↓
8100ms  ├─ Parse JSON response
        │    ↓
8200ms  ├─ Save to database
        │    ↓
8300ms  └─ Display in UI

Cache Hit Timeline:

0ms    ─┐
        │ User selects recipient
50ms    ├─ Load from st.session_state
        │    ↓
100ms   └─ Display in UI (instant!)

Subsequent Visits (within 7 days):
- 0-100ms: Instant load from cache
- No API calls
- No user waiting
```

---

This architecture ensures:
- ✅ Fast initial research (8-12s)
- ✅ Instant cached loads (<100ms)
- ✅ Proper separation of concerns
- ✅ Compliance with caching rules
- ✅ Graceful error handling
- ✅ Scalable design
