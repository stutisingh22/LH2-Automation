"""
Bulk Domain Import — CSV/XLSX upload, classify independent vs media house, add to DB + Notion.

Flow:
- Parse file → extract domains (with www trimming / normalization for existence check).
- If domain already exists (main companies or advisor/media house DB) → flag only.
- If new: AI classifies independent vs media_house.
- Independent → add to Notion (Independent DB) + app companies table.
- Media house → if parent matches existing media house → add as child to Notion (DB2) + advisor_companies; else flag for review.
"""

import io
import logging
from typing import List

import pandas as pd
import streamlit as st

from config import normalize_domain
from db_ops import get_company_by_domain, upsert_company, delete_company_by_domain
from db_advisor import (
    get_advisor_company_by_domain, get_advisor_parents_with_children, upsert_advisor_company, delete_advisor_company
)
from notion_sync import create_notion_contact_page, archive_notion_page
from notion_sync_advisor import create_notion_advisor_page, archive_notion_advisor_page
from ai_engine import classify_domains_independent_or_media_house, classify_entries_independent_or_media_house
from services.ui_data_loader import clear_ui_cache

log = logging.getLogger(__name__)

# Column names we treat as domain (first match wins)
DOMAIN_COLUMN_CANDIDATES = ("domain", "Domain", "website", "Website", "url", "URL", "site", "Site", "domain_name")


def _parse_domains_from_file(uploaded_file) -> List[str]:
    """Parse CSV or XLSX and return list of domain-like strings (raw, not yet normalized)."""
    raw = uploaded_file.read()
    name = (uploaded_file.name or "").lower()
    domains = []
    try:
        if name.endswith(".xlsx") or name.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
        else:
            df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        log.warning("Parse file failed: %s", e)
        return []
    if df.empty:
        return []
    # Find domain column
    col = None
    for c in DOMAIN_COLUMN_CANDIDATES:
        if c in df.columns:
            col = c
            break
    if col is None:
        col = df.columns[0]
    for v in df[col].dropna().astype(str).str.strip():
        v = (v or "").strip()
        if not v or v in ("nan", "NaN", ""):
            continue
        # Basic domain-like: has a dot and no spaces
        if "." in v and " " not in v:
            domains.append(v)
    return list(dict.fromkeys(domains))  # preserve order, dedupe


def _parse_entries_from_text(text: str) -> List[str]:
    """Parse manual input: newline or comma separated names/domains; strip and dedupe."""
    if not text or not text.strip():
        return []
    parts = []
    for line in text.replace(",", "\n").splitlines():
        for part in line.split(","):
            p = (part or "").strip()
            if p and p not in ("nan", "NaN"):
                parts.append(p)
    return list(dict.fromkeys(parts))


def get_existing_parent_media_houses() -> List[str]:
    """Return list of existing parent media house names (display names)."""
    out = []
    for group in get_advisor_parents_with_children():
        p = (group.get("parent_name") or "").strip()
        if p and p != "(No parent)":
            out.append(p)
    return sorted(list(dict.fromkeys(out)))


def analyze_from_file(uploaded_file) -> dict:
    """Analyze a CSV/XLSX and return preview rows; no writes."""
    domains_raw = _parse_domains_from_file(uploaded_file)
    return analyze_from_domains(domains_raw)


def analyze_from_text(text: str) -> dict:
    """Analyze manual input (names/domains) and return preview rows; no writes."""
    entries = _parse_entries_from_text(text or "")
    return analyze_from_entries(entries)


def analyze_from_domains(domains_raw: List[str]) -> dict:
    """Analyze list of domains (strings). Returns {"rows": [...], "errors": [...]}. No writes."""
    result = {"rows": [], "errors": []}
    if not domains_raw:
        result["errors"].append("No domains found.")
        return result

    # Normalize and dedupe
    seen = set()
    domains = []
    for d in domains_raw:
        norm = normalize_domain(d)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        domains.append(norm)

    existing_parents = get_existing_parent_media_houses()
    classified = classify_domains_independent_or_media_house(domains)
    by_dom = {normalize_domain(c.get("domain") or ""): c for c in classified if c}

    for d in domains:
        exists = bool(get_company_by_domain(d) or get_advisor_company_by_domain(d))
        c = by_dom.get(d) or {}
        cl = (c.get("classification") or "independent").strip().lower().replace(" ", "_")
        if cl not in ("independent", "media_house"):
            cl = "independent"
        parent = (c.get("parent_name") or "").strip()
        result["rows"].append({
            "do_import": not exists,
            "input": d,
            "domain": d,
            "exists": exists,
            "classification": cl,
            "parent_name": parent,
            "parent_confirmed": "",
            "status": "exists" if exists else "new",
        })
    result["existing_parents"] = existing_parents
    return result


