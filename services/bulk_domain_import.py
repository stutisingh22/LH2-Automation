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
    get_advisor_company_by_domain, upsert_advisor_company, delete_advisor_company
)
from notion_sync import create_notion_contact_page, archive_notion_page
from notion_sync_advisor import create_notion_advisor_page, archive_notion_advisor_page
from ai_engine import classify_domains_independent_or_media_house, classify_entries_independent_or_media_house
from services.ui_data_loader import clear_ui_cache, load_advisor_parents_with_children
from services.cache_registry import invalidate_group
from services.ownership_verifier import verify_parent_from_web

log = logging.getLogger(__name__)

# Column names we treat as domain (first match wins)
DOMAIN_COLUMN_CANDIDATES = ("domain", "Domain", "website", "Website", "url", "URL", "site", "Site", "domain_name")

def _norm_parent_key(name: str) -> str:
    """Normalize parent/company names for matching (case/space/punctuation-insensitive)."""
    s = (name or "").strip().lower()
    return "".join(ch for ch in s if ch.isalnum())


def _suggest_parent_from_data(ai_parent_name: str, ai_parent_match: str, existing_parents: List[str]) -> tuple[str, str]:
    """
    Suggest a parent from the existing list only. Returns (suggested_parent, reason).
    Never returns a value not present in existing_parents.
    """
    if not existing_parents:
        return "", "no existing parents"
    existing = list(existing_parents)
    by_key = {_norm_parent_key(p): p for p in existing if p}

    # Do NOT trust model-provided matches by themselves; they must be verified via evidence (or deterministic normalization).

    k = _norm_parent_key(ai_parent_name or "")
    if k and k in by_key:
        return by_key[k], f"normalized_key_match({ai_parent_name}→{by_key[k]})"

    # Containment heuristic on normalized keys (e.g. 'voxmedia' vs 'voxmediagroup')
    if k:
        for ek, disp in by_key.items():
            if ek and (ek in k or k in ek):
                return disp, f"normalized_contains({ai_parent_name}→{disp})"

    return "", "no_safe_match"


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
    """Return list of existing parent media house names (display names). Uses cached loader for performance."""
    out = []
    for group in load_advisor_parents_with_children():
        p = (group.get("parent_name") or "").strip()
        if p and p != "(No parent)":
            out.append(p)
    return sorted(list(dict.fromkeys(out)))


def analyze_from_file(uploaded_file, verify_ownership: bool = True, max_verify: int = 500) -> dict:
    """Analyze a CSV/XLSX and return preview rows; no writes."""
    domains_raw = _parse_domains_from_file(uploaded_file)
    return analyze_from_domains(domains_raw, verify_ownership=verify_ownership, max_verify=max_verify)


def analyze_from_text(text: str, verify_ownership: bool = True, max_verify: int = 500) -> dict:
    """Analyze manual input (names/domains) and return preview rows; no writes."""
    entries = _parse_entries_from_text(text or "")
    return analyze_from_entries(entries, verify_ownership=verify_ownership, max_verify=max_verify)


def analyze_from_domains(domains_raw: List[str], verify_ownership: bool = True, max_verify: int = 500) -> dict:
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
    classified = classify_domains_independent_or_media_house(domains, allowed_parents=existing_parents)
    by_dom = {normalize_domain(c.get("domain") or ""): c for c in classified if c}

    for d in domains:
        existing_company = get_company_by_domain(d)
        existing_advisor = get_advisor_company_by_domain(d)
        exists_db = bool(existing_company or existing_advisor)
        exists = bool(exists_db)
        existing_parent = (existing_advisor or {}).get("parent_name") or ""
        existing_parent = (existing_parent or "").strip() if existing_parent else ""

        c = by_dom.get(d) or {}
        suggested, reason = ("", "")
        evidence_urls = ""
        new_parent_candidate = ""

        # If domain exists in DB in any form: use only DB classification and parent — never override with AI.
        if exists_db:
            if existing_advisor:
                cl = "media_house"
                display_parent = existing_parent
                suggested = existing_parent
                reason = "existing_in_db"
            else:
                cl = "independent"
                display_parent = ""
                suggested = ""
                reason = "existing_in_db (independent)"
        else:
            cl = (c.get("classification") or "independent").strip().lower().replace(" ", "_")
            if cl not in ("independent", "media_house"):
                cl = "independent"
            parent = (c.get("parent_name") or "").strip()
            parent_match = (c.get("parent_match") or "").strip()
            display_parent = parent

            if cl == "media_house":
                # New domain: run full research (web verification) to get operational/ultimate parent, then suggest from existing only.
                if verify_ownership and max_verify > 0:
                    ver = verify_parent_from_web(d, existing_parents)
                    if ver.get("parent_match"):
                        suggested = ver["parent_match"]
                        display_parent = suggested
                        reason = f"web_verified({ver.get('match_reason','')},conf={ver.get('confidence', 0):.2f})"
                    elif ver.get("direct_parent") or ver.get("ultimate_parent"):
                        display_parent = ver.get("ultimate_parent") or ver.get("direct_parent") or ""
                        new_parent_candidate = (ver.get("new_parent_candidate") or "").strip()
                    ev = ver.get("evidence") or []
                    evidence_urls = "\n".join([e.get("href", "") for e in ev[:5] if e.get("href")])
                    max_verify -= 1
                if not suggested:
                    suggested, reason = _suggest_parent_from_data(parent, parent_match, existing_parents)
                    if suggested:
                        display_parent = suggested
        result["rows"].append({
            "do_import": not exists,
            "input": d,
            "domain": d,
            "exists": exists,
            "exists_source": ("db" if exists_db else ""),
            "classification": cl,
            "parent_name": display_parent,
            "parent_suggested": suggested,
            "match_reason": reason,
            "evidence": evidence_urls,
            "new_parent_candidate": new_parent_candidate,
            "parent_confirmed": suggested,
            "status": "exists" if exists else "new",
        })
    result["existing_parents"] = existing_parents
    return result


