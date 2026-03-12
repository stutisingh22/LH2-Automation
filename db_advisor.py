"""
db_advisor.py — Database ops for Advisor (NOTION_DATABASE_ID_2) companies only.
When DATABASE_URL is set (Supabase), uses the main DB so all data is in Supabase.
Otherwise uses lh2_advisor.db in the same directory as the main app's SQLite so both persist in one place.
"""

import json
import logging
import os
import re
import shutil
from contextlib import contextmanager
from sqlalchemy import create_engine, or_, func
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

from config import normalize_domain

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
    """Return (session_factory, model_class) for the current backend. Session and model always match.
    When DATABASE_URL is set, factory is None and callers must use db_advisor() which delegates to db_ops.db().
    """
    global _advisor_factory_and_model
    from config import DATABASE_URL
    now_using = "supabase" if (DATABASE_URL and str(DATABASE_URL).strip()) else "sqlite"
    if _advisor_factory_and_model is not None and _advisor_factory_and_model[2] == now_using:
        return _advisor_factory_and_model[0], _advisor_factory_and_model[1]
    _advisor_factory_and_model = None
    if DATABASE_URL:
        from models import AdvisorCompany as MainAdvisorCompany
        _advisor_factory_and_model = (None, MainAdvisorCompany, "supabase")
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
    """Yield a session bound to the advisor backend. When DATABASE_URL is set, uses main db_ops.db()."""
    factory, _ = _get_advisor_factory_and_model()
    if factory is None:
        from db_ops import db
        with db() as session:
            yield session
    else:
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
        ed = c.extra_data
        if isinstance(ed, dict):
            out["extra_data"] = ed
        elif isinstance(ed, str) and (ed or "").strip():
            try:
                out["extra_data"] = json.loads(ed)
            except Exception:
                out["extra_data"] = {}
        else:
            out["extra_data"] = {}
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


def delete_advisor_company(domain, parent_name) -> bool:
    """Delete an advisor company row by (domain, parent_name). Returns True if deleted."""
    AdvisorCompany = _model()
    d = normalize_domain(domain)
    p = (parent_name or "").strip()
    if not d or not p:
        return False
    with db_advisor() as s:
        row = s.query(AdvisorCompany).filter_by(domain=d.strip(), parent_name=p).first()
        if not row:
            # try raw domain in case existing row stored with www
            row = s.query(AdvisorCompany).filter_by(domain=domain.strip(), parent_name=p).first()
        if not row:
            return False
        s.delete(row)
        s.flush()
        return True


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
        if hasattr(row, "extra_data") and row.extra_data is not None:
            ed = row.extra_data
            if isinstance(ed, dict):
                current = ed
            elif isinstance(ed, str) and (ed or "").strip():
                try:
                    current = json.loads(ed)
                except Exception:
                    current = {}
            else:
                current = {}
        else:
            current = {}
        row.extra_data = {**(current or {}), **patch}
        s.flush()


def get_advisor_company_by_domain(domain):
    """Return advisor company dict if one with this domain exists. Compares using normalized domain (protocol/www/path stripped). None otherwise."""
    norm = normalize_domain(domain)
    if not norm:
        return None
    AdvisorCompany = _model()
    with db_advisor() as s:
        www = "www." + norm
        row = (
            s.query(AdvisorCompany)
            .filter(
                or_(
                    AdvisorCompany.domain == norm,
                    AdvisorCompany.domain == www,
                    func.lower(AdvisorCompany.domain) == norm,
                    func.lower(AdvisorCompany.domain) == www,
                )
            )
            .first()
        )
        if row:
            return _advisor_comp_dict(row)
        # Fallback: stored value may have protocol/path (e.g. https://allrecipes.com). Match by normalizing stored domain.
        for r in s.query(AdvisorCompany).all():
            if normalize_domain(r.domain or "") == norm:
                return _advisor_comp_dict(r)
    return None


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


def _parent_slug(parent_name):
    """Slug for parent company row domain (unique, no clash with child domains)."""
    s = (parent_name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "parent"
    return s


def get_or_create_advisor_company_for_parent(parent_name):
    """
    Get or create an advisor_company row representing the parent (media house).
    Used so the parent can have the same arrow/flow as children (deep research, strategist, actions).
    Returns advisor company dict with id, domain, parent_name, ... or None.
    Domain is __parent_<slug> so it does not appear in child lists.
    """
    p = (parent_name or "").strip() or "(No parent)"
    slug = _parent_slug(p)
    domain = f"__parent_{slug}"
    AdvisorCompany = _model()
    with db_advisor() as s:
        row = s.query(AdvisorCompany).filter_by(domain=domain).first()
        if row:
            return _advisor_comp_dict(row)
        row = AdvisorCompany(
            domain=domain,
            parent_name=p,
            notion_page_id=None,
        )
        s.add(row)
        s.flush()
        return _advisor_comp_dict(row)


def is_advisor_parent_company(advisor_comp):
    """True if this advisor company dict is a parent (media house) row, not a child."""
    if not advisor_comp or not isinstance(advisor_comp, dict):
        return False
    d = (advisor_comp.get("domain") or "").strip()
    return d.startswith("__parent_")


def get_advisor_parents_with_children():
    """
    Return list of {"parent_name": str, "children": [dict, ...]} for display.
    Each child has id, domain, parent_name, notion_page_id, created_at.
    Excludes __parent_* rows so they are not listed as children.
    """
    all_rows = get_all_advisor_companies()
    by_parent = {}
    for row in all_rows:
        if (row.get("domain") or "").strip().startswith("__parent_"):
            continue
        p = row["parent_name"] or "(No parent)"
        if p not in by_parent:
            by_parent[p] = []
        by_parent[p].append(row)
    return [{"parent_name": p, "children": kids} for p, kids in sorted(by_parent.items())]
