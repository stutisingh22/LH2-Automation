"""
db_advisor.py — Database ops for Advisor (NOTION_DATABASE_ID_2) companies only.
When DATABASE_URL is set (Supabase), uses the main DB so all data is in Supabase.
Otherwise uses lh2_advisor.db in the same directory as the main app's SQLite so both persist in one place.
"""

import logging
import os
import shutil
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

log = logging.getLogger(__name__)

_DB_DIR = os.path.dirname(os.path.abspath(__file__))
_OLD_ADVISOR_SQLITE_PATH = os.path.join(_DB_DIR, "lh2_advisor.db")

# (session_factory, model_class, backend) so session and model always match the same backend
_advisor_factory_and_model = None


def _advisor_sqlite_path():
    """Canonical path for lh2_advisor.db: same directory as main app SQLite so data stays in one place."""
    from db_ops import get_local_db_directory
    return os.path.join(get_local_db_directory(), "lh2_advisor.db")


def _migrate_advisor_db_if_needed(canonical_path):
    """If user had lh2_advisor.db next to this file, copy it to canonical path once so data is not lost."""
    if canonical_path == _OLD_ADVISOR_SQLITE_PATH:
        return
    if os.path.exists(canonical_path):
        return
    if not os.path.exists(_OLD_ADVISOR_SQLITE_PATH) or os.path.getsize(_OLD_ADVISOR_SQLITE_PATH) == 0:
        return
    try:
        shutil.copy2(_OLD_ADVISOR_SQLITE_PATH, canonical_path)
        log.info("Migrated lh2_advisor.db to canonical path: %s", canonical_path)
    except Exception as e:
        log.warning("Could not migrate lh2_advisor.db: %s", e)


def _advisor_engine():
    path = _advisor_sqlite_path()
    _migrate_advisor_db_if_needed(path)
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@contextmanager
def init_db_advisor():
    """Create tables if needed. Call once at startup or first use (SQLite path only)."""
    engine = _advisor_engine()
    from models_advisor import BaseAdvisor
    BaseAdvisor.metadata.create_all(engine)
    yield engine


# Lazy (factory, model) pair; invalidated when backend (sqlite vs supabase) changes
_advisor_factory_and_model = None


def _get_advisor_factory_and_model():
    """Return (session_factory, model_class) for the current backend. Session and model always match."""
    global _advisor_factory_and_model
    from config import DATABASE_URL
    now_using = "supabase" if (DATABASE_URL and str(DATABASE_URL).strip()) else "sqlite"
    if _advisor_factory_and_model is not None and _advisor_factory_and_model[2] == now_using:
        return _advisor_factory_and_model[0], _advisor_factory_and_model[1]
    _advisor_factory_and_model = None
    if DATABASE_URL:
        from db_ops import get_engine
        from models import AdvisorCompany as MainAdvisorCompany
        factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
        _advisor_factory_and_model = (factory, MainAdvisorCompany, "supabase")
    else:
        from models_advisor import BaseAdvisor, AdvisorCompany as SqliteAdvisorCompany
        from sqlalchemy import text
        engine = _advisor_engine()
        BaseAdvisor.metadata.create_all(engine)
        try:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE advisor_companies ADD COLUMN extra_data TEXT"))
                conn.commit()
        except Exception:
            pass
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        _advisor_factory_and_model = (factory, SqliteAdvisorCompany, "sqlite")
    return _advisor_factory_and_model[0], _advisor_factory_and_model[1]


def _get_advisor_session_factory():
    f, _ = _get_advisor_factory_and_model()
    return f


def _model():
    _, m = _get_advisor_factory_and_model()
    return m


@contextmanager
def db_advisor():
    factory, _ = _get_advisor_factory_and_model()
    session = scoped_session(factory)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.remove()


