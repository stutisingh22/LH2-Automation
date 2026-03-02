# LH2 Platform - Caching Strategy Summary

## Quick Reference: When to Cache and When Not To

### ❌ NEVER Cache (The 'Active' Rule)

**External API Calls - Always Run Live**

| Service | Module | Examples | Why No Caching |
|---------|--------|----------|----------------|
| **Anthropic AI** | `ai_engine.py` | `compute_lead_score()`, `get_strategist_response_stream()`, `intelligence_request()`, `advisor_chat()` | Deep research must be fresh and accurate |
| **SignalHire** | `signalhire_enrichment.py` | `request_enrichment()`, `has_verified_email()` | Contact enrichment data changes frequently |
| **Google Drive** | `drive_sync.py` | `list_drive_files()`, `create_doc_in_drive()`, `refresh_all_drive_contexts()` | Documents update in real-time |
| **Notion API** | `notion_sync.py` | `sync_notion_to_db()`, `lookup_lead_in_notion()`, `fetch_notes_for_page()` | Notion data is source of truth |
| **Gmail API** | `gmail_service.py` | `create_gmail_draft()`, `parse_email_content()` | Email operations must be real-time |
| **Web Research** | `research.py`, `web_search.py` | All research functions | Web data changes constantly |

### ✅ Always Cache (The 'Display' Rule)

**Database Reads - Safe to Cache with TTL=60**

| Function Type | Examples | Cache Strategy |
|---------------|----------|----------------|
| **List views** | `load_all_companies()`, `load_all_spocs()` | `@st.cache_data(ttl=60)` |
| **Single entity** | `get_company(id)`, `get_contact(id)` | `@st.cache_data(ttl=60)` |
| **Aggregates** | `get_pipeline_stats()` | Uses materialized view (DB-level cache) |
| **History/logs** | `get_session_actions()`, `get_recent_activity()` | `@st.cache_data(ttl=60)` |

**Important**: Always call `clear_ui_cache()` immediately after ANY database write!

### 🔄 Use Session State (The 'In-Memory' Rule)

**Progress Tracking - Not Persistent Caching**

Use `st.session_state` for:
- ✅ Live scan progress indicators
- ✅ Intermediate research results during active operations
- ✅ Streaming AI responses
- ✅ Multi-step enrichment workflows

**Pattern:**
```python
# Track progress in session state (not cached)
if 'research_progress' not in st.session_state:
    st.session_state.research_progress = []

# Run live (NOT cached)
result = run_live_research()  # Fresh API call
st.session_state.research_progress.append(result)

# Only cache AFTER user saves
if st.button("Save"):
    save_to_db(st.session_state.research_progress)
    clear_ui_cache()  # Now it's in the display cache
```

## Decision Tree

```
┌─────────────────────────────────────────────────────────┐
│ Does this function call an external API?               │
│ (Anthropic, SignalHire, Drive, Notion, Gmail)          │
└───────────┬─────────────────────────────────────────────┘
            │
            ├─ YES ───→ ❌ NEVER cache
            │            Use st.session_state for progress only
            │
            └─ NO ────→ Does it read from PostgreSQL/SQLite?
                        │
                        ├─ YES ───→ ✅ Cache with @st.cache_data(ttl=60)
                        │            Call clear_ui_cache() after writes!
                        │
                        └─ NO ────→ Pure computation (no side effects)?
                                    │
                                    ├─ YES ───→ ✅ Cache with @st.cache_data
                                    │
                                    └─ NO ────→ ❌ Do not cache
```

## Common Mistakes

| Mistake | Why It's Bad | Fix |
|---------|--------------|-----|
| Caching AI calls | Stale research, wasted cache memory | Remove `@st.cache_data` |
| Caching streaming functions | Breaks generators, returns stale data | Never cache generators/streams |
| Not clearing cache after writes | UI shows stale data | Always call `clear_ui_cache()` |
| Mixing research + display | Unclear caching semantics | Separate into two functions |
| Long TTL (>60s) | Data goes stale | Use TTL=60 or database-level caching |

## Architecture Summary

```
┌──────────────────────────────────────────────────────────────┐
│                        UI Layer (apps.py)                     │
├──────────────────────────────────────────────────────────────┤
│  [Display]                                  [Research]        │
│  • Cached database reads                   • Live API calls  │
│  • Fast (<50ms)                             • Fresh data     │
│  • TTL=60 seconds                           • No caching     │
│  • services/ui_data_loader                  • Session state  │
└──────────────────────────────────────────────────────────────┘
                         │                          │
                         ↓                          ↓
┌──────────────────────────────┐  ┌──────────────────────────┐
│   Database Layer (db_ops)    │  │  Service Layer           │
│   • PostgreSQL/SQLite        │  │  • ai_engine.py          │
│   • Materialized views       │  │  • notion_sync.py        │
│   • GIN indexes              │  │  • gmail_service.py      │
│   • 200ms latency floor      │  │  • research.py           │
└──────────────────────────────┘  └──────────────────────────┘
```

## Key Principles

1. **Research = Live**: AI, APIs, enrichment always fresh
2. **Display = Cached**: Database reads cached for speed
3. **Progress = Session State**: Track without caching
4. **Write = Clear Cache**: Invalidate immediately
5. **Balance**: Fast UI + Accurate research

## Related Files

- `.cursorrules` - Complete caching rules and examples
- `services/ui_data_loader.py` - Cached database reads
- `db_ops.py` - Database operations (some internally cached)
- `MIGRATION_GUIDE.md` - Performance optimization details
