"""
db_ops.py — Database engine, session factory, and all CRUD operations

FIX LOG:
  - SQLite path is now absolute (next to this file) so data persists across restarts
  - Search does fuzzy matching: strips TLDs and matches partial domain names
"""

import json
import logging
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

import streamlit as st
from passlib.hash import pbkdf2_sha256
from sqlalchemy import create_engine, or_, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool, StaticPool, NullPool
from sqlalchemy.exc import OperationalError

from config import DATABASE_URL, CONTACT_STATUSES
from models import (
    Base, User, Company, Contact, Session, Message,
    Action, MemoryEvent, GlobalInsight, AdvisorConversation, EnrichmentResult,
    ProcessedGmailMessage, SpocContact, CachedContext,
)

log = logging.getLogger(__name__)

# Absolute path for SQLite — prefer next to this file; fallback to user dir if project dir is read-only
_DB_DIR = os.path.dirname(os.path.abspath(__file__))
_SQLITE_PATH = os.path.join(_DB_DIR, "lh2_platform.db")
_SQLITE_FALLBACK_DIR = os.path.join(os.path.expanduser("~"), ".lh2_platform")
_SQLITE_FALLBACK_PATH = os.path.join(_SQLITE_FALLBACK_DIR, "lh2_platform.db")


def _sqlite_path_writable(db_dir: str, db_path: str) -> bool:
    """Return True if the given directory and (if existing) file are writable."""
    if not os.access(db_dir, os.W_OK):
        return False
    if os.path.exists(db_path) and not os.access(db_path, os.W_OK):
        return False
    return True


def _get_writable_sqlite_path():
    """Return a (db_dir, db_path) where we can create/write the SQLite DB. Prefer project dir; fallback to ~/.lh2_platform."""
    if _sqlite_path_writable(_DB_DIR, _SQLITE_PATH):
        return _DB_DIR, _SQLITE_PATH
    try:
        os.makedirs(_SQLITE_FALLBACK_DIR, mode=0o700, exist_ok=True)
        if _sqlite_path_writable(_SQLITE_FALLBACK_DIR, _SQLITE_FALLBACK_PATH):
            log.info(
                "Project SQLite path not writable (e.g. sandbox); using fallback: %s",
                _SQLITE_FALLBACK_PATH,
            )
            return _SQLITE_FALLBACK_DIR, _SQLITE_FALLBACK_PATH
    except OSError as e:
        log.warning("Could not use fallback SQLite dir %s: %s", _SQLITE_FALLBACK_DIR, e)
    raise RuntimeError(
        f"SQLite database path is not writable: {_SQLITE_PATH!r}. "
        "Fix permissions (e.g. chmod u+w on the file and directory) or use PostgreSQL (set DATABASE_URL in keys.env)."
    )


def get_local_db_directory():
    """Return the directory used for local SQLite DBs (same as main app). Advisor DB uses this so both DBs stay in one place."""
    return _get_writable_sqlite_path()[0]


# ── Engine + Session Factory ──────────────────────────────────

_engine = None  # Set by init_db(); use get_engine() for code that needs the engine


def _create_sqlite_engine():
    _db_dir, db_path = _get_writable_sqlite_path()
    log.info("Using SQLite at %s", db_path)
    # NullPool: each db() call opens a fresh connection from the file.
    # This ensures the Streamlit app sees writes made by the callback server
    # (which runs as a separate process with its own SQLite connection).
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )


@st.cache_resource
def init_db():
    engine = None
    if DATABASE_URL:
        try:
            engine = create_engine(
                DATABASE_URL, poolclass=QueuePool, pool_size=5,
                max_overflow=10, pool_pre_ping=True, pool_recycle=300,
            )
            Base.metadata.create_all(engine)
            try:
                CachedContext.__table__.create(bind=engine, checkfirst=True)
            except Exception:
                pass
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("DB connected successfully (PostgreSQL)")
        except OperationalError as e:
            log.warning("PostgreSQL unreachable (%s), falling back to SQLite", e)
            engine = _create_sqlite_engine()
            Base.metadata.create_all(engine)
            try:
                CachedContext.__table__.create(bind=engine, checkfirst=True)
            except Exception:
                pass
    if engine is None:
        engine = _create_sqlite_engine()
        Base.metadata.create_all(engine)
        try:
            CachedContext.__table__.create(bind=engine, checkfirst=True)
        except Exception:
            pass
    # Add draft_revisions / draft_original columns if missing (existing DBs)
    is_sqlite = "sqlite" in (engine.url.drivername or "")
    for col, typ in (("draft_revisions", "TEXT" if is_sqlite else "JSON"), ("draft_original", "TEXT")):
        try:
            with engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(f"ALTER TABLE actions ADD COLUMN {col} {typ}"))
        except Exception as e:
            err = str(e).lower()
            if "duplicate column" in err or "already exists" in err:
                pass
            else:
                log.warning("Migration %s: %s", col, e)
    db_type = "PostgreSQL" if "postgresql" in (engine.url.drivername or "") else "SQLite"
    print(f"DB connected successfully ({db_type})")
    global _engine
    _engine = engine
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_engine():
    """Return the shared engine. Calls init_db() to ensure it exists."""
    init_db()
    return _engine


SessionFactory = init_db()


@contextmanager
def db():
    session = scoped_session(SessionFactory)
    try:
        yield session
        session.commit()
    except OperationalError as e:
        session.rollback()
        if "readonly" in (e.orig.args[0] if e.orig else "").lower() or "readonly" in str(e).lower():
            raise RuntimeError(
                f"Database is read-only: {e}. "
                f"If using SQLite, ensure the file and directory are writable: {_SQLITE_PATH!r}. "
                "Or use PostgreSQL by setting DATABASE_URL in keys.env and ensuring the server is reachable."
            ) from e
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.remove()


# ── Serializers ───────────────────────────────────────────────

