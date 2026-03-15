"""
migrate_to_postgres.py — One-time migration: copy data from SQLite into PostgreSQL (Supabase).
Uses the same SQLite source as the app (via sqlite_db_connection). Run once, then use DATABASE_URL.

Usage (from project root):
  python standalone/migrations/migrate_to_postgres.py

Requires: keys.env with DATABASE_URL set to your Supabase Postgres URL.
Note: If the password in DATABASE_URL contains @ or other special chars, URL-encode it
      (e.g. @ -> %40).
"""

import os
import sys

# Load env before importing
from dotenv import load_dotenv
load_dotenv("keys.env")
load_dotenv(".env")

# Project root for imports (standalone/migrations/ -> parent.parent)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

from sqlite_db_connection import get_platform_db_path
from sqlalchemy import create_engine, text
from sqlalchemy import inspect
from sqlalchemy.types import Boolean

# Tables in dependency order (parents before children)
TABLE_ORDER = [
    "users",
    "companies",
    "contacts",
    "sessions",
    "messages",
    "actions",
    "memory_events",
    "global_insights",
    "advisor_conversations",
    "enrichment_results",
]


def main():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL not set. Add it to keys.env and run again.")
        sys.exit(1)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    _SQLITE_PATH = get_platform_db_path()
    if not os.path.isfile(_SQLITE_PATH):
        print(f"ERROR: SQLite file not found: {_SQLITE_PATH}")
        sys.exit(1)

    print("Connecting to SQLite...")
    sqlite_engine = create_engine(
        f"sqlite:///{_SQLITE_PATH}",
        connect_args={"check_same_thread": False},
    )
    print("Connecting to PostgreSQL (Supabase)...")
    try:
        pg_engine = create_engine(
            database_url,
            pool_pre_ping=True,
        )
        with pg_engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        print(f"ERROR: Cannot connect to PostgreSQL: {e}")
        print("If your password contains @ or #, URL-encode it (e.g. @ -> %40).")
        sys.exit(1)

    # Create schema on Postgres (from models)
    from models import Base
    print("Creating tables on PostgreSQL (if not exist)...")
    Base.metadata.create_all(pg_engine)

    # Copy each table
    for table_name in TABLE_ORDER:
        with sqlite_engine.connect() as src:
            insp = inspect(sqlite_engine)
            if table_name not in insp.get_table_names():
                print(f"  Skip {table_name} (not in SQLite)")
                continue
            rows = src.execute(text(f'SELECT * FROM "{table_name}"')).fetchall()
            col_names = list(rows[0]._mapping.keys()) if rows else []
            if not rows:
                print(f"  {table_name}: 0 rows")
                continue

        # Insert into Postgres (only columns that exist in both)
        with pg_engine.connect() as dst_conn:
            pg_insp = inspect(pg_engine)
            pg_col_specs = {c["name"]: c["type"] for c in pg_insp.get_columns(table_name)}
            cols_to_use = [c for c in col_names if c in pg_col_specs]
            if not cols_to_use:
                print(f"  Skip {table_name} (no matching columns)")
                continue

            def coerce_value(col_name, val):
                """SQLite stores booleans as 0/1; Postgres needs bool."""
                if val is None:
                    return None
                col_type = pg_col_specs.get(col_name)
                if isinstance(col_type, Boolean):
                    return bool(val) if isinstance(val, (int, float)) else val
                return val

            placeholders = ", ".join([f'"{c}"' for c in cols_to_use])
            values_placeholder = ", ".join([f":{c}" for c in cols_to_use])
            insert_sql = f'INSERT INTO "{table_name}" ({placeholders}) VALUES ({values_placeholder})'
            trans = dst_conn.begin()
            try:
                for row in rows:
                    mapping = row._mapping
                    payload = {c: coerce_value(c, mapping.get(c)) for c in cols_to_use}
                    dst_conn.execute(text(insert_sql), payload)
                trans.commit()
            except Exception as e:
                trans.rollback()
                print(f"  ERROR copying {table_name}: {e}")
                raise

        # Reset sequence so next INSERT gets correct id
        try:
            with pg_engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM \"{table_name}\"), 1))"
                    ))
        except Exception as e:
            print(f"  (Sequence reset for {table_name}: {e})")

        print(f"  {table_name}: {len(rows)} rows")

    print("Done. App will use DATABASE_URL from keys.env from now on.")
    sqlite_engine.dispose()
    pg_engine.dispose()


if __name__ == "__main__":
    main()
