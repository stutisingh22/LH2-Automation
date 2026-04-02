"""
db_ops.py — Database engine, session factory, and all CRUD operations

FIX LOG:
  - SQLite path is now under sqlite-db/ (project or ~/.lh2_platform/sqlite-db) so data persists and stays in one folder
  - Search does fuzzy matching: strips TLDs and matches partial domain names
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import threading

from passlib.hash import pbkdf2_sha256
from sqlalchemy import create_engine, or_, text, func
from sqlalchemy.orm import load_only, sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool, StaticPool, NullPool
from sqlalchemy.exc import OperationalError, IntegrityError

from config import DATABASE_URL, CONTACT_STATUSES, normalize_domain
from models import (
    Base, User, Company, Contact, Session, Message,
    Action, MemoryEvent, GlobalInsight, AdvisorConversation, EnrichmentResult,
    ProcessedGmailMessage, SpocContact, CachedContext, AdvisorCompany,
    GmailAccount, EmailThread, InboxEmail, GmailOutreachSyncEvent, Campaign,
)
from sqlite_db_connection import get_local_db_directory, create_platform_engine

log = logging.getLogger(__name__)


# ── Engine + Session Factory ──────────────────────────────────

_engine = None  # Set by init_db(); use get_engine() for code that needs the engine
_session_factory_lock = threading.Lock()
_session_factory = None


def _create_sqlite_engine():
    return create_platform_engine()


def ensure_performance_indexes(engine):
    """Create composite indexes if missing (safe for existing DBs)."""
    stmts = [
        "CREATE INDEX IF NOT EXISTS ix_contacts_company_id_name ON contacts (company_id, name)",
        "CREATE INDEX IF NOT EXISTS ix_sessions_company_id_updated_at ON sessions (company_id, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_actions_session_id_created_at ON actions (session_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_spoc_contacts_company_spoc ON spoc_contacts (company_id, spoc_contact_id)",
    ]
    try:
        with engine.begin() as conn:
            for sql in stmts:
                conn.execute(text(sql))
    except Exception as e:
        log.warning("ensure_performance_indexes: %s", e)


def init_db():
    """Initialize engine and return sessionmaker (singleton; safe for Streamlit + FastAPI)."""
    global _engine, _session_factory
    if _session_factory is not None:
        return _session_factory
    with _session_factory_lock:
        if _session_factory is not None:
            return _session_factory
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
        for col, typ in (
            ("outcome_reason", "TEXT"),
            ("outreach_sync_meta", "TEXT" if is_sqlite else "JSON"),
        ):
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
        try:
            with engine.connect() as conn:
                with conn.begin():
                    conn.execute(
                        text(f"ALTER TABLE sessions ADD COLUMN extra_data {('TEXT' if is_sqlite else 'JSON')}")
                    )
        except Exception as e:
            err = str(e).lower()
            if "duplicate column" in err or "already exists" in err:
                pass
            else:
                log.warning("Migration sessions.extra_data: %s", e)
        try:
            GmailOutreachSyncEvent.__table__.create(bind=engine, checkfirst=True)
        except Exception as e:
            log.warning("gmail_outreach_sync_events table: %s", e)
        for col, typ in (
            ("last_sync_at", "DATETIME" if is_sqlite else "TIMESTAMP"),
            ("last_sync_error", "TEXT"),
            ("last_sync_stats", "TEXT" if is_sqlite else "JSON"),
        ):
            try:
                with engine.connect() as conn:
                    with conn.begin():
                        conn.execute(text(f"ALTER TABLE gmail_accounts ADD COLUMN {col} {typ}"))
            except Exception as e:
                err = str(e).lower()
                if "duplicate column" in err or "already exists" in err:
                    pass
                else:
                    log.warning("Migration gmail_accounts.%s: %s", col, e)
        ensure_performance_indexes(engine)
        db_type = "PostgreSQL" if "postgresql" in (engine.url.drivername or "") else "SQLite"
        print(f"DB connected successfully ({db_type})")
        _engine = engine
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        return _session_factory


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
                f"If using SQLite, ensure the file and directory are writable (see sqlite-db/). "
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
    ed = getattr(x, "extra_data", None)
    return {
        "id": x.id, "company_id": x.company_id,
        "contact_id": x.contact_id, "owner_user_id": x.owner_user_id,
        "strategic_summary": x.strategic_summary,
        "summary_msg_count": x.summary_msg_count or 0,
        "lead_score": x.lead_score or 0,
        "extra_data": _normalize_extra_data(ed),
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
    sync_meta = getattr(a, "outreach_sync_meta", None)
    if sync_meta is not None and not isinstance(sync_meta, dict):
        sync_meta = dict(sync_meta) if sync_meta else None
    return {
        "id": a.id, "session_id": a.session_id, "user_id": a.user_id,
        "action_type": a.action_type, "channel": a.channel,
        "description": a.description, "content_sent": a.content_sent,
        "response_content": a.response_content, "response_type": a.response_type,
        "status": a.status, "outcome": a.outcome,
        "outcome_reason": getattr(a, "outcome_reason", None),
        "outreach_sync_meta": sync_meta,
        "context_note": a.context_note,
        "sequence_id": a.sequence_id, "sequence_step": a.sequence_step,
        "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "draft_revisions": revs,
        "draft_original": (orig or "").strip() or None,
    }


def _campaign_dict(c):
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "starts_at": c.starts_at.isoformat() if c.starts_at else None,
        "ends_at": c.ends_at.isoformat() if c.ends_at else None,
        "extra_data": _normalize_extra_data(c.extra_data),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def list_campaigns(include_inactive: bool = True):
    with db() as s:
        q = s.query(Campaign)
        if not include_inactive:
            q = q.filter(Campaign.status == "active")
        return [_campaign_dict(x) for x in q.order_by(Campaign.created_at.desc()).all()]


def get_campaign(campaign_id: int):
    with db() as s:
        row = s.get(Campaign, int(campaign_id)) if campaign_id else None
        return _campaign_dict(row) if row else None


def create_campaign(
    name: str,
    *,
    status: str = "active",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    extra_data: dict | None = None,
):
    nm = (name or "").strip()
    if not nm:
        raise ValueError("Campaign name is required")
    st = (status or "active").strip().lower() or "active"
    with db() as s:
        existing = s.query(Campaign).filter(func.lower(Campaign.name) == nm.lower()).first()
        if existing:
            return _campaign_dict(existing)
        row = Campaign(
            name=nm,
            status=st,
            starts_at=starts_at,
            ends_at=ends_at,
            extra_data=extra_data or {},
        )
        s.add(row)
        s.flush()
        return _campaign_dict(row)


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


def get_company_by_domain(domain):
    """Return company dict if a company with this domain exists (normalized: www trimmed). None otherwise."""
    norm = normalize_domain(domain)
    if not norm:
        return None
    with db() as s:
        www = "www." + norm
        c = (
            s.query(Company)
            .filter(
                or_(
                    Company.domain == norm,
                    Company.domain == www,
                    func.lower(Company.domain) == norm,
                    func.lower(Company.domain) == www,
                )
            )
            .first()
        )
        return _comp_dict(c) if c else None


def get_company_platform_presence_bundle_by_domain(domain) -> dict:
    """
    Lightweight read for intelligence UI: only platform_presence / social_from_search
    from extra_data (avoids loading the full company row for overlay merge).
    """
    norm = normalize_domain(domain)
    if not norm:
        return {}
    www = "www." + norm
    with db() as s:
        c = (
            s.query(Company)
            .filter(
                or_(
                    Company.domain == norm,
                    Company.domain == www,
                    func.lower(Company.domain) == norm,
                    func.lower(Company.domain) == www,
                )
            )
            .options(load_only(Company.extra_data))
            .first()
        )
        if not c:
            return {}
        ed = _normalize_extra_data(c.extra_data)
        if not isinstance(ed, dict):
            return {}
        out = {}
        if ed.get("platform_presence"):
            out["platform_presence"] = ed["platform_presence"]
        if ed.get("social_from_search"):
            out["social_from_search"] = ed["social_from_search"]
        return out


def bulk_get_monthly_visits_by_domains(domains) -> dict[str, int | None]:
    """
    normalized domain -> monthly_visits for companies matching get_company_by_domain rules.
    Uses IN lists instead of 4N OR clauses (avoids huge SQL and SQLite's ~999 variable limit).
    """
    norms = {d for d in domains if d}
    if not norms:
        return {}
    variants_list = sorted(norms | {f"www.{n}" for n in norms})
    eng = get_engine()
    is_sqlite = "sqlite" in (eng.url.drivername or "")
    # Two IN(chunk) clauses => 2 * len(chunk) bound params; SQLite default max is often 999.
    batch_size = 450 if is_sqlite else 8000

    out: dict[str, int | None] = {}
    with db() as s:
        for i in range(0, len(variants_list), batch_size):
            chunk = variants_list[i : i + batch_size]
            if not chunk:
                continue
            rows = (
                s.query(Company.domain, Company.monthly_visits)
                .filter(
                    or_(
                        Company.domain.in_(chunk),
                        func.lower(Company.domain).in_(chunk),
                    )
                )
                .all()
            )
            for domain_val, mv in rows:
                nk = normalize_domain(domain_val or "")
                if nk in norms:
                    out[nk] = mv
    return out


def delete_company_by_domain(domain) -> bool:
    """Delete a company row by domain (normalized: www trimmed). Returns True if deleted."""
    norm = normalize_domain(domain)
    if not norm:
        return False
    with db() as s:
        www = "www." + norm
        c = (
            s.query(Company)
            .filter(
                or_(
                    Company.domain == norm,
                    Company.domain == www,
                    func.lower(Company.domain) == norm,
                    func.lower(Company.domain) == www,
                )
            )
            .first()
        )
        if not c:
            return False
        s.delete(c)
        s.flush()
        return True


def merge_company_extra_data(company_id: int, patch: dict):
    """
    Merge keys into Company.extra_data (JSON). Shallow-merge at top level.
    """
    if not isinstance(patch, dict):
        return False
    s = SessionFactory()
    try:
        c = s.get(Company, company_id)
        if not c:
            return False
        # IMPORTANT: copy before update. If we mutate the same dict instance that SQLAlchemy
        # already tracks, JSON changes may not be detected and only updated_at gets written.
        base = dict(_normalize_extra_data(c.extra_data))
        base.update(patch)
        c.extra_data = dict(base)  # new dict so ORM detects change and persists to DB
        c.updated_at = datetime.utcnow()
        s.flush()
        s.commit()
        return True
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


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


def lookup_existing_shadow_pairs_for_advisors(advisor_company_ids: list[int]) -> dict[int, tuple[int, int]]:
    """
    Advisor id -> (shadow_company_id, leadership_contact_id) when both rows already exist.
    Does not create; used to batch-load Media House shadows before falling back to get_or_create.
    """
    uniq = sorted({int(i) for i in advisor_company_ids if i})
    if not uniq:
        return {}
    domains = [f"__advisor_{aid}" for aid in uniq]
    with db() as s:
        comps = s.query(Company).filter(Company.domain.in_(domains)).all()
        dom_to_comp = {c.domain: c for c in comps}
        cids = [c.id for c in comps]
        contacts_by_company: dict[int, int] = {}
        if cids:
            for ct in (
                s.query(Contact)
                .filter(Contact.company_id.in_(cids), Contact.name == "Leadership")
                .all()
            ):
                contacts_by_company[ct.company_id] = ct.id
        out: dict[int, tuple[int, int]] = {}
        for aid in uniq:
            comp = dom_to_comp.get(f"__advisor_{aid}")
            if not comp:
                continue
            ctid = contacts_by_company.get(comp.id)
            if not ctid:
                continue
            out[aid] = (comp.id, ctid)
        return out


def bulk_ensure_shadow_companies_for_advisor_ids(
    advisor_company_ids: list[int],
) -> dict[int, tuple[int | None, int | None]]:
    """
    For each advisor id: return (shadow_company_id, leadership_contact_id), creating rows in one
    transaction when missing. Matches get_or_create_shadow_company_for_advisor semantics; use this
    on read-heavy paths to avoid one transaction per child.
    """
    uniq = sorted({int(i) for i in advisor_company_ids if i})
    if not uniq:
        return {}
    domains = [f"__advisor_{aid}" for aid in uniq]
    out: dict[int, tuple[int | None, int | None]] = {}
    with db() as s:
        adv_by_id = {r.id: r for r in s.query(AdvisorCompany).filter(AdvisorCompany.id.in_(uniq)).all()}
        comps = {c.domain: c for c in s.query(Company).filter(Company.domain.in_(domains)).all()}

        for aid in uniq:
            dom = f"__advisor_{aid}"
            if dom in comps:
                continue
            adv = adv_by_id.get(aid)
            if not adv:
                continue
            name = ((adv.domain or "") or (adv.parent_name or "") or "Child").strip() or "Child"
            c = Company(domain=dom, name=name, extra_data={"source_advisor_company_id": aid})
            s.add(c)
        s.flush()
        comps = {c.domain: c for c in s.query(Company).filter(Company.domain.in_(domains)).all()}

        cids_existing = [c.id for c in comps.values()]
        ct_map: dict[int, Contact] = {}
        if cids_existing:
            for ct in (
                s.query(Contact)
                .filter(Contact.company_id.in_(cids_existing), Contact.name == "Leadership")
                .all()
            ):
                ct_map[ct.company_id] = ct

        for aid in uniq:
            dom = f"__advisor_{aid}"
            c = comps.get(dom)
            if not c or c.id in ct_map:
                continue
            adv = adv_by_id.get(aid)
            if not adv:
                continue
            ct = Contact(company_id=c.id, name="Leadership", role="Leadership")
            s.add(ct)
        s.flush()

        cids = [c.id for c in comps.values()]
        ct_id_by_company: dict[int, int] = {}
        if cids:
            for ct in (
                s.query(Contact)
                .filter(Contact.company_id.in_(cids), Contact.name == "Leadership")
                .all()
            ):
                ct_id_by_company[ct.company_id] = ct.id

        for aid in uniq:
            adv = adv_by_id.get(aid)
            if not adv:
                out[aid] = (None, None)
                continue
            dom = f"__advisor_{aid}"
            c = comps.get(dom)
            if not c:
                out[aid] = (None, None)
                continue
            ctid = ct_id_by_company.get(c.id)
            if not ctid:
                out[aid] = (None, None)
                continue
            out[aid] = (c.id, ctid)
    return out


def bulk_get_companies_by_ids(company_ids: list[int]) -> dict[int, dict]:
    uniq = sorted({int(i) for i in company_ids if i})
    if not uniq:
        return {}
    with db() as s:
        rows = s.query(Company).filter(Company.id.in_(uniq)).all()
        return {c.id: _comp_dict(c) for c in rows}


def bulk_get_contacts_by_ids(contact_ids: list[int]) -> dict[int, dict]:
    uniq = sorted({int(i) for i in contact_ids if i})
    if not uniq:
        return {}
    with db() as s:
        rows = s.query(Contact).filter(Contact.id.in_(uniq)).all()
        return {c.id: _ct_dict(c) for c in rows}


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


def get_contacts_for_company_ids(company_ids: list[int]) -> dict[int, list]:
    """Contacts per company_id, ordered by name (same rows as get_contacts)."""
    uniq = sorted({int(i) for i in company_ids if i})
    if not uniq:
        return {}
    with db() as s:
        rows = (
            s.query(Contact)
            .filter(Contact.company_id.in_(uniq))
            .order_by(Contact.company_id, Contact.name)
            .all()
        )
    out: dict[int, list] = {cid: [] for cid in uniq}
    for c in rows:
        cid = c.company_id
        if cid in out:
            out[cid].append(_ct_dict(c))
    return out


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


def update_contact_extra_data(contact_id, extra_data):
    """Update contact's extra_data field (replaces entire field, not merged)."""
    with db() as s:
        c = s.get(Contact, contact_id)
        if not c:
            return False
        c.extra_data = extra_data
        c.updated_at = datetime.utcnow()
        return True


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
    """Update enrichment by request_id (called from callback handler). Returns True if a row was updated."""
    with db() as s:
        _ensure_enrichment_table(s)
        r = s.query(EnrichmentResult).filter_by(request_id=str(request_id)).first()
        if r:
            r.result_json = result_json
            r.status = status
            return True
        return False


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


def get_spoc_contacts_by_spocs(company_id: int, spoc_contact_ids: list[int]) -> dict[int, list]:
    """SPOC rows grouped by leadership contact id (same row shape as get_spoc_contacts_by_spoc)."""
    ids = sorted({int(i) for i in spoc_contact_ids})
    if not ids:
        return {}
    with db() as s:
        _ensure_spoc_contacts_table(s)
        rows = (
            s.query(SpocContact)
            .filter(
                SpocContact.company_id == company_id,
                SpocContact.spoc_contact_id.in_(ids),
            )
            .order_by(SpocContact.spoc_contact_id, SpocContact.name)
            .all()
        )
        out: dict[int, list] = {i: [] for i in ids}
        for r in rows:
            out[r.spoc_contact_id].append(
                {
                    "id": r.id,
                    "company_id": r.company_id,
                    "spoc_contact_id": r.spoc_contact_id,
                    "name": r.name,
                    "role": r.role,
                    "email": r.email,
                    "linkedin_url": r.linkedin_url,
                    "phone": r.phone,
                    "extra_data": r.extra_data,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
            )
        return out


def get_spoc_contacts_by_company_and_leader(company_ids: list[int]) -> dict[int, dict[int, list]]:
    """
    For each company_id, map leadership contact id (spoc_contact_id) -> SPOC-tagged rows.
    Same row shape as get_spoc_contacts_by_spocs values; use for batching independent CRM.
    """
    uniq = sorted({int(i) for i in company_ids if i})
    if not uniq:
        return {}
    with db() as s:
        _ensure_spoc_contacts_table(s)
        rows = (
            s.query(SpocContact)
            .filter(SpocContact.company_id.in_(uniq))
            .order_by(SpocContact.company_id, SpocContact.spoc_contact_id, SpocContact.name)
            .all()
        )
    nested: dict[int, dict[int, list]] = {cid: {} for cid in uniq}
    for r in rows:
        cid = r.company_id
        if cid not in nested:
            continue
        lid = r.spoc_contact_id
        if lid not in nested[cid]:
            nested[cid][lid] = []
        nested[cid][lid].append(
            {
                "id": r.id,
                "company_id": r.company_id,
                "spoc_contact_id": r.spoc_contact_id,
                "name": r.name,
                "role": r.role,
                "email": r.email,
                "linkedin_url": r.linkedin_url,
                "phone": r.phone,
                "extra_data": r.extra_data,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return nested


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


def get_spoc_contacts_for_company_ids(company_ids: list[int]) -> dict[int, list]:
    """SPOC rows grouped by company_id (same row shape as get_spoc_contacts_by_company)."""
    uniq = sorted({int(i) for i in company_ids if i})
    if not uniq:
        return {}
    with db() as s:
        _ensure_spoc_contacts_table(s)
        rows = (
            s.query(SpocContact)
            .filter(SpocContact.company_id.in_(uniq))
            .order_by(SpocContact.company_id, SpocContact.spoc_contact_id, SpocContact.name)
            .all()
        )
        out: dict[int, list] = {cid: [] for cid in uniq}
        for r in rows:
            cid = r.company_id
            if cid in out:
                out[cid].append(
                    {
                        "id": r.id,
                        "company_id": r.company_id,
                        "spoc_contact_id": r.spoc_contact_id,
                        "name": r.name,
                        "role": r.role,
                        "email": r.email,
                        "linkedin_url": r.linkedin_url,
                        "phone": r.phone,
                        "extra_data": r.extra_data,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                    }
                )
        return out


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


def update_spoc_contact(spoc_contact_id: int, *, email=None, role=None, linkedin_url=None, phone=None, name=None):
    """Update editable fields on a SPOC contact row. Returns False when row missing."""
    if not spoc_contact_id:
        return False
    with db() as s:
        _ensure_spoc_contacts_table(s)
        row = s.get(SpocContact, spoc_contact_id)
        if not row:
            return False
        if email is not None:
            row.email = (str(email).strip() or None)
        if role is not None:
            row.role = (str(role).strip() or None)
        if linkedin_url is not None:
            row.linkedin_url = (str(linkedin_url).strip() or None)
        if phone is not None:
            row.phone = (str(phone).strip() or None)
        if name is not None:
            row.name = str(name).strip()
        row.updated_at = datetime.utcnow()
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


def merge_session_extra_data(session_id: int, patch: dict):
    """Shallow-merge keys into Session.extra_data. Returns False if session missing."""
    if not patch:
        return False
    with db() as s:
        row = s.get(Session, session_id)
        if not row:
            return False
        base = dict(_normalize_extra_data(getattr(row, "extra_data", None)))
        base.update(patch)
        row.extra_data = dict(base)
        row.updated_at = datetime.utcnow()
        s.flush()
        return True


def get_sessions_for_company(company_id):
    with db() as s:
        return [
            _sess_dict(x)
            for x in s.query(Session).filter_by(company_id=company_id)
            .order_by(Session.updated_at.desc()).all()
        ]


def get_sessions_for_companies(company_ids: list[int]) -> dict[int, list]:
    """All sessions per company_id, each list ordered by updated_at desc (same as get_sessions_for_company)."""
    uniq = sorted({int(i) for i in company_ids if i})
    if not uniq:
        return {}
    with db() as s:
        rows = (
            s.query(Session)
            .filter(Session.company_id.in_(uniq))
            .order_by(Session.company_id, Session.updated_at.desc())
            .all()
        )
        out: dict[int, list] = {cid: [] for cid in uniq}
        for x in rows:
            cid = x.company_id
            if cid in out:
                out[cid].append(_sess_dict(x))
        return out


def _advisor_id_from_company_dict(comp: dict | None) -> int | None:
    """Resolve advisor_companies.id from a Media Houses shadow company dict, or None."""
    if not comp:
        return None
    ed = comp.get("extra_data") if isinstance(comp.get("extra_data"), dict) else {}
    raw = ed.get("source_advisor_company_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    dom = (comp.get("domain") or "").strip()
    if dom.startswith("__advisor_"):
        suf = dom[len("__advisor_") :]
        if suf.isdigit():
            return int(suf)
    return None


def _collect_adv_map_for_company_rows(s, comps: list) -> dict[int, dict]:
    advisor_ids: set[int] = set()
    for comp in comps:
        if comp is None:
            continue
        mini = {
            "domain": getattr(comp, "domain", None),
            "name": getattr(comp, "name", None),
            "extra_data": getattr(comp, "extra_data", None),
        }
        aid = _advisor_id_from_company_dict(mini)
        if aid is not None:
            advisor_ids.add(aid)
    if not advisor_ids:
        return {}
    rows = s.query(AdvisorCompany).filter(AdvisorCompany.id.in_(sorted(advisor_ids))).all()
    return {r.id: _advisor_comp_dict(r) for r in rows}


def _apply_orm_company_to_session_dict(d: dict, comp, adv_by_id: dict[int, dict]) -> None:
    d["company_domain"] = comp.domain
    d["company_name"] = comp.name
    ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
    if ed.get("source_advisor_company_id") is not None:
        try:
            d["source_advisor_company_id"] = int(ed["source_advisor_company_id"])
        except (TypeError, ValueError):
            d["source_advisor_company_id"] = ed.get("source_advisor_company_id")
    mini = {"domain": comp.domain, "name": comp.name, "extra_data": comp.extra_data}
    aid = _advisor_id_from_company_dict(mini)
    if aid is None:
        return
    d["source_advisor_company_id"] = aid
    adv = adv_by_id.get(aid)
    if adv:
        pn = (adv.get("parent_name") or "").strip() or "(No parent)"
        bd = (adv.get("domain") or "").strip() or "—"
    else:
        pn = "(No parent)"
        bd = (comp.name or "").strip() or "—"
    d["media_parent_name"] = pn
    d["media_brand_domain"] = bd
    d["company_workspace_label"] = f"{pn} · {bd}"


def attach_media_workspace_labels_to_company_dict(comp: dict | None) -> dict | None:
    """
    For Media Houses shadow companies (__advisor_<id>), add media_parent_name,
    media_brand_domain, and company_workspace_label for UI. No-op for independent companies.
    """
    if not comp:
        return None
    out = dict(comp)
    aid = _advisor_id_from_company_dict(out)
    if aid is None:
        return out
    adv = get_advisor_company(aid)
    if adv:
        pn = (adv.get("parent_name") or "").strip() or "(No parent)"
        bd = (adv.get("domain") or "").strip() or "—"
    else:
        pn = "(No parent)"
        bd = (out.get("name") or "").strip() or "—"
    out["media_parent_name"] = pn
    out["media_brand_domain"] = bd
    out["company_workspace_label"] = f"{pn} · {bd}"
    return out


def _finalize_session_rows(s, sess_rows: list, include_owner: bool) -> list:
    cids = [x.company_id for x in sess_rows if x.company_id]
    comps_by_id = {}
    if cids:
        for c in s.query(Company).filter(Company.id.in_(list(set(cids)))).all():
            comps_by_id[c.id] = c
    adv_by_id = _collect_adv_map_for_company_rows(s, list(comps_by_id.values()))

    owner_map = {}
    if include_owner:
        oids = {x.owner_user_id for x in sess_rows if x.owner_user_id}
        if oids:
            for u in s.query(User).filter(User.id.in_(sorted(oids))).all():
                owner_map[u.id] = u

    results = []
    for sess in sess_rows:
        d = _sess_dict(sess)
        if include_owner:
            owner = owner_map.get(sess.owner_user_id) if sess.owner_user_id else None
            if owner:
                d["owner_username"] = owner.username
        comp = comps_by_id.get(sess.company_id)
        if comp:
            try:
                _apply_orm_company_to_session_dict(d, comp, adv_by_id)
            except Exception as e:
                log.error("_apply_orm_company_to_session_dict session %s: %s", sess.id, e)
                d["company_domain"] = comp.domain
                d["company_name"] = comp.name
        try:
            ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
        except Exception as e:
            log.error("session contact load failed for session %s: %s", sess.id, e)
            ct = None
        if ct:
            d["contact_name"] = ct.name
            d["contact_role"] = ct.role
        results.append(d)
    return results


def get_user_sessions(user_id):
    """Sessions owned by this user (kept for compatibility). Prefer get_all_sessions for shared visibility."""
    with db() as s:
        rows = s.query(Session).filter_by(owner_user_id=user_id).order_by(Session.updated_at.desc()).all()
        return _finalize_session_rows(s, rows, include_owner=False)


def get_all_sessions():
    """All sessions (shared visibility). Includes company_domain, contact_name, owner_username."""
    with db() as s:
        rows = s.query(Session).order_by(Session.updated_at.desc()).all()
        return _finalize_session_rows(s, rows, include_owner=True)


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
            adv_by_id = _collect_adv_map_for_company_rows(s, [comp])
            _apply_orm_company_to_session_dict(d, comp, adv_by_id)
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


def gmail_outreach_sync_already_applied(gmail_account_id: int, gmail_message_id: str) -> bool:
    """Idempotency for Outreach State Engine (per connected Gmail account)."""
    mid = (gmail_message_id or "").strip()
    if not mid:
        return True
    with db() as s:
        return (
            s.query(GmailOutreachSyncEvent)
            .filter_by(gmail_account_id=int(gmail_account_id), gmail_message_id=mid)
            .first()
            is not None
        )


def record_gmail_outreach_sync_event(
    *,
    gmail_account_id: int,
    gmail_message_id: str,
    user_id: int,
    action_id: int | None,
    event_kind: str,
    applied_status: str | None,
    outcome_reason: str | None = None,
) -> None:
    try:
        with db() as s:
            s.add(
                GmailOutreachSyncEvent(
                    gmail_account_id=int(gmail_account_id),
                    gmail_message_id=str(gmail_message_id).strip(),
                    user_id=int(user_id),
                    action_id=int(action_id) if action_id is not None else None,
                    event_kind=str(event_kind),
                    applied_status=(applied_status or None),
                    outcome_reason=outcome_reason,
                )
            )
    except IntegrityError:
        pass  # concurrent / duplicate sync — idempotent


def get_session_id_for_contact_owner(contact_id: int, owner_user_id: int) -> int | None:
    with db() as s:
        sess = (
            s.query(Session)
            .filter_by(contact_id=int(contact_id), owner_user_id=int(owner_user_id))
            .order_by(Session.updated_at.desc())
            .first()
        )
        return int(sess.id) if sess else None


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


def get_session_actions_for_sessions(session_ids: list[int]) -> dict[int, list]:
    """Batch-load actions for sessions. Lists match get_session_actions order (created_at desc per session)."""
    uniq = sorted({int(i) for i in session_ids if i})
    if not uniq:
        return {}
    with db() as s:
        actions = (
            s.query(Action)
            .filter(Action.session_id.in_(uniq))
            .order_by(Action.session_id, Action.created_at.desc())
            .all()
        )
        out: dict[int, list] = {i: [] for i in uniq}
        for a in actions:
            sid = a.session_id
            if sid in out:
                out[sid].append(_act_dict(a))
        return out


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
                    ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                    if ed and ed.get("source_advisor_company_id") is not None:
                        d["source_advisor_company_id"] = ed["source_advisor_company_id"]
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
                    ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                    if ed and ed.get("source_advisor_company_id") is not None:
                        d["source_advisor_company_id"] = ed["source_advisor_company_id"]
                if ct:
                    d["contact_name"] = ct.name
            results.append(d)
        return results


def get_timeline_actions(company_id=None, contact_id=None, limit=100):
    with db() as s:
        q = s.query(Action).join(Session, Action.session_id == Session.id)
        if company_id:
            q = q.filter(Session.company_id == company_id)
        if contact_id is not None:
            # Include both contact-specific and company-level (no contact) actions
            # so SPOC/company actions logged without a specific contact still show up.
            q = q.filter(or_(Session.contact_id == contact_id, Session.contact_id.is_(None)))
        results = []
        for a in q.order_by(
            func.coalesce(Action.scheduled_at, Action.created_at).desc(),
        ).limit(limit).all():
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
                    if ed and ed.get("source_advisor_company_id") is not None:
                        d["source_advisor_company_id"] = ed["source_advisor_company_id"]
                if sess.contact_id:
                    ct = s.get(Contact, sess.contact_id)
                    if ct:
                        d["contact_name"] = ct.name
            results.append(d)
        return results


def update_action(aid, status=None, outcome=None, response_content=None, response_type=None,
                  description=None, scheduled_at=None, action_type=None, channel=None, content_sent=None,
                  draft_revisions=None, draft_original=None, outcome_reason=None, outreach_sync_meta=None,
                  completed_at=None):
    with db() as s:
        a = s.get(Action, aid)
        if not a:
            return
        if status is not None:
            a.status = status
            if completed_at is not None:
                try:
                    if isinstance(completed_at, datetime):
                        ca = completed_at
                    else:
                        ca = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
                    if getattr(ca, "tzinfo", None) is not None:
                        ca = ca.astimezone(timezone.utc).replace(tzinfo=None)
                    a.completed_at = ca
                except Exception:
                    a.completed_at = datetime.utcnow()
            elif status in ("completed", "deal_closed", "meeting_booked"):
                a.completed_at = datetime.utcnow()
        if outcome is not None:
            a.outcome = outcome
        if outcome_reason is not None and hasattr(a, "outcome_reason"):
            a.outcome_reason = outcome_reason
        if outreach_sync_meta is not None and hasattr(a, "outreach_sync_meta"):
            a.outreach_sync_meta = outreach_sync_meta if isinstance(outreach_sync_meta, dict) else dict(outreach_sync_meta)
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


def get_action_by_id(aid):
    with db() as s:
        a = s.get(Action, int(aid))
        return _act_dict(a) if a else None


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
                    ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                    if ed and ed.get("source_advisor_company_id") is not None:
                        d["source_advisor_company_id"] = ed["source_advisor_company_id"]
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
                    ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                    if ed and ed.get("source_advisor_company_id") is not None:
                        d["source_advisor_company_id"] = ed["source_advisor_company_id"]
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
                    ed = comp.extra_data if isinstance(comp.extra_data, dict) else {}
                    if ed and ed.get("source_advisor_company_id") is not None:
                        d["source_advisor_company_id"] = ed["source_advisor_company_id"]
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


def get_company_ids_with_session_summary():
    """
    Return set of company_id that have at least one session with non-empty strategic_summary.
    Used so the Companies list can show "researched" when research lives in session (canonical) even if
    company.extra_data['research_full'] is not set.
    """
    with db() as s:
        rows = (
            s.query(Session.company_id)
            .filter(
                Session.company_id.isnot(None),
                Session.strategic_summary.isnot(None),
                Session.strategic_summary != "",
            )
            .distinct()
            .all()
        )
        return set(r[0] for r in rows if r[0] is not None)


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
            from integrations.ai import summarize_advisor_title
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


# ── Advisor companies (NOTION_DATABASE_ID_2); same DB as platform ─────────────────

def _advisor_comp_dict(c):
    """Turn AdvisorCompany ORM row into dict for API."""
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


@contextmanager
def db_advisor():
    """Yield session for advisor tables. Same as db() — one DB for platform and advisor."""
    with db() as session:
        yield session


def upsert_advisor_company(domain, parent_name, notion_page_id=None):
    """Upsert by (domain, parent_name). Returns row id."""
    with db() as s:
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
    d = normalize_domain(domain)
    p = (parent_name or "").strip()
    if not d or not p:
        return False
    with db() as s:
        row = s.query(AdvisorCompany).filter_by(domain=d.strip(), parent_name=p).first()
        if not row:
            row = s.query(AdvisorCompany).filter_by(domain=domain.strip(), parent_name=p).first()
        if not row:
            return False
        s.delete(row)
        s.flush()
        return True


def bulk_upsert_advisor_companies(companies_list):
    """Upsert many advisor companies in one transaction. Returns number of companies upserted."""
    if not companies_list:
        return 0
    with db() as s:
        for data in companies_list:
            domain = (data.get("domain") or "").strip()
            parent_name = (data.get("parent_name") or "").strip() or "(No parent)"
            notion_page_id = data.get("notion_page_id")
            row = s.query(AdvisorCompany).filter_by(domain=domain, parent_name=parent_name).first()
            if row:
                if notion_page_id is not None:
                    row.notion_page_id = notion_page_id
            else:
                s.add(AdvisorCompany(domain=domain, parent_name=parent_name, notion_page_id=notion_page_id))
        return len(companies_list)


def get_advisor_company(advisor_id):
    """Return a single advisor company by id, or None."""
    if advisor_id is None:
        return None
    with db() as s:
        c = s.query(AdvisorCompany).filter_by(id=advisor_id).first()
        return _advisor_comp_dict(c) if c else None


def bulk_get_advisor_companies_by_ids(advisor_ids: list[int]) -> dict[int, dict]:
    """Advisor id -> advisor company dict."""
    uniq = sorted({int(i) for i in advisor_ids if i})
    if not uniq:
        return {}
    with db() as s:
        rows = s.query(AdvisorCompany).filter(AdvisorCompany.id.in_(uniq)).all()
        return {r.id: _advisor_comp_dict(r) for r in rows}


def merge_advisor_company_extra_data(advisor_id, patch) -> bool:
    """Merge patch dict into advisor company's extra_data. Returns False if row missing or invalid input."""
    if not advisor_id or not isinstance(patch, dict):
        return False
    s = SessionFactory()
    try:
        row = s.query(AdvisorCompany).filter_by(id=advisor_id).first()
        if not row:
            return False
        ed = getattr(row, "extra_data", None)
        if isinstance(ed, dict):
            current = ed
        elif isinstance(ed, str) and (ed or "").strip():
            try:
                current = json.loads(ed)
            except Exception:
                current = {}
        else:
            current = {}
        row.extra_data = {**(current or {}), **patch}
        s.flush()
        s.commit()
        return True
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_advisor_company_by_domain(domain):
    """Return advisor company dict if one with this domain exists; None otherwise."""
    norm = normalize_domain(domain)
    if not norm:
        return None
    with db() as s:
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
        for r in s.query(AdvisorCompany).all():
            if normalize_domain(r.domain or "") == norm:
                return _advisor_comp_dict(r)
    return None


def get_advisor_company_by_domain_and_parent(domain: str, parent_name: str) -> dict | None:
    """Exact match on stripped domain and parent_name (use (No parent) when parent is empty)."""
    d = (domain or "").strip()
    p = (parent_name or "").strip() or "(No parent)"
    if not d:
        return None
    with db() as s:
        row = s.query(AdvisorCompany).filter_by(domain=d, parent_name=p).first()
        return _advisor_comp_dict(row) if row else None


def get_all_advisor_companies():
    """Return all advisor companies as list of dicts."""
    with db() as s:
        return [_advisor_comp_dict(c) for c in s.query(AdvisorCompany).order_by(AdvisorCompany.parent_name, AdvisorCompany.domain).all()]


def search_advisor_companies(q):
    """Fuzzy search on domain and parent_name."""
    with db() as s:
        q = (q or "").strip()
        if not q:
            return get_all_advisor_companies()
        like = f"%{q}%"
        return [
            _advisor_comp_dict(c)
            for c in s.query(AdvisorCompany)
            .filter(AdvisorCompany.domain.ilike(like) | AdvisorCompany.parent_name.ilike(like))
            .order_by(AdvisorCompany.parent_name, AdvisorCompany.domain)
            .all()
        ]


def _parent_slug(parent_name):
    """Slug for parent company row domain."""
    s = (parent_name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "parent"
    return s


def get_or_create_advisor_company_for_parent(parent_name):
    """Get or create an advisor_company row representing the parent (media house). Returns advisor company dict or None."""
    p = (parent_name or "").strip() or "(No parent)"
    slug = _parent_slug(p)
    domain = f"__parent_{slug}"
    with db() as s:
        row = s.query(AdvisorCompany).filter_by(domain=domain).first()
        if row:
            return _advisor_comp_dict(row)
        row = AdvisorCompany(domain=domain, parent_name=p, notion_page_id=None)
        s.add(row)
        s.flush()
        return _advisor_comp_dict(row)


def delete_advisor_parent(parent_name) -> bool:
    """Delete the advisor_company row that represents a parent (media house)."""
    p = (parent_name or "").strip()
    if not p:
        return False
    slug = _parent_slug(p)
    domain = f"__parent_{slug}"
    return delete_advisor_company(domain, p)


def is_advisor_parent_company(advisor_comp):
    """True if this advisor company dict is a parent (media house) row."""
    if not advisor_comp or not isinstance(advisor_comp, dict):
        return False
    d = (advisor_comp.get("domain") or "").strip()
    return d.startswith("__parent_")


def get_advisor_parents_with_children():
    """Return list of {"parent_name": str, "children": [dict, ...]}. Excludes __parent_* rows from children."""
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


# ── Gmail multi-user inbox (OAuth + sync) ─────────────────────

def _thread_out(t: EmailThread, gmail_email: str | None = None) -> dict:
    return {
        "id": t.id,
        "gmail_thread_id": t.thread_id,
        "subject": t.subject,
        "snippet": t.snippet,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "gmail_account_id": t.gmail_account_id,
        "connected_email": (gmail_email or "").strip() or None,
    }


def _inbox_email_out(e: InboxEmail, include_body: bool = False) -> dict:
    d = {
        "id": e.id,
        "gmail_message_id": e.gmail_message_id,
        "thread_id": e.thread_id,
        "from_email": e.from_email,
        "to_emails": e.to_emails or [],
        "subject": e.subject,
        "body_snippet": e.body_snippet,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "is_sent": bool(e.is_sent),
        "thread_label": e.thread_label,
    }
    if include_body:
        d["body_full"] = e.body_full
    return d


def get_known_contact_emails() -> set[str]:
    """All known contact emails from core contacts + SPOC contacts."""
    out: set[str] = set()
    with db() as s:
        for (em,) in s.query(Contact.email).filter(Contact.email.isnot(None)).all():
            v = (em or "").strip().lower()
            if v:
                out.add(v)
        for (em,) in s.query(SpocContact.email).filter(SpocContact.email.isnot(None)).all():
            v = (em or "").strip().lower()
            if v:
                out.add(v)
    return out


def email_matches_known_contacts(
    from_email: str | None,
    to_emails: list | None,
    known_emails: set[str],
    connected_email: str | None = None,
) -> bool:
    if not known_emails:
        return True
    participants = set()
    frm = (from_email or "").strip().lower()
    if frm:
        participants.add(frm)
    for e in to_emails or []:
        v = (str(e or "").strip().lower())
        if v:
            participants.add(v)
    mine = (connected_email or "").strip().lower()
    if mine:
        participants.discard(mine)
    return bool(participants & known_emails)


def upsert_gmail_account(user_id: int, email: str, access_token_enc: str, refresh_token_enc: str | None, token_expiry):
    """Create or update a connected Gmail row; tokens must already be encrypted.
    If refresh_token_enc is falsy on update, the previous refresh_token_enc is kept.
    """
    norm = (email or "").strip().lower()
    if not norm:
        raise ValueError("email required")
    with db() as s:
        row = s.query(GmailAccount).filter_by(user_id=user_id, email=norm).first()
        if row:
            row.access_token_enc = access_token_enc
            if refresh_token_enc:
                row.refresh_token_enc = refresh_token_enc
            row.token_expiry = token_expiry
            row.updated_at = datetime.utcnow()
        else:
            row = GmailAccount(
                user_id=user_id,
                email=norm,
                access_token_enc=access_token_enc,
                refresh_token_enc=refresh_token_enc,
                token_expiry=token_expiry,
            )
            s.add(row)
        s.flush()
        return row.id


def get_gmail_account_by_id(account_id: int) -> dict | None:
    with db() as s:
        r = s.get(GmailAccount, account_id)
        if not r:
            return None
        return {
            "id": r.id,
            "user_id": r.user_id,
            "email": r.email,
            "access_token_enc": r.access_token_enc,
            "refresh_token_enc": r.refresh_token_enc,
            "token_expiry": r.token_expiry,
        }


def list_gmail_accounts() -> list[dict]:
    """All connections (for global sync jobs)."""
    with db() as s:
        rows = s.query(GmailAccount).order_by(GmailAccount.id).all()
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "email": r.email,
                "access_token_enc": r.access_token_enc,
                "refresh_token_enc": r.refresh_token_enc,
                "token_expiry": r.token_expiry,
            }
            for r in rows
        ]


def list_gmail_accounts_for_user(user_id: int) -> list[dict]:
    """Public-safe list (no tokens)."""
    with db() as s:
        rows = (
            s.query(GmailAccount)
            .filter_by(user_id=user_id)
            .order_by(GmailAccount.email)
            .all()
        )
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "email": r.email,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "last_sync_at": r.last_sync_at.isoformat() if getattr(r, "last_sync_at", None) else None,
                "last_sync_error": getattr(r, "last_sync_error", None),
                "last_sync_stats": getattr(r, "last_sync_stats", None),
            }
            for r in rows
        ]


def update_gmail_account_tokens(account_id: int, access_token_enc: str, refresh_token_enc: str | None, token_expiry) -> bool:
    with db() as s:
        r = s.get(GmailAccount, account_id)
        if not r:
            return False
        r.access_token_enc = access_token_enc
        if refresh_token_enc:
            r.refresh_token_enc = refresh_token_enc
        r.token_expiry = token_expiry
        r.updated_at = datetime.utcnow()
        return True


def update_gmail_account_sync_run(
    account_id: int,
    *,
    ok: bool,
    error: str | None = None,
    stats: dict | None = None,
) -> None:
    """Persist last sync outcome for operational diagnostics."""
    with db() as s:
        r = s.get(GmailAccount, int(account_id))
        if not r:
            return
        r.last_sync_at = datetime.utcnow()
        r.last_sync_error = (error or None) if not ok else None
        if hasattr(r, "last_sync_stats"):
            r.last_sync_stats = stats if isinstance(stats, dict) else stats
        r.updated_at = datetime.utcnow()


def get_inbox_email_by_gmail_id(gmail_account_id: int, gmail_message_id: str) -> bool:
    with db() as s:
        q = (
            s.query(InboxEmail)
            .filter_by(gmail_account_id=gmail_account_id, gmail_message_id=gmail_message_id)
            .first()
        )
        return q is not None


def upsert_email_thread(
    user_id: int,
    gmail_account_id: int,
    gmail_thread_id: str,
    subject: str | None,
    snippet: str | None,
    last_message_at,
) -> int:
    def _as_naive_utc(dt):
        """Normalize datetimes so comparisons don't mix aware/naive values."""
        if not dt:
            return dt
        tzinfo = getattr(dt, "tzinfo", None)
        if tzinfo is not None:
            # Convert aware -> UTC naive for stable DB comparisons across SQLite/Postgres.
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    if not gmail_thread_id:
        gmail_thread_id = "_orphan_"
    last_message_at = _as_naive_utc(last_message_at)
    with db() as s:
        t = (
            s.query(EmailThread)
            .filter_by(gmail_account_id=gmail_account_id, thread_id=gmail_thread_id)
            .first()
        )
        if not t:
            t = EmailThread(
                user_id=user_id,
                gmail_account_id=gmail_account_id,
                thread_id=gmail_thread_id,
                subject=subject,
                snippet=snippet,
                last_message_at=last_message_at,
            )
            s.add(t)
            s.flush()
            return t.id
        existing_last = _as_naive_utc(t.last_message_at)
        is_newer = bool(last_message_at and (existing_last is None or last_message_at >= existing_last))
        if is_newer:
            t.last_message_at = last_message_at
        # Keep thread preview metadata tied to the latest message only.
        if subject and (is_newer or not (t.subject or "").strip()):
            t.subject = subject
        if snippet and (is_newer or not (t.snippet or "").strip()):
            t.snippet = snippet
        t.updated_at = datetime.utcnow()
        s.flush()
        return t.id


def upsert_inbox_email(
    gmail_message_id: str,
    thread_pk: int,
    user_id: int,
    gmail_account_id: int,
    from_email: str | None,
    to_emails: list | None,
    subject: str | None,
    body_snippet: str | None,
    body_full: str | None,
    timestamp,
    is_sent: bool,
    thread_label: str | None = None,
) -> int:
    if timestamp is not None and getattr(timestamp, "tzinfo", None) is not None:
        timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    with db() as s:
        existing = (
            s.query(InboxEmail)
            .filter_by(gmail_account_id=gmail_account_id, gmail_message_id=gmail_message_id)
            .first()
        )
        if existing:
            return existing.id
        row = InboxEmail(
            gmail_message_id=gmail_message_id,
            thread_id=thread_pk,
            user_id=user_id,
            gmail_account_id=gmail_account_id,
            from_email=from_email,
            to_emails=to_emails or [],
            subject=subject,
            body_snippet=body_snippet,
            body_full=body_full,
            timestamp=timestamp,
            is_sent=is_sent,
            thread_label=thread_label,
        )
        s.add(row)
        s.flush()
        return row.id


def list_inbox_threads_for_user(
    user_id: int,
    limit: int = 200,
    *,
    known_emails: set[str] | None = None,
) -> list[dict]:
    with db() as s:
        allowed_thread_ids: set[int] | None = None
        if known_emails is not None:
            allowed_thread_ids = set()
            rows = (
                s.query(
                    InboxEmail.thread_id,
                    InboxEmail.from_email,
                    InboxEmail.to_emails,
                    GmailAccount.email,
                )
                .join(GmailAccount, InboxEmail.gmail_account_id == GmailAccount.id)
                .filter(InboxEmail.user_id == user_id)
                .all()
            )
            for tid, frm, tos, connected in rows:
                if email_matches_known_contacts(frm, tos, known_emails, connected):
                    allowed_thread_ids.add(tid)
            if not allowed_thread_ids:
                return []
        q = (
            s.query(EmailThread, GmailAccount.email)
            .join(GmailAccount, EmailThread.gmail_account_id == GmailAccount.id)
            .filter(EmailThread.user_id == user_id)
        )
        if allowed_thread_ids is not None:
            q = q.filter(EmailThread.id.in_(allowed_thread_ids))
        q = q.order_by(EmailThread.last_message_at.desc()).limit(limit)
        return [_thread_out(t, ga) for t, ga in q.all()]


def list_inbox_emails_for_user(
    user_id: int,
    limit: int = 300,
    thread_pk: int | None = None,
    *,
    known_emails: set[str] | None = None,
) -> list[dict]:
    with db() as s:
        allowed_thread_ids: set[int] | None = None
        if known_emails is not None:
            allowed_thread_ids = set()
            rows = (
                s.query(
                    InboxEmail.thread_id,
                    InboxEmail.from_email,
                    InboxEmail.to_emails,
                    GmailAccount.email,
                )
                .join(GmailAccount, InboxEmail.gmail_account_id == GmailAccount.id)
                .filter(InboxEmail.user_id == user_id)
                .all()
            )
            for tid, frm, tos, connected in rows:
                if email_matches_known_contacts(frm, tos, known_emails, connected):
                    allowed_thread_ids.add(tid)
            if not allowed_thread_ids:
                return []
        qq = (
            s.query(InboxEmail, GmailAccount.email)
            .join(GmailAccount, InboxEmail.gmail_account_id == GmailAccount.id)
            .filter(InboxEmail.user_id == user_id)
        )
        if allowed_thread_ids is not None:
            qq = qq.filter(InboxEmail.thread_id.in_(allowed_thread_ids))
        if thread_pk is not None:
            qq = qq.filter(InboxEmail.thread_id == thread_pk)
        rows = qq.order_by(InboxEmail.timestamp.desc()).limit(limit).all()
        return [_inbox_email_out(e, include_body=False) for e, _connected in rows]


def get_inbox_thread_detail(
    thread_pk: int,
    user_id: int,
    *,
    known_emails: set[str] | None = None,
) -> dict | None:
    with db() as s:
        t = s.query(EmailThread).filter_by(id=thread_pk, user_id=user_id).first()
        if not t:
            return None
        ga = s.get(GmailAccount, t.gmail_account_id)
        conn_email = ga.email if ga else None
        msgs = (
            s.query(InboxEmail)
            .filter_by(thread_id=thread_pk, user_id=user_id)
            .order_by(InboxEmail.timestamp.asc())
            .all()
        )
        if known_emails is not None:
            # Thread-level filtering only: once thread matches a known contact, return full conversation.
            has_match = any(
                email_matches_known_contacts(m.from_email, m.to_emails, known_emails, conn_email)
                for m in msgs
            )
            if not has_match:
                return None
        return {
            "thread": _thread_out(t, conn_email),
            "messages": [_inbox_email_out(m, include_body=True) for m in msgs],
        }