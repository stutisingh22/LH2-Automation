"""
db_ops.py — Database engine, session factory, and all CRUD operations

FIX LOG:
  - SQLite path is now absolute (next to this file) so data persists across restarts
  - Search does fuzzy matching: strips TLDs and matches partial domain names
"""

import logging
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

import streamlit as st
from passlib.hash import pbkdf2_sha256
from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool, StaticPool

from config import DATABASE_URL, CONTACT_STATUSES
from models import (
    Base, User, Company, Contact, Session, Message,
    Action, MemoryEvent, GlobalInsight,
)

log = logging.getLogger(__name__)

# Absolute path for SQLite — always next to this file, never relative to cwd
_DB_DIR = os.path.dirname(os.path.abspath(__file__))
_SQLITE_PATH = os.path.join(_DB_DIR, "lh2_platform.db")


# ── Engine + Session Factory ──────────────────────────────────

@st.cache_resource
def init_db():
    if DATABASE_URL:
        engine = create_engine(
            DATABASE_URL, poolclass=QueuePool, pool_size=5,
            max_overflow=10, pool_pre_ping=True, pool_recycle=300,
        )
    else:
        log.info("Using SQLite at %s", _SQLITE_PATH)
        engine = create_engine(
            f"sqlite:///{_SQLITE_PATH}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


SessionFactory = init_db()


@contextmanager
def db():
    session = scoped_session(SessionFactory)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.remove()


# ── Serializers ───────────────────────────────────────────────

def _comp_dict(c):
    return {
        "id": c.id, "name": c.name, "domain": c.domain,
        "publisher_type": c.publisher_type, "content_type": c.content_type,
        "monthly_visits": c.monthly_visits, "notion_page_id": c.notion_page_id,
        "research_status": c.research_status, "gmail_thread": c.gmail_thread,
        "extra_data": c.extra_data or {},
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
        base = c.extra_data or {}
        base.update(patch)
        c.extra_data = base
        c.updated_at = datetime.utcnow()
        s.flush()
        return True


def get_all_companies():
    with db() as s:
        return [_comp_dict(c) for c in s.query(Company).order_by(Company.name).all()]


def search_companies(q):
    """
    Fuzzy search: matches partial domains, names, and strips TLD suffixes.
    Searching "nofilmschool" will find "nofilmschool.com".
    """
    with db() as s:
        q = q.strip()
        if not q:
            return [_comp_dict(c) for c in s.query(Company).order_by(Company.name).all()]

        like = f"%{q}%"
        # Also try without common TLDs in case user types "site.com" but stored as "site"
        q_no_tld = q
        for tld in (".com", ".in", ".org", ".net", ".co", ".io", ".us", ".uk"):
            if q.lower().endswith(tld):
                q_no_tld = q[:-len(tld)]
                break
        like2 = f"%{q_no_tld}%"

        return [
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


# ── Sessions ──────────────────────────────────────────────────

def get_or_create_session(company_id, contact_id, user_id, score=0):
    """
    FIX: Explicitly handle contact_id=None so we don't create orphan sessions.
    """
    with db() as s:
        q = s.query(Session).filter_by(
            company_id=company_id,
            owner_user_id=user_id,
        )
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
    with db() as s:
        results = []
        for sess in s.query(Session).filter_by(owner_user_id=user_id).order_by(Session.updated_at.desc()).all():
            d = _sess_dict(sess)
            comp = s.get(Company, sess.company_id)
            ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
            if comp:
                d["company_domain"] = comp.domain
                d["company_name"] = comp.name
            if ct:
                d["contact_name"] = ct.name
                d["contact_role"] = ct.role
            results.append(d)
        return results


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


def get_session_actions(sid):
    with db() as s:
        return [
            _act_dict(a) for a in
            s.query(Action).filter_by(session_id=sid).order_by(Action.created_at.desc()).all()
        ]


def get_user_actions(uid, limit=50):
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
                if sess.contact_id:
                    ct = s.get(Contact, sess.contact_id)
                    if ct:
                        d["contact_name"] = ct.name
            results.append(d)
        return results


def update_action(aid, status=None, outcome=None, response_content=None, response_type=None,
                  description=None, scheduled_at=None, action_type=None, channel=None):
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


# ── Session Summary (L2) ─────────────────────────────────────

def get_session_summary(sid):
    with db() as s:
        sess = s.get(Session, sid)
        if sess:
            return (sess.strategic_summary, sess.summary_msg_count or 0)
        return (None, 0)


def save_session_summary(sid, summary, msg_count):
    with db() as s:
        sess = s.get(Session, sid)
        if sess:
            sess.strategic_summary = summary
            sess.summary_msg_count = msg_count


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

def get_pipeline_stats():
    with db() as s:
        return {
            status: s.query(Contact).filter_by(status=status).count()
            for status in CONTACT_STATUSES
        }