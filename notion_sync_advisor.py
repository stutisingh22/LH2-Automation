"""
notion_sync_advisor.py — Sync NOTION_DATABASE_ID_2 (Advisor DB) to lh2_advisor.db.
DB2 schema: title property = domain (child company), select property "media" = parent company.
Does not touch notion_sync.py or the primary Notion DB.
"""

import logging
import os
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv("keys.env")
load_dotenv(".env")

from config import NOTION_TOKEN
from db_advisor import upsert_advisor_company


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


def _parse_advisor_page(page):
    """
    DB2: title = domain (child), select "media" = parent company.
    """
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
    """
    Fetch all pages from NOTION_DATABASE_ID_2 and upsert into lh2_advisor.db.
    Returns {"companies": count, "error": msg} on failure.
    """
    if not NOTION_TOKEN:
        return {"companies": 0, "error": "Notion token is missing"}
    if not NOTION_DATABASE_ID_2:
        return {"companies": 0, "error": "NOTION_DATABASE_ID_2 is not set"}

    all_results = []
    has_more = True
    cursor = None

    while has_more:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            res = _notion_post(f"databases/{NOTION_DATABASE_ID_2}/query", body)
        except Exception as e:
            return {"companies": 0, "error": str(e)}
        batch = res.get("results", [])
        all_results.extend(batch)
        has_more = res.get("has_more", False)
        cursor = res.get("next_cursor")

    if not all_results:
        return {"companies": 0}

    cc = 0
    progress = st.progress(0, text="Syncing Advisor DB...") if st else None
    for i, page in enumerate(all_results):
        if progress:
            progress.progress((i + 1) / len(all_results), text=f"Syncing {i + 1}/{len(all_results)}...")
        data = _parse_advisor_page(page)
        if not data:
            continue
        try:
            upsert_advisor_company(
                domain=data["domain"],
                parent_name=data["parent_name"],
                notion_page_id=data.get("page_id"),
            )
            cc += 1
        except Exception as e:
            log.error("Failed to upsert advisor company %s: %s", data.get("domain"), e)
    if progress:
        progress.empty()
    return {"companies": cc}
