"""
config.py — Configuration, env vars, and constants
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Load from project root (directory containing this file) so env is found
# regardless of where the app is run from (important for DATABASE_URL / Postgres)
_config_dir = Path(__file__).resolve().parent
load_dotenv(_config_dir / "keys.env")
load_dotenv(_config_dir / ".env")

# ── Database ──────────────────────────────────────────────────
# Use same DATABASE_URL (e.g. Supabase) on all systems so data is shared (laptop + server).
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    try:
        import streamlit as st
        DATABASE_URL = (st.secrets.get("DATABASE_URL") or "").strip()
    except Exception:
        pass
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL:
    import logging
    _log = logging.getLogger(__name__)
    _log.warning(
        "DATABASE_URL is not set. Using local SQLite; data will NOT be shared with other systems. "
        "Set DATABASE_URL in keys.env (e.g. Supabase) so laptop and server use the same database."
    )

# ── API Keys ──────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
MAIN_FOLDER_ID = (os.getenv("MAIN_FOLDER_ID") or "").strip()  # Parent folder for AI knowledge (all subfolders/files)
CONTEXT_FOLDER_ID = (os.getenv("CONTEXT_FOLDER_ID") or "").strip()  # Folder containing training docs, outreach guidelines, etc.
SIGNALHIRE_API_KEY = os.getenv("SIGNALHIRE_API_KEY", "")
# Optional: base URL for your server that receives SignalHire callback (e.g. https://yourdomain.com)
SIGNALHIRE_CALLBACK_BASE_URL = os.getenv("SIGNALHIRE_CALLBACK_BASE_URL", "").rstrip("/")
# ── Notion DB ID (sanitized) ─────────────────────────────────
def _clean_notion_id(raw: str) -> str:
    """Accept full URLs, IDs with/without dashes, IDs with ?v= params."""
    clean = raw.strip().strip('"').strip("'")
    if not clean:
        return ""
    # Extract from full Notion URL
    if "notion.so" in clean or "notion.site" in clean:
        clean = clean.split("?")[0].rstrip("/").split("/")[-1]
    elif "?" in clean:
        clean = clean.split("?")[0]
    clean = clean.replace("-", "")
    # Validate: must be 32 hex chars
    if len(clean) == 32 and all(c in "0123456789abcdef" for c in clean.lower()):
        return f"{clean[:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:]}"
    return clean  # Return as-is if we can't parse it

NOTION_DATABASE_ID = _clean_notion_id(os.getenv("NOTION_DATABASE_ID", ""))
NOTION_DATABASE_ID_2 = _clean_notion_id(os.getenv("NOTION_DATABASE_ID_2", ""))

# ── AI Settings ───────────────────────────────────────────────
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-5")
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "4000"))
SUMMARY_INTERVAL = int(os.getenv("SUMMARY_INTERVAL", "20"))
MAX_CONTEXT_MESSAGES = 20
MAX_DRIVE_TOKENS = 6000

# ── Contact Pipeline ─────────────────────────────────────────
# "Awaiting Reply" and "Replied Negative" folded into "Outreach Sent" until action is taken
CONTACT_STATUSES = [
    "not_contacted", "outreach_sent",
    "replied_positive", "meeting_booked",
    "partnership_active", "closed_lost",
]
CONTACT_STATUS_LABELS = {
    "not_contacted": "Not Contacted",
    "outreach_sent": "Outreach Sent",
    "replied_positive": "Replied Positive",
    "meeting_booked": "Meeting Booked",
    "partnership_active": "Partnership Active",
    "closed_lost": "Closed Lost",
}
# Short labels for pipeline strip only (readable, single line, no wrap)
CONTACT_STATUS_LABELS_STRIP = {
    "not_contacted": "Not Contacted",
    "outreach_sent": "Outreach Sent",
    "replied_positive": "Replied",
    "meeting_booked": "Meeting Booked",
    "partnership_active": "Partnership",
    "closed_lost": "Closed Lost",
}
CONTACT_STATUS_COLORS = {
    "not_contacted": "gray",
    "outreach_sent": "blue",
    "replied_positive": "green",
    "meeting_booked": "green",
    "partnership_active": "green",
    "closed_lost": "red",
}


def normalize_contact_status(status):
    """Map legacy statuses into outreach_sent until user takes an action."""
    if status in ("awaiting_reply", "replied_negative"):
        return "outreach_sent"
    return status

# ── Actions ───────────────────────────────────────────────────
ACTION_TYPES = ["Outreach", "Follow-up", "Meeting", "Note", "Research", "Other"]
ACTION_CHANNELS = ["LinkedIn", "Email", "Phone", "In-Person", "Other"]
ACTION_STATUSES = [
    "planned", "sent", "completed", "no_response",
    "replied_positive", "replied_negative", "meeting_booked",
    "deal_closed", "deal_lost",
]
ACTION_STATUS_COLORS = {
    "planned": "blue",
    "sent": "yellow",
    "completed": "green",
    "no_response": "red",
    "replied_positive": "green",
    "replied_negative": "red",
    "meeting_booked": "green",
    "deal_closed": "green",
    "deal_lost": "red",
}

# ── Roles & Permissions ──────────────────────────────────────
USER_ROLES = ["admin", "strategist", "viewer"]
ROLE_PERMISSIONS = {
    "admin": {
        "can_delete_sessions": True, "can_edit_company": True,
        "can_log_outcomes": True, "can_manage_users": True,
        "can_use_strategist": True,
    },
    "strategist": {
        "can_delete_sessions": False, "can_edit_company": True,
        "can_log_outcomes": True, "can_manage_users": False,
        "can_use_strategist": True,
    },
    "viewer": {
        "can_delete_sessions": False, "can_edit_company": False,
        "can_log_outcomes": False, "can_manage_users": False,
        "can_use_strategist": False,
    },
}

# ── Timezones ─────────────────────────────────────────────────
TIMEZONE_COUNTRIES = {
    "uk": "Europe/London", "united kingdom": "Europe/London",
    "britain": "Europe/London", "india": "Asia/Kolkata",
    "australia": "Australia/Sydney", "canada": "America/Toronto",
    "germany": "Europe/Berlin", "france": "Europe/Paris",
    "japan": "Asia/Tokyo", "brazil": "America/Sao_Paulo",
    "singapore": "Asia/Singapore", "uae": "Asia/Dubai",
    "emirates": "Asia/Dubai", "israel": "Asia/Jerusalem",
    "korea": "Asia/Seoul", "spain": "Europe/Madrid",
    "italy": "Europe/Rome", "mexico": "America/Mexico_City",
    "netherlands": "Europe/Amsterdam", "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo", "denmark": "Europe/Copenhagen",
    "portugal": "Europe/Lisbon", "poland": "Europe/Warsaw",
    "turkey": "Europe/Istanbul", "south africa": "Africa/Johannesburg",
    "china": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
    "taiwan": "Asia/Taipei", "thailand": "Asia/Bangkok",
    "indonesia": "Asia/Jakarta", "philippines": "Asia/Manila",
    "new zealand": "Pacific/Auckland",
    "argentina": "America/Argentina/Buenos_Aires",
    "chile": "America/Santiago", "colombia": "America/Bogota",
}
US_TZ = {
    "US/Pacific": [
        "ca", "california", "wa", "washington", "or", "oregon",
        "nv", "nevada",
    ],
    "US/Mountain": [
        "co", "colorado", "az", "arizona", "ut", "utah",
        "mt", "montana", "nm", "new mexico", "wy", "wyoming",
        "id", "idaho",
    ],
    "US/Central": [
        "il", "illinois", "tx", "texas", "mn", "minnesota",
        "wi", "wisconsin", "mo", "missouri", "ia", "iowa",
        "tn", "tennessee", "la", "louisiana", "ok", "oklahoma",
        "ar", "arkansas", "ks", "kansas", "ne", "nebraska",
        "al", "alabama", "ms", "mississippi",
    ],
}