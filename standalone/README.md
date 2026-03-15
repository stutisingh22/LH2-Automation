# Standalone scripts

Run these from the **project root** (parent of this folder). They add the project root to `sys.path` so imports (`db_ops`, `integrations`, etc.) work.

Scripts are grouped into subfolders so you can tell at a glance what’s used by the main app vs one-off vs optional.

---

## auth/
One-time or rare setup; output is used by the main app.
- **drive_auth.py** — Google Drive OAuth: `python standalone/auth/drive_auth.py`  
  → Creates `token.json`; Drive sync in apps.py uses it for Save to Drive.

## sync/
Ongoing sync or servers; output feeds the main app (or run alongside it).
- **gmail_sent_sync.py** — Sync sent Gmail to DB: `python standalone/sync/gmail_sent_sync.py`  
  → Writes to DB (actions, sessions, processed_gmail_messages); app shows Sent Sync / contact timeline.
- **gmail_notion_sync.py** — Gmail → Notion sync (legacy; uses `token_sync.pickle`).  
  → Not referenced by apps.py — optional/legacy.
- **signalhire_callback_server.py** — Flask server for SignalHire webhooks: `python standalone/sync/signalhire_callback_server.py`  
  → apps.py tells users to run this for SignalHire enrichment; callback writes to DB.

## migrations/
One-time moves; after running, the main app uses the target DB/data.
- **migrate_to_postgres.py** — SQLite → PostgreSQL: `python standalone/migrations/migrate_to_postgres.py`  
  → After migration, apps.py uses the same Postgres DB (DATABASE_URL).
- **migrate_similarweb_to_server_db.py** — Copy Similarweb data to server DB: `python standalone/migrations/migrate_similarweb_to_server_db.py "postgresql://..."`  
  → company monthly_visits / platform_presence_analytics in DB are shown in the app.

## social/
Social / Similarweb / traffic scripts and their CSVs. Some feed the app (e.g. after sync to DB); others just produce CSVs.
- **run_social_from_google_search.py** — Build/update `social_from_search.csv` (in project root)
- **run_social_media_houses.py** — Media-house social CSV (Instagram, Facebook, YT, etc.); output: `social/social_from_search_media_houses.csv`
- **run_similarweb_independent.py** — Fetch Similarweb for all companies (needs SIMILARWEB_COOKIES)
- **run_similarweb_media_houses.py** — Similarweb for media-house CSV rows
- **run_brands_traffic_matrix.py** — Build brands/traffic matrix; output: `social/brands_traffic_matrix.csv` + DB
- **auto_similarweb_on_import.py** — Similarweb right after bulk domain import
- **sync_social_from_search_to_db.py** — Write root `social_from_search.csv` into DB
- **refresh_social_followers.py** — CLI: refresh social follower counts for a domain
- **social_follower_research.py** — Module used by run_brands_traffic_matrix and run_social_media_houses

CSVs in this folder: `social_from_search_media_houses.csv`, `brands_traffic_matrix.csv`.

## other/
Optional helpers; not required for the main app.
- **realtime_enricher.py** — Notion + Claude realtime enrichment helper
