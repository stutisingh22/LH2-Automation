"""
models_advisor.py — ORM models for the Advisor (DB2) Notion source only.
Separate from main models.py so primary platform logic is unaffected.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base

BaseAdvisor = declarative_base()


class AdvisorCompany(BaseAdvisor):
    """
    Child company from NOTION_DATABASE_ID_2.
    - domain: title property (child company identifier)
    - parent_name: select property "media" (parent company)
    """
    __tablename__ = "advisor_companies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(255), nullable=False, index=True)
    parent_name = Column(String(255), nullable=False, index=True)  # media = parent company
    notion_page_id = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)
