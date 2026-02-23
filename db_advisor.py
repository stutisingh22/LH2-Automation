"""
db_advisor.py — Database ops for Advisor (NOTION_DATABASE_ID_2) companies only.
Uses a separate SQLite file lh2_advisor.db so primary platform logic is unaffected.
"""

import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

from models_advisor import BaseAdvisor, AdvisorCompany

_DB_DIR = os.path.dirname(os.path.abspath(__file__))
_ADVISOR_SQLITE_PATH = os.path.join(_DB_DIR, "lh2_advisor.db")


def _advisor_engine():
    return create_engine(
        f"sqlite:///{_ADVISOR_SQLITE_PATH}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@contextmanager
def init_db_advisor():
    """Create tables if needed. Call once at startup or first use."""
    engine = _advisor_engine()
    BaseAdvisor.metadata.create_all(engine)
    yield engine


# Lazy session factory (created on first use)
_advisor_session_factory = None


def _get_advisor_session_factory():
    global _advisor_session_factory
    if _advisor_session_factory is None:
        engine = _advisor_engine()
        BaseAdvisor.metadata.create_all(engine)
        _advisor_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _advisor_session_factory


@contextmanager
def db_advisor():
    session = scoped_session(_get_advisor_session_factory())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.remove()


def _advisor_comp_dict(c):
    return {
        "id": c.id,
        "domain": c.domain,
        "parent_name": c.parent_name,
        "notion_page_id": c.notion_page_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def upsert_advisor_company(domain, parent_name, notion_page_id=None):
    """Upsert by (domain, parent_name). Returns row id."""
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


def get_all_advisor_companies():
    """Return all advisor companies as list of dicts (domain, parent_name, id, ...)."""
    with db_advisor() as s:
        return [_advisor_comp_dict(c) for c in s.query(AdvisorCompany).order_by(AdvisorCompany.parent_name, AdvisorCompany.domain).all()]


def search_advisor_companies(q):
    """Fuzzy search on domain and parent_name."""
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