def analyze_from_entries(entries: List[str]) -> dict:
    """Analyze list of names/domains. Returns preview rows; no writes."""
    result = {"rows": [], "errors": []}
    entries = [e.strip() for e in (entries or []) if e and str(e).strip()]
    if not entries:
        result["errors"].append("No names or domains entered.")
        return result

    existing_parents = get_existing_parent_media_houses()
    classified = classify_entries_independent_or_media_house(entries)
    # map by exact input (lower) to keep order
    by_inp = {(c.get("input") or "").strip().lower(): c for c in (classified or []) if isinstance(c, dict)}
    for inp in entries:
        c = by_inp.get(inp.strip().lower()) or {}
        dom = normalize_domain(c.get("domain") or inp)
        exists = bool(get_company_by_domain(dom) or get_advisor_company_by_domain(dom))
        cl = (c.get("classification") or "independent").strip().lower().replace(" ", "_")
        if cl not in ("independent", "media_house"):
            cl = "independent"
        parent = (c.get("parent_name") or "").strip()
        result["rows"].append({
            "do_import": not exists,
            "input": inp,
            "domain": dom,
            "exists": exists,
            "classification": cl,
            "parent_name": parent,
            "parent_confirmed": "",
            "status": "exists" if exists else "new",
        })
    result["existing_parents"] = existing_parents
    return result


def commit_rows(rows: List[dict]) -> dict:
    """
    Commit reviewed rows into DBs/Notion. Requires explicit parent_confirmed for media_house rows.
    Returns summary + created items for undo.
    """
    result = {
        "added_independent": [],
        "added_media_house": [],
        "flagged_already_exists": [],
        "flagged_for_review": [],
        "errors": [],
        "created": [],  # for undo: list of dicts
    }
    existing_parents = set(get_existing_parent_media_houses())

    for r in rows or []:
        if not r.get("do_import"):
            continue
        dom = normalize_domain(r.get("domain") or "")
        if not dom:
            continue
        label = (r.get("input") or dom).strip()
        # existence check again at commit time
        if get_company_by_domain(dom) or get_advisor_company_by_domain(dom):
            result["flagged_already_exists"].append(label)
            continue
        cl = (r.get("classification") or "").strip().lower().replace(" ", "_")
        if cl not in ("independent", "media_house"):
            cl = "independent"

        if cl == "independent":
            try:
                notion_page_id = create_notion_contact_page(domain=dom, name=label)
                company_id = upsert_company(dom, name=label, notion_page_id=notion_page_id)
                result["added_independent"].append(label)
                result["created"].append({
                    "type": "independent",
                    "domain": dom,
                    "company_id": company_id,
                    "notion_page_id": notion_page_id,
                })
            except Exception as e:
                log.exception("Commit independent failed for %s: %s", dom, e)
                result["errors"].append(f"{label}: {e}")
        else:
            parent_confirmed = (r.get("parent_confirmed") or "").strip()
            # Safety: do not write media_house unless parent is explicitly selected and exists
            if not parent_confirmed or parent_confirmed not in existing_parents:
                result["flagged_for_review"].append(label)
                continue
            try:
                notion_page_id = create_notion_advisor_page(dom, parent_confirmed)
                advisor_id = upsert_advisor_company(dom, parent_confirmed, notion_page_id=notion_page_id)
                result["added_media_house"].append(label)
                result["created"].append({
                    "type": "media_house",
                    "domain": dom,
                    "parent_name": parent_confirmed,
                    "advisor_id": advisor_id,
                    "notion_page_id": notion_page_id,
                })
            except Exception as e:
                log.exception("Commit media_house failed for %s: %s", dom, e)
                result["errors"].append(f"{label}: {e}")

    clear_ui_cache()
    return result


def undo_created(created: List[dict]) -> dict:
    """Undo a previous commit: deletes DB rows and archives Notion pages created by this tool."""
    out = {"undone": 0, "errors": []}
    for item in created or []:
        try:
            t = item.get("type")
            page_id = item.get("notion_page_id")
            if t == "independent":
                delete_company_by_domain(item.get("domain"))
                if page_id:
                    archive_notion_page(page_id)
                out["undone"] += 1
            elif t == "media_house":
                delete_advisor_company(item.get("domain"), item.get("parent_name"))
                if page_id:
                    archive_notion_advisor_page(page_id)
                out["undone"] += 1
        except Exception as e:
            out["errors"].append(str(e))
    clear_ui_cache()
    return out


__all__ = [
    "_parse_entries_from_text",
    "analyze_from_file",
    "analyze_from_text",
    "analyze_from_domains",
    "analyze_from_entries",
    "commit_rows",
    "undo_created",
    "get_existing_parent_media_houses",
]
