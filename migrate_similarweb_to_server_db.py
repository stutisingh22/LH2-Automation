"""
Migrate Similarweb data (from run_similarweb_independent.py) from local DB to server DB.

Reads from source: Company.monthly_visits and Company.extra_data["platform_presence_analytics"].
Updates target (server) companies by domain.

Usage (from project root):

  # Pass server Postgres URL as first argument (easiest — no need to edit any file):
  python migrate_similarweb_to_server_db.py "postgresql://USER:PASSWORD@HOST:5432/DATABASE"

  # Where to get that URL:
  # - Supabase: Project Settings → Database → Connection string (URI). Replace [YOUR-PASSWORD] with your DB password.
  # - Railway / Render / other: use the Postgres connection URL they give you.
  # - Your own server: postgresql://user:password@your-server-ip:5432/your_db_name

  # Or put it in keys.env as SERVER_DATABASE_URL=... or DATABASE_URL=... and run:
  python migrate_similarweb_to_server_db.py

Source is always local lh2_platform.db in this folder unless you set SOURCE_DATABASE_URL.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root / "keys.env", override=True)
load_dotenv(_root / ".env")

# Normalize domain for matching (strip protocol, www, path; lowercase)
def _normalize_domain(domain: str | None) -> str:
    if not domain or not isinstance(domain, str):
        return ""
    s = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    if s.startswith("www."):
        s = s[4:].strip()
    if "/" in s:
        s = s.split("/")[0].strip()
    return s.rstrip("/").strip() or ""


def _get_extra_data_dict(raw) -> dict:
    """Return extra_data as a dict; parse JSON string if needed."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def main():
    source_url = (os.getenv("SOURCE_DATABASE_URL") or "").strip()
    if not source_url:
        sqlite_path = _root / "lh2_platform.db"
        source_url = f"sqlite:///{sqlite_path}"
        if not sqlite_path.exists():
            print(f"ERROR: Local SQLite not found at {sqlite_path} and SOURCE_DATABASE_URL not set.")
            sys.exit(1)
        print(f"Source: SQLite at {sqlite_path}")
    else:
        if source_url.startswith("postgres://"):
            source_url = source_url.replace("postgres://", "postgresql://", 1)
        print("Source: SOURCE_DATABASE_URL")

    # Server URL: from first CLI arg, or from env (keys.env / .env)
    server_url = ""
    if len(sys.argv) >= 2 and (sys.argv[1] or "").strip():
        server_url = (sys.argv[1] or "").strip()
    if not server_url:
        server_url = (os.getenv("SERVER_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not server_url:
        print("ERROR: Server Postgres URL not set.")
        print("  Pass it as the first argument:")
        print('    python migrate_similarweb_to_server_db.py "postgresql://USER:PASSWORD@HOST:5432/DATABASE"')
        print("  Or set SERVER_DATABASE_URL (or DATABASE_URL) in keys.env in this folder.")
        print("  Supabase: Project Settings → Database → Connection string (URI).")
        sys.exit(1)
    if server_url.startswith("postgres://"):
        server_url = server_url.replace("postgres://", "postgresql://", 1)
    print("Target: server DB (Postgres)")

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import Engine

    def _engine(url: str) -> Engine:
        if url.startswith("sqlite"):
            return create_engine(url, connect_args={"check_same_thread": False})
        return create_engine(url, pool_pre_ping=True)

    src_engine = _engine(source_url)
    dst_engine = _engine(server_url)

    # Read from source: companies with monthly_visits or platform_presence_analytics
    with src_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, domain, monthly_visits, extra_data FROM companies")
        ).fetchall()

    to_migrate = []
    for row in rows:
        domain = (row.domain or "").strip() if hasattr(row, "domain") else (row[1] or "").strip()
        if not domain or (domain.startswith("__advisor_") and len(domain) > 10):
            continue
        monthly_visits = row.monthly_visits if hasattr(row, "monthly_visits") else row[2]
        extra_data = row.extra_data if hasattr(row, "extra_data") else row[3]
        ed = _get_extra_data_dict(extra_data)
        ppa = ed.get("platform_presence_analytics") if isinstance(ed.get("platform_presence_analytics"), dict) else None
        if monthly_visits is not None or ppa:
            to_migrate.append({
                "domain": domain,
                "monthly_visits": monthly_visits,
                "platform_presence_analytics": ppa,
            })

    if not to_migrate:
        print("No companies with Similarweb data (monthly_visits or platform_presence_analytics) in source.")
        src_engine.dispose()
        dst_engine.dispose()
        return

    print(f"Found {len(to_migrate)} companies with Similarweb data to migrate.")

    updated = 0
    skipped = 0
    with dst_engine.connect() as conn:
        trans = conn.begin()
        try:
            for item in to_migrate:
                domain = item["domain"]
                norm = _normalize_domain(domain)
                if not norm:
                    skipped += 1
                    continue
                # Find target company by domain (try norm and www.norm)
                r = conn.execute(
                    text("""
                        SELECT id, extra_data FROM companies
                        WHERE LOWER(TRIM(domain)) = :norm
                           OR LOWER(TRIM(domain)) = :www
                    """),
                    {"norm": norm, "www": "www." + norm},
                ).fetchone()
                if not r:
                    skipped += 1
                    continue
                target_id = r[0]
                target_extra = _get_extra_data_dict(r[1])

                # Merge platform_presence_analytics from source into target extra_data
                if item.get("platform_presence_analytics"):
                    target_extra["platform_presence_analytics"] = {
                        **(target_extra.get("platform_presence_analytics") or {}),
                        **item["platform_presence_analytics"],
                    }
                # Only write extra_data when we merged Similarweb analytics (don't overwrite server with unchanged)
                extra_json = json.dumps(target_extra) if item.get("platform_presence_analytics") else None

                # Update target row: monthly_visits and extra_data (Postgres JSONB via cast)
                if extra_json is not None:
                    conn.execute(
                        text("""
                            UPDATE companies
                            SET monthly_visits = COALESCE(:monthly_visits, monthly_visits),
                                extra_data = CAST(:extra_data AS jsonb),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """),
                        {"id": target_id, "monthly_visits": item.get("monthly_visits"), "extra_data": extra_json},
                    )
                else:
                    conn.execute(
                        text("""
                            UPDATE companies
                            SET monthly_visits = COALESCE(:monthly_visits, monthly_visits),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """),
                        {"id": target_id, "monthly_visits": item.get("monthly_visits")},
                    )
                updated += 1
            trans.commit()
        except Exception as e:
            trans.rollback()
            print(f"ERROR: {e}")
            src_engine.dispose()
            dst_engine.dispose()
            sys.exit(1)

    src_engine.dispose()
    dst_engine.dispose()
    print(f"Done. Updated {updated} companies on server DB, skipped {skipped} (no matching domain).")


if __name__ == "__main__":
    main()
