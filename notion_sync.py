"""
notion_sync.py — Notion integration using direct HTTP requests

FIX LOG:
  - Uses requests directly (notion-client library broken)
  - Property name: "Email" (not "SPOC Email")
  - Per-page error handling: one bad page doesn't kill entire sync
  - Notes fetched separately (skipped during bulk sync to avoid rate limits)
  - Progress bar during sync
"""

import logging
import time
import requests
import streamlit as st

from config import NOTION_TOKEN, NOTION_DATABASE_ID
from db_ops import upsert_company, upsert_contact

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
    """POST to Notion API with retry on rate limit."""
    for attempt in range(retries):
        r = requests.post(
            f"{NOTION_API}/{path}",
            headers=_headers(),
            json=body or {},
        )
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 1))
            log.warning("Rate limited, waiting %.1fs", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def _notion_get(path, retries=3):
    """GET from Notion API with retry on rate limit."""
    for attempt in range(retries):
        r = requests.get(
            f"{NOTION_API}/{path}",
            headers=_headers(),
        )
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 1))
            log.warning("Rate limited, waiting %.1fs", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


# ── Property Extractors ──────────────────────────────────────

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
        elif ptype == "url":
            return p.get("url") or ""
        elif ptype == "select":
            sel = p.get("select")
            return sel["name"] if sel else ""
        elif ptype == "number":
            return p.get("number")
    except (KeyError, IndexError, TypeError) as e:
        log.debug("Property extraction error for %s (%s): %s", name, ptype, e)
        return "" if ptype != "number" else None


def fetch_notes_for_page(page_id):
    """Fetch child_page blocks as research notes. Called on-demand, not during bulk sync."""
    notes = []
    try:
        children = _notion_get(f"blocks/{page_id}/children")
        for block in children.get("results", []):
            if block["type"] == "child_page":
                title = block["child_page"]["title"]
                nblocks = _notion_get(f"blocks/{block['id']}/children")
                texts = []
                for nb in nblocks.get("results", []):
                    btype = nb["type"]
                    if btype in ("paragraph", "bulleted_list_item", "numbered_list_item"):
                        parts = nb[btype].get("rich_text", [])
                        t = " ".join(p["plain_text"] for p in parts)
                        if t.strip():
                            texts.append(t)
                if texts:
                    notes.append({"title": title, "content": "\n".join(texts)})
    except Exception as e:
        log.warning("Failed to fetch notes for page %s: %s", page_id, e)
    return notes


# ── Fetch All Pages from Notion ──────────────────────────────

def _fetch_all_pages():
    """Paginate through entire Notion database. Returns raw page objects."""
    if not NOTION_TOKEN:
        st.error("Notion token is missing")
        return []
    if not NOTION_DATABASE_ID:
        st.error("Notion database ID is missing")
        return []

    all_results = []
    has_more = True
    cursor = None
    page_num = 0

    while has_more:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            res = _notion_post(f"databases/{NOTION_DATABASE_ID}/query", body)
        except Exception as e:
            st.error(f"Notion query failed on page {page_num + 1}: {e}")
            break
        batch = res.get("results", [])
        all_results.extend(batch)
        has_more = res.get("has_more", False)
        cursor = res.get("next_cursor")
        page_num += 1

    return all_results


def _parse_page(page):
    """Extract company data from a single Notion page. Returns dict or None."""
    try:
        p = page["properties"]
        domain = _gp(p, "Domain", "title")
        if not domain:
            return None
        return {
            "page_id": page["id"],
            "domain": domain.strip(),
            "name": _gp(p, "SPOC 2 (Editor)") or domain.strip(),
            "email": _gp(p, "Email"),
            "linkedin": _gp(p, "Owner/Lead Editor LinkedIn", "url"),
            "city": _gp(p, "City"),
            "state": _gp(p, "State"),
            "country": _gp(p, "Country"),
            "content_type": _gp(p, "Type of content", "select"),
            "publisher_type": _gp(p, "Type of Publisher", "select"),
            "monthly_visits": _gp(p, "Monthly visits (As of Oct'25)", "number"),
            "research_status": _gp(p, "Research", "select"),
            "gmail_thread": _gp(p, "Gmail Draft Link", "url"),
        }
    except Exception as e:
        log.warning("Failed to parse page %s: %s", page.get("id", "?"), e)
        return None


# ── Sync to Database ─────────────────────────────────────────

def sync_notion_to_db():
    """
    Full sync: fetch all Notion pages → upsert into local DB.
    Notes are NOT fetched during bulk sync to avoid rate limits.
    Notes are fetched on-demand when viewing a company.
    """
    pages = _fetch_all_pages()
    if not pages:
        return {"companies": 0, "contacts": 0, "skipped": 0}

    cc, ctc, skipped, errors = 0, 0, 0, 0
    progress = st.progress(0, text="Syncing...")

    for i, page in enumerate(pages):
        # Update progress
        progress.progress((i + 1) / len(pages), text=f"Syncing {i + 1}/{len(pages)}...")

        # Parse page
        data = _parse_page(page)
        if not data:
            skipped += 1
            continue

        # Upsert company — wrapped in try/except so one bad row doesn't kill the rest
        try:
            cid = upsert_company(
                domain=data["domain"],
                name=data.get("name") or data["domain"],
                publisher_type=data.get("publisher_type"),
                content_type=data.get("content_type"),
                monthly_visits=data.get("monthly_visits"),
                notion_page_id=data.get("page_id"),
                research_status=data.get("research_status"),
                gmail_thread=data.get("gmail_thread"),
                extra_data={},  # Notes fetched on-demand, not here
            )
            cc += 1

            # Upsert contact if name exists
            cn = data.get("name", "").strip()
            if cn and cn != data["domain"]:
                upsert_contact(
                    company_id=cid, name=cn, role="SPOC / Editor",
                    email=data.get("email"), linkedin=data.get("linkedin"),
                    city=data.get("city"), state=data.get("state"),
                    country=data.get("country"), notion_page_id=data.get("page_id"),
                )
                ctc += 1
        except Exception as e:
            errors += 1
            log.error("Failed to upsert %s: %s", data.get("domain", "?"), e)
            continue

    progress.empty()

    result = {"companies": cc, "contacts": ctc, "skipped": skipped}
    if errors:
        result["errors"] = errors
    return result


# ── Create a new contact/SPOC page in Notion ───────────────────

def create_notion_contact_page(domain, name, email=None, linkedin=None, role=None, city=None, state=None, country=None):
    """
    Create a new page in the Notion database for this SPOC (same company domain).
    Returns the new page id, or None on failure.
    """
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        log.warning("Notion token or database ID missing; cannot create page")
        return None


def archive_notion_page(page_id: str) -> bool:
    """Archive (soft-delete) a Notion page by id. Returns True if request succeeded."""
    if not NOTION_TOKEN or not page_id:
        return False
    try:
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=_headers(),
            json={"archived": True},
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("Archive Notion page failed for %s: %s", page_id, e)
        return False
    if not (domain and name):
        log.warning("Domain and name required to create Notion contact page")
        return None
    def _rt(content):
        return [{"type": "text", "text": {"content": (content or "")[:2000]}}]
    properties = {
        "Domain": {"title": _rt(domain.strip())},
        "SPOC 2 (Editor)": {"rich_text": _rt(name.strip())},
        "Email": {"rich_text": _rt((email or "").strip())},
        "City": {"rich_text": _rt((city or "").strip())},
        "State": {"rich_text": _rt((state or "").strip())},
        "Country": {"rich_text": _rt((country or "").strip())},
    }
    if linkedin and str(linkedin).strip().startswith("http"):
        properties["Owner/Lead Editor LinkedIn"] = {"url": str(linkedin).strip()}
    try:
        res = _notion_post("pages", {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties})
        return res.get("id")
    except Exception as e:
        log.error("Create Notion contact page failed: %s", e)
        if st:
            st.error(f"Could not create Notion page: {e}")
        return None


# ── Lookup Single Lead by LinkedIn URL ────────────────────────

def lookup_lead_in_notion(linkedin_url):
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        st.error("Notion token or database ID is missing")
        return None

    clean_url = linkedin_url.strip().rstrip("/")
    st.toast(f"Searching Notion for: {clean_url[:40]}...")

    try:
        res = _notion_post(
            f"databases/{NOTION_DATABASE_ID}/query",
            body={
                "filter": {
                    "property": "Owner/Lead Editor LinkedIn",
                    "url": {"contains": clean_url},
                },
            },
        )

        results = res.get("results", [])
        st.toast(f"Notion returned {len(results)} match(es)")

        if not results:
            return None

        page = results[0]
        data = _parse_page(page)
        if not data:
            return None

        # For single lookups, fetch notes too (only 1 page, no rate limit issue)
        data["research_notes"] = fetch_notes_for_page(page["id"])
        return data

    except Exception as e:
        st.error(f"Notion lookup failed: {e}")
        return None