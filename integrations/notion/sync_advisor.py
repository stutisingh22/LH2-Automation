"""
Notion Advisor (DB2) sync. NOTION_DATABASE_ID_2 → advisor_companies in main DB (lh2_platform.db or Supabase).
"""

import logging
import os
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv("keys.env")
load_dotenv(".env")

from config import NOTION_TOKEN
from db_ops import bulk_upsert_advisor_companies
from config import normalize_domain


def _clean_notion_id(raw: str) -> str:
    clean = (raw or "").strip().strip('"').strip("'")
    if not clean:
        return ""
    if "notion.so" in clean or "notion.site" in clean:
        clean = clean.split("?")[0].rstrip("/").split("/")[-1]
    elif "?" in clean:
        clean = clean.split("?")[0]
    clean = clean.replace("-", "")
    if len(clean) == 32 and all(c in "0123456789abcdef" for c in clean.lower()):
        return f"{clean[:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:]}"
    return clean


NOTION_DATABASE_ID_2 = _clean_notion_id(os.getenv("NOTION_DATABASE_ID_2", ""))

log = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _notion_post(path, body=None, retries=3):
    for attempt in range(retries):
        r = requests.post(
            f"{NOTION_API}/{path}",
            headers=_headers(),
            json=body or {},
        )
        if r.status_code == 429:
            import time
            wait = float(r.headers.get("Retry-After", 1))
            log.warning("Rate limited, waiting %.1fs", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def _notion_patch(path, body=None, retries=3):
    for attempt in range(retries):
        r = requests.patch(
            f"{NOTION_API}/{path}",
            headers=_headers(),
            json=body or {},
        )
        if r.status_code == 429:
            import time
            wait = float(r.headers.get("Retry-After", 1))
            log.warning("Rate limited, waiting %.1fs", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def _gp(props, name, ptype="rich_text"):
    try:
        p = props.get(name)
        if not p:
            return "" if ptype != "number" else None
        if ptype == "title":
            return p["title"][0]["plain_text"] if p.get("title") else ""
        elif ptype == "rich_text":
            parts = p.get("rich_text", [])
            return " ".join(x["plain_text"] for x in parts) if parts else ""
        elif ptype == "select":
            sel = p.get("select")
            return sel["name"] if sel else ""
        return ""
    except (KeyError, IndexError, TypeError):
        return "" if ptype != "number" else None


def create_notion_advisor_page(domain, parent_name):
    """Create a single page in NOTION_DATABASE_ID_2 (Media Houses). Returns page id or None."""
    if not NOTION_TOKEN or not NOTION_DATABASE_ID_2:
        log.warning("Notion token or NOTION_DATABASE_ID_2 missing; cannot create advisor page")
        return None
    domain = (domain or "").strip()
    parent_name = (parent_name or "").strip() or "(No parent)"
    if not domain:
        return None
    props = {
        "Domain": {"title": [{"type": "text", "text": {"content": domain[:2000]}}]},
    }
    try:
        props["Media"] = {"select": {"name": parent_name[:100]}}
    except Exception:
        pass
    try:
        res = _notion_post("pages", {
            "parent": {"database_id": NOTION_DATABASE_ID_2},
            "properties": props,
        })
        return res.get("id")
    except Exception as e:
        log.error("Create Notion advisor page failed: %s", e)
        return None


def archive_notion_advisor_page(page_id: str) -> bool:
    """Archive (soft-delete) a Notion page in DB2 by id. Returns True if request succeeded."""
    if not NOTION_TOKEN or not page_id:
        return False
    try:
        _notion_patch(f"pages/{page_id}", {"archived": True})
        return True
    except Exception as e:
        log.warning("Archive Notion advisor page failed for %s: %s", page_id, e)
        return False


def notion_advisor_page_exists_by_domain(domain: str) -> str | None:
    """Live lookup in Notion DB2 by Domain (title). Returns page_id if found, else None."""
    if not NOTION_TOKEN or not NOTION_DATABASE_ID_2:
        return None
    d = normalize_domain(domain)
    if not d:
        return None
    try:
        res = _notion_post(
            f"databases/{NOTION_DATABASE_ID_2}/query",
            body={
                "page_size": 1,
                "filter": {"property": "Domain", "title": {"contains": d}},
            },
        )
        results = res.get("results") or []
        return results[0]["id"] if results else None
    except Exception as e:
        log.warning("Notion DB2 exists-by-domain failed for %s: %s", d, e)
        return None


def _parse_advisor_page(page):
    """DB2: title = domain (child), select "media" = parent company."""
    try:
        p = page["properties"]
        domain = _gp(p, "domain", "title") or _gp(p, "Domain", "title")
        if not domain:
            return None
        parent = _gp(p, "media", "select") or _gp(p, "Media", "select") or ""
        return {
            "page_id": page["id"],
            "domain": domain.strip(),
            "parent_name": parent.strip() or "(No parent)",
        }
    except Exception as e:
        log.warning("Failed to parse advisor page %s: %s", page.get("id", "?"), e)
        return None


def sync_notion_advisor_to_db():
    """Fetch all pages from NOTION_DATABASE_ID_2 and upsert into advisor_companies (main DB)."""
    if not NOTION_TOKEN:
        return {"companies": 0, "error": "Notion token is missing"}
    if not NOTION_DATABASE_ID_2:
        return {"companies": 0, "error": "NOTION_DATABASE_ID_2 is not set"}

    all_results = []
    has_more = True
    cursor = None
    progress = st.progress(0, text="Fetching from Notion...") if st else None

    while has_more:
        if progress:
            progress.progress(0.1, text=f"Fetching from Notion... ({len(all_results)} so far)")
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            res = _notion_post(f"databases/{NOTION_DATABASE_ID_2}/query", body)
        except Exception as e:
            if progress:
                progress.empty()
            return {"companies": 0, "error": str(e)}
        batch = res.get("results", [])
        all_results.extend(batch)
        has_more = res.get("has_more", False)
        cursor = res.get("next_cursor")

    if not all_results:
        if progress:
            progress.empty()
        return {"companies": 0}

    if progress:
        progress.progress(0.3, text="Parsing pages...")
    parsed = []
    n = len(all_results)
    for i, page in enumerate(all_results):
        if progress and i > 0 and i % 50 == 0:
            progress.progress(0.3 + 0.3 * (i / n), text=f"Parsing {i + 1}/{n}...")
        data = _parse_advisor_page(page)
        if data:
            parsed.append({
                "domain": data["domain"],
                "parent_name": data["parent_name"],
                "notion_page_id": data.get("page_id"),
            })
    if progress:
        progress.progress(0.7, text=f"Saving {len(parsed)} companies...")
    try:
        cc = bulk_upsert_advisor_companies(parsed)
    except Exception as e:
        log.error("Bulk upsert advisor companies failed: %s", e)
        if progress:
            progress.empty()
        return {"companies": 0, "error": str(e)}
    if progress:
        progress.empty()
    return {"companies": cc}
