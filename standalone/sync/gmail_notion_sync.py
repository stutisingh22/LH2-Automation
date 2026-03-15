import os
import re
import sys
import time
import pickle
import logging
import requests
from datetime import datetime
from pathlib import Path

# Project root for imports and keys.env
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from notion_client import Client
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from dotenv import load_dotenv
load_dotenv(_ROOT / "keys.env")
load_dotenv(_ROOT / ".env")

# --- LOGGING ---
log = logging.getLogger("LH2-GmailSync")
log.setLevel(logging.INFO)
if not log.handlers:
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(console)

# --- CONFIG ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
# Standard Notion Client for updates (updates use a different endpoint that usually works)
notion = Client(auth=NOTION_TOKEN)

GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TOKEN_FILE = 'token_sync.pickle'
POLL_INTERVAL = 1800 
SCAN_WINDOW = "newer_than:12h"
IGNORE_DOMAINS = {"lh2holdings.com", "gmail.com", "outlook.com", "hotmail.com"}

def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'wb') as f:
            pickle.dump(creds, f)
    return build('gmail', 'v1', credentials=creds)

def load_notion_lookup():
    """Builds local map using direct HTTP requests to bypass library bugs."""
    email_map = {}
    domain_map = {}
    has_more = True
    start_cursor = None
    
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    while has_more:
        # Constructing the exact URL Notion expects
        url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        try:
            res = requests.post(url, headers=headers, json=payload)
            res.raise_for_status()
            response = res.json()
        except Exception as e:
            log.error(f"  Direct API Request failed: {e}")
            break

        results = response.get("results", [])
        has_more = response.get("has_more", False)
        start_cursor = response.get("next_cursor")

        for page in results:
            page_id = page["id"]
            props = page["properties"]
            current_link = props.get("Gmail Draft Link", {}).get("url")

            # Domain Extraction
            title_data = props.get("Domain", {}).get("title", [])
            if title_data:
                d_val = title_data[0]["text"]["content"].strip().lower()
                domain_map[d_val] = (page_id, current_link)

            # Email Extraction
            e_val = props.get("Email", {}).get("email")
            if e_val:
                email_map[e_val.strip().lower()] = (page_id, current_link)

    log.info(f"  Notion Indexed: {len(domain_map)} domains, {len(email_map)} emails.")
    return email_map, domain_map

def scan_and_sync():
    service = get_gmail_service()
    email_map, domain_map = load_notion_lookup()

    if not email_map and not domain_map:
        log.warning("  Lookup failed. Check your NOTION_TOKEN or DATABASE_ID.")
        return

    response = service.users().threads().list(userId='me', q=SCAN_WINDOW, maxResults=50).execute()
    threads = response.get('threads', [])

    if not threads:
        log.info("  No Gmail activity.")
        return

    log.info(f"  Auditing {len(threads)} threads...")
    links_synced = 0

    for thread in threads:
        t_id = thread['id']
        thread_link = f"https://mail.google.com/mail/u/0/#inbox/{t_id}"

        try:
            t_data = service.users().threads().get(userId='me', id=t_id, format='metadata', metadataHeaders=['From', 'To', 'Cc']).execute()
        except: continue

        found_emails = set()
        for msg in t_data.get('messages', []):
            headers = msg.get('payload', {}).get('headers', [])
            for h in headers:
                if h['name'] in ('From', 'To', 'Cc'):
                    emails = re.findall(r"[\w.-]+@[\w.-]+", h['value'])
                    found_emails.update(e.lower() for e in emails)

        for email in found_emails:
            domain = email.split("@")[-1]
            if domain in IGNORE_DOMAINS: continue

            target_data = email_map.get(email) or domain_map.get(domain)
            
            if target_data:
                page_id, existing_link = target_data
                if existing_link != thread_link:
                    try:
                        notion.pages.update(page_id=page_id, properties={"Gmail Draft Link": {"url": thread_link}})
                        links_synced += 1
                        log.info(f"  ✅ Linked: {email if email in email_map else domain}")
                    except Exception as e:
                        log.error(f"  Notion update error: {e}")
                break 

    log.info(f"  Cycle finished. {links_synced} updates.")

if __name__ == "__main__":
    log.info("🚀 Starting LH2 Sync Bot...")
    while True:
        try:
            scan_and_sync()
        except Exception as e:
            log.error(f"Cycle Error: {e}")
        time.sleep(POLL_INTERVAL)