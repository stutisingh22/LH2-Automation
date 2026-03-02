# Architecture Hardening - Quick Reference

## 🎯 When to Invalidate Which Cache Group

```python
from services.cache_registry import invalidate_group, invalidate_multiple

# SPOC Operations
delete_spoc_contact(id)           → invalidate_group("spoc")
upsert_spoc_contact(...)          → invalidate_group("spoc")

# Contact Operations  
update_contact_status(id, status) → invalidate_multiple("contact", "pipeline")
upsert_contact(...)               → invalidate_group("contact")

# Company Operations
upsert_company(...)               → invalidate_group("company")
merge_company_extra_data(...)     → invalidate_multiple("company", "research")

# Research Operations
save_session_summary(...)         → invalidate_group("research")
force_refresh_research(...)       → invalidate_multiple("research", "action")

# Action Operations
log_action(...)                   → invalidate_group("action")
update_action(...)                → invalidate_group("action")
```

## 📦 Cache Groups

| Group | What It Contains | Invalidate When... |
|-------|-----------------|-------------------|
| `spoc` | SPOC contacts, tracking | SPOC created/updated/deleted |
| `contact` | Contact lists, details | Contact created/updated |
| `pipeline` | Pipeline stats, status counts | Contact status changes |
| `company` | Company profiles, metadata | Company created/updated |
| `research` | Strategic briefs, summaries | Research saved |
| `action` | Action history, timelines | Action logged/updated |

## 🔧 Orchestrator Functions (Auto-Invalidation)

```python
from services.orchestrator import (
    update_contact_status_cached,      # Auto: contact + pipeline
    upsert_spoc_contact_cached,        # Auto: spoc
    delete_spoc_contact_cached,        # Auto: spoc
    log_action_cached,                 # Auto: action
    save_session_summary_cached,       # Auto: research
)

# Use like normal db_ops, but with automatic cache invalidation
update_contact_status_cached(contact_id, "replied_positive")
st.rerun()
```

## ⚠️ What NOT to Do

```python
# ❌ DON'T use clear_ui_cache() anymore
from services.ui_data_loader import clear_ui_cache
clear_ui_cache()  # Deprecated - too broad

# ❌ DON'T forget to invalidate after writes
update_contact_status(id, status)
st.rerun()  # BUG: Cache not cleared!

# ❌ DON'T invalidate unrelated groups
upsert_spoc_contact(...)
invalidate_group("research")  # Why? SPOCs don't affect research!
```

## ✅ What TO Do

```python
# ✅ DO use targeted invalidation
from services.cache_registry import invalidate_group

delete_spoc_contact(id)
invalidate_group("spoc")  # Only affects SPOC views
st.rerun()

# ✅ DO use orchestrator for cleaner code
from services.orchestrator import delete_spoc_contact_cached

delete_spoc_contact_cached(id)  # Auto-invalidates "spoc"
st.rerun()

# ✅ DO invalidate multiple when operation affects multiple domains
update_contact_status(id, "replied_positive")
invalidate_multiple("contact", "pipeline")  # Status affects both
st.rerun()
```

## 🚀 Performance Impact

| Operation | Before (Global) | After (Surgical) | Speedup |
|-----------|----------------|------------------|---------|
| Update contact | Clears 8 caches | Clears 2 caches | **4x fewer misses** |
| Delete SPOC | Clears 8 caches | Clears 1 cache | **8x fewer misses** |
| Save research | Clears 8 caches | Clears 2 caches | **4x fewer misses** |
| Log action | Clears 8 caches | Clears 1 cache | **8x fewer misses** |

## 📋 Migration Checklist

Find these patterns in apps.py:

- [ ] `clear_ui_cache()` → Replace with `invalidate_group()`
- [ ] `force_refresh_research()` → Already updated ✓
- [ ] Direct `db_ops` writes → Consider `orchestrator` wrapper
- [ ] Manual cache clears → Use appropriate `invalidate_group()`

## 🔍 Debugging Cache Issues

```python
# Check what's registered in a group
from services.cache_registry import get_registered_caches

caches = get_registered_caches("spoc")
print(f"SPOC group has {len(caches)} cached functions")

# Nuclear option (migrations/emergencies only)
from services.cache_registry import clear_all_caches
clear_all_caches()  # ⚠️ Use sparingly
```

## 🎓 Architecture Rules

1. **External APIs = NEVER cached**
   - `ai_engine.*` - Live
   - `gmail_service.*` - Live  
   - `notion_sync.*` - Live
   - `signalhire_enrichment.*` - Live
   - `drive_sync.*` - Live

2. **Database reads = Cached**
   - Use `ui_data_loader.load_*()` functions
   - Registered to appropriate cache groups

3. **Database writes = Invalidate affected groups**
   - Use `invalidate_group()` or orchestrator
   - Only clear what changed

4. **UI → Service → DB boundary**
   - UI: apps.py (render only)
   - Service: ui_data_loader (reads), orchestrator (writes)
   - DB: db_ops (actual queries)
