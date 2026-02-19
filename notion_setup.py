import os
from dotenv import load_dotenv
from notion_client import Client

load_dotenv("keys.env")
client = Client(auth=os.getenv("NOTION_TOKEN"))
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

def fetch_all_leads():
    all_results = []
    has_more = True
    next_cursor = None

    print("Fetching all items from Notion...")

    while has_more:
        # We pass the 'start_cursor' to tell Notion where we left off
        response = client.databases.query(
            database_id=DATABASE_ID,
            start_cursor=next_cursor
        )
        
        all_results.extend(response.get("results", []))
        
        # Check if there's another page of 100 items
        has_more = response.get("has_more")
        next_cursor = response.get("next_cursor")
        
        print(f"Retrieved {len(all_results)} items so far...")

    print(f"✅ Total items found in database: {len(all_results)}")
    return all_results

if __name__ == "__main__":
    fetch_all_leads()