import os
import sys
import time
import re
from pathlib import Path

# Project root for imports and keys.env
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from notion_client import Client
from anthropic import Anthropic
from dotenv import load_dotenv
load_dotenv(_ROOT / "keys.env")
load_dotenv(_ROOT / ".env")

notion = Client(auth=os.getenv("NOTION_TOKEN"))
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

def extract_tag(text, tag):
    """Regex to pull content from between custom tags."""
    pattern = f"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""

def safe_number(value):
    if not value: return 0
    try:
        clean = re.sub(r'[^\d.]', '', str(value))
        return float(clean) if clean else 0
    except: return 0

def safe_text(value):
    return {"rich_text": [{"text": {"content": str(value)[:2000]}}]} if value else {"rich_text": []}

def safe_select(value, allowed_options=None):
    """
    Strips commas and enforces a strict whitelist if provided.
    """
    if not value or value.lower() == "n/a": return None
    clean_val = str(value).split(',')[0].strip()
    
    if allowed_options and clean_val not in allowed_options:
        return None
        
    return {"select": {"name": clean_val}}

def get_institutional_data(domain):
    """Grounded Editorial Audit with swapped Type of Content logic."""
    prompt = f"""
    Target: {domain}
    
    CRITICAL INSTRUCTIONS:
    1. Articles/Day (VERIFICATION STEP): 
       - Scan the recent output of {domain}.
       - Check timestamps of the last 10 articles.
       - Calculate the daily average output.
       - DO NOT HALLUCINATE. If no frequency is clear, use 0.

    2. Type of Content: You MUST choose exactly one: 'Everyday' or 'Evergreen'.
       - 'Everyday': High-frequency news, politics, sports, daily updates.
       - 'Evergreen': Reference material, tutorials, reviews, static high-value content.

    3. Type of Publisher: Provide a short description of their niche/entity type (e.g., Digital Magazine, Tech Blog, Regional News). No commas.

    4. Monthly Visits: Provide a strict, verified estimate for 2026 traffic patterns.
    
    Return ONLY these tags:
    <articles_day></articles_day>
    <city></city>
    <country></country>
    <visits></visits>
    <editor_name></editor_name>
    <linkedin></linkedin>
    <space></space>
    <state></state>
    <publisher_type></publisher_type>
    <content_type></content_type>
    <email></email>
    """
    message = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    res = message.content[0].text
    
    return {
        "articles": extract_tag(res, "articles_day"),
        "city": extract_tag(res, "city"),
        "country": extract_tag(res, "country"),
        "visits": extract_tag(res, "visits"),
        "editor": extract_tag(res, "editor_name"),
        "linkedin": extract_tag(res, "linkedin"),
        "space": extract_tag(res, "space"),
        "state": extract_tag(res, "state"),
        "pub_type": extract_tag(res, "publisher_type"),
        "content_type": extract_tag(res, "content_type"),
        "email": extract_tag(res, "email")
    }

def enrich_notion_row():
    try:
        rows = notion.databases.query(
            database_id=DATABASE_ID,
            filter={"property": "To reach out", "select": {"equals": "New"}}
        ).get("results")

        for row in rows:
            page_id = row["id"]
            domain = row["properties"]["Domain"]["title"][0]["text"]["content"]
            print(f"⚡ High-Precision Enrichment: {domain}...")

            data = get_institutional_data(domain)

            payload = {
                "To reach out": {"select": {"name": "To Reach Out"}},
                "Research": {"select": {"name": "Completed"}},
                "Articles/Day": {"number": safe_number(data["articles"])},
                "Monthly visits (As of Oct'25)": safe_text(data["visits"]),
                "SPOC 2 (Editor)": safe_text(data["editor"]),
                
                "City": safe_select(data["city"]),
                "Country": safe_select(data["country"]),
                "Space": safe_select(data["space"]),
                "State": safe_select(data["state"]),
                
                # UPDATED LOGIC: Publisher Type is descriptive, Content Type is Everyday/Evergreen
                "Type of Publisher": safe_select(data["pub_type"]),
                "Type of content": safe_select(data["content_type"], allowed_options=["Everyday", "Evergreen"]),
                
                "Owner/Lead Editor LinkedIn": {"url": data["linkedin"] if "http" in str(data["linkedin"]) else None},
                "Email": {"email": data["email"] if "@" in str(data["email"]) else None}
            }

            clean_payload = {k: v for k, v in payload.items() if v is not None}
            notion.pages.update(page_id=page_id, properties=clean_payload)
            print(f"✅ Success: {domain} updated.")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("🚀 Factual Enrichment Engine Active (Targeted Mode)...")
    while True:
        enrich_notion_row()
        time.sleep(15)