import requests, os, sqlite3
from dotenv import load_dotenv
load_dotenv("keys.env")
load_dotenv(".env")

token = os.getenv("NOTION_TOKEN")
db_id = "2fe4dd35-068d-808c-b8f5-d6580051b09e"

# 1. Check Notion directly
print("=== NOTION ===")
r = requests.post(
    f"https://api.notion.com/v1/databases/{db_id}/query",
    headers={
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    },
    json={
        "filter": {
            "property": "Domain",
            "title": {"contains": "newsweek"},
        }
    },
)
if r.ok:
    results = r.json().get("results", [])
    print(f"Notion matches for 'newsweek': {len(results)}")
    for page in results:
        props = page["properties"]
        domain = props.get("Domain", {}).get("title", [])
        domain_text = domain[0]["plain_text"] if domain else "(empty)"
        print(f"  Domain: {domain_text}")
        print(f"  Page ID: {page['id']}")
else:
    print(f"Notion error: {r.status_code} {r.text[:200]}")

# 2. Check how many pages total in Notion
print("\n=== NOTION TOTAL ===")
total = 0
cursor = None
while True:
    body = {"page_size": 100}
    if cursor:
        body["start_cursor"] = cursor
    r2 = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json=body,
    )
    data = r2.json()
    total += len(data.get("results", []))
    if not data.get("has_more"):
        break
    cursor = data.get("next_cursor")
print(f"Total pages in Notion: {total}")

# 3. Check local DB
print("\n=== LOCAL DB ===")
c = sqlite3.connect("lh2_platform.db")
local_total = c.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
print(f"Total companies in local DB: {local_total}")
print(f"Missing from local: {total - local_total} pages")
c.close()