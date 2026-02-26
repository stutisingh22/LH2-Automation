"""
models.py — SQLAlchemy ORM models
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime,
    ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship, declarative_base

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
    contacts = relationship(
        "Contact", back_populates="company", cascade="all, delete-orphan"
    )


class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
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
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    strategic_summary = Column(Text)
    summary_msg_count = Column(Integer, default=0)
    lead_score = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer, ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Action(Base):
    __tablename__ = "actions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer, ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
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
    draft_revisions = Column(JSON)  # list of revision strings (persists across refresh)
    draft_original = Column(Text)  # original draft before any revisions (for Version 1 after refresh)


class MemoryEvent(Base):
    __tablename__ = "memory_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer, ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
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


class CachedContext(Base):
    """Persisted text cache for long-running context builds (Drive, playbooks)."""
    __tablename__ = "cached_contexts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdvisorConversation(Base):
    __tablename__ = "advisor_conversations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255))
    messages = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EnrichmentResult(Base):
    """Stores SignalHire (or other) enrichment results for contact lookup."""
    __tablename__ = "enrichment_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String(64), index=True)  # SignalHire requestId when using callback
    identifier = Column(String(512), nullable=False)  # LinkedIn URL or email used for lookup
    result_json = Column(JSON)  # Normalized {verified, enriched, contacts[], source, confidence}
    status = Column(String(20), default="pending")  # pending | completed | failed
    created_at = Column(DateTime, default=datetime.utcnow)


class AdvisorCompany(Base):
    """Media Houses / Advisor companies (Notion DB2). Stored in main DB when DATABASE_URL is set (Supabase)."""
    __tablename__ = "advisor_companies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(255), nullable=False, index=True)
    parent_name = Column(String(255), nullable=False, index=True)
    notion_page_id = Column(String(64))
    extra_data = Column(JSON)  # research_full, etc., when using main DB
    created_at = Column(DateTime, default=datetime.utcnow)


class SpocContact(Base):
    """
    Contacts saved from the Strategist (brief/research + LinkedIn + SignalHire), tagged under a SPOC.
    Separate table so existing contacts table is unchanged; clear columns for email, linkedin_url, phone.
    """
    __tablename__ = "spoc_contacts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    spoc_contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = Column(String(255), nullable=False)
    role = Column(String(150))
    email = Column(String(255))
    linkedin_url = Column(Text)
    phone = Column(String(100))
    extra_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProcessedGmailMessage(Base):
    """Tracks Gmail message IDs we've already synced so we don't log the same sent email twice."""
    __tablename__ = "processed_gmail_messages"
    __table_args__ = (UniqueConstraint("user_id", "gmail_message_id", name="uq_user_gmail_msg"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    gmail_message_id = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)