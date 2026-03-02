# LH2 Platform - Database & Infrastructure Documentation

**Last Updated**: February 27, 2026  
**Version**: 3.0

---

## Table of Contents
1. [Overview](#overview)
2. [Database Schema (ERD)](#database-schema-erd)
3. [Current Infrastructure Specs](#current-infrastructure-specs)
4. [Performance Considerations & Pain Points](#performance-considerations--pain-points)
5. [Optimization Strategies](#optimization-strategies)
6. [Migration & Backup](#migration--backup)

---

## 1. Overview

### Architecture Summary
- **Application**: Streamlit web application (~15K lines of Python)
- **Database**: PostgreSQL (Supabase) with SQLite fallback for local development
- **Deployment**: Streamlit Cloud (Singapore region) + Local development (India)
- **ORM**: SQLAlchemy 2.x with connection pooling
- **Key Integrations**: Anthropic Claude, Google Drive, Notion, SignalHire, Gmail

### Data Flow
```
User (India) 
    ↓
Streamlit Cloud (Singapore) 
    ↓
PostgreSQL (Sydney - Supabase)
    ↓
External APIs: Drive, Notion, SignalHire
```

### Latency Concerns
- **Geographic Spread**: 
  - Users: India
  - App Server: Singapore
  - Database: Sydney, Australia
  - **Round-trip latency**: ~150-300ms per query
- **Network**: IPv4-only (Streamlit Cloud limitation, using Supabase Session Pooler)

---

## 2. Database Schema (ERD)

### Core Tables & Relationships

#### **Users & Authentication**
```
┌─────────────────────┐
│ users               │
├─────────────────────┤
│ id (PK)             │
│ username (UNIQUE)   │◄──┐
│ password_hash       │   │
│ email               │   │
│ role                │   │ (FK references)
│ is_active           │   │
│ created_at          │   │
└─────────────────────┘   │
                          │
```

#### **Companies & Contacts (1:N)**
```
┌─────────────────────────┐         ┌─────────────────────────┐
│ companies               │         │ contacts                │
├─────────────────────────┤         ├─────────────────────────┤
│ id (PK)                 │◄────────│ id (PK)                 │
│ name                    │         │ company_id (FK)         │
│ domain (UNIQUE, INDEX)  │         │ name                    │
│ publisher_type          │         │ role                    │
│ content_type            │         │ email                   │
│ monthly_visits          │         │ linkedin                │
│ notion_page_id          │         │ city, state, country    │
│ research_status         │         │ status                  │
│ gmail_thread            │         │ notion_page_id          │
│ extra_data (JSON)       │         │ extra_data (JSON)       │
│ created_at, updated_at  │         │ created_at, updated_at  │
└─────────────────────────┘         └─────────────────────────┘
         │                                      │
         │                                      │
         └──────────┬───────────────────────────┘
                    │
                    ▼
         ┌─────────────────────────┐
         │ sessions                │
         ├─────────────────────────┤
         │ id (PK)                 │
         │ company_id (FK)         │
         │ contact_id (FK)         │
         │ owner_user_id (FK)      │
         │ strategic_summary       │
         │ summary_msg_count       │
         │ lead_score              │
         │ created_at, updated_at  │
         └─────────────────────────┘
```

#### **Conversation & Actions (Session-based)**
```
┌─────────────────────────┐         ┌─────────────────────────┐
│ messages                │         │ actions                 │
├─────────────────────────┤         ├─────────────────────────┤
│ id (PK)                 │         │ id (PK)                 │
│ session_id (FK, INDEX)  │         │ session_id (FK, INDEX)  │
│ role                    │         │ user_id (FK)            │
│ content (TEXT)          │         │ action_type             │
│ created_at (INDEX)      │         │ channel                 │
└─────────────────────────┘         │ description             │
                                    │ content_sent            │
┌─────────────────────────┐         │ response_content        │
│ memory_events           │         │ response_type           │
├─────────────────────────┤         │ status (INDEX)          │
│ id (PK)                 │         │ outcome                 │
│ session_id (FK, INDEX)  │         │ context_note            │
│ event_type              │         │ sequence_id             │
│ summary (TEXT)          │         │ sequence_step           │
│ importance_score (IDX)  │         │ scheduled_at            │
│ created_at (INDEX)      │         │ completed_at            │
└─────────────────────────┘         │ draft_revisions (JSON)  │
                                    │ draft_original          │
                                    │ created_at              │
                                    └─────────────────────────┘
```

#### **SPOC Tracking (Many-to-Many with metadata)**
```
┌─────────────────────────┐
│ spoc_contacts           │
├─────────────────────────┤
│ id (PK)                 │
│ company_id (FK, INDEX)  │
│ spoc_contact_id (FK)    │──► references contacts.id (the SPOC)
│ name                    │    (Person working under this SPOC)
│ role                    │
│ email                   │
│ linkedin_url            │
│ phone                   │
│ extra_data (JSON)       │
│ created_at, updated_at  │
└─────────────────────────┘
```

#### **Enrichment & External Data**
```
┌─────────────────────────┐         ┌─────────────────────────┐
│ enrichment_results      │         │ processed_gmail_msgs    │
├─────────────────────────┤         ├─────────────────────────┤
│ id (PK)                 │         │ id (PK)                 │
│ user_id (FK, INDEX)     │         │ user_id (FK, INDEX)     │
│ request_id (INDEX)      │         │ gmail_message_id (IDX)  │
│ identifier (512 chars)  │         │ created_at              │
│ result_json (JSON)      │         └─────────────────────────┘
│ status (INDEX)          │
│ created_at              │         UNIQUE (user_id, gmail_message_id)
└─────────────────────────┘
```

#### **AI Context Caching (Performance)**
```
┌─────────────────────────┐
│ cached_contexts         │
├─────────────────────────┤
│ id (PK)                 │
│ key (UNIQUE, INDEX)     │──► Format: "drive_ctx:company:domain.com"
│ content (TEXT)          │    or "lh2_knowledge:global"
│ created_at              │
│ updated_at              │
└─────────────────────────┘

Purpose: Cache Drive API results (expensive 10-30s scans)
TTL: Content checked via updated_at timestamp
```

#### **Advisor/Media Houses (Separate tracking)**
```
┌─────────────────────────┐         ┌─────────────────────────┐
│ advisor_companies       │         │ advisor_conversations   │
├─────────────────────────┤         ├─────────────────────────┤
│ id (PK)                 │         │ id (PK)                 │
│ domain (INDEX)          │         │ user_id (FK, INDEX)     │
│ parent_name (INDEX)     │         │ title                   │
│ notion_page_id          │         │ messages (JSON)         │
│ extra_data (JSON)       │         │ created_at, updated_at  │
│ created_at              │         └─────────────────────────┘
└─────────────────────────┘
```

#### **Global AI Insights**
```
┌─────────────────────────┐
│ global_insights         │
├─────────────────────────┤
│ id (PK)                 │
│ user_id (FK, INDEX)     │
│ insight (TEXT)          │
│ confidence_score (IDX)  │
│ source_count            │
│ created_at (INDEX)      │
└─────────────────────────┘
```

### Complete Index Summary
```sql
-- High-traffic indexes (query-critical)
users.username (UNIQUE)
users.email (INDEX)
companies.domain (UNIQUE, INDEX)
contacts.company_id (INDEX)
contacts.name (INDEX)
sessions.company_id (INDEX)
sessions.contact_id (INDEX)
messages.session_id (INDEX)
messages.created_at (INDEX)
actions.session_id (INDEX)
actions.status (INDEX)
spoc_contacts.company_id (INDEX)
spoc_contacts.spoc_contact_id (INDEX)

-- Medium-traffic indexes (reporting/filtering)
memory_events.session_id (INDEX)
memory_events.importance_score (INDEX)
memory_events.created_at (INDEX)
global_insights.user_id (INDEX)
global_insights.confidence_score (INDEX)
global_insights.created_at (INDEX)
enrichment_results.identifier (INDEX)
enrichment_results.status (INDEX)

-- Cache performance
cached_contexts.key (UNIQUE, INDEX)
```

---

## 3. Current Infrastructure Specs

### **Database: PostgreSQL on Supabase**

#### **Tier**: Shared Pool (Free/Starter Tier)
- **Server Location**: Sydney, Australia (aws-1-ap-southeast-2)
- **Connection String**: Session Pooler (IPv4 compatible)
  ```
  postgresql://postgres.ecuykspwhaovxzzacnte:PASSWORD@
  aws-1-ap-southeast-2.pooler.supabase.com:5432/postgres
  ```
- **Connection Pool**: 
  - App-side: SQLAlchemy QueuePool
    - `pool_size=5`
    - `max_overflow=10`
    - `pool_pre_ping=True`
    - `pool_recycle=300` (5 minutes)
  - Supabase Pooler: Shared connection limits (varies by tier)

#### **Storage**
- **Current DB Size**: ~50-200 MB (estimated from SQLite migration)
- **Row Counts** (approximate):
  - Companies: 200-500
  - Contacts: 1,000-3,000
  - Sessions: 500-1,500
  - Messages: 5,000-15,000
  - Actions: 2,000-8,000
  - Enrichments: 500-2,000

#### **Performance Characteristics**
- **Latency** (from Streamlit Cloud Singapore):
  - Simple SELECT: 150-200ms
  - JOIN query (2-3 tables): 250-350ms
  - Complex aggregation: 400-800ms
- **Throughput**: Limited by shared pool concurrency
- **Constraints**: 
  - Shared CPU/memory with other tenants
  - No dedicated resources
  - Connection limits enforced at pooler level

### **Application Server: Streamlit Cloud**

#### **Specs** (Inferred - Streamlit Community Cloud)
- **Region**: Singapore
- **CPU**: Shared, ~0.5-1 vCPU
- **Memory**: ~1 GB RAM per app instance
- **Storage**: Ephemeral (no persistent disk)
- **Python Version**: 3.13
- **Concurrency**: 1 user = 1 thread (Streamlit limitation)
- **Network**: IPv4 only (hence Session Pooler requirement)

#### **Session Management**
- Streamlit uses WebSocket for real-time updates
- Each user connection spawns a new Python thread
- `@st.cache_resource` and `@st.cache_data` for in-memory caching
- No shared cache between different app instances (horizontal scaling)

### **Local Development**

#### **Specs** (Your Machine - India)
- **Database**: SQLite fallback (`lh2_platform.db`)
- **Python**: 3.13 (in `.venv`)
- **Network**: Standard broadband (latency to Sydney: ~100-150ms)

### **External APIs**

| Service | Purpose | Rate Limits | Latency |
|---------|---------|-------------|---------|
| **Anthropic Claude** | AI conversations, research | Varies by tier | 2-10s per request |
| **Google Drive API** | Context documents, playbooks | 1000 req/100s | 200-500ms |
| **Notion API** | Research notes, company DB | 3 req/s | 300-600ms |
| **SignalHire API** | Contact enrichment | 100 req/day (free), callback-based | 5-60s async |
| **Gmail API** | Email sync, sent tracking | 250 quota units/user/s | 300-800ms |

---

## 4. Performance Considerations & Pain Points

### **Current Performance Issues**

#### **1. Geographic Latency (Major)**
- **Root Cause**: 3-hop architecture
  - User (India) → App (Singapore) → DB (Sydney)
  - Each query adds ~150-300ms base latency
  - Multiple queries per page load compound the issue

- **Impact**:
  - Companies page load: 3-8 seconds (due to multiple DB calls)
  - Brief generation: 15-30 seconds (Drive scans + AI calls)
  - Contact enrichment: 20-60 seconds (SignalHire async)

- **Current Mitigations**:
  - `@st.cache_data` decorators on heavy queries
  - TTLs: 30-60 minutes for contact/company data
  - Persistent `cached_contexts` table for Drive content
  - Reduced N+1 queries with eager loading

#### **2. Drive API Slowness (Major)**
- **Root Cause**: 
  - Scanning 50-200 files per company context build
  - Parsing PDFs and Google Docs (10-30 seconds total)
  - No global file index (re-scans every time)

- **Current Mitigations**:
  - `CachedContext` table with per-company keys
  - Cache TTL: 24 hours (can be manually refreshed)
  - In-memory file lists cached with `@st.cache_data`

- **Remaining Issues**:
  - Cache misses on worker restarts (Streamlit Cloud)
  - No incremental updates (full re-scan on cache expiry)

#### **3. Shared Pool Contention (Medium)**
- **Root Cause**: 
  - Supabase Starter tier = shared CPU/memory
  - Peak usage from multiple concurrent users
  - No dedicated compute resources

- **Symptoms**:
  - Occasional query timeouts (>5s)
  - Slow response during peak hours
  - Connection pool exhaustion warnings

#### **4. N+1 Query Patterns (Partially Fixed)**
- **Previous Issue**: 
  - Loading 100 companies → 100+ queries for contacts
  - Loading contact status → individual SELECT per contact

- **Current Mitigations**:
  - Caching all companies/contacts at once
  - Removed pagination (data already cached)
  - Composite indexes for common JOIN patterns

#### **5. Unoptimized Queries (Low-Medium)**
- **Symptoms**:
  - Full table scans on `extra_data` JSON fields
  - No GIN indexes on JSON columns
  - Sorting/filtering on non-indexed columns

---

## 5. Optimization Strategies

### **Immediate Wins (Low-Hanging Fruit)**

#### **A. Database-Level Optimizations**

1. **Add GIN Indexes for JSON Queries**
   ```sql
   -- If frequently querying extra_data fields
   CREATE INDEX idx_companies_extra_data_gin ON companies USING GIN (extra_data);
   CREATE INDEX idx_contacts_extra_data_gin ON contacts USING GIN (extra_data);
   ```

2. **Composite Indexes for Common Queries**
   ```sql
   -- For SPOC tracking queries
   CREATE INDEX idx_spoc_lookup ON spoc_contacts (spoc_contact_id, company_id);
   
   -- For timeline queries
   CREATE INDEX idx_actions_timeline ON actions (session_id, completed_at DESC, status);
   
   -- For enrichment lookups
   CREATE INDEX idx_enrichment_user_status ON enrichment_results (user_id, status, created_at DESC);
   ```

3. **Materialized Views for Heavy Aggregations**
   ```sql
   -- Pre-compute pipeline stats (refresh every 5 minutes)
   CREATE MATERIALIZED VIEW mv_pipeline_stats AS
   SELECT 
       c.status,
       COUNT(*) as count,
       COUNT(DISTINCT c.company_id) as company_count
   FROM contacts c
   GROUP BY c.status;
   
   CREATE INDEX ON mv_pipeline_stats (status);
   ```

4. **Analyze Query Plans**
   ```sql
   -- Identify slow queries
   SELECT query, calls, total_time, mean_time
   FROM pg_stat_statements
   ORDER BY mean_time DESC
   LIMIT 20;
   ```

#### **B. Application-Level Optimizations**

1. **Batch Database Writes**
   ```python
   # Instead of N inserts
   session.bulk_insert_mappings(Contact, contacts_list)
   session.commit()
   ```

2. **Eager Loading for Relationships**
   ```python
   from sqlalchemy.orm import joinedload
   
   companies = session.query(Company)\
       .options(joinedload(Company.contacts))\
       .all()
   ```

3. **Connection Pool Tuning**
   ```python
   # Adjust for Supabase pooler limits
   engine = create_engine(
       DATABASE_URL,
       pool_size=3,           # Reduce if hitting limits
       max_overflow=7,        # Total = 10 connections max
       pool_timeout=30,       # Wait 30s for connection
       pool_pre_ping=True,
   )
   ```

4. **Read Replicas** (if available on your Supabase tier)
   ```python
   # Separate engine for read-heavy operations
   read_engine = create_engine(READ_REPLICA_URL, ...)
   ```

#### **C. Caching Strategy Enhancements**

1. **Redis/External Cache** (for multi-instance support)
   - Replace `@st.cache_data` with Redis
   - Shared cache across Streamlit app instances
   - Persist across worker restarts

2. **Hierarchical Cache**
   ```python
   # L1: In-memory (fast, ephemeral)
   @st.cache_data(ttl=300)  # 5 minutes
   def get_companies_l1():
       return get_companies_l2()
   
   # L2: Database cache (persistent)
   def get_companies_l2():
       cached = get_cached_context("companies:all")
       if cached: return json.loads(cached)
       data = db_query_companies()
       save_cached_context("companies:all", json.dumps(data))
       return data
   ```

3. **Incremental Drive Sync**
   ```python
   # Store last scan timestamp, only fetch new/modified files
   last_scan = get_cached_context("drive:last_scan_time")
   new_files = drive.list(modifiedTime__gt=last_scan)
   ```

### **Medium-Term Improvements (1-2 weeks)**

#### **A. Upgrade Supabase Tier**
- **Pro Tier** ($25/month):
  - Dedicated CPU/memory
  - 2GB database size (vs 500MB free)
  - Pooler improvements
  - Daily backups

- **Expected Impact**: 
  - 30-50% latency reduction on complex queries
  - No more shared contention issues

#### **B. Regional Database Replica**
- **Option 1**: Supabase Read Replicas (Pro+ tier)
  - Deploy replica in Singapore (closer to app server)
  - Route all SELECTs to replica
  - Writes still go to primary (Sydney)

- **Option 2**: Multi-Region Setup
  - US East replica (see `DATABASE_URL_US` in keys.env)
  - Geo-routing based on user location
  - Trade-off: Cross-region write latency

#### **C. Async Query Patterns**
```python
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

# Run multiple queries concurrently
async def load_dashboard_data():
    async with async_session() as session:
        companies, contacts, actions = await asyncio.gather(
            session.execute(select(Company)),
            session.execute(select(Contact)),
            session.execute(select(Action)),
        )
    return companies, contacts, actions
```

### **Long-Term Optimizations (1-3 months)**

#### **A. Data Archival Strategy**
```sql
-- Archive old messages (> 6 months) to separate table
CREATE TABLE messages_archive (LIKE messages INCLUDING ALL);

-- Move old data monthly
INSERT INTO messages_archive 
SELECT * FROM messages 
WHERE created_at < NOW() - INTERVAL '6 months';

DELETE FROM messages 
WHERE created_at < NOW() - INTERVAL '6 months';
```

#### **B. Separate Analytics Database**
- **CDC (Change Data Capture)** from PostgreSQL → ClickHouse/BigQuery
- Heavy reporting queries run on analytics DB
- Real-time sync via Debezium or Supabase Realtime

#### **C. Vertical Partitioning**
```sql
-- Split hot (frequently updated) and cold data
CREATE TABLE contacts_hot AS 
SELECT id, company_id, name, status, updated_at FROM contacts;

CREATE TABLE contacts_cold AS
SELECT id, extra_data, notion_page_id, created_at FROM contacts;
```

#### **D. Application Architecture Changes**
1. **Background Workers**:
   - Celery/RQ for async tasks (enrichment, Drive sync)
   - Offload from main Streamlit thread

2. **API Gateway**:
   - FastAPI/Flask backend for DB operations
   - Streamlit becomes pure frontend
   - Better connection pooling control

3. **GraphQL Layer** (optional):
   - Reduce over-fetching
   - Client-side query optimization

---

## 6. Migration & Backup

### **Current Migration Tools**

#### **A. SQLite → PostgreSQL**
Script: `migrate_to_postgres_dedup.py`

**Features**:
- Idempotent (uses `ON CONFLICT DO NOTHING`)
- Preserves primary keys
- Creates missing indexes post-migration
- Validates table schemas

**Usage**:
```bash
python migrate_to_postgres_dedup.py
```

**Validation**:
```sql
-- Check row counts match
SELECT 'companies' as table, COUNT(*) FROM companies
UNION ALL
SELECT 'contacts', COUNT(*) FROM contacts
-- ... etc
```

#### **B. Backup Strategy**

1. **Supabase Automatic Backups** (Pro tier):
   - Daily snapshots
   - 7-day retention
   - Point-in-time recovery

2. **Manual Exports** (Current setup):
   ```bash
   # PostgreSQL dump
   pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
   
   # SQLite backup
   sqlite3 lh2_platform.db ".backup backup_$(date +%Y%m%d).db"
   ```

3. **Application-Level Export**:
   - Export sessions as CSV (`export_session_csv` function)
   - JSON exports for archival

### **Rollback Plan**

If PostgreSQL becomes unavailable:
1. Set `DATABASE_URL=""` in `keys.env` or Streamlit secrets
2. App automatically falls back to SQLite
3. Data out-of-sync until PostgreSQL is restored
4. Re-sync via Notion/Gmail/Drive backfill scripts

---

## 7. Monitoring & Observability

### **Current Gaps**
❌ No query performance monitoring  
❌ No connection pool metrics  
❌ No alerting on slow queries  
❌ No error tracking (e.g., Sentry)

### **Recommended Additions**

#### **A. Database Monitoring**
```sql
-- Enable pg_stat_statements (Supabase Dashboard)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Monitor slow queries
SELECT 
    query, 
    calls, 
    total_time::numeric(10,2), 
    mean_time::numeric(10,2),
    max_time::numeric(10,2)
FROM pg_stat_statements
WHERE mean_time > 500  -- slower than 500ms
ORDER BY mean_time DESC;
```

#### **B. Application Metrics**
```python
import time
import logging

def log_db_query_time(query_name):
    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start
            if elapsed > 1.0:  # Log slow queries
                logging.warning(f"Slow query: {query_name} took {elapsed:.2f}s")
            return result
        return wrapper
    return decorator

@log_db_query_time("get_all_companies")
def get_all_companies():
    # ... existing code
```

#### **C. Healthcheck Endpoint**
```python
# Add to apps.py
def db_healthcheck():
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "OK"
    except Exception as e:
        return False, str(e)
```

---

## 8. Cost Analysis

### **Current Costs** (Estimated)

| Service | Tier | Cost/Month |
|---------|------|------------|
| Supabase PostgreSQL | Free (Sydney) | $0 |
| Streamlit Cloud | Community | $0 |
| Anthropic Claude | Pay-as-you-go | $50-150 (depends on usage) |
| Google Workspace | N/A (existing) | $0 (included) |
| SignalHire | Free tier | $0 |
| **Total** | | **$50-150/month** |

### **Recommended Upgrades**

| Service | Tier | Cost/Month | Benefits |
|---------|------|------------|----------|
| Supabase | Pro (Sydney) | $25 | Dedicated resources, faster queries |
| *or* Supabase | Pro (Singapore) | $25 | +Replica in SG for low latency |
| Redis Cloud | 30MB | $5-10 | Shared cache across app instances |
| **Upgraded Total** | | **$80-185/month** | ~50% latency reduction |

---

## 9. Quick Reference

### **Connection Strings**
```bash
# Sydney (current primary)
DATABASE_URL_SYDNEY=postgresql://postgres.ecuykspwhaovxzzacnte:PASSWORD@aws-1-ap-southeast-2.pooler.supabase.com:5432/postgres

# US East (backup, if needed)
DATABASE_URL_US=postgresql://postgres.mxhjoybldclewsutnagn:PASSWORD@aws-1-us-east-1.pooler.supabase.com:5432/postgres
```

### **Key Files**
- **Models**: `models.py` (222 lines)
- **DB Operations**: `db_ops.py` (1492 lines)
- **Config**: `config.py` (189 lines)
- **Main App**: `apps.py` (3286 lines)
- **AI Engine**: `ai_engine.py` (1037 lines)
- **Drive Sync**: `drive_sync.py` (561 lines)

### **Critical Indexes**
Run this after any schema changes:
```sql
-- Verify all indexes exist
SELECT tablename, indexname 
FROM pg_indexes 
WHERE schemaname = 'public'
ORDER BY tablename, indexname;

-- Should see ~25-30 indexes across all tables
```

### **Performance Baseline**
```sql
-- Run this to establish baseline query times
EXPLAIN ANALYZE SELECT * FROM companies LIMIT 100;
EXPLAIN ANALYZE SELECT * FROM contacts WHERE company_id = 1;
EXPLAIN ANALYZE SELECT s.*, c.name FROM sessions s JOIN contacts c ON s.contact_id = c.id LIMIT 50;
```

---

## 10. FAQ

**Q: Why is the database in Sydney when the app is in Singapore?**  
A: Supabase free tier doesn't allow region selection. Pro tier lets you choose region at creation time. To migrate regions, you'd need to create a new project and use `migrate_to_postgres_dedup.py`.

**Q: Can we use a faster database like MySQL or MongoDB?**  
A: Possible, but requires significant refactoring (SQLAlchemy supports MySQL, but Supabase is PostgreSQL-only). MongoDB would need a complete ORM rewrite. Stick with PostgreSQL and optimize it.

**Q: Why not use Streamlit's built-in session state instead of a database?**  
A: Session state is ephemeral (lost on refresh) and not shared across users. The database enables multi-user collaboration, data persistence, and historical tracking.

**Q: How do I know if I'm hitting connection pool limits?**  
A: Look for errors like `QueuePool limit exceeded` or `TimeoutError: pool timeout`. Increase `pool_size` cautiously (Supabase pooler has hard limits).

**Q: What's the difference between `@st.cache_data` and `cached_contexts` table?**  
A: 
- `@st.cache_data`: In-memory, lost on worker restart, fast (0ms)
- `cached_contexts`: Database-backed, persists across restarts, slower (~200ms)
- Use both: in-memory for short-lived, DB for expensive operations (Drive scans)

---

## Document Maintenance

**Last Reviewed**: Feb 27, 2026  
**Next Review**: March 2026 (or after major infrastructure changes)  
**Owner**: Rohit

**Change Log**:
- 2026-02-27: Initial documentation created
- Future: Add actual query performance metrics from pg_stat_statements