def _normalize_extra_data(ed):
    """Return extra_data as a dict; parse JSON string if needed so research_full etc. persist after refresh."""
    if ed is None:
        return {}
    if isinstance(ed, dict):
        return ed
    if isinstance(ed, str) and (ed or "").strip():
        try:
            return json.loads(ed)
        except Exception:
            return {}
    return {}


def _comp_dict(c):
    return {
        "id": c.id, "name": c.name, "domain": c.domain,
        "publisher_type": c.publisher_type, "content_type": c.content_type,
        "monthly_visits": c.monthly_visits, "notion_page_id": c.notion_page_id,
        "research_status": c.research_status, "gmail_thread": c.gmail_thread,
        "extra_data": _normalize_extra_data(c.extra_data),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _ct_dict(c):
    return {
        "id": c.id, "company_id": c.company_id, "name": c.name,
        "role": c.role, "email": c.email, "linkedin": c.linkedin,
        "city": c.city, "state": c.state, "country": c.country,
        "status": c.status, "notion_page_id": c.notion_page_id,
        "extra_data": c.extra_data or {},
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _sess_dict(x):
    return {
        "id": x.id, "company_id": x.company_id,
        "contact_id": x.contact_id, "owner_user_id": x.owner_user_id,
        "strategic_summary": x.strategic_summary,
        "summary_msg_count": x.summary_msg_count or 0,
        "lead_score": x.lead_score or 0,
        "created_at": x.created_at.isoformat() if x.created_at else None,
        "updated_at": x.updated_at.isoformat() if x.updated_at else None,
    }


def _act_dict(a):
    revs = getattr(a, "draft_revisions", None)
    if revs is not None and not isinstance(revs, list):
        revs = list(revs) if revs else []
    if revs is None:
        revs = []
    orig = getattr(a, "draft_original", None)
    return {
        "id": a.id, "session_id": a.session_id, "user_id": a.user_id,
        "action_type": a.action_type, "channel": a.channel,
        "description": a.description, "content_sent": a.content_sent,
        "response_content": a.response_content, "response_type": a.response_type,
        "status": a.status, "outcome": a.outcome,
        "context_note": a.context_note,
        "sequence_id": a.sequence_id, "sequence_step": a.sequence_step,
        "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "draft_revisions": revs,
        "draft_original": (orig or "").strip() or None,
    }


# ── Users ─────────────────────────────────────────────────────

def create_user(username, password, email=None, role="strategist"):
    with db() as s:
        if s.query(User).filter_by(username=username).first():
            return False
        s.add(User(
            username=username,
            password_hash=pbkdf2_sha256.hash(password),
            email=email, role=role,
        ))
        return True


def authenticate(username, password):
    with db() as s:
        u = s.query(User).filter_by(username=username, is_active=True).first()
        if u and pbkdf2_sha256.verify(password, u.password_hash):
            return {"id": u.id, "username": u.username, "email": u.email, "role": u.role}
    return None


def get_user(user_id):
    with db() as s:
        u = s.get(User, user_id)
        if u and u.is_active:
            return {"id": u.id, "username": u.username, "email": u.email, "role": u.role}
    return None


# ── Companies ─────────────────────────────────────────────────

def upsert_company(domain, **kw):
    with db() as s:
        c = s.query(Company).filter_by(domain=domain).first()
        if c:
            for k, v in kw.items():
                if v is not None and hasattr(c, k):
                    setattr(c, k, v)
            c.updated_at = datetime.utcnow()
            s.flush()
            return c.id
        filtered = {k: v for k, v in kw.items() if v is not None and hasattr(Company, k)}
        c = Company(domain=domain, **filtered)
        s.add(c)
        s.flush()
        return c.id


def get_company(company_id):
    with db() as s:
        c = s.get(Company, company_id)
        return _comp_dict(c) if c else None


def merge_company_extra_data(company_id: int, patch: dict):
    """
    Merge keys into Company.extra_data (JSON). Shallow-merge at top level.
    """
    if not isinstance(patch, dict):
        return False
    with db() as s:
        c = s.get(Company, company_id)
        if not c:
            return False
        base = _normalize_extra_data(c.extra_data)
        base.update(patch)
        c.extra_data = dict(base)  # new dict so ORM detects change and persists to DB
        c.updated_at = datetime.utcnow()
        s.flush()
        return True


def get_all_companies(exclude_advisor_shadows=True):
    """Return all companies. When exclude_advisor_shadows=True (default), exclude companies that are shadows for Media Houses children."""
    with db() as s:
        rows = s.query(Company).order_by(Company.name).all()
        out = [_comp_dict(c) for c in rows]
    if exclude_advisor_shadows:
        out = [c for c in out if not (c.get("extra_data") or {}).get("source_advisor_company_id")]
    return out


def get_or_create_shadow_company_for_advisor(advisor_company_id):
    """
    Get or create a (company_id, contact_id) in the main DB that represents this Media Houses child,
    so Strategist/Actions/Timeline can run the same as for Independent contacts.
    Returns (company_id, contact_id) or (None, None) if advisor company not found.
    """
    from db_advisor import get_advisor_company
    adv = get_advisor_company(advisor_company_id)
    if not adv:
        return None, None
    aid = int(advisor_company_id)
    domain = f"__advisor_{aid}"
    with db() as s:
        # Look up by domain first (unique); avoids duplicate INSERT when extra_data is stored as string or missing
        c = s.query(Company).filter_by(domain=domain).first()
        if c:
            company_id = c.id
            ct = s.query(Contact).filter_by(company_id=company_id, name="Leadership").first()
            if ct:
                return company_id, ct.id
            ct = Contact(company_id=company_id, name="Leadership", role="Leadership")
            s.add(ct)
            s.flush()
            return company_id, ct.id
        name = (adv.get("domain") or adv.get("parent_name") or "Child").strip() or "Child"
        c = Company(domain=domain, name=name, extra_data={"source_advisor_company_id": aid})
        s.add(c)
        s.flush()
        company_id = c.id
        ct = Contact(company_id=company_id, name="Leadership", role="Leadership")
        s.add(ct)
        s.flush()
        return company_id, ct.id


def search_companies(q, exclude_advisor_shadows=True):
    """
    Fuzzy search: matches partial domains, names, and strips TLD suffixes.
    When exclude_advisor_shadows=True (default), excludes Media Houses shadow companies.
    """
    with db() as s:
        q = q.strip()
        if not q:
            rows = s.query(Company).order_by(Company.name).all()
            out = [_comp_dict(c) for c in rows]
        else:
            like = f"%{q}%"
            q_no_tld = q
            for tld in (".com", ".in", ".org", ".net", ".co", ".io", ".us", ".uk"):
                if q.lower().endswith(tld):
                    q_no_tld = q[:-len(tld)]
                    break
            like2 = f"%{q_no_tld}%"
            out = [
                _comp_dict(c)
                for c in s.query(Company)
                .filter(
                    or_(
                        Company.domain.ilike(like),
                        Company.name.ilike(like),
                        Company.domain.ilike(like2),
                        Company.name.ilike(like2),
                    )
                )
                .order_by(Company.name)
                .all()
            ]
        if exclude_advisor_shadows:
            out = [c for c in out if not (c.get("extra_data") or {}).get("source_advisor_company_id")]
        return out


# ── Contacts ──────────────────────────────────────────────────

def upsert_contact(company_id, name, **kw):
    with db() as s:
        c = s.query(Contact).filter_by(company_id=company_id, name=name).first()
        if c:
            for k, v in kw.items():
                if v is not None and hasattr(c, k):
                    setattr(c, k, v)
            c.updated_at = datetime.utcnow()
            s.flush()
            return c.id
        filtered = {k: v for k, v in kw.items() if v is not None and hasattr(Contact, k)}
        c = Contact(company_id=company_id, name=name, **filtered)
        s.add(c)
        s.flush()
        return c.id


def get_contacts(company_id):
    with db() as s:
        return [
            _ct_dict(c)
            for c in s.query(Contact).filter_by(company_id=company_id).order_by(Contact.name).all()
        ]


def get_contact(contact_id):
    with db() as s:
        c = s.get(Contact, contact_id)
        return _ct_dict(c) if c else None


def update_contact_status(contact_id, status):
    with db() as s:
        c = s.get(Contact, contact_id)
        if c:
            c.status = status
            c.updated_at = datetime.utcnow()


def update_contact(contact_id, **kw):
    """Update contact fields; extra_data is shallow-merged."""
    with db() as s:
        c = s.get(Contact, contact_id)
        if not c:
            return
        if "extra_data" in kw:
            patch = kw.pop("extra_data")
            base = c.extra_data or {}
            if isinstance(patch, dict):
                base = {**base, **patch}
            c.extra_data = base
        for k, v in kw.items():
            if hasattr(c, k):
                setattr(c, k, v)
        c.updated_at = datetime.utcnow()


def get_user_by_email(email):
    """Return user dict (id, username, email) for the given email, or None. Email match is case-insensitive."""
    if not (email or "").strip():
        return None
    with db() as s:
        u = s.query(User).filter(User.email.ilike((email or "").strip())).first()
        return {"id": u.id, "username": u.username, "email": u.email} if u else None


def get_contact_by_email(email):
    """Return first contact with this email, with company_id. Email match is case-insensitive. Returns dict or None."""
    if not (email or "").strip():
        return None
    with db() as s:
        c = s.query(Contact).filter(Contact.email.ilike((email or "").strip())).first()
        if not c:
            return None
        return {"id": c.id, "company_id": c.company_id, "email": c.email, "name": c.name, "status": c.status}


# ── Enrichment results ───────────────────────────────────────

def save_enrichment_pending(user_id, request_id, identifier):
    """Save a pending enrichment request (real API with callback)."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = EnrichmentResult(
            user_id=user_id, request_id=str(request_id), identifier=identifier,
            status="pending",
        )
        s.add(r)
        s.flush()
        return r.id


def save_enrichment_completed(record_id, result_json):
    """Mark enrichment as completed with result (mock or callback)."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = s.get(EnrichmentResult, record_id)
        if r:
            r.result_json = result_json
            r.status = "completed"


def _ensure_enrichment_table(session):
    """Create enrichment_results table if it does not exist (same engine as session)."""
    try:
        EnrichmentResult.__table__.create(session.get_bind(), checkfirst=True)
    except Exception:
        pass


def save_enrichment_result_direct(user_id, identifier, result_json, status="completed"):
    """Save enrichment result directly (e.g. mock or when callback is not used). Returns record id."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = EnrichmentResult(
            user_id=user_id, identifier=identifier,
            result_json=result_json, status=status,
        )
        s.add(r)
        s.flush()
        return r.id


def get_enrichment_result(record_id):
    """Get enrichment result by record id."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = s.get(EnrichmentResult, record_id)
        if not r:
            return None
        return {
            "id": r.id, "user_id": r.user_id, "request_id": r.request_id,
            "identifier": r.identifier, "result_json": r.result_json,
            "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None,
        }


def get_enrichment_by_request_id(request_id):
    """Get enrichment record by SignalHire request_id (for callback updates)."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = s.query(EnrichmentResult).filter_by(request_id=str(request_id)).first()
        if not r:
            return None
        return {
            "id": r.id, "user_id": r.user_id, "request_id": r.request_id,
            "identifier": r.identifier, "result_json": r.result_json,
            "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None,
        }


def update_enrichment_result(request_id, result_json, status="completed"):
    """Update enrichment by request_id (called from callback handler)."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = s.query(EnrichmentResult).filter_by(request_id=str(request_id)).first()
        if r:
            r.result_json = result_json
            r.status = status


def update_enrichment_result_by_identifier(identifier, result_json, status="completed"):
    """Update first pending enrichment with this identifier (fallback when callback has no Request-Id header)."""
    if not (identifier or "").strip():
        return False
    with db() as s:
        _ensure_enrichment_table(s)
        r = (
            s.query(EnrichmentResult)
            .filter_by(identifier=(identifier or "").strip(), status="pending")
            .first()
        )
        if r:
            r.result_json = result_json
            r.status = status
            return True
    return False


def get_completed_enrichment_by_identifier(identifier):
    """Get most recent completed enrichment for this identifier (e.g. LinkedIn URL). Used to fill email/phone for rows already marked completed but with empty result."""
    if not (identifier or "").strip():
        return None
    ident = (identifier or "").strip().rstrip("/")
    with db() as s:
        _ensure_enrichment_table(s)
        rows = (
            s.query(EnrichmentResult)
            .filter_by(status="completed")
            .order_by(EnrichmentResult.created_at.desc())
            .limit(50)
            .all()
        )
        for r in rows:
            if not r.result_json:
                continue
            stored = (r.identifier or "").strip().rstrip("/")
            if stored != ident:
                continue
            res = r.result_json or {}
            if res.get("emails") or res.get("phones"):
                return {
                    "id": r.id, "request_id": r.request_id, "identifier": r.identifier,
                    "result_json": res, "status": r.status,
                }
    return None


def get_latest_enrichment_for_user(user_id, status="completed"):
    """Get most recent completed enrichment for user (for Add to company)."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = (
            s.query(EnrichmentResult)
            .filter_by(user_id=user_id, status=status)
            .order_by(EnrichmentResult.created_at.desc())
            .first()
        )
        if not r:
            return None
        return {
            "id": r.id, "user_id": r.user_id, "request_id": r.request_id,
            "identifier": r.identifier, "result_json": r.result_json or {},
            "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None,
        }


# ── SPOC contacts (strategist-saved, tagged under a SPOC; separate table, distinct email/linkedin/phone) ──

def _ensure_spoc_contacts_table(session):
    try:
        SpocContact.__table__.create(session.get_bind(), checkfirst=True)
    except Exception:
        pass


def upsert_spoc_contact(company_id, spoc_contact_id, name, role=None, email=None, linkedin_url=None, phone=None, extra_data=None):
    """
    Create or update a contact under a SPOC in spoc_contacts. Match by (company_id, spoc_contact_id, name).
    Returns the spoc_contact row id.
    """
    if not spoc_contact_id:
        return None
    with db() as s:
        _ensure_spoc_contacts_table(s)
        row = s.query(SpocContact).filter_by(
            company_id=company_id, spoc_contact_id=spoc_contact_id, name=(name or "").strip()
        ).first()
        if row:
            if role is not None:
                row.role = role
            if email is not None:
                row.email = email
            if linkedin_url is not None:
                row.linkedin_url = linkedin_url
            if phone is not None:
                row.phone = phone
            if extra_data is not None and isinstance(extra_data, dict):
                row.extra_data = {**(row.extra_data or {}), **extra_data}
            row.updated_at = datetime.utcnow()
            s.flush()
            return row.id
        row = SpocContact(
            company_id=company_id, spoc_contact_id=spoc_contact_id, name=(name or "").strip(),
            role=role, email=email or None, linkedin_url=linkedin_url or None, phone=phone or None,
            extra_data=extra_data,
        )
        s.add(row)
        s.flush()
        return row.id


def get_spoc_contacts_by_spoc(spoc_contact_id):
    """All contacts tagged under this SPOC (contact id)."""
    with db() as s:
        _ensure_spoc_contacts_table(s)
        return [
            {
                "id": r.id, "company_id": r.company_id, "spoc_contact_id": r.spoc_contact_id,
                "name": r.name, "role": r.role, "email": r.email, "linkedin_url": r.linkedin_url,
                "phone": r.phone, "extra_data": r.extra_data,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in s.query(SpocContact).filter_by(spoc_contact_id=spoc_contact_id).order_by(SpocContact.name).all()
        ]


def get_spoc_contacts_by_company(company_id):
    """All SPOC-tagged contacts for this company."""
    with db() as s:
        _ensure_spoc_contacts_table(s)
        return [
            {
                "id": r.id, "company_id": r.company_id, "spoc_contact_id": r.spoc_contact_id,
                "name": r.name, "role": r.role, "email": r.email, "linkedin_url": r.linkedin_url,
                "phone": r.phone, "extra_data": r.extra_data,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in s.query(SpocContact).filter_by(company_id=company_id).order_by(SpocContact.spoc_contact_id, SpocContact.name).all()
        ]


def delete_spoc_contact(spoc_contact_id: int):
    """Delete a SPOC contact by its spoc_contacts.id."""
    if not spoc_contact_id:
        return False
    with db() as s:
        _ensure_spoc_contacts_table(s)
        row = s.get(SpocContact, spoc_contact_id)
        if not row:
            return False
        s.delete(row)
        s.flush()
        return True


# ── Sessions ──────────────────────────────────────────────────

def get_or_create_session(company_id, contact_id, user_id, score=0):
    """
    One session per (company_id, contact_id) shared by all users. Find by company+contact only;
    if none exists, create with owner_user_id=user_id.
    """
    with db() as s:
        q = s.query(Session).filter_by(company_id=company_id)
        if contact_id is not None:
            q = q.filter_by(contact_id=contact_id)
        else:
            q = q.filter(Session.contact_id.is_(None))
        sess = q.order_by(Session.updated_at.desc()).first()

        if sess:
            sess.updated_at = datetime.utcnow()
            sess.lead_score = score
            s.flush()
            return sess.id

        sess = Session(
            company_id=company_id, contact_id=contact_id,
            owner_user_id=user_id, lead_score=score,
        )
        s.add(sess)
        s.flush()
        return sess.id


def get_session(sid):
    with db() as s:
        sess = s.get(Session, sid)
        return _sess_dict(sess) if sess else None


def get_sessions_for_company(company_id):
    with db() as s:
        return [
            _sess_dict(x)
            for x in s.query(Session).filter_by(company_id=company_id)
            .order_by(Session.updated_at.desc()).all()
        ]


def get_user_sessions(user_id):
    """Sessions owned by this user (kept for compatibility). Prefer get_all_sessions for shared visibility."""
    with db() as s:
        results = []
        for sess in s.query(Session).filter_by(owner_user_id=user_id).order_by(Session.updated_at.desc()).all():
            d = _sess_dict(sess)
            try:
                comp = s.get(Company, sess.company_id) if sess.company_id else None
            except Exception as e:
                log.error("get_user_sessions: failed to load Company %s for session %s: %s", sess.company_id, sess.id, e)
                comp = None
            try:
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
            except Exception as e:
                log.error("get_user_sessions: failed to load Contact %s for session %s: %s", sess.contact_id, sess.id, e)
                ct = None
            if comp:
                d["company_domain"] = comp.domain
                d["company_name"] = comp.name
                ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                if ed:
                    d["source_advisor_company_id"] = ed.get("source_advisor_company_id")
            if ct:
                d["contact_name"] = ct.name
                d["contact_role"] = ct.role
            results.append(d)
        return results


def get_all_sessions():
    """All sessions (shared visibility). Includes company_domain, contact_name, owner_username."""
    with db() as s:
        results = []
        for sess in s.query(Session).order_by(Session.updated_at.desc()).all():
            d = _sess_dict(sess)
            try:
                comp = s.get(Company, sess.company_id) if sess.company_id else None
            except Exception as e:
                log.error("get_all_sessions: failed to load Company %s for session %s: %s", sess.company_id, sess.id, e)
                comp = None
            try:
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
            except Exception as e:
                log.error("get_all_sessions: failed to load Contact %s for session %s: %s", sess.contact_id, sess.id, e)
                ct = None
            owner = s.get(User, sess.owner_user_id) if sess.owner_user_id else None
            if owner:
                d["owner_username"] = owner.username
            if comp:
                d["company_domain"] = comp.domain
                d["company_name"] = comp.name
                ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                if ed:
                    d["source_advisor_company_id"] = ed.get("source_advisor_company_id")
            if ct:
                d["contact_name"] = ct.name
                d["contact_role"] = ct.role
            results.append(d)
        return results


def get_session_for_contact(company_id, contact_id):
    """Return the (shared) session for this company+contact, or None. Used for timeline."""
    with db() as s:
        q = s.query(Session).filter_by(company_id=company_id)
        if contact_id is not None:
            q = q.filter_by(contact_id=contact_id)
        else:
            q = q.filter(Session.contact_id.is_(None))
        sess = q.order_by(Session.updated_at.desc()).first()
        if not sess:
            return None
        d = _sess_dict(sess)
        comp = s.get(Company, sess.company_id) if sess.company_id else None
        ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
        if comp:
            d["company_domain"] = comp.domain
            d["company_name"] = comp.name
        if ct:
            d["contact_name"] = ct.name
            d["contact_role"] = ct.role
        return d


def delete_session(sid):
    with db() as s:
        sess = s.get(Session, sid)
        if sess:
            s.query(Message).filter_by(session_id=sid).delete()
            s.query(Action).filter_by(session_id=sid).delete()
            s.query(MemoryEvent).filter_by(session_id=sid).delete()
            s.delete(sess)


# ── Messages ──────────────────────────────────────────────────

def save_message(sid, role, content):
    with db() as s:
        s.add(Message(session_id=sid, role=role, content=content))
        sess = s.get(Session, sid)
        if sess:
            sess.updated_at = datetime.utcnow()


def load_messages(sid):
    with db() as s:
        return [
            {"id": m.id, "role": m.role, "content": m.content,
             "created_at": m.created_at.isoformat()}
            for m in s.query(Message).filter_by(session_id=sid)
            .order_by(Message.created_at.asc()).all()
        ]


def get_message_count(sid):
    with db() as s:
        return s.query(Message).filter_by(session_id=sid).count()


# ── Actions ───────────────────────────────────────────────────

def log_action(sid, uid, atype, channel, desc, status="planned",
               content_sent=None, context_note=None, scheduled_at=None,
               seq_id=None, seq_step=None):
    with db() as s:
        sched = None
        if scheduled_at:
            try:
                sched = datetime.fromisoformat(scheduled_at)
            except (ValueError, TypeError):
                log.warning("Could not parse scheduled_at: %s", scheduled_at)
        a = Action(
            session_id=sid, user_id=uid, action_type=atype,
            channel=channel, description=desc, status=status,
            content_sent=content_sent, context_note=context_note,
            scheduled_at=sched, sequence_id=seq_id, sequence_step=seq_step,
        )
        s.add(a)
        s.flush()
        return a.id


def is_gmail_message_processed(user_id, gmail_message_id):
    """True if we already recorded this Gmail message for this user (idempotency)."""
    with db() as s:
        return s.query(ProcessedGmailMessage).filter_by(
            user_id=user_id, gmail_message_id=str(gmail_message_id)
        ).first() is not None


def mark_gmail_message_processed(user_id, gmail_message_id):
    """Record that we processed this Gmail message so we don't double-log."""
    with db() as s:
        try:
            s.add(ProcessedGmailMessage(user_id=user_id, gmail_message_id=str(gmail_message_id)))
        except Exception:
            s.rollback()
            pass  # ignore duplicate


def record_sent_email(sender_email, recipient_email, subject, body, gmail_message_id, sent_at=None):
    """
    When a Command Center user (sender_email) sends an email to a contact (recipient_email):
    find or create session, log a completed Email action, update contact status if needed.
    Returns (action_id, None) on success, or (None, error_message) on failure.
    Idempotent: if this gmail_message_id was already processed for this user, returns (None, "already_processed").
    """
    user = get_user_by_email(sender_email)
    if not user:
        return None, "sender_not_found"
    contact = get_contact_by_email(recipient_email)
    if not contact:
        return None, "recipient_not_found"
    if is_gmail_message_processed(user["id"], gmail_message_id):
        return None, "already_processed"
    score = 0
    try:
        sid = get_or_create_session(
            contact["company_id"], contact["id"], user["id"], score=score
        )
    except Exception as e:
        log.exception("get_or_create_session failed: %s", e)
        return None, str(e)
    desc = f"Email to {contact.get('name') or recipient_email}"
    if (subject or "").strip():
        desc += f" — {subject.strip()[:80]}"
    content_sent = None
    if (subject or "").strip() or (body or "").strip():
        content_sent = f"Subject: {subject or ''}\n\n{body or ''}"
    try:
        aid = log_action(
            sid, user["id"], "Outreach", "Email", desc, status="completed",
            content_sent=(content_sent[:50000] if content_sent else None),  # allow full thread
        )
    except Exception as e:
        log.exception("log_action failed: %s", e)
        return None, str(e)
    if aid and sent_at:
        with db() as s:
            a = s.get(Action, aid)
            if a:
                try:
                    a.completed_at = sent_at if hasattr(sent_at, "isoformat") else datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
                except Exception:
                    a.completed_at = datetime.utcnow()
    if (contact.get("status") or "").strip() == "not_contacted":
        update_contact_status(contact["id"], "contacted")
    mark_gmail_message_processed(user["id"], gmail_message_id)
    return aid, None


def get_session_actions(sid):
    with db() as s:
        return [
            _act_dict(a) for a in
            s.query(Action).filter_by(session_id=sid).order_by(Action.created_at.desc()).all()
        ]


def get_user_actions(uid, limit=50):
    """Actions by this user (kept for compatibility). Prefer get_all_actions for shared visibility."""
    with db() as s:
        results = []
        for a in s.query(Action).filter_by(user_id=uid).order_by(Action.created_at.desc()).limit(limit).all():
            d = _act_dict(a)
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp:
                    d["company_domain"] = comp.domain
                if ct:
                    d["contact_name"] = ct.name
            results.append(d)
        return results


def get_all_actions(limit=200):
    """All actions (shared visibility). Includes username for who logged it."""
    with db() as s:
        results = []
        for a in s.query(Action).order_by(Action.created_at.desc()).limit(limit).all():
            d = _act_dict(a)
            u = s.get(User, a.user_id)
            if u:
                d["username"] = u.username
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp:
                    d["company_domain"] = comp.domain
                if ct:
                    d["contact_name"] = ct.name
            results.append(d)
        return results


def get_timeline_actions(company_id=None, contact_id=None, limit=100):
    with db() as s:
        q = s.query(Action).join(Session, Action.session_id == Session.id)
        if company_id:
            q = q.filter(Session.company_id == company_id)
        if contact_id:
            q = q.filter(Session.contact_id == contact_id)
        results = []
        for a in q.order_by(Action.created_at.desc()).limit(limit).all():
            d = _act_dict(a)
            u = s.get(User, a.user_id)
            if u:
                d["username"] = u.username
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                if comp:
                    d["company_domain"] = comp.domain
                    ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                    d["source_advisor_company_id"] = ed.get("source_advisor_company_id")
                if sess.contact_id:
                    ct = s.get(Contact, sess.contact_id)
                    if ct:
                        d["contact_name"] = ct.name
            results.append(d)
        return results


def update_action(aid, status=None, outcome=None, response_content=None, response_type=None,
                  description=None, scheduled_at=None, action_type=None, channel=None, content_sent=None,
                  draft_revisions=None, draft_original=None):
    with db() as s:
        a = s.get(Action, aid)
        if not a:
            return
        if status is not None:
            a.status = status
            if status in ("completed", "deal_closed", "meeting_booked"):
                a.completed_at = datetime.utcnow()
        if outcome is not None:
            a.outcome = outcome
        if response_content is not None:
            a.response_content = response_content
        if response_type is not None:
            a.response_type = response_type
        if description is not None:
            a.description = description
        if scheduled_at is not None:
            if isinstance(scheduled_at, str):
                try:
                    a.scheduled_at = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                except Exception:
                    pass
            else:
                a.scheduled_at = scheduled_at
        if action_type is not None:
            a.action_type = action_type
        if channel is not None:
            a.channel = channel
        if content_sent is not None:
            a.content_sent = content_sent
        if draft_revisions is not None:
            if hasattr(a, "draft_revisions"):
                a.draft_revisions = draft_revisions if isinstance(draft_revisions, list) else list(draft_revisions)
        if draft_original is not None:
            if hasattr(a, "draft_original"):
                a.draft_original = draft_original


def delete_action(aid):
    with db() as s:
        a = s.get(Action, aid)
        if a:
            s.delete(a)


def get_overdue_followups(days=3):
    cutoff = datetime.utcnow() - timedelta(days=days)
    with db() as s:
        results = []
        for a in (
            s.query(Action)
            .filter(
                Action.scheduled_at < cutoff,
                Action.completed_at.is_(None),
                Action.status.in_(["planned", "sent"]),
            )
            .order_by(Action.scheduled_at)
            .all()
        ):
            d = _act_dict(a)
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp:
                    d["company_domain"] = comp.domain
                if ct:
                    d["contact_name"] = ct.name
            results.append(d)
        return results


def get_todays_scheduled():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
    today_end = today_start + timedelta(days=1)
    with db() as s:
        results = []
        for a in (
            s.query(Action)
            .filter(Action.scheduled_at >= today_start, Action.scheduled_at < today_end)
            .order_by(Action.scheduled_at)
            .all()
        ):
            d = _act_dict(a)
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp:
                    d["company_domain"] = comp.domain
                if ct:
                    d["contact_name"] = ct.name
            results.append(d)
        return results


def get_recent_activity(limit=20):
    with db() as s:
        results = []
        for a in s.query(Action).order_by(Action.created_at.desc()).limit(limit).all():
            d = _act_dict(a)
            u = s.get(User, a.user_id)
            if u:
                d["username"] = u.username
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp:
                    d["company_domain"] = comp.domain
                if ct:
                    d["contact_name"] = ct.name
            results.append(d)
        return results


# ── Memory Events (L3) ───────────────────────────────────────

def save_memory_event(sid, event_type, summary, importance=5):
    with db() as s:
        s.add(MemoryEvent(
            session_id=sid, event_type=event_type,
            summary=summary, importance_score=min(importance, 10),
        ))


def get_memory_events(sid, min_imp=3, limit=10):
    with db() as s:
        return [
            {
                "id": e.id, "event_type": e.event_type,
                "summary": e.summary, "importance_score": e.importance_score,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in s.query(MemoryEvent)
            .filter(MemoryEvent.session_id == sid, MemoryEvent.importance_score >= min_imp)
            .order_by(MemoryEvent.importance_score.desc(), MemoryEvent.created_at.desc())
            .limit(limit)
            .all()
        ]


# ── Global Insights (L4) ─────────────────────────────────────

def save_global_insight(uid, insight, confidence=0.5):
    with db() as s:
        s.add(GlobalInsight(user_id=uid, insight=insight, confidence_score=confidence))


def get_global_insights(uid, limit=10):
    """Insights for this user (kept for compatibility). Prefer get_all_global_insights for shared visibility."""
    with db() as s:
        return [
            {
                "id": g.id, "insight": g.insight,
                "confidence_score": g.confidence_score,
                "source_count": g.source_count,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
            for g in s.query(GlobalInsight).filter_by(user_id=uid)
            .order_by(GlobalInsight.confidence_score.desc(), GlobalInsight.created_at.desc())
            .limit(limit)
            .all()
        ]


def get_all_global_insights(limit=20):
    """All global insights (shared visibility). Includes username."""
    with db() as s:
        results = []
        for g in s.query(GlobalInsight).order_by(
            GlobalInsight.confidence_score.desc(), GlobalInsight.created_at.desc()
        ).limit(limit).all():
            d = {
                "id": g.id, "insight": g.insight,
                "confidence_score": g.confidence_score,
                "source_count": g.source_count,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
            u = s.get(User, g.user_id)
            if u:
                d["username"] = u.username
            results.append(d)
        return results


# ── Cached contexts ────────────────────────────────────────────

_cached_contexts_ready = False


def _ensure_cached_contexts_table():
    """Create cached_contexts table once if missing."""
    global _cached_contexts_ready
    if _cached_contexts_ready:
        return
    init_db()
    eng = get_engine()
    if not eng:
        return
    try:
        CachedContext.__table__.create(bind=eng, checkfirst=True)
        _cached_contexts_ready = True
    except Exception:
        # If creation fails, leave ready as False to retry later.
        _cached_contexts_ready = False


def get_cached_context(key: str, max_age_sec: int | None = None):
    """Return cached text for key, or None if missing/stale."""
    if not key:
        return None
    _ensure_cached_contexts_table()
    with db() as s:
        try:
            row = s.query(CachedContext).filter_by(key=key).first()
        except OperationalError as e:
            if "cached_contexts" in str(e).lower():
                return None
            raise
        if not row:
            return None
        if max_age_sec and row.updated_at:
            age = (datetime.utcnow() - row.updated_at).total_seconds()
            if age > max_age_sec:
                return None
        return row.content or ""


def save_cached_context(key: str, content: str):
    """Upsert cached text for key."""
    if not key:
        return False
    _ensure_cached_contexts_table()
    with db() as s:
        try:
            row = s.query(CachedContext).filter_by(key=key).first()
        except OperationalError as e:
            if "cached_contexts" in str(e).lower():
                return False
            raise
        if row:
            row.content = content or ""
            row.updated_at = datetime.utcnow()
        else:
            row = CachedContext(key=key, content=content or "", updated_at=datetime.utcnow())
            s.add(row)
        s.flush()
        return True


def delete_cached_contexts_with_prefix(prefix: str) -> int:
    """Delete cached contexts whose keys start with prefix."""
    if not prefix:
        return 0
    _ensure_cached_contexts_table()
    with db() as s:
        try:
            rows = s.query(CachedContext).filter(CachedContext.key.like(f"{prefix}%")).all()
        except OperationalError as e:
            if "cached_contexts" in str(e).lower():
                return 0
            raise
        count = len(rows)
        for row in rows:
            s.delete(row)
        s.flush()
        return count


# ── Session Summary (L2) ─────────────────────────────────────

def get_session_summary(sid):
    with db() as s:
        sess = s.get(Session, sid)
        if sess:
            return (sess.strategic_summary, sess.summary_msg_count or 0)
        return (None, 0)


def save_session_summary(sid, summary, msg_count=0):
    """
    Save strategic summary to sessions table.
    
    Args:
        sid: Session ID
        summary: Strategic summary text
        msg_count: Message count (default: 0)
    """
    with db() as s:
        sess = s.get(Session, sid)
        if sess:
            sess.strategic_summary = summary
            sess.summary_msg_count = msg_count
            s.flush()  # Ensure changes are committed
        # Context manager auto-commits on exit


# ── Users ─────────────────────────────────────────────────────

def get_all_users():
    with db() as s:
        return [
            {"id": u.id, "username": u.username, "email": u.email, "role": u.role}
            for u in s.query(User).filter_by(is_active=True).order_by(User.username).all()
        ]


# ── Sequence Actions ──────────────────────────────────────────

def bulk_log_actions(sid, uid, actions_list, seq_id=None):
    """
    Add a batch of actions as a sequence.
    actions_list: list of dicts with keys: channel, description, status, scheduled_at, content_sent
    Returns list of created action IDs.
    """
    import uuid
    if not seq_id:
        seq_id = f"SEQ-{uuid.uuid4().hex[:8].upper()}"
    ids = []
    with db() as s:
        for step, act in enumerate(actions_list, 1):
            sched = None
            if act.get("scheduled_at"):
                try:
                    sched = datetime.fromisoformat(str(act["scheduled_at"]))
                except (ValueError, TypeError):
                    pass
            a = Action(
                session_id=sid, user_id=uid,
                action_type=act.get("action_type", "Outreach"),
                channel=act.get("channel", "Email"),
                description=act.get("description", ""),
                status=act.get("status", "planned"),
                content_sent=act.get("content_sent"),
                context_note=act.get("context_note"),
                scheduled_at=sched,
                sequence_id=seq_id,
                sequence_step=step,
            )
            s.add(a)
            s.flush()
            ids.append(a.id)
    return ids, seq_id


def get_sequence_actions(sid, seq_id):
    """Get all actions in a sequence, ordered by step."""
    with db() as s:
        return [
            _act_dict(a) for a in
            s.query(Action).filter_by(session_id=sid, sequence_id=seq_id)
            .order_by(Action.sequence_step).all()
        ]


def get_active_sequences(sid):
    """Get all unique sequence IDs for a session with their status summary."""
    with db() as s:
        actions = s.query(Action).filter(
            Action.session_id == sid,
            Action.sequence_id.isnot(None),
        ).order_by(Action.sequence_step).all()

        seqs = {}
        for a in actions:
            if a.sequence_id not in seqs:
                seqs[a.sequence_id] = {"id": a.sequence_id, "steps": [], "total": 0, "done": 0}
            d = _act_dict(a)
            seqs[a.sequence_id]["steps"].append(d)
            seqs[a.sequence_id]["total"] += 1
            if a.status in ("sent", "completed", "replied_positive", "replied_negative",
                            "meeting_booked", "deal_closed", "deal_lost", "no_response"):
                seqs[a.sequence_id]["done"] += 1
        return list(seqs.values())


# ── Pipeline Stats ────────────────────────────────────────────

def get_pipeline_stats(use_cache=True):
    """
    Counts by pipeline status. 'outreach_sent' includes legacy awaiting_reply/replied_negative.
    
    Args:
        use_cache: If True and DATABASE_URL is set (PostgreSQL), use materialized view.
                   If False or SQLite, fall back to direct query.
    
    Returns:
        Dict mapping status -> count
    """
    # Use materialized view if PostgreSQL and cache is enabled
    if use_cache and DATABASE_URL and DATABASE_URL.startswith("postgresql"):
        try:
            with db() as s:
                # Query the materialized view for fast cached results
                result = s.execute(text(
                    "SELECT status, contact_count FROM mv_pipeline_stats WHERE status != '_metadata'"
                ))
                counts = {row[0]: int(row[1]) for row in result}
                
                # Ensure all expected statuses are present
                for status in CONTACT_STATUSES:
                    if status not in counts:
                        counts[status] = 0
                
                return counts
        except Exception as e:
            # Fallback to direct query if view doesn't exist yet
            import logging
            logging.warning(f"Failed to query mv_pipeline_stats, falling back to direct query: {e}")
    
    # Fallback: Direct query (SQLite or if materialized view unavailable)
    with db() as s:
        counts = {}
        for status in CONTACT_STATUSES:
            if status == "outreach_sent":
                counts[status] = s.query(Contact).filter(
                    or_(Contact.status == "outreach_sent", Contact.status == "awaiting_reply", Contact.status == "replied_negative")
                ).count()
            else:
                counts[status] = s.query(Contact).filter_by(status=status).count()
        return counts


def refresh_pipeline_stats():
    """
    Refresh the materialized view mv_pipeline_stats.
    
    This function should be called after bulk contact updates to ensure
    pipeline statistics are current. For PostgreSQL/Supabase only.
    
    Architecture: Implements the performance optimization strategy from
    .cursorrules by using cached aggregates instead of live queries.
    
    Usage:
        # After bulk status updates
        for contact in contacts:
            update_contact_status(contact.id, "replied_positive")
        refresh_pipeline_stats()  # Update cached stats
        
        # Or from services/ui_data_loader.py
        from db_ops import refresh_pipeline_stats
        clear_ui_cache()
        refresh_pipeline_stats()
    
    Returns:
        bool: True if refresh succeeded, False if not PostgreSQL or view doesn't exist
    """
    if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
        # SQLite doesn't support materialized views
        return False
    
    try:
        with db() as s:
            # Use CONCURRENTLY to avoid locking the view during refresh
            # This allows reads to continue using stale data while refresh is in progress
            s.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_pipeline_stats"))
            s.commit()
        return True
    except Exception as e:
        import logging
        logging.error(f"Failed to refresh mv_pipeline_stats: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# ADVISOR CONVERSATIONS
# ══════════════════════════════════════════════════════════════

def save_advisor_conversation(user_id, messages, title=None):
    """Save or update advisor conversation."""
    with db() as s:
        if not title:
            from ai_engine import summarize_advisor_title
            title = summarize_advisor_title(messages)
        
        conv = AdvisorConversation(
            user_id=user_id,
            title=title,
            messages=messages
        )
        s.add(conv)
        s.commit()
        return conv.id


def get_advisor_conversations(user_id, limit=20):
    """Advisor conversations for this user (kept for compatibility). Prefer get_all_advisor_conversations for shared visibility."""
    with db() as s:
        convs = s.query(AdvisorConversation).filter_by(user_id=user_id).order_by(AdvisorConversation.updated_at.desc()).limit(limit).all()
        return [{"id": c.id, "title": c.title, "messages": c.messages, "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat(), "msg_count": len(c.messages)} for c in convs]


def get_all_advisor_conversations(limit=30):
    """All saved advisor conversations (shared visibility). Includes username."""
    with db() as s:
        convs = s.query(AdvisorConversation).order_by(AdvisorConversation.updated_at.desc()).limit(limit).all()
        out = []
        for c in convs:
            d = {"id": c.id, "title": c.title, "messages": c.messages, "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat(), "msg_count": len(c.messages)}
            u = s.get(User, c.user_id)
            if u:
                d["username"] = u.username
            out.append(d)
        return out


def load_advisor_conversation(conv_id):
    """Load specific advisor conversation."""
    with db() as s:
        conv = s.get(AdvisorConversation, conv_id)
        if conv:
            return {"id": conv.id, "title": conv.title, "messages": conv.messages}
        return None


def delete_advisor_conversation(conv_id):
    """Delete advisor conversation."""
    with db() as s:
        conv = s.get(AdvisorConversation, conv_id)
        if conv:
            s.delete(conv)
            s.commit()
            return True
        return False