"""
LH2 RELATIONSHIP INTELLIGENCE PLATFORM v4
===========================================
Media partnership command center.

Flow: Company → Person → Session
DB:   PostgreSQL via SQLAlchemy (falls back to SQLite if no DATABASE_URL)
AI:   Layered infinite memory (L1–L5)
Sync: Notion companies/contacts, Google Drive documents
Auth: Multi-user with role-based access (admin/strategist/viewer)

Tabs: Dashboard | Companies | Intelligence | Strategist | Actions | Timeline | Pipeline | History

Requirements:
    pip install streamlit anthropic notion-client python-dotenv passlib pytz sqlalchemy psycopg2-binary google-api-python-client google-auth PyPDF2

Run:
    streamlit run lh2_platform_v4.py

Env vars (or .env / keys.env):
    DATABASE_URL              — postgresql://user:pass@host:5432/dbname (optional, falls back to SQLite)
    ANTHROPIC_API_KEY         — Anthropic API key
    NOTION_TOKEN              — Notion integration token
    NOTION_DATABASE_ID        — Notion database ID
    GOOGLE_SERVICE_ACCOUNT_JSON — Google service account JSON string (optional)
    GOOGLE_DRIVE_FOLDER_ID    — Drive folder ID (optional)
"""

import streamlit as st
import os
import json
import re
import csv
import io
import hashlib
import pytz
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from passlib.hash import pbkdf2_sha256
from anthropic import Anthropic
from notion_client import Client as NotionClient

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime,
    ForeignKey, JSON, Index, create_engine,
)
from sqlalchemy.orm import (
    relationship, declarative_base, sessionmaker, scoped_session,
)
from sqlalchemy.pool import QueuePool, StaticPool

load_dotenv("keys.env")
load_dotenv(".env")


# ╔══════════════════════════════════════════════════════════════╗
# ║  PAGE CONFIG                                                 ║
# ╚══════════════════════════════════════════════════════════════╝

st.set_page_config(page_title="LH2 Command Center", page_icon="◆", layout="wide", initial_sidebar_state="collapsed")


# ╔══════════════════════════════════════════════════════════════╗
# ║  CONFIGURATION                                               ║
# ╚══════════════════════════════════════════════════════════════╝

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
_raw_notion_db = os.getenv("NOTION_DATABASE_ID", "")
# Strip URLs, view params, whitespace, and normalize to UUID format
_clean = _raw_notion_db.strip().strip('"').strip("'")
if "notion.so" in _clean or "notion.site" in _clean:
    # Extract the 32-char hex ID from a full URL
    _clean = _clean.split("?")[0].rstrip("/").split("/")[-1]
elif "?" in _clean:
    _clean = _clean.split("?")[0]
_clean = _clean.replace("-", "")
if len(_clean) == 32:
    NOTION_DATABASE_ID = f"{_clean[:8]}-{_clean[8:12]}-{_clean[12:16]}-{_clean[16:20]}-{_clean[20:]}"
else:
    NOTION_DATABASE_ID = _clean
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-5")
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "4000"))
SUMMARY_INTERVAL = int(os.getenv("SUMMARY_INTERVAL", "20"))
MAX_CONTEXT_MESSAGES = 20
MAX_DRIVE_TOKENS = 6000

CONTACT_STATUSES = [
    "not_contacted", "outreach_sent", "awaiting_reply",
    "replied_positive", "replied_negative", "meeting_booked",
    "partnership_active", "closed_lost",
]
CONTACT_STATUS_LABELS = {
    "not_contacted": "Not Contacted", "outreach_sent": "Outreach Sent",
    "awaiting_reply": "Awaiting Reply", "replied_positive": "Replied Positive",
    "replied_negative": "Replied Negative", "meeting_booked": "Meeting Booked",
    "partnership_active": "Partnership Active", "closed_lost": "Closed Lost",
}
CONTACT_STATUS_COLORS = {
    "not_contacted": "gray", "outreach_sent": "blue", "awaiting_reply": "yellow",
    "replied_positive": "green", "replied_negative": "red", "meeting_booked": "green",
    "partnership_active": "green", "closed_lost": "red",
}
ACTION_TYPES = ["Outreach", "Follow-up", "Meeting", "Note", "Research", "Other"]
ACTION_CHANNELS = ["LinkedIn", "Email", "Phone", "In-Person", "Other"]
ACTION_STATUSES = [
    "planned", "sent", "completed", "no_response",
    "replied_positive", "replied_negative", "meeting_booked",
    "deal_closed", "deal_lost",
]
ACTION_STATUS_COLORS = {
    "planned": "blue", "sent": "yellow", "completed": "green",
    "no_response": "red", "replied_positive": "green", "replied_negative": "red",
    "meeting_booked": "green", "deal_closed": "green", "deal_lost": "red",
}
USER_ROLES = ["admin", "strategist", "viewer"]
ROLE_PERMISSIONS = {
    "admin": {"can_delete_sessions": True, "can_edit_company": True, "can_log_outcomes": True, "can_manage_users": True, "can_use_strategist": True},
    "strategist": {"can_delete_sessions": False, "can_edit_company": True, "can_log_outcomes": True, "can_manage_users": False, "can_use_strategist": True},
    "viewer": {"can_delete_sessions": False, "can_edit_company": False, "can_log_outcomes": False, "can_manage_users": False, "can_use_strategist": False},
}
TIMEZONE_COUNTRIES = {
    "uk": "Europe/London", "united kingdom": "Europe/London", "britain": "Europe/London",
    "india": "Asia/Kolkata", "australia": "Australia/Sydney", "canada": "America/Toronto",
    "germany": "Europe/Berlin", "france": "Europe/Paris", "japan": "Asia/Tokyo",
    "brazil": "America/Sao_Paulo", "singapore": "Asia/Singapore",
    "uae": "Asia/Dubai", "emirates": "Asia/Dubai", "israel": "Asia/Jerusalem",
    "korea": "Asia/Seoul", "spain": "Europe/Madrid", "italy": "Europe/Rome",
    "mexico": "America/Mexico_City", "netherlands": "Europe/Amsterdam",
    "sweden": "Europe/Stockholm", "norway": "Europe/Oslo", "denmark": "Europe/Copenhagen",
    "portugal": "Europe/Lisbon", "poland": "Europe/Warsaw", "turkey": "Europe/Istanbul",
    "south africa": "Africa/Johannesburg", "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "taiwan": "Asia/Taipei", "thailand": "Asia/Bangkok",
    "indonesia": "Asia/Jakarta", "philippines": "Asia/Manila", "new zealand": "Pacific/Auckland",
    "argentina": "America/Argentina/Buenos_Aires", "chile": "America/Santiago",
    "colombia": "America/Bogota",
}
US_TZ = {
    "US/Pacific": ["ca","california","wa","washington","or","oregon","nv","nevada"],
    "US/Mountain": ["co","colorado","az","arizona","ut","utah","mt","montana","nm","new mexico","wy","wyoming","id","idaho"],
    "US/Central": ["il","illinois","tx","texas","mn","minnesota","wi","wisconsin","mo","missouri","ia","iowa","tn","tennessee","la","louisiana","ok","oklahoma","ar","arkansas","ks","kansas","ne","nebraska","al","alabama","ms","mississippi"],
}


# ╔══════════════════════════════════════════════════════════════╗
# ║  CSS                                                         ║
# ╚══════════════════════════════════════════════════════════════╝

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap');
:root{--bg:#f5f6f8;--surface:#fff;--border:#e0e3e8;--border-light:#eef0f3;--text:#1c2024;--text-2:#4b5563;--text-3:#8b949e;--accent:#2563eb;--accent-bg:#eff6ff;--success:#16a34a;--success-bg:#dcfce7;--warn:#b45309;--warn-bg:#fef3c7;--danger:#dc2626;--danger-bg:#fee2e2;--radius:10px}
.stApp{background:var(--bg);color:var(--text);font-family:'DM Sans',-apple-system,system-ui,sans-serif}
#MainMenu,footer,header{visibility:hidden}.block-container{padding:1.2rem 2rem 2rem;max-width:1400px}
p,li,span,div,label,.stMarkdown,td,th{color:var(--text)!important}
.stTabs [data-baseweb="tab-list"]{gap:0;background:var(--surface);border-bottom:1px solid var(--border);padding:0 1rem;border-radius:var(--radius) var(--radius) 0 0}
.stTabs [data-baseweb="tab"]{height:46px;font-size:11px;font-weight:600;letter-spacing:1.8px;text-transform:uppercase;color:var(--text-3)!important;background:transparent!important;border:none!important;border-bottom:2px solid transparent!important;padding:0 1.1rem;transition:all .12s}
.stTabs [aria-selected="true"]{color:var(--text)!important;border-bottom-color:var(--accent)!important}
.stTabs [data-baseweb="tab"]:hover{color:var(--text-2)!important}
.stTextInput>div>div>input,.stTextArea>div>div>textarea{background:var(--surface)!important;border:1px solid var(--border)!important;color:var(--text)!important;border-radius:8px!important;font-size:14px!important;padding:10px 14px!important}
.stTextInput>div>div>input:focus,.stTextArea>div>div>textarea:focus{border-color:var(--accent)!important;box-shadow:0 0 0 3px rgba(37,99,235,.06)!important}
.stSelectbox>div>div{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:8px!important}
.stSelectbox label,.stTextInput label,.stTextArea label{color:var(--text-2)!important;font-weight:500!important;font-size:12px!important;letter-spacing:.3px}
.stButton>button{border-radius:8px;background:var(--surface);color:var(--text);border:1px solid var(--border);padding:8px 18px;font-weight:500;font-size:13px;width:100%;transition:all .12s;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.stButton>button:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-bg)}
div[data-testid="stFormSubmitButton"]>button{background:var(--accent)!important;color:#fff!important;border:none!important;font-weight:600!important;box-shadow:0 1px 3px rgba(37,99,235,.2)}
div[data-testid="stFormSubmitButton"]>button:hover{background:#1d4ed8!important}
.stChatMessage{background:var(--surface)!important;border:1px solid var(--border-light)!important;border-radius:var(--radius)!important;margin-bottom:6px}
.stChatMessage p,.stChatMessage span,.stChatMessage div{color:var(--text)!important}
.stChatInput>div{border:1px solid var(--border)!important;border-radius:var(--radius)!important;background:var(--surface)!important}
.c{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.02)}
.c-l{font-size:10px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--text-3)!important;margin-bottom:5px}
.c-v{font-size:14px;font-weight:500;color:var(--text)!important;line-height:1.45;word-break:break-word}
.sh{font-size:11px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--text-3)!important;margin:22px 0 10px 0}
.b{display:inline-block;padding:3px 10px;border-radius:20px;font-size:10.5px;font-weight:600;letter-spacing:.4px;text-transform:uppercase}
.b-blue{background:var(--accent-bg);color:#1d4ed8}.b-green{background:var(--success-bg);color:var(--success)}
.b-yellow{background:var(--warn-bg);color:var(--warn)}.b-red{background:var(--danger-bg);color:var(--danger)}
.b-gray{background:#f3f4f6;color:var(--text-2)}
.rb{background:#fafbfc;border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;padding:14px 18px;font-size:13px;line-height:1.75;color:var(--text-2)!important;max-height:380px;overflow-y:auto}
.pc{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:5px;transition:border-color .1s}.pc:hover{border-color:var(--accent)}
.mx{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px;text-align:center}
.mx-n{font-size:26px;font-weight:700;color:var(--text)}.mx-l{font-size:10px;font-weight:500;letter-spacing:1.2px;text-transform:uppercase;color:var(--text-3);margin-top:4px}
.mem-indicator{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:500}
.mem-active{background:#dcfce7;color:#166534}.mem-stale{background:#fef3c7;color:#92400e}
.tl-item{position:relative;padding-left:24px;margin-bottom:12px}
.tl-item::before{content:'';position:absolute;left:6px;top:8px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.tl-item::after{content:'';position:absolute;left:9px;top:20px;width:2px;height:calc(100% + 4px);background:var(--border)}
.tl-item:last-child::after{display:none}
.alert-card{background:var(--warn-bg);border:1px solid #fde68a;border-radius:var(--radius);padding:12px 16px;margin-bottom:8px}
.alert-danger{background:var(--danger-bg);border-color:#fca5a5}
.streamlit-expanderHeader{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:8px!important}
[data-testid="stForm"]{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px}
.stAlert{border-radius:8px!important}::-webkit-scrollbar{width:5px}::-webkit-scrollbar-thumb{background:#d1d5db;border-radius:3px}
.stDownloadButton>button{background:#f9fafb!important;color:var(--text)!important;border:1px solid var(--border)!important}
a{color:var(--accent)!important;text-decoration:none!important}a:hover{text-decoration:underline!important}
</style>
""", unsafe_allow_html=True)


# ╔══════════════════════════════════════════════════════════════╗
# ║  UI HELPERS                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

def bdg(text, color="gray"):
    return f"<span class='b b-{color}'>{text}</span>"

def crd(label, value):
    return f"<div class='c'><div class='c-l'>{label}</div><div class='c-v'>{value or '—'}</div></div>"

def mcard(number, label):
    return f"<div class='mx'><div class='mx-n'>{number}</div><div class='mx-l'>{label}</div></div>"

def sh(text):
    return f"<div class='sh'>{text}</div>"

def tl_item(date, desc, badge_text=None, badge_color="blue"):
    b = f" {bdg(badge_text, badge_color)}" if badge_text else ""
    return f"<div class='tl-item'><div style='font-size:11px;color:var(--text-3);'>{date}</div><div style='font-size:13px;margin-top:3px;'>{desc}{b}</div></div>"

def alert(text, level="warn"):
    cls = "alert-danger" if level == "danger" else "alert-card"
    return f"<div class='{cls}'>{text}</div>"

def fmt_v(v):
    if not v: return "—"
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000: return f"{v/1_000:.0f}K"
    return str(v)

def get_local_time(country, state=None):
    c = (country or "").lower().strip()
    s = (state or "").lower().strip()
    tz_str = None
    if any(x in c for x in ["us", "united states", "america"]):
        tz_str = "US/Eastern"
        for zone, states in US_TZ.items():
            if s in states:
                tz_str = zone; break
    else:
        for key, tz in TIMEZONE_COUNTRIES.items():
            if key in c:
                tz_str = tz; break
    if not tz_str: return "—", None
    try:
        tz = pytz.timezone(tz_str)
        return datetime.now(tz).strftime('%I:%M %p %Z'), tz_str
    except: return "—", None


# ╔══════════════════════════════════════════════════════════════╗
# ║  SQLALCHEMY MODELS                                           ║
# ╚══════════════════════════════════════════════════════════════╝

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    email = Column(String(255))
    role = Column(String(20), nullable=False, default="strategist")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    domain = Column(String(255), unique=True, nullable=False, index=True)
    publisher_type = Column(String(100))
    content_type = Column(String(100))
    monthly_visits = Column(Integer)
    notion_page_id = Column(String(64))
    research_status = Column(String(50))
    gmail_thread = Column(Text)
    extra_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    contacts = relationship("Contact", back_populates="company", cascade="all, delete-orphan")

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    role = Column(String(150))
    email = Column(String(255))
    linkedin = Column(Text)
    city = Column(String(100))
    state = Column(String(100))
    country = Column(String(100))
    status = Column(String(50), default="not_contacted")
    notion_page_id = Column(String(64))
    extra_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    company = relationship("Company", back_populates="contacts")

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    strategic_summary = Column(Text)
    summary_msg_count = Column(Integer, default=0)
    lead_score = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Action(Base):
    __tablename__ = "actions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action_type = Column(String(50), nullable=False)
    channel = Column(String(50))
    description = Column(Text)
    content_sent = Column(Text)
    response_content = Column(Text)
    response_type = Column(String(50))
    status = Column(String(50), default="planned")
    outcome = Column(Text)
    context_note = Column(Text)
    sequence_id = Column(String(20))
    sequence_step = Column(Integer)
    scheduled_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class MemoryEvent(Base):
    __tablename__ = "memory_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    summary = Column(Text, nullable=False)
    importance_score = Column(Integer, default=5)
    created_at = Column(DateTime, default=datetime.utcnow)

class GlobalInsight(Base):
    __tablename__ = "global_insights"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    insight = Column(Text, nullable=False)
    confidence_score = Column(Float, default=0.5)
    source_count = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


# ╔══════════════════════════════════════════════════════════════╗
# ║  DATABASE ENGINE + SESSION                                   ║
# ╚══════════════════════════════════════════════════════════════╝

@st.cache_resource
def init_db():
    if DATABASE_URL:
        engine = create_engine(DATABASE_URL, poolclass=QueuePool, pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=300, echo=False)
    else:
        engine = create_engine("sqlite:///lh2_platform.db", connect_args={"check_same_thread": False}, poolclass=StaticPool, echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)

SessionFactory = init_db()

@contextmanager
def db():
    session = scoped_session(SessionFactory)
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.remove()


# ╔══════════════════════════════════════════════════════════════╗
# ║  DATABASE OPERATIONS                                         ║
# ╚══════════════════════════════════════════════════════════════╝

# ---- Users ----

def create_user(username, password, email=None, role="strategist"):
    with db() as s:
        if s.query(User).filter_by(username=username).first(): return False
        s.add(User(username=username, password_hash=pbkdf2_sha256.hash(password), email=email, role=role))
        return True

def authenticate(username, password):
    with db() as s:
        u = s.query(User).filter_by(username=username, is_active=True).first()
        if u and pbkdf2_sha256.verify(password, u.password_hash):
            return {"id": u.id, "username": u.username, "email": u.email, "role": u.role}
    return None

# ---- Companies ----

def upsert_company(domain, **kw):
    with db() as s:
        c = s.query(Company).filter_by(domain=domain).first()
        if c:
            for k, v in kw.items():
                if v is not None and hasattr(c, k): setattr(c, k, v)
            c.updated_at = datetime.utcnow()
            s.flush(); return c.id
        c = Company(domain=domain, **{k: v for k, v in kw.items() if v is not None and hasattr(Company, k)})
        s.add(c); s.flush(); return c.id

def get_company(company_id):
    with db() as s:
        c = s.get(Company, company_id)
        return _comp_dict(c) if c else None

def get_all_companies():
    with db() as s:
        return [_comp_dict(c) for c in s.query(Company).order_by(Company.name).all()]

def search_companies(q):
    with db() as s:
        like = f"%{q}%"
        return [_comp_dict(c) for c in s.query(Company).filter((Company.domain.ilike(like))|(Company.name.ilike(like))).order_by(Company.name).all()]

def _comp_dict(c):
    return {"id": c.id, "name": c.name, "domain": c.domain, "publisher_type": c.publisher_type,
            "content_type": c.content_type, "monthly_visits": c.monthly_visits,
            "notion_page_id": c.notion_page_id, "research_status": c.research_status,
            "gmail_thread": c.gmail_thread, "extra_data": c.extra_data or {},
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}

# ---- Contacts ----

def upsert_contact(company_id, name, **kw):
    with db() as s:
        c = s.query(Contact).filter_by(company_id=company_id, name=name).first()
        if c:
            for k, v in kw.items():
                if v is not None and hasattr(c, k): setattr(c, k, v)
            c.updated_at = datetime.utcnow()
            s.flush(); return c.id
        c = Contact(company_id=company_id, name=name, **{k: v for k, v in kw.items() if v is not None and hasattr(Contact, k)})
        s.add(c); s.flush(); return c.id

def get_contacts(company_id):
    with db() as s:
        return [_ct_dict(c) for c in s.query(Contact).filter_by(company_id=company_id).order_by(Contact.name).all()]

def get_contact(contact_id):
    with db() as s:
        c = s.get(Contact, contact_id)
        return _ct_dict(c) if c else None

def update_contact_status(contact_id, status):
    with db() as s:
        c = s.get(Contact, contact_id)
        if c: c.status = status; c.updated_at = datetime.utcnow()

def _ct_dict(c):
    return {"id": c.id, "company_id": c.company_id, "name": c.name, "role": c.role,
            "email": c.email, "linkedin": c.linkedin, "city": c.city, "state": c.state,
            "country": c.country, "status": c.status, "notion_page_id": c.notion_page_id,
            "extra_data": c.extra_data or {}, "created_at": c.created_at.isoformat() if c.created_at else None}

# ---- Sessions ----

def get_or_create_session(company_id, contact_id, user_id, score=0):
    with db() as s:
        sess = s.query(Session).filter_by(company_id=company_id, contact_id=contact_id, owner_user_id=user_id).order_by(Session.updated_at.desc()).first()
        if sess:
            sess.updated_at = datetime.utcnow(); sess.lead_score = score; s.flush(); return sess.id
        sess = Session(company_id=company_id, contact_id=contact_id, owner_user_id=user_id, lead_score=score)
        s.add(sess); s.flush(); return sess.id

def get_session(sid):
    with db() as s:
        sess = s.get(Session, sid)
        return _sess_dict(sess) if sess else None

def get_sessions_for_company(company_id):
    with db() as s:
        return [_sess_dict(x) for x in s.query(Session).filter_by(company_id=company_id).order_by(Session.updated_at.desc()).all()]

def get_user_sessions(user_id):
    with db() as s:
        results = []
        for sess in s.query(Session).filter_by(owner_user_id=user_id).order_by(Session.updated_at.desc()).all():
            d = _sess_dict(sess)
            comp = s.get(Company, sess.company_id)
            ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
            if comp: d["company_domain"] = comp.domain; d["company_name"] = comp.name
            if ct: d["contact_name"] = ct.name; d["contact_role"] = ct.role
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

def _sess_dict(x):
    return {"id": x.id, "company_id": x.company_id, "contact_id": x.contact_id,
            "owner_user_id": x.owner_user_id, "strategic_summary": x.strategic_summary,
            "summary_msg_count": x.summary_msg_count or 0, "lead_score": x.lead_score or 0,
            "created_at": x.created_at.isoformat() if x.created_at else None,
            "updated_at": x.updated_at.isoformat() if x.updated_at else None}

# ---- Messages ----

def save_message(sid, role, content):
    with db() as s:
        s.add(Message(session_id=sid, role=role, content=content))
        sess = s.get(Session, sid)
        if sess: sess.updated_at = datetime.utcnow()

def load_messages(sid):
    with db() as s:
        return [{"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in s.query(Message).filter_by(session_id=sid).order_by(Message.created_at.asc()).all()]

def get_message_count(sid):
    with db() as s: return s.query(Message).filter_by(session_id=sid).count()

# ---- Actions ----

def log_action(sid, uid, atype, channel, desc, status="planned", content_sent=None, context_note=None, scheduled_at=None, seq_id=None, seq_step=None):
    with db() as s:
        sched = None
        if scheduled_at:
            try: sched = datetime.fromisoformat(scheduled_at)
            except: pass
        a = Action(session_id=sid, user_id=uid, action_type=atype, channel=channel, description=desc, status=status, content_sent=content_sent, context_note=context_note, scheduled_at=sched, sequence_id=seq_id, sequence_step=seq_step)
        s.add(a); s.flush(); return a.id

def get_session_actions(sid):
    with db() as s:
        return [_act_dict(a) for a in s.query(Action).filter_by(session_id=sid).order_by(Action.created_at.desc()).all()]

def get_user_actions(uid, limit=50):
    with db() as s:
        results = []
        for a in s.query(Action).filter_by(user_id=uid).order_by(Action.created_at.desc()).limit(limit).all():
            d = _act_dict(a)
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp: d["company_domain"] = comp.domain
                if ct: d["contact_name"] = ct.name
            results.append(d)
        return results

def get_timeline_actions(company_id=None, contact_id=None, limit=100):
    with db() as s:
        q = s.query(Action).join(Session, Action.session_id == Session.id)
        if company_id: q = q.filter(Session.company_id == company_id)
        if contact_id: q = q.filter(Session.contact_id == contact_id)
        results = []
        for a in q.order_by(Action.created_at.desc()).limit(limit).all():
            d = _act_dict(a)
            u = s.get(User, a.user_id)
            if u: d["username"] = u.username
            sess = s.get(Session, a.session_id)
            if sess and sess.contact_id:
                ct = s.get(Contact, sess.contact_id)
                if ct: d["contact_name"] = ct.name
            results.append(d)
        return results

def update_action(aid, status=None, outcome=None, response_content=None, response_type=None):
    with db() as s:
        a = s.get(Action, aid)
        if not a: return
        if status:
            a.status = status
            if status in ("completed", "deal_closed", "meeting_booked"): a.completed_at = datetime.utcnow()
        if outcome: a.outcome = outcome
        if response_content: a.response_content = response_content
        if response_type: a.response_type = response_type

def get_overdue_followups(days=3):
    cutoff = datetime.utcnow() - timedelta(days=days)
    with db() as s:
        results = []
        for a in s.query(Action).filter(Action.scheduled_at < cutoff, Action.completed_at.is_(None), Action.status.in_(["planned", "sent"])).order_by(Action.scheduled_at).all():
            d = _act_dict(a)
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp: d["company_domain"] = comp.domain
                if ct: d["contact_name"] = ct.name
            results.append(d)
        return results

def get_todays_scheduled():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
    today_end = today_start + timedelta(days=1)
    with db() as s:
        results = []
        for a in s.query(Action).filter(Action.scheduled_at >= today_start, Action.scheduled_at < today_end).order_by(Action.scheduled_at).all():
            d = _act_dict(a)
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp: d["company_domain"] = comp.domain
                if ct: d["contact_name"] = ct.name
            results.append(d)
        return results

def get_recent_activity(limit=20):
    with db() as s:
        results = []
        for a in s.query(Action).order_by(Action.created_at.desc()).limit(limit).all():
            d = _act_dict(a)
            u = s.get(User, a.user_id)
            if u: d["username"] = u.username
            sess = s.get(Session, a.session_id)
            if sess:
                comp = s.get(Company, sess.company_id)
                ct = s.get(Contact, sess.contact_id) if sess.contact_id else None
                if comp: d["company_domain"] = comp.domain
                if ct: d["contact_name"] = ct.name
            results.append(d)
        return results

def _act_dict(a):
    return {"id": a.id, "session_id": a.session_id, "user_id": a.user_id, "action_type": a.action_type,
            "channel": a.channel, "description": a.description, "content_sent": a.content_sent,
            "response_content": a.response_content, "response_type": a.response_type,
            "status": a.status, "outcome": a.outcome, "context_note": a.context_note,
            "sequence_id": a.sequence_id, "sequence_step": a.sequence_step,
            "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None}

# ---- Memory Events (L3) ----

def save_memory_event(sid, event_type, summary, importance=5):
    with db() as s: s.add(MemoryEvent(session_id=sid, event_type=event_type, summary=summary, importance_score=min(importance, 10)))

def get_memory_events(sid, min_imp=3, limit=10):
    with db() as s:
        return [{"id": e.id, "event_type": e.event_type, "summary": e.summary, "importance_score": e.importance_score, "created_at": e.created_at.isoformat() if e.created_at else None}
                for e in s.query(MemoryEvent).filter(MemoryEvent.session_id == sid, MemoryEvent.importance_score >= min_imp).order_by(MemoryEvent.importance_score.desc(), MemoryEvent.created_at.desc()).limit(limit).all()]

# ---- Global Insights (L4) ----

def save_global_insight(uid, insight, confidence=0.5):
    with db() as s: s.add(GlobalInsight(user_id=uid, insight=insight, confidence_score=confidence))

def get_global_insights(uid, limit=10):
    with db() as s:
        return [{"id": g.id, "insight": g.insight, "confidence_score": g.confidence_score, "source_count": g.source_count, "created_at": g.created_at.isoformat() if g.created_at else None}
                for g in s.query(GlobalInsight).filter_by(user_id=uid).order_by(GlobalInsight.confidence_score.desc(), GlobalInsight.created_at.desc()).limit(limit).all()]

# ---- Session Summary (L2) ----

def get_session_summary(sid):
    with db() as s:
        sess = s.get(Session, sid)
        return (sess.strategic_summary, sess.summary_msg_count or 0) if sess else (None, 0)

def save_session_summary(sid, summary, msg_count):
    with db() as s:
        sess = s.get(Session, sid)
        if sess: sess.strategic_summary = summary; sess.summary_msg_count = msg_count

# ---- Pipeline Stats ----

def get_pipeline_stats():
    with db() as s:
        return {status: s.query(Contact).filter_by(status=status).count() for status in CONTACT_STATUSES}


# ╔══════════════════════════════════════════════════════════════╗
# ║  NOTION INTEGRATION                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def _notion():
    return NotionClient(auth=NOTION_TOKEN) if NOTION_TOKEN else None

def _notion_query(nc, db_id, **kw):
    if hasattr(nc, 'databases') and hasattr(nc.databases, 'query'):
        return nc.databases.query(database_id=db_id, **kw)
    return nc.request(path=f"databases/{db_id}/query", method="post", body=kw if kw else {})

def _gp(props, name, ptype="rich_text"):
    try:
        p = props.get(name)
        if not p: return "" if ptype != "number" else None
        if ptype == "title": return p["title"][0]["plain_text"] if p.get("title") else ""
        elif ptype == "rich_text":
            parts = p.get("rich_text", [])
            return " ".join(x["plain_text"] for x in parts) if parts else ""
        elif ptype == "url": return p.get("url") or ""
        elif ptype == "select":
            sel = p.get("select")
            return sel["name"] if sel else ""
        elif ptype == "number": return p.get("number")
    except: return "" if ptype != "number" else None

def _fetch_notes(nc, page_id):
    notes = []
    try:
        children = nc.blocks.children.list(block_id=page_id)
        for block in children.get("results", []):
            if block["type"] == "child_page":
                title = block["child_page"]["title"]
                nblocks = nc.blocks.children.list(block_id=block["id"])
                texts = []
                for nb in nblocks.get("results", []):
                    btype = nb["type"]
                    if btype in ("paragraph", "bulleted_list_item", "numbered_list_item"):
                        parts = nb[btype].get("rich_text", [])
                        t = " ".join(p["plain_text"] for p in parts)
                        if t.strip(): texts.append(t)
                if texts: notes.append({"title": title, "content": "\n".join(texts)})
    except: pass
    return notes

@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_notion_companies():
    nc = _notion()
    if not nc or not NOTION_DATABASE_ID: return []
    all_results, has_more, cursor = [], True, None
    try:
        while has_more:
            kw = {}
            if cursor: kw["start_cursor"] = cursor
            res = _notion_query(nc, NOTION_DATABASE_ID, **kw)
            all_results.extend(res.get("results", []))
            has_more = res.get("has_more", False)
            cursor = res.get("next_cursor")
        companies = []
        for page in all_results:
            p = page["properties"]
            domain = _gp(p, "Domain", "title")
            if not domain: continue
            companies.append({"page_id": page["id"], "domain": domain, "name": _gp(p, "SPOC 2 (Editor)") or domain,
                "email": _gp(p, "SPOC Email"), "linkedin": _gp(p, "Owner/Lead Editor LinkedIn", "url"),
                "city": _gp(p, "City"), "state": _gp(p, "State"), "country": _gp(p, "Country"),
                "content_type": _gp(p, "Type of content", "select"), "publisher_type": _gp(p, "Type of Publisher", "select"),
                "monthly_visits": _gp(p, "Monthly visits (As of Oct'25)", "number"),
                "research_status": _gp(p, "Research", "select"), "gmail_thread": _gp(p, "Gmail Draft Link", "url")})
        return companies
    except Exception as e:
        st.error(f"Notion error: {e}"); return []

def sync_notion_to_db():
    companies_data = fetch_all_notion_companies()
    if not companies_data: return {"companies": 0, "contacts": 0}
    nc = _notion()
    cc, ctc = 0, 0
    for c in companies_data:
        domain = c.get("domain", "").strip()
        if not domain: continue
        notes = _fetch_notes(nc, c["page_id"]) if nc and c.get("page_id") else []
        cid = upsert_company(domain=domain, name=c.get("name") or domain, publisher_type=c.get("publisher_type"),
            content_type=c.get("content_type"), monthly_visits=c.get("monthly_visits"),
            notion_page_id=c.get("page_id"), research_status=c.get("research_status"),
            gmail_thread=c.get("gmail_thread"), extra_data={"research_notes": notes})
        cc += 1
        cn = c.get("name", "").strip()
        if cn:
            upsert_contact(company_id=cid, name=cn, role="SPOC / Editor", email=c.get("email"),
                linkedin=c.get("linkedin"), city=c.get("city"), state=c.get("state"),
                country=c.get("country"), notion_page_id=c.get("page_id"))
            ctc += 1
    fetch_all_notion_companies.clear()
    return {"companies": cc, "contacts": ctc}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_lead_from_notion(linkedin_url):
    nc = _notion()
    if not nc or not NOTION_DATABASE_ID: return None
    try:
        res = _notion_query(nc, NOTION_DATABASE_ID, filter={"property": "Owner/Lead Editor LinkedIn", "url": {"contains": linkedin_url.strip().rstrip("/")}})
        if not res.get("results"): return None
        page = res["results"][0]; p = page["properties"]; pid = page["id"]
        notes = _fetch_notes(nc, pid)
        return {"page_id": pid, "domain": _gp(p, "Domain", "title"), "name": _gp(p, "SPOC 2 (Editor)"),
            "email": _gp(p, "SPOC Email"), "linkedin": _gp(p, "Owner/Lead Editor LinkedIn", "url"),
            "city": _gp(p, "City"), "state": _gp(p, "State"), "country": _gp(p, "Country"),
            "content_type": _gp(p, "Type of content", "select"), "publisher_type": _gp(p, "Type of Publisher", "select"),
            "monthly_visits": _gp(p, "Monthly visits (As of Oct'25)", "number"),
            "research_status": _gp(p, "Research", "select"), "gmail_thread": _gp(p, "Gmail Draft Link", "url"),
            "research_notes": notes}
    except Exception as e:
        st.error(f"Notion error: {e}"); return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  GOOGLE DRIVE INTEGRATION                                    ║
# ╚══════════════════════════════════════════════════════════════╝

_drive_svc = None
_docs_svc = None
_file_cache = {}

def _drive():
    global _drive_svc
    if _drive_svc: return _drive_svc
    if not GOOGLE_SERVICE_ACCOUNT_JSON: return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(
            json.loads(GOOGLE_SERVICE_ACCOUNT_JSON),
            scopes=["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/documents.readonly"])
        _drive_svc = build("drive", "v3", credentials=creds)
        return _drive_svc
    except: return None

def _docs():
    global _docs_svc
    if _docs_svc: return _docs_svc
    if not GOOGLE_SERVICE_ACCOUNT_JSON: return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(
            json.loads(GOOGLE_SERVICE_ACCOUNT_JSON),
            scopes=["https://www.googleapis.com/auth/documents.readonly"])
        _docs_svc = build("docs", "v1", credentials=creds)
        return _docs_svc
    except: return None

def list_drive_files(company_name=None):
    svc = _drive()
    if not svc: return []
    try:
        parts = ["trashed = false"]
        if GOOGLE_DRIVE_FOLDER_ID: parts.append(f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents")
        if company_name:
            safe = company_name.replace("'", "\\'")
            parts.append(f"name contains '{safe}'")
        r = svc.files().list(q=" and ".join(parts), pageSize=50, fields="files(id,name,mimeType,modifiedTime,webViewLink)", orderBy="modifiedTime desc").execute()
        return r.get("files", [])
    except: return []

def fetch_doc_text(file_id, mime):
    cached = _file_cache.get(file_id)
    if cached and (datetime.utcnow() - cached["at"]).total_seconds() < 600: return cached["text"]
    text = None
    if mime == "application/vnd.google-apps.document":
        ds = _docs()
        if ds:
            try:
                doc = ds.documents().get(documentId=file_id).execute()
                parts = []
                for el in doc.get("body", {}).get("content", []):
                    if "paragraph" in el:
                        for e in el["paragraph"].get("elements", []):
                            if "textRun" in e: parts.append(e["textRun"].get("content", ""))
                text = "".join(parts).strip()
            except: pass
    elif mime == "application/pdf":
        svc = _drive()
        if svc:
            try:
                content = svc.files().get_media(fileId=file_id).execute()
                from PyPDF2 import PdfReader
                reader = PdfReader(io.BytesIO(content))
                text = "\n".join(pg.extract_text() or "" for pg in reader.pages).strip()
            except: pass
    if text: _file_cache[file_id] = {"text": text, "at": datetime.utcnow()}
    return text

def build_drive_context(company_name, company_domain=None):
    files = list_drive_files(company_name=company_name)
    if not files and company_domain: files = list_drive_files(company_name=company_domain)
    if not files: return ""
    parts, tokens = [], 0
    for f in files[:10]:
        text = fetch_doc_text(f["id"], f.get("mimeType", ""))
        if not text: continue
        ft = len(text) // 4
        if tokens + ft > MAX_DRIVE_TOKENS:
            rem = MAX_DRIVE_TOKENS - tokens
            if rem > 200: text = text[:rem*4] + "\n...(truncated)"; ft = rem
            else: break
        parts.append(f"--- {f['name']} ---\n{text}")
        tokens += ft
    return "\n\n".join(parts)


# ╔══════════════════════════════════════════════════════════════╗
# ║  LEAD SCORING                                                ║
# ╚══════════════════════════════════════════════════════════════╝

def compute_lead_score(comp, ct=None):
    score = 0
    v = comp.get("monthly_visits") or 0
    if v >= 10_000_000: score += 30
    elif v >= 1_000_000: score += 25
    elif v >= 500_000: score += 20
    elif v >= 100_000: score += 15
    elif v > 0: score += 10
    if comp.get("research_status") == "Completed": score += 25
    elif comp.get("research_status") == "In Progress": score += 10
    extra = comp.get("extra_data") or {}
    score += min(len(extra.get("research_notes", [])) * 5, 20)
    if ct:
        if ct.get("email"): score += 10
        if ct.get("linkedin"): score += 5
    pt = (comp.get("publisher_type") or "").lower()
    if "niche" in pt or "independent" in pt: score += 10
    elif "digital media" in pt: score += 8
    return min(score, 100)


# ╔══════════════════════════════════════════════════════════════╗
# ║  AI STRATEGIST + MEMORY LAYERS                               ║
# ╚══════════════════════════════════════════════════════════════╝

def _ai():
    return Anthropic(api_key=ANTHROPIC_API_KEY)

# L2: Rolling summary
def maybe_update_summary(sid, comp, ct):
    cnt = get_message_count(sid)
    _, last = get_session_summary(sid)
    if cnt - last < SUMMARY_INTERVAL: return
    try:
        msgs = load_messages(sid)
        if len(msgs) < 6: return
        convo = "\n".join([f"[{m['role']}] {m['content'][:500]}" for m in msgs[-30:]])
        r = _ai().messages.create(model=AI_MODEL, max_tokens=600, messages=[{"role": "user", "content": f"""Compress this outreach strategy conversation into a structured memory summary. Be concise but preserve all decisions.
TARGET: {comp.get('domain','?')} / {ct.get('name','?')}
CONVERSATION:
{convo}
Output format (under 400 words):
STRATEGIC DIRECTION: [1-2 sentences]
POSITIONING: [how we frame LH2]
KEY OBJECTIONS: [concerns]
TONE CALIBRATION: [style]
DECISIONS MADE: [specific choices]
CURRENT STATUS: [where we are]"""}])
        save_session_summary(sid, r.content[0].text.strip(), cnt)
    except: pass

# L3: Episodic extraction
def extract_episodic_memories(sid, ai_resp, comp):
    try:
        r = _ai().messages.create(model=AI_MODEL, max_tokens=400, messages=[{"role": "user", "content": f"""From this AI strategist response, extract 0-3 important episodic events worth remembering for future interactions with {comp.get('domain','this target')}.
RESPONSE:
{ai_resp[:2000]}
For each event, output one line: EVENT|type|summary|importance_score(1-10)
Types: decision, objection, insight, rejection, positive_signal, budget_info, timing_info, contact_change
If nothing noteworthy, output NONE."""}])
        text = r.content[0].text.strip()
        if text == "NONE": return
        for line in text.split("\n"):
            parts = line.strip().split("|")
            if len(parts) == 4 and parts[0] == "EVENT":
                try: sc = int(parts[3].strip())
                except: sc = 5
                save_memory_event(sid, parts[1].strip(), parts[2].strip(), min(sc, 10))
    except: pass

# L4: Insight from outcome
def generate_insight_from_outcome(uid, sid, action, comp):
    try:
        r = _ai().messages.create(model=AI_MODEL, max_tokens=200, messages=[{"role": "user", "content": f"""Generate ONE concise strategic insight (1-2 sentences) from this outreach outcome.
Target: {comp.get('domain','?')} ({comp.get('publisher_type','?')}, {comp.get('content_type','?')})
Action: {action.get('action_type','')} via {action.get('channel','')}
Outcome: {action.get('status','')}
Description: {action.get('description','')}
Return ONLY the insight. Make it generalizable."""}])
        ins = r.content[0].text.strip()
        save_global_insight(uid, ins, 0.6)
        return ins
    except: return None

# L5: Retrieval
def retrieve_relevant_memories(sid, uid, query, limit=5):
    kw = set(re.findall(r'\b[a-zA-Z]{4,}\b', query.lower()))
    kw -= {"what","when","where","this","that","they","their","about","should","would","could","have","with","from","been","some","more","into","also","just","like","make","want","need","will","does","than","very","most","much"}
    if not kw: return []
    mems = []
    for e in get_memory_events(sid, min_imp=3):
        ew = set(re.findall(r'\b[a-zA-Z]{4,}\b', e["summary"].lower()))
        ol = len(kw & ew)
        if ol > 0: mems.append({"type": "episodic", "text": e["summary"], "score": ol + e["importance_score"]/10})
    for g in get_global_insights(uid, limit=20):
        gw = set(re.findall(r'\b[a-zA-Z]{4,}\b', g["insight"].lower()))
        ol = len(kw & gw)
        if ol > 0: mems.append({"type": "global", "text": g["insight"], "score": ol + g["confidence_score"]})
    mems.sort(key=lambda x: x["score"], reverse=True)
    return mems[:limit]

# Action parsing
def parse_ai_actions(text):
    found = []
    for line in text.split("\n"):
        ll = line.lower().strip()
        if any(k in ll for k in ["send email","send linkedin","follow up","schedule call","send inmail","action:"]):
            ch = "Email"
            if "linkedin" in ll or "inmail" in ll: ch = "LinkedIn"
            elif "call" in ll: ch = "Phone"
            tm = re.search(r'(\w+day)[,\s]+(\w+ \d{1,2})[,\s]*([\d:]+\s*[AP]M)', line, re.IGNORECASE)
            sched = None
            if tm:
                try: sched = datetime.strptime(f"{tm.group(2)}, 2026 {tm.group(3)}", "%B %d, %Y %I:%M %p").isoformat()
                except: pass
            found.append({"type": "Outreach", "channel": ch, "description": line.strip()[:300], "scheduled_at": sched})
    return found[:6]

# Main strategist
def get_strategist_response(comp, ct, messages, user_msg, uid, sid):
    client = _ai()
    summary, _ = get_session_summary(sid)
    summary_block = f"\n{summary}" if summary else "(No summary yet.)"
    events = get_memory_events(sid, min_imp=4)
    events_block = "\n".join([f"- [{e['event_type']}] {e['summary']} (importance: {e['importance_score']}/10)" for e in events[:8]]) or "(No key events yet.)"
    ginsights = get_global_insights(uid, limit=8)
    global_block = "\n".join([f"- {g['insight']} (conf: {g['confidence_score']:.1f})" for g in ginsights]) or "(No global insights yet.)"
    relevant = retrieve_relevant_memories(sid, uid, user_msg)
    retrieval_block = "\n".join([f"- [{m['type']}] {m['text']}" for m in relevant]) or "(No relevant memories.)"
    research_context = ""
    extra = comp.get("extra_data") or {}
    for note in extra.get("research_notes", []):
        research_context += f"\n--- {note['title']} ---\n{note['content']}\n"
    drive_ctx = build_drive_context(comp.get("name", ""), comp.get("domain", ""))
    actions = get_session_actions(sid)
    action_history = "\n".join([f"- [{a['status']}] {a['action_type']} via {a['channel']}: {a['description'][:100]}" for a in actions[:10]]) or "(No prior actions.)"
    local_time, tz_str = get_local_time(ct.get("country", ""), ct.get("state", ""))
    score = compute_lead_score(comp, ct)

    system = f"""You are the LH2 Strategic Outreach Advisor for digital media partnerships.
You have layered memory — you remember everything about this target across the entire conversation, plus patterns from all past outreach.

== SECTION A: COMPANY PROFILE ==
Domain: {comp.get('domain','?')} | Type: {comp.get('publisher_type','?')} | Content: {comp.get('content_type','?')}
Monthly Visits: {comp.get('monthly_visits','?')} | Research: {comp.get('research_status','?')} | Score: {score}/100

== SECTION B: CONTACT PROFILE ==
Name: {ct.get('name','?')} | Role: {ct.get('role','?')} | Email: {ct.get('email','N/A')}
LinkedIn: {ct.get('linkedin','N/A')} | Location: {ct.get('city','')}, {ct.get('state','')} {ct.get('country','')}
Local Time: {local_time} ({tz_str or '?'}) | Status: {ct.get('status','not_contacted')}

== SECTION C: SESSION MEMORY SUMMARY ==
{summary_block}

== SECTION D: KEY EVENTS (Episodic Memory) ==
{events_block}

== SECTION E: RESEARCH CONTEXT ==
{research_context if research_context else '(No Notion research notes.)'}

{"== SECTION E2: DRIVE RESEARCH CONTEXT ==" + chr(10) + drive_ctx if drive_ctx else ""}

== SECTION F: INTERACTION HISTORY ==
{action_history}

== SECTION G: GLOBAL OUTREACH PATTERNS ==
{global_block}

== SECTION H: RELEVANT RETRIEVED MEMORIES ==
{retrieval_block}

== SECTION I: RULES ==
- Reference research findings and memory in every recommendation.
- Messages: human, professional, smart. No jargon. No filler.
- Specify exact send times with timezone.
- Emails: include subject line. LinkedIn requests: under 300 chars.
- Be decisive. State what to do, not what could be done.
- When suggesting actions, format: ACTION: [channel] [day, date, time TZ] — [description]"""

    api_msgs = [{"role": m["role"], "content": m["content"]} for m in messages[-MAX_CONTEXT_MESSAGES:]]
    api_msgs.append({"role": "user", "content": user_msg})
    try:
        r = client.messages.create(model=AI_MODEL, max_tokens=AI_MAX_TOKENS, system=system, messages=api_msgs)
        return r.content[0].text
    except Exception as e:
        return f"Error: {e}"


# ╔══════════════════════════════════════════════════════════════╗
# ║  EXPORT                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def export_session_csv(sid, domain):
    msgs, acts, evts = load_messages(sid), get_session_actions(sid), get_memory_events(sid, min_imp=1)
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["LH2 Export"]); w.writerow(["Domain", domain, "Exported", datetime.utcnow().isoformat()]); w.writerow([])
    w.writerow(["== CONVERSATION =="]); w.writerow(["Time","Role","Content"])
    for m in msgs: w.writerow([m["created_at"], m["role"], m["content"]])
    w.writerow([]); w.writerow(["== ACTIONS =="]); w.writerow(["Time","Type","Channel","Description","Status","Outcome","Content Sent","Response"])
    for a in acts: w.writerow([a["created_at"],a["action_type"],a["channel"],a["description"],a["status"],a.get("outcome",""),a.get("content_sent",""),a.get("response_content","")])
    w.writerow([]); w.writerow(["== MEMORY EVENTS =="]); w.writerow(["Time","Type","Summary","Importance"])
    for e in evts: w.writerow([e["created_at"],e["event_type"],e["summary"],e["importance_score"]])
    return out.getvalue()


# ╔══════════════════════════════════════════════════════════════╗
# ║  PERMISSIONS                                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def has_perm(perm):
    u = st.session_state.get("user")
    if not u: return False
    return ROLE_PERMISSIONS.get(u.get("role", "viewer"), {}).get(perm, False)


# ╔══════════════════════════════════════════════════════════════╗
# ║  AUTH GATE                                                   ║
# ╚══════════════════════════════════════════════════════════════╝

def render_auth():
    if "user" not in st.session_state: st.session_state.user = None
    if st.session_state.user: return True
    _, col, _ = st.columns([1.1, 0.8, 1.1])
    with col:
        st.markdown("""<div style='text-align:center;margin-top:14vh;margin-bottom:36px;'>
            <div style='font-size:14px;font-weight:700;letter-spacing:5px;color:var(--accent);margin-bottom:6px;'>◆ LH2</div>
            <div style='font-size:22px;font-weight:600;color:var(--text);'>Command Center</div>
            <div style='font-size:13px;color:var(--text-3);margin-top:6px;'>Relationship intelligence platform</div>
        </div>""", unsafe_allow_html=True)
        t1, t2 = st.tabs(["SIGN IN", "REGISTER"])
        with t1:
            with st.form("login"):
                u = st.text_input("Username", placeholder="Username")
                p = st.text_input("Password", type="password", placeholder="Password")
                if st.form_submit_button("Sign In"):
                    user = authenticate(u, p)
                    if user: st.session_state.user = user; st.rerun()
                    else: st.error("Invalid credentials")
        with t2:
            with st.form("register"):
                nu = st.text_input("Username", placeholder="Choose username")
                ne = st.text_input("Email", placeholder="your@email.com")
                np = st.text_input("Password", type="password", placeholder="Min 6 characters")
                if st.form_submit_button("Create Account"):
                    if len(nu) < 3 or len(np) < 6: st.error("Username 3+, password 6+ chars")
                    elif create_user(nu, np, ne): st.success("Created. Sign in above.")
                    else: st.error("Username taken")
    return False

if not render_auth(): st.stop()


# ╔══════════════════════════════════════════════════════════════╗
# ║  SESSION STATE                                               ║
# ╚══════════════════════════════════════════════════════════════╝

user = st.session_state.user
for k, d in [("sel_company", None), ("sel_contact", None), ("session_id", None), ("chat_messages", [])]:
    if k not in st.session_state: st.session_state[k] = d


# ╔══════════════════════════════════════════════════════════════╗
# ║  HEADER                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

h1, h2 = st.columns([3, 1])
with h1:
    parts = ["◆ LH2 COMMAND CENTER"]
    if st.session_state.sel_company:
        comp = get_company(st.session_state.sel_company)
        if comp: parts.append(f"<span style='color:var(--text-3);font-weight:400;'>→ {comp['domain']}</span>")
    if st.session_state.sel_contact:
        ct = get_contact(st.session_state.sel_contact)
        if ct: parts.append(f"<span style='color:var(--text-3);font-weight:400;'>→ {ct['name']}</span>")
    st.markdown(f"<div style='font-size:11px;font-weight:700;letter-spacing:3px;color:var(--accent);padding-bottom:6px;border-bottom:1px solid var(--border);margin-bottom:4px;'>{' '.join(parts)}</div>", unsafe_allow_html=True)
with h2:
    c1, c2 = st.columns([2.5, 1])
    with c1:
        sid = st.session_state.session_id
        if sid:
            summary, smcount = get_session_summary(sid)
            total = get_message_count(sid)
            evc = len(get_memory_events(sid, min_imp=1))
            mc = "mem-active" if summary else "mem-stale"
            ml = f"Memory: {total}msg / {evc}ev" if summary else f"Memory: {total}msg"
            st.markdown(f"<div style='text-align:right;margin-top:2px;'><span class='mem-indicator {mc}'>{ml}</span></div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<p style='text-align:right;font-size:12px;color:var(--text-3)!important;margin-top:4px;'>{user['username']} ({user['role']})</p>", unsafe_allow_html=True)
    with c2:
        if st.button("Sign Out", key="logout"):
            for k in list(st.session_state.keys()): del st.session_state[k]
            st.rerun()


# ╔══════════════════════════════════════════════════════════════╗
# ║  TABS                                                        ║
# ╚══════════════════════════════════════════════════════════════╝

tab_dash, tab_comp, tab_intel, tab_strat, tab_actions, tab_tl, tab_pipe, tab_hist = st.tabs(
    ["DASHBOARD", "COMPANIES", "INTELLIGENCE", "STRATEGIST", "ACTIONS", "TIMELINE", "PIPELINE", "HISTORY"])


# ════════════════════ DASHBOARD ════════════════════

with tab_dash:
    st.markdown(sh("OVERVIEW"), unsafe_allow_html=True)
    stats = get_pipeline_stats()
    mc = st.columns(4)
    with mc[0]: st.markdown(mcard(len(get_all_companies()), "Companies"), unsafe_allow_html=True)
    with mc[1]: st.markdown(mcard(stats.get("outreach_sent", 0) + stats.get("awaiting_reply", 0), "Active Outreach"), unsafe_allow_html=True)
    with mc[2]: st.markdown(mcard(stats.get("meeting_booked", 0), "Meetings Booked"), unsafe_allow_html=True)
    with mc[3]: st.markdown(mcard(stats.get("partnership_active", 0), "Partnerships"), unsafe_allow_html=True)

    st.markdown(sh("STATUS BREAKDOWN"), unsafe_allow_html=True)
    scols = st.columns(len(CONTACT_STATUSES))
    for col, s in zip(scols, CONTACT_STATUSES):
        with col: st.markdown(f"<div style='text-align:center;'><div style='font-size:20px;font-weight:700;'>{stats.get(s,0)}</div><div style='margin-top:4px;'>{bdg(CONTACT_STATUS_LABELS.get(s,s), CONTACT_STATUS_COLORS.get(s,'gray'))}</div></div>", unsafe_allow_html=True)

    overdue = get_overdue_followups(3)
    todays = get_todays_scheduled()
    if overdue or todays:
        st.markdown(sh("SMART ALERTS"), unsafe_allow_html=True)
        for a in overdue[:5]: st.markdown(alert(f"⚠️ Overdue: {a.get('contact_name','?')} at {a.get('company_domain','?')} — {a['description'][:100]}", "danger"), unsafe_allow_html=True)
        for a in todays[:5]: st.markdown(alert(f"📅 Today: {a.get('contact_name','?')} — {a['description'][:100]}"), unsafe_allow_html=True)

    st.markdown(sh("RECENT ACTIVITY"), unsafe_allow_html=True)
    recent = get_recent_activity(10)
    if recent:
        for a in recent:
            sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
            st.markdown(f"<div class='c' style='padding:10px 16px;'><div style='display:flex;justify-content:space-between;'><span class='c-l'>{a.get('company_domain','?')} · {a.get('contact_name','?')} · {a['action_type']}</span>{bdg(a['status'],sc)}</div><div style='font-size:12px;margin-top:4px;'>{a['description'][:200]}</div><div style='font-size:10px;color:var(--text-3);margin-top:3px;'>{a.get('username','?')} · {a['channel']} · {(a.get('created_at') or '')[:16]}</div></div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);'>No activity yet.</div>", unsafe_allow_html=True)


# ════════════════════ COMPANIES ════════════════════

with tab_comp:
    cc1, cc2, cc3 = st.columns([2, 1, 1])
    with cc1: sq = st.text_input("Search", placeholder="Search by domain or name...", label_visibility="collapsed", key="csearch")
    with cc2:
        if st.button("🔄 Sync from Notion", key="nsync", use_container_width=True):
            with st.spinner("Syncing..."): r = sync_notion_to_db(); st.success(f"Synced {r['companies']} companies, {r['contacts']} contacts"); st.rerun()
    with cc3:
        if st.button("Reset Selection", key="resetsel", use_container_width=True):
            st.session_state.sel_company = None; st.session_state.sel_contact = None; st.session_state.session_id = None; st.session_state.chat_messages = []; st.rerun()

    with st.expander("Quick add via LinkedIn URL"):
        li_url = st.text_input("LinkedIn URL", placeholder="Paste LinkedIn URL...", key="liurl")
        if st.button("Sync from LinkedIn", key="lisync"):
            if li_url:
                with st.spinner("Syncing..."):
                    lead = fetch_lead_from_notion(li_url)
                    if lead and lead.get("domain"):
                        cid = upsert_company(domain=lead["domain"], name=lead.get("name") or lead["domain"], publisher_type=lead.get("publisher_type"), content_type=lead.get("content_type"), monthly_visits=lead.get("monthly_visits"), notion_page_id=lead.get("page_id"), research_status=lead.get("research_status"), gmail_thread=lead.get("gmail_thread"), extra_data={"research_notes": lead.get("research_notes", [])})
                        if lead.get("name"): upsert_contact(company_id=cid, name=lead["name"], role="SPOC / Editor", email=lead.get("email"), linkedin=lead.get("linkedin"), city=lead.get("city"), state=lead.get("state"), country=lead.get("country"), notion_page_id=lead.get("page_id"))
                        st.session_state.sel_company = cid; st.success(f"Synced: {lead['domain']}"); st.rerun()
                    else: st.error("No match in Notion")

    companies = search_companies(sq) if sq else get_all_companies()
    if not companies:
        st.markdown("<div style='text-align:center;padding:80px 0;color:var(--text-3);'><div style='font-size:32px;opacity:.2;margin-bottom:14px;'>◆</div><div style='font-size:14px;font-weight:500;'>No companies yet</div><div style='font-size:13px;margin-top:4px;opacity:.7;'>Click 'Sync from Notion' to import</div></div>", unsafe_allow_html=True)
    else:
        st.markdown(sh(f"COMPANIES ({len(companies)})"), unsafe_allow_html=True)
        for comp in companies:
            x1, x2, x3 = st.columns([2.5, 1.5, 0.5])
            with x1: st.markdown(f"<div class='c' style='padding:10px 14px;'><div style='font-size:14px;font-weight:500;'>{comp['domain']}</div><div style='font-size:12px;color:var(--text-3);margin-top:2px;'>{comp.get('name','')} · {comp.get('publisher_type','')} · {fmt_v(comp.get('monthly_visits'))}</div></div>", unsafe_allow_html=True)
            with x2:
                cts = get_contacts(comp["id"])
                st.markdown(f"<div style='font-size:12px;color:var(--text-2);margin-top:8px;'>{len(cts)} contact(s)</div>", unsafe_allow_html=True)
            with x3:
                if st.button("→", key=f"sc_{comp['id']}", use_container_width=True):
                    st.session_state.sel_company = comp["id"]; st.session_state.sel_contact = None; st.session_state.session_id = None; st.session_state.chat_messages = []; st.rerun()


# ════════════════════ INTELLIGENCE ════════════════════

with tab_intel:
    cid = st.session_state.sel_company
    ctid = st.session_state.sel_contact
    if not cid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);font-size:13px;'>Select a company from the COMPANIES tab first.</div>", unsafe_allow_html=True)
    else:
        comp = get_company(cid)
        if not comp: st.error("Company not found.")
        else:
            st.markdown(sh("COMPANY PROFILE"), unsafe_allow_html=True)
            r1 = st.columns(5)
            for col, (l, v) in zip(r1, [("Domain", comp.get("domain")), ("Publisher Type", comp.get("publisher_type")), ("Content Type", comp.get("content_type")), ("Monthly Visits", fmt_v(comp.get("monthly_visits"))), ("Research", comp.get("research_status"))]):
                with col: st.markdown(crd(l, v), unsafe_allow_html=True)

            extra = comp.get("extra_data") or {}
            notes = extra.get("research_notes", [])
            if notes:
                st.markdown(sh("RESEARCH NOTES"), unsafe_allow_html=True)
                for n in notes:
                    with st.expander(n["title"]): st.markdown(f"<div class='rb'>{n['content'][:4000]}</div>", unsafe_allow_html=True)

            if GOOGLE_SERVICE_ACCOUNT_JSON:
                df = list_drive_files(company_name=comp.get("domain", ""))
                if df:
                    st.markdown(sh(f"DRIVE DOCUMENTS ({len(df)})"), unsafe_allow_html=True)
                    for f in df[:10]: st.markdown(f"<div class='c' style='padding:8px 14px;'><a href='{f.get('webViewLink','#')}' target='_blank'>{f['name']}</a> <span style='font-size:11px;color:var(--text-3);margin-left:12px;'>{f.get('mimeType','')}</span></div>", unsafe_allow_html=True)

            if comp.get("gmail_thread"): st.markdown(f"{sh('GMAIL')}<a href='{comp['gmail_thread']}' target='_blank'>Open Thread →</a>", unsafe_allow_html=True)

            # Contacts
            st.markdown(sh("CONTACTS"), unsafe_allow_html=True)
            contacts = get_contacts(cid)
            if not contacts: st.markdown("<div style='padding:20px;color:var(--text-3);font-size:13px;'>No contacts synced yet.</div>", unsafe_allow_html=True)
            for ct in contacts:
                ct1, ct2, ct3 = st.columns([2.5, 1.5, 0.5])
                with ct1:
                    scolor = CONTACT_STATUS_COLORS.get(ct.get("status","not_contacted"),"gray")
                    slabel = CONTACT_STATUS_LABELS.get(ct.get("status","not_contacted"), ct.get("status",""))
                    lt, _ = get_local_time(ct.get("country",""), ct.get("state",""))
                    st.markdown(f"<div class='c' style='padding:10px 14px;'><div style='display:flex;justify-content:space-between;'><span style='font-size:14px;font-weight:500;'>{ct['name']}</span>{bdg(slabel,scolor)}</div><div style='font-size:12px;color:var(--text-3);margin-top:3px;'>{ct.get('role','')} · {ct.get('email','')} · {lt}</div></div>", unsafe_allow_html=True)
                with ct2:
                    if ct.get("linkedin"): st.markdown(f"<a href='{ct['linkedin']}' target='_blank' style='font-size:12px;'>LinkedIn →</a>", unsafe_allow_html=True)
                with ct3:
                    if st.button("Select", key=f"sct_{ct['id']}", use_container_width=True):
                        st.session_state.sel_contact = ct["id"]
                        sid = get_or_create_session(cid, ct["id"], user["id"], compute_lead_score(comp, ct))
                        st.session_state.session_id = sid; st.session_state.chat_messages = load_messages(sid); st.rerun()

            # Active workspace
            if ctid:
                ct = get_contact(ctid)
                if ct:
                    st.markdown(sh(f"ACTIVE WORKSPACE — {ct['name']}"), unsafe_allow_html=True)
                    r2 = st.columns(5)
                    score = compute_lead_score(comp, ct)
                    for col, (l, v) in zip(r2, [("Lead Score", f"{'🟢' if score>=60 else '🟡' if score>=35 else '🔴'} {score}/100"), ("Email", ct.get("email")), ("Location", ", ".join(filter(None, [ct.get("city"), ct.get("state"), ct.get("country")]))), ("Local Time", get_local_time(ct.get("country",""), ct.get("state",""))[0]), ("Status", CONTACT_STATUS_LABELS.get(ct.get("status","not_contacted"), ct.get("status","")))]):
                        with col: st.markdown(crd(l, v), unsafe_allow_html=True)

                    ns = st.selectbox("Update Contact Status", CONTACT_STATUSES, index=CONTACT_STATUSES.index(ct.get("status","not_contacted")) if ct.get("status") in CONTACT_STATUSES else 0, format_func=lambda x: CONTACT_STATUS_LABELS.get(x,x), key="ctss")
                    if ns != ct.get("status"): update_contact_status(ctid, ns); st.rerun()

                    all_sess = get_sessions_for_company(cid)
                    if all_sess:
                        st.markdown(sh(f"ALL SESSIONS FOR {comp['domain']} ({len(all_sess)})"), unsafe_allow_html=True)
                        for sx in all_sess[:10]:
                            mc2 = get_message_count(sx["id"])
                            hm = "✓ Memory" if sx.get("strategic_summary") else ""
                            st.markdown(f"<div class='c' style='padding:8px 14px;'><span style='font-size:12px;'>Session #{sx['id']} · {mc2} messages {hm}</span><span style='font-size:11px;color:var(--text-3);margin-left:12px;'>Updated: {(sx.get('updated_at') or '')[:16]}</span></div>", unsafe_allow_html=True)

                    sid = st.session_state.session_id
                    if sid:
                        summary, smcount = get_session_summary(sid)
                        evts = get_memory_events(sid, min_imp=1)
                        if summary or evts:
                            st.markdown(sh("SESSION MEMORY"), unsafe_allow_html=True)
                            if summary:
                                with st.expander(f"Rolling Summary (from {smcount} messages)"): st.markdown(f"<div class='rb'>{summary}</div>", unsafe_allow_html=True)
                            if evts:
                                with st.expander(f"Episodic Events ({len(evts)})"):
                                    for e in evts:
                                        ic = "green" if e["importance_score"]>=7 else "yellow" if e["importance_score"]>=4 else "gray"
                                        st.markdown(f"<div class='c' style='padding:10px 14px;'><span style='font-size:11px;color:var(--text-3);'>{e['event_type']}</span> {bdg(str(e['importance_score'])+'/10',ic)}<div style='font-size:13px;margin-top:4px;'>{e['summary']}</div></div>", unsafe_allow_html=True)


# ════════════════════ STRATEGIST ════════════════════

with tab_strat:
    cid = st.session_state.sel_company; ctid = st.session_state.sel_contact; sid = st.session_state.session_id
    if not cid or not ctid or not sid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);font-size:13px;'>Select a company and contact in COMPANIES → INTELLIGENCE first.</div>", unsafe_allow_html=True)
    elif not has_perm("can_use_strategist"):
        st.warning("Your role does not have strategist access.")
    else:
        comp = get_company(cid); ct = get_contact(ctid)
        if not comp or not ct: st.error("Company or contact not found.")
        else:
            q1, q2, q3, q4 = st.columns(4)
            with q1:
                if st.button("Draft LinkedIn", key="qa1"): st.session_state.qa = "Draft a LinkedIn connection request for this contact. Under 300 characters. Specific to research."
            with q2:
                if st.button("Draft Email", key="qa2"): st.session_state.qa = "Draft the initial outreach email with subject line. Ground in research. Human and direct."
            with q3:
                if st.button("Send Time", key="qa3"): st.session_state.qa = "What's the optimal day and time for first outreach? Be specific: day, time, timezone."
            with q4:
                if st.button("Full Sequence", key="qa4"): st.session_state.qa = "Design a 4-touch outreach sequence. Timing, channel, angle per touch. Format each step as ACTION: [details]"

            for m in st.session_state.chat_messages:
                with st.chat_message(m["role"]): st.markdown(m["content"])

            qa = st.session_state.pop("qa", None)
            user_input = st.chat_input("Ask the strategist...")
            prompt = qa or user_input

            if prompt:
                with st.chat_message("user"): st.markdown(prompt)
                save_message(sid, "user", prompt)
                st.session_state.chat_messages.append({"role": "user", "content": prompt, "created_at": datetime.utcnow().isoformat()})

                with st.chat_message("assistant"):
                    with st.spinner("Analyzing..."):
                        response = get_strategist_response(comp, ct, st.session_state.chat_messages, prompt, user["id"], sid)
                        st.markdown(response)

                save_message(sid, "assistant", response)
                st.session_state.chat_messages.append({"role": "assistant", "content": response, "created_at": datetime.utcnow().isoformat()})
                maybe_update_summary(sid, comp, ct)
                extract_episodic_memories(sid, response, comp)

                detected = parse_ai_actions(response)
                if detected:
                    st.markdown(sh(f"DETECTED {len(detected)} ACTION(S)"), unsafe_allow_html=True)
                    for i, da in enumerate(detected):
                        dc1, dc2 = st.columns([3, 1])
                        with dc1: st.markdown(f"<div class='c' style='padding:10px 14px;'><span class='c-l'>{da['channel']}</span><div style='font-size:12px;margin-top:3px;'>{da['description'][:200]}</div></div>", unsafe_allow_html=True)
                        with dc2:
                            if st.button("Add to Plan", key=f"aa_{i}"):
                                log_action(sid, user["id"], da["type"], da["channel"], da["description"], "planned", scheduled_at=da.get("scheduled_at"))
                                st.success("Added!"); st.rerun()


# ════════════════════ ACTIONS ════════════════════

with tab_actions:
    cid = st.session_state.sel_company; ctid = st.session_state.sel_contact; sid = st.session_state.session_id
    if not sid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);'>Select a company and contact first.</div>", unsafe_allow_html=True)
    else:
        comp = get_company(cid) or {}; ct = get_contact(ctid) or {}
        st.markdown(sh(f"ACTIONS — {comp.get('domain','')} / {ct.get('name','')}"), unsafe_allow_html=True)

        if has_perm("can_log_outcomes"):
            with st.expander("Log New Action"):
                with st.form("actform"):
                    af1, af2 = st.columns(2)
                    with af1: atype = st.selectbox("Type", ACTION_TYPES)
                    with af2: achan = st.selectbox("Channel", ACTION_CHANNELS)
                    adesc = st.text_area("Description", placeholder="What happened or what's planned?", height=80)
                    acontent = st.text_area("Content Sent", placeholder="Exact message text (optional)", height=60)
                    acontext = st.text_input("Context", placeholder="Why this action? (optional)")
                    af3, af4 = st.columns(2)
                    with af3: astatus = st.selectbox("Status", ACTION_STATUSES)
                    with af4: asched = st.date_input("Scheduled", value=None)
                    if st.form_submit_button("Log Action"):
                        log_action(sid, user["id"], atype, achan, adesc, astatus, content_sent=acontent or None, context_note=acontext or None, scheduled_at=asched.isoformat() if asched else None)
                        if astatus in ("replied_positive","replied_negative","meeting_booked","deal_closed","deal_lost","no_response"):
                            ins = generate_insight_from_outcome(user["id"], sid, {"action_type":atype,"channel":achan,"description":adesc,"status":astatus}, comp)
                            if ins: st.info(f"Insight: {ins}")
                        st.success("Logged"); st.rerun()

        actions = get_session_actions(sid)
        if actions:
            for a in actions:
                ac1, ac2 = st.columns([3.5, 1])
                with ac1:
                    sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
                    seq = f" · Step {a['sequence_step']}" if a.get("sequence_step") else ""
                    resp_info = f"<div style='font-size:12px;color:var(--text-2);margin-top:4px;background:#f8f9fa;padding:6px 10px;border-radius:6px;'>Response: {a['response_content'][:200]}</div>" if a.get("response_content") else ""
                    st.markdown(f"<div class='c'><div style='display:flex;justify-content:space-between;align-items:center;'><span class='c-l'>{a['action_type']} · {a['channel']}{seq}</span>{bdg(a['status'],sc)}</div><div class='c-v' style='font-size:13px;margin-top:6px;'>{a['description'][:250]}</div>{resp_info}<div style='font-size:11px;color:var(--text-3);margin-top:5px;'>{(a.get('created_at') or '')[:16]}</div></div>", unsafe_allow_html=True)
                with ac2:
                    if has_perm("can_log_outcomes"):
                        ns = st.selectbox("Status", ACTION_STATUSES, index=ACTION_STATUSES.index(a["status"]) if a["status"] in ACTION_STATUSES else 0, key=f"as_{a['id']}", label_visibility="collapsed")
                        if ns != a["status"]:
                            update_action(a["id"], status=ns)
                            if ns in ("replied_positive","replied_negative","meeting_booked","deal_closed","deal_lost","no_response"):
                                generate_insight_from_outcome(user["id"], sid, {**a, "status": ns}, comp)
                            st.rerun()
        else:
            st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);font-size:13px;'>No actions yet.</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        ec, _ = st.columns([1, 3])
        with ec: st.download_button("Export CSV", data=export_session_csv(sid, comp.get("domain","")), file_name=f"lh2_{comp.get('domain','export')}_{datetime.utcnow().strftime('%Y%m%d')}.csv", mime="text/csv")


# ════════════════════ TIMELINE ════════════════════

with tab_tl:
    cid = st.session_state.sel_company
    if not cid:
        st.markdown("<div style='text-align:center;padding:60px 0;color:var(--text-3);font-size:13px;'>Select a company first.</div>", unsafe_allow_html=True)
    else:
        comp = get_company(cid) or {}
        ctid = st.session_state.sel_contact
        fl = f"OUTREACH TIMELINE — {comp.get('domain','')}"
        if ctid:
            ct = get_contact(ctid) or {}
            fl += f" / {ct.get('name','')}"
        st.markdown(sh(fl), unsafe_allow_html=True)
        tl_actions = get_timeline_actions(company_id=cid, contact_id=ctid, limit=50)
        if tl_actions:
            for a in tl_actions:
                date = (a.get("created_at") or "")[:10]
                desc = f"{a['action_type']} via {a['channel']}"
                if a.get("contact_name"): desc += f" → {a['contact_name']}"
                desc += f": {a['description'][:150]}"
                if a.get("username"): desc += f" (by {a['username']})"
                sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
                st.markdown(tl_item(date, desc, a["status"], sc), unsafe_allow_html=True)
        else:
            st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);'>No timeline entries yet.</div>", unsafe_allow_html=True)


# ════════════════════ PIPELINE ════════════════════

with tab_pipe:
    st.markdown(sh("CONTACT PIPELINE"), unsafe_allow_html=True)
    all_comp = get_all_companies()
    pipeline = {s: [] for s in CONTACT_STATUSES}
    for cd in all_comp:
        for ctd in get_contacts(cd["id"]):
            status = ctd.get("status", "not_contacted")
            if status in pipeline: pipeline[status].append({"contact": ctd, "company": cd})

    mcols = st.columns(len(CONTACT_STATUSES))
    for col, s in zip(mcols, CONTACT_STATUSES):
        with col: st.markdown(mcard(len(pipeline[s]), CONTACT_STATUS_LABELS.get(s,s)), unsafe_allow_html=True)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    bcols = st.columns(len(CONTACT_STATUSES))
    for i, s in enumerate(CONTACT_STATUSES):
        with bcols[i]:
            sc = CONTACT_STATUS_COLORS.get(s, "gray")
            st.markdown(f"<div style='text-align:center;margin-bottom:8px;'>{bdg(CONTACT_STATUS_LABELS.get(s,s),sc)}</div>", unsafe_allow_html=True)
            items = pipeline[s]
            if items:
                for item in items:
                    ctd, cd = item["contact"], item["company"]
                    score = compute_lead_score(cd, ctd)
                    si = "🟢" if score>=60 else "🟡" if score>=35 else "🔴"
                    st.markdown(f"<div class='pc'><div style='font-size:13px;font-weight:500;'>{ctd['name']}</div><div style='font-size:11px;color:var(--text-3);margin-top:2px;'>{cd['domain']}</div><div style='font-size:11px;color:var(--text-2);margin-top:3px;'>{si} {score}/100</div></div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='text-align:center;padding:24px 0;font-size:11px;color:#ccc;'>—</div>", unsafe_allow_html=True)


# ════════════════════ HISTORY ════════════════════

with tab_hist:
    ginsights = get_global_insights(user["id"], limit=8)
    if ginsights:
        st.markdown(sh("GLOBAL OUTREACH PATTERNS"), unsafe_allow_html=True)
        for g in ginsights:
            cc2 = "green" if g["confidence_score"]>=0.7 else "yellow" if g["confidence_score"]>=0.4 else "gray"
            conf_val = g['confidence_score']
            st.markdown(f"<div class='c' style='padding:12px 16px;'><div style='display:flex;justify-content:space-between;'><span style='font-size:13px;color:var(--text);'>{g['insight']}</span>{bdg(f'conf: {conf_val:.1f}',cc2)}</div><div style='font-size:10px;color:var(--text-3);margin-top:4px;'>{(g.get('created_at') or '')[:10]}</div></div>", unsafe_allow_html=True)

    all_ua = get_user_actions(user["id"], limit=200)
    if all_ua:
        st.markdown(sh("OUTREACH ANALYTICS"), unsafe_allow_html=True)
        total = len(all_ua)
        sent = sum(1 for a in all_ua if a["status"] in ("sent","completed","replied_positive","replied_negative","meeting_booked","deal_closed"))
        pos = sum(1 for a in all_ua if a["status"] in ("replied_positive","meeting_booked","deal_closed"))
        mtg = sum(1 for a in all_ua if a["status"] == "meeting_booked")
        nr = sum(1 for a in all_ua if a["status"] == "no_response")
        am = st.columns(5)
        for col, (n, l) in zip(am, [(total,"Total Actions"),(sent,"Sent"),(f"{(pos/sent*100):.0f}%" if sent else "—","Positive Rate"),(mtg,"Meetings"),(nr,"No Response")]):
            with col: st.markdown(mcard(n, l), unsafe_allow_html=True)

    st.markdown(sh("ALL SESSIONS"), unsafe_allow_html=True)
    sessions = get_user_sessions(user["id"])
    if sessions:
        for sx in sessions:
            sh1, sh2, sh3 = st.columns([2, 1.5, 1])
            with sh1:
                mt = " · ✓ Memory" if sx.get("strategic_summary") else ""
                st.markdown(f"<div class='c'><div class='c-l'>{sx.get('company_domain','?')}</div><div class='c-v'>{sx.get('contact_name','?')}{mt}</div></div>", unsafe_allow_html=True)
            with sh2:
                mc2 = get_message_count(sx["id"]); ac2 = len(get_session_actions(sx["id"]))
                st.markdown(f"<div class='c'><div class='c-l'>Activity</div><div class='c-v' style='font-size:13px;'>{mc2} messages · {ac2} actions · Score {sx['lead_score']}</div></div>", unsafe_allow_html=True)
            with sh3:
                b1, b2 = st.columns(2)
                with b1:
                    if st.button("Open", key=f"ho_{sx['id']}"):
                        st.session_state.sel_company = sx["company_id"]; st.session_state.sel_contact = sx["contact_id"]
                        st.session_state.session_id = sx["id"]; st.session_state.chat_messages = load_messages(sx["id"]); st.rerun()
                with b2:
                    if has_perm("can_delete_sessions"):
                        if st.button("Del", key=f"hd_{sx['id']}"):
                            delete_session(sx["id"])
                            if st.session_state.session_id == sx["id"]: st.session_state.session_id = None; st.session_state.chat_messages = []
                            st.rerun()
    else:
        st.markdown("<div style='text-align:center;padding:40px;color:var(--text-3);'>No sessions.</div>", unsafe_allow_html=True)

    if all_ua:
        st.markdown(sh("RECENT ACTIONS"), unsafe_allow_html=True)
        for a in all_ua[:12]:
            sc = ACTION_STATUS_COLORS.get(a["status"], "gray")
            st.markdown(f"<div class='c' style='padding:10px 16px;'><div style='display:flex;justify-content:space-between;'><span class='c-l'>{a.get('company_domain','?')} · {a['action_type']}</span>{bdg(a['status'],sc)}</div><div style='font-size:12px;margin-top:4px;'>{a['description'][:200]}</div><div style='font-size:10px;color:var(--text-3);margin-top:3px;'>{a['channel']} · {(a.get('created_at') or '')[:16]}</div></div>", unsafe_allow_html=True)