def _advisor_comp_dict(c):
    out = {
        "id": c.id,
        "domain": c.domain,
        "parent_name": c.parent_name,
        "notion_page_id": c.notion_page_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if hasattr(c, "extra_data") and c.extra_data is not None:
        out["extra_data"] = c.extra_data if isinstance(c.extra_data, dict) else {}
    else:
        out["extra_data"] = {}
    return out


def upsert_advisor_company(domain, parent_name, notion_page_id=None):
    """Upsert by (domain, parent_name). Returns row id."""
    AdvisorCompany = _model()
    with db_advisor() as s:
        row = s.query(AdvisorCompany).filter_by(domain=domain.strip(), parent_name=(parent_name or "").strip()).first()
        if row:
            if notion_page_id is not None:
                row.notion_page_id = notion_page_id
            s.flush()
            return row.id
        row = AdvisorCompany(
            domain=domain.strip(),
            parent_name=(parent_name or "").strip(),
            notion_page_id=notion_page_id,
        )
        s.add(row)
        s.flush()
        return row.id


def bulk_upsert_advisor_companies(companies_list):
    """
    Upsert many companies in a single transaction. Much faster than calling
    upsert_advisor_company() per row when syncing from Notion.
    companies_list: list of dicts with keys domain, parent_name, notion_page_id (optional).
    Returns number of companies upserted.
    """
    if not companies_list:
        return 0
    AdvisorCompany = _model()
    with db_advisor() as s:
        for data in companies_list:
            domain = (data.get("domain") or "").strip()
            parent_name = (data.get("parent_name") or "").strip() or "(No parent)"
            notion_page_id = data.get("notion_page_id")
            row = s.query(AdvisorCompany).filter_by(domain=domain, parent_name=parent_name).first()
            if row:
                if notion_page_id is not None:
                    row.notion_page_id = notion_page_id
            else:
                s.add(AdvisorCompany(
                    domain=domain,
                    parent_name=parent_name,
                    notion_page_id=notion_page_id,
                ))
        return len(companies_list)


def get_advisor_company(advisor_id):
    """Return a single advisor company by id, or None. Includes extra_data (research etc.)."""
    if advisor_id is None:
        return None
    AdvisorCompany = _model()
    with db_advisor() as s:
        c = s.query(AdvisorCompany).filter_by(id=advisor_id).first()
        return _advisor_comp_dict(c) if c else None


def merge_advisor_company_extra_data(advisor_id, patch):
    """Merge patch dict into advisor company's extra_data (e.g. research_full). Persists to DB (Supabase or SQLite)."""
    if not advisor_id or not patch:
        return
    AdvisorCompany = _model()
    with db_advisor() as s:
        row = s.query(AdvisorCompany).filter_by(id=advisor_id).first()
        if not row:
            return
        current = row.extra_data if hasattr(row, "extra_data") and isinstance(row.extra_data, dict) else {}
        row.extra_data = {**(current or {}), **patch}
        s.flush()


def get_all_advisor_companies():
    """Return all advisor companies as list of dicts (domain, parent_name, id, ...)."""
    AdvisorCompany = _model()
    with db_advisor() as s:
        return [_advisor_comp_dict(c) for c in s.query(AdvisorCompany).order_by(AdvisorCompany.parent_name, AdvisorCompany.domain).all()]


def search_advisor_companies(q):
    """Fuzzy search on domain and parent_name."""
    AdvisorCompany = _model()
    with db_advisor() as s:
        q = (q or "").strip()
        if not q:
            return get_all_advisor_companies()
        like = f"%{q}%"
        return [
            _advisor_comp_dict(c)
            for c in s.query(AdvisorCompany)
            .filter(
                AdvisorCompany.domain.ilike(like) | AdvisorCompany.parent_name.ilike(like)
            )
            .order_by(AdvisorCompany.parent_name, AdvisorCompany.domain)
            .all()
        ]


def get_advisor_parents_with_children():
    """
    Return list of {"parent_name": str, "children": [dict, ...]} for display.
    Each child has id, domain, parent_name, notion_page_id, created_at.
    """
    all_rows = get_all_advisor_companies()
    by_parent = {}
    for row in all_rows:
        p = row["parent_name"] or "(No parent)"
        if p not in by_parent:
            by_parent[p] = []
        by_parent[p].append(row)
    return [{"parent_name": p, "children": kids} for p, kids in sorted(by_parent.items())]