def analyze_from_entries(entries: List[str], verify_ownership: bool = True, max_verify: int = 500) -> dict:
    """Analyze list of names/domains. Returns preview rows; no writes."""
    result = {"rows": [], "errors": []}
    entries = [e.strip() for e in (entries or []) if e and str(e).strip()]
    if not entries:
        result["errors"].append("No names or domains entered.")
        return result

    existing_parents = get_existing_parent_media_houses()
    classified = classify_entries_independent_or_media_house(entries, allowed_parents=existing_parents)
    # map by exact input (lower) to keep order
    by_inp = {(c.get("input") or "").strip().lower(): c for c in (classified or []) if isinstance(c, dict)}
    for inp in entries:
        c = by_inp.get(inp.strip().lower()) or {}
        dom = normalize_domain(c.get("domain") or inp)
        existing_company = get_company_by_domain(dom)
        existing_advisor = get_advisor_company_by_domain(dom)
        exists_db = bool(existing_company or existing_advisor)
        exists = bool(exists_db)
        existing_parent = (existing_advisor or {}).get("parent_name") or ""
        existing_parent = (existing_parent or "").strip() if existing_parent else ""

        suggested, reason = ("", "")
        evidence_urls = ""
        new_parent_candidate = ""

        # If domain exists in DB in any form: use only DB classification and parent — never override with AI.
        if exists_db:
            if existing_advisor:
                cl = "media_house"
                display_parent = existing_parent
                suggested = existing_parent
                reason = "existing_in_db"
            else:
                cl = "independent"
                display_parent = ""
                suggested = ""
                reason = "existing_in_db (independent)"
        else:
            cl = (c.get("classification") or "independent").strip().lower().replace(" ", "_")
            if cl not in ("independent", "media_house"):
                cl = "independent"
            parent = (c.get("parent_name") or "").strip()
            parent_match = (c.get("parent_match") or "").strip()
            display_parent = parent

            if cl == "media_house":
                if verify_ownership and max_verify > 0:
                    ver = verify_parent_from_web(dom, existing_parents)
                    if ver.get("parent_match"):
                        suggested = ver["parent_match"]
                        display_parent = suggested
                        reason = f"web_verified({ver.get('match_reason','')},conf={ver.get('confidence', 0):.2f})"
                    elif ver.get("direct_parent") or ver.get("ultimate_parent"):
                        display_parent = ver.get("ultimate_parent") or ver.get("direct_parent") or ""
                        new_parent_candidate = (ver.get("new_parent_candidate") or "").strip()
                    ev = ver.get("evidence") or []
                    evidence_urls = "\n".join([e.get("href", "") for e in ev[:5] if e.get("href")])
                    max_verify -= 1
                if not suggested:
                    suggested, reason = _suggest_parent_from_data(parent, parent_match, existing_parents)
                    if suggested:
                        display_parent = suggested
        result["rows"].append({
            "do_import": not exists,
            "input": inp,
            "domain": dom,
            "exists": exists,
            "exists_source": ("db" if exists_db else ""),
            "classification": cl,
            "parent_name": display_parent,
            "parent_suggested": suggested,
            "match_reason": reason,
            "evidence": evidence_urls,
            "new_parent_candidate": new_parent_candidate,
            "parent_confirmed": suggested,
            "status": "exists" if exists else "new",
        })
    result["existing_parents"] = existing_parents
    return result


def commit_rows(rows: List[dict], allow_new_parents: bool = False) -> dict:
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
            if not parent_confirmed:
                result["flagged_for_review"].append(label)
                continue
            if (parent_confirmed not in existing_parents) and (not allow_new_parents):
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
    invalidate_group("advisor")
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
    invalidate_group("advisor")
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
