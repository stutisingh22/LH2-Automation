"""
models.py — SQLAlchemy ORM models
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime,
    ForeignKey, JSON, UniqueConstraint, Index,
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
    __table_args__ = (Index("ix_contacts_company_id_name", "company_id", "name"),)


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
    __table_args__ = (Index("ix_sessions_company_id_updated_at", "company_id", "updated_at"),)


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
    outcome_reason = Column(Text)  # deterministic rule path / explainability (Gmail sync)
    outreach_sync_meta = Column(JSON)  # {source, gmail_message_id, event_kind, synced_at, ...}
    context_note = Column(Text)
    sequence_id = Column(String(20))
    sequence_step = Column(Integer)
    scheduled_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    draft_revisions = Column(JSON)  # list of revision strings (persists across refresh)
    draft_original = Column(Text)  # original draft before any revisions (for Version 1 after refresh)
    __table_args__ = (Index("ix_actions_session_id_created_at", "session_id", "created_at"),)


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
    """
    Media Houses / Advisor companies (Notion DB2). Stored in main DB when DATABASE_URL is set (Supabase).
    - domain: title property (child company identifier)
    - parent_name: select property "media" (parent company)
    - extra_data: optional JSON for research_full etc.
    """
    __tablename__ = "advisor_companies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(255), nullable=False, index=True)
    parent_name = Column(String(255), nullable=False, index=True)  # media = parent company
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
    __table_args__ = (Index("ix_spoc_contacts_company_spoc", "company_id", "spoc_contact_id"),)


class ProcessedGmailMessage(Base):
    """Tracks Gmail message IDs we've already synced so we don't log the same sent email twice."""
    __tablename__ = "processed_gmail_messages"
    __table_args__ = (UniqueConstraint("user_id", "gmail_message_id", name="uq_user_gmail_msg"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    gmail_message_id = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class GmailAccount(Base):
    """
    Connected Gmail (OAuth) per app user. Tokens encrypted at rest.
    """
    __tablename__ = "gmail_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    access_token_enc = Column(Text, nullable=False)
    refresh_token_enc = Column(Text)
    token_expiry = Column(DateTime)
    last_sync_at = Column(DateTime)
    last_sync_error = Column(Text)
    last_sync_stats = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_gmail_account_user_email"),)


class EmailThread(Base):
    """Aggregated Gmail thread metadata per connected account."""
    __tablename__ = "email_threads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    gmail_account_id = Column(Integer, ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(64), nullable=False)
    subject = Column(String(1024))
    snippet = Column(Text)
    last_message_at = Column(DateTime, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("gmail_account_id", "thread_id", name="uq_gmail_thread_account"),
        Index("ix_email_threads_user_last", "user_id", "last_message_at"),
    )


class InboxEmail(Base):
    """Individual messages pulled from Gmail API (inbox + sent)."""
    __tablename__ = "emails"
    id = Column(Integer, primary_key=True, autoincrement=True)
    gmail_message_id = Column(String(64), nullable=False, index=True)
    thread_id = Column(Integer, ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    gmail_account_id = Column(Integer, ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    from_email = Column(String(255))
    to_emails = Column(JSON)
    subject = Column(String(1024))
    body_snippet = Column(Text)
    body_full = Column(Text)
    timestamp = Column(DateTime, nullable=False, index=True)
    is_sent = Column(Boolean, default=False, nullable=False, index=True)
    thread_label = Column(String(32))
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("gmail_account_id", "gmail_message_id", name="uq_inbox_email_account_msg"),
        Index("ix_emails_user_ts", "user_id", "timestamp"),
        Index("ix_emails_thread_ts", "thread_id", "timestamp"),
    )


class GmailOutreachSyncEvent(Base):
    """
    Idempotency: one sync-side transition per Gmail message per connected account.
    Prevents duplicate Action updates when sync runs repeatedly.
    """
    __tablename__ = "gmail_outreach_sync_events"
    __table_args__ = (
        UniqueConstraint("gmail_account_id", "gmail_message_id", name="uq_gmail_outreach_msg"),
        Index("ix_gmail_outreach_action", "action_id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    gmail_account_id = Column(Integer, ForeignKey("gmail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    gmail_message_id = Column(String(64), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    action_id = Column(Integer, ForeignKey("actions.id", ondelete="SET NULL"), nullable=True, index=True)
    event_kind = Column(String(24), nullable=False)  # sent_sync | reply_sync
    applied_status = Column(String(50))
    outcome_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)