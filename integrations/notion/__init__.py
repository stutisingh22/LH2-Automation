"""
Notion integration. Single entry point for the app.
Implementation: sync.py (primary DB), sync_advisor.py (Advisor DB2).
"""

from integrations.notion.sync import (
    sync_notion_to_db,
    lookup_lead_in_notion,
    fetch_notes_for_page,
    create_notion_contact_page,
    archive_notion_page,
)
from integrations.notion.sync_advisor import (
    sync_notion_advisor_to_db,
    create_notion_advisor_page,
    archive_notion_advisor_page,
)

__all__ = [
    "sync_notion_to_db",
    "lookup_lead_in_notion",
    "fetch_notes_for_page",
    "create_notion_contact_page",
    "archive_notion_page",
    "sync_notion_advisor_to_db",
    "create_notion_advisor_page",
    "archive_notion_advisor_page",
]
