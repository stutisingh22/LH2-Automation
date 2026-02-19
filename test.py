import os
import io
import time
import re
from datetime import datetime, timedelta
from pypdf import PdfReader
from notion_client import Client
from anthropic import Anthropic
from dotenv import load_dotenv

# Google Drive Imports
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Load keys
load_dotenv("keys.env")

# --- CONFIGURATION ---
DRIVE_FOLDER_ID = "1qntM9z5wasM2C5hRkYaKEaIJrm2DJTCH"
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Initialize Clients
notion = Client(auth=os.getenv("NOTION_TOKEN"))
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

cached_context = ""
last_sync_time = None


def get_drive_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


def sync_drive_memory():
    try:
        service = get_drive_service()
        query = f"'{DRIVE_FOLDER_ID}' in parents and mimeType='application/pdf'"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        items = results.get('files', [])

        full_text = ""
        for item in items:
            print(f"🔄 Syncing memory from: {item['name']}...")
            request = service.files().get_media(fileId=item['id'])
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

            reader = PdfReader(fh)
            for page in reader.pages:
                full_text += page.extract_text() + "\n"

        return full_text
    except Exception as e:
        print(f"❌ Drive Sync Error: {e}")
        return cached_context


def ensure_memory_freshness():
    global cached_context, last_sync_time
    now = datetime.now()
    if last_sync_time is None or (now - last_sync_time) > timedelta(hours=12):
        print("💡 Refreshing LH2 Doctrine memory...")
        cached_context = sync_drive_memory()
        last_sync_time = now
        print(f"🧠 Memory updated at {last_sync_time.strftime('%H:%M:%S')}")


def deep_research_agent(domain, context):

    prompt = f"""
{context}

You are producing institutional-grade account research for LH2 Corp Dev & Partnerships.

Your output must match the analytical rigor, decisiveness, structural clarity,
and economic thinking of LH2’s gold-standard ClassAction research memo.

This is not descriptive research.
This is internal deal-team intelligence.

You are writing for:
- Founder
- Corp Dev
- Partnerships
- Strategic leadership

Your objectives:
1. Identify the structural truth of the business.
2. Identify the single most important strategic signal.
3. Map economic power precisely.
4. Determine whether Accelerate truly fits.
5. Construct a sharp, operator-level wedge.
6. Define a clear, sequenced outreach motion.

No fluff.
No filler.
No emojis.
No markdown bolding.
No duplicated headers.
No vague statements.

Target Domain: {domain}

============================================================
0. ELIGIBILITY CHECK (MANDATORY GATE)
============================================================

Provide a structured table:

Criterion | Assessment | Pass/Fail

Required Criteria:
- Content behind paywall?
- Factual, attribution-led, POV-light?
- Category alignment with LH2 supported universe?
- Can a 5–15 person pod plausibly move revenue?

End with:
Eligibility Verdict: Proceed / Stop

If Stop → end analysis immediately.

============================================================
1. COMPANY & OPERATING CONTEXT
============================================================

Ownership & Structure
- Legal operating entity
- Parent company (if applicable)
- PE / institutional backing
- Headcount estimate
- Degree of autonomy (independent vs centralized)
- Who legally controls monetization

Scale Indicators
- Monthly visits (estimate)
- Revenue range (directional)
- Seasonality
- Revenue concentration risks

Business Model Breakdown Table:
Revenue Stream | Estimated Weight | Mechanics

Operational Pressure Points (4–6 bullets)

Then include:

CRITICAL STRATEGIC SIGNAL:
Identify ONE finding that materially changes how LH2 should think about this account.
Examples:
- PE-backed ownership
- Recent executive hire from digital roll-up
- Bankruptcy / recapitalization
- Centralized monetization
- Heavy affiliate dependency
- Search algorithm vulnerability
- Single-writer concentration risk

Explain clearly:
- Why this signal matters
- How it changes LH2’s motion

============================================================
2. ARTICLE FREQUENCY & EXECUTION CAPACITY
============================================================

Methodology & Time Window:
Explain how cadence was estimated.

Metrics:
- Articles per weekday
- Articles per month
- Active writers
- Output per writer

Content Breakdown Table:
Section | Volume Share | Templated? | Pod-Friendly?

Execution Gap Analysis:
- Where are they under-covering?
- What content surface area is being left on the table?
- Where is throughput constrained?
- What is the competitor volume gap?
- Where could a 5–15 person pod realistically 2–3x output?
- What is the single biggest bottleneck?

============================================================
3. POWER MAPPING & STAKEHOLDER PRIORITIZATION
============================================================

Organizational Reality:
- Who legally owns the business?
- Who owns P&L?
- Is monetization centralized?
- Does editorial control economics?
- Where does actual economic authority sit?

Identify ONE POWER CENTER.
Explain why this role is decisive.

Provide a ranked stakeholder table with MINIMUM 4 and MAXIMUM 5 named individuals.

Table format:

Priority | Name | Exact Title | Economic Authority (High/Medium/Low) | Why They Matter | What They Likely Care About

Rules:
- Do NOT fabricate titles.
- If inferred, label clearly.
- Include:
  • At least one economic decision-maker
  • At least one operational execution owner
  • At least one strategic/investor-level stakeholder (if applicable)
- Editors only if equity holders or economic influencers.

Then include:

CONTACT ORDER LOGIC:
- Why this ranking order is correct.
- How the Critical Strategic Signal affects sequencing.
- Whether outreach should start top-down, bottom-up, or parallel.

WHO TO AVOID:
List 2–3 specific roles or individuals and explain why outreach would stall or misfire.

This section must read like internal deal-team strategy, not outreach copy.

============================================================
4. INDIVIDUAL CONTEXT & STRATEGIC INTERPRETATION
============================================================

For the TOP TWO stakeholders only:

For each:
- Background summary
- Career trajectory signals
- Strategic mindset interpretation
- How they will perceive LH2
- Personalization angle

This is operator psychology, not biography.

============================================================
5. FIT ASSESSMENT
============================================================

Rating:
Strong / Medium-Strong / Medium / Weak

Why Not Strong:
Explicit structural friction or risk.

Why It Is Attractive:
Structural upside.

Single Most Acute Pain:
One sentence.

Be intellectually honest.
Do not force fit.

============================================================
6. OUTREACH SEQUENCING STRATEGY
============================================================

Define:

Primary Entry Point:
- Who
- Why
- Angle
- Who should send

Secondary Parallel Path:
- Who
- Why
- Risk profile

Escalation Path:
- If primary stalls, who next?

Influence Path:
- Who can internally champion?

Explain clearly how sequencing aligns with the identified Power Center.

============================================================
7. ACCELERATE POSITIONING & LANGUAGE GUIDE
============================================================

Correct Accelerate Framing:
(Execution gap, risk transfer, incremental revenue logic)

Language To Explicitly Avoid:
(Anything that triggers editorial defensiveness or PE skepticism)

Most Compelling Entry Category:
(Exact content vertical to anchor wedge)

============================================================
FINAL SECTION:
SUMMARY — THE EXACT WEDGE
============================================================

Write a 5–8 sentence wedge paragraph
with operator empathy and economic clarity.

Then list 6–8 bullet points explaining
why this aligns with LH2 targeting doctrine.

Tone:
Institutional.
Decisive.
Investment memo level.
No fluff.
No emojis.
No markdown.
"""

    message = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=3500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


def add_clean_block(block_id, text):
    lines = text.split("\n")
    children = []

    for line in lines:
        if not line.strip():
            continue

        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": line.strip()}
                }]
            }
        })

    for i in range(0, len(children), 100):
        notion.blocks.children.append(block_id=block_id, children=children[i:i+100])


def write_to_notion(page_id, research_text):
    """
    Splits using exact divider lines and creates one Notion page per major section.
    """

    text = research_text.replace("\r\n", "\n")

    sections = re.split(r"\n={10,}\n", text)

    for section in sections:
        section = section.strip()
        if not section or len(section) < 50:
            continue

        lines = section.split("\n")
        title = lines[0].strip()

        note = notion.pages.create(
            parent={"page_id": page_id},
            properties={
                "title": [{
                    "text": {"content": title[:100]}
                }]
            }
        )

        add_clean_block(note["id"], section)

    notion.pages.update(
        page_id=page_id,
        properties={"Research": {"select": {"name": "Completed"}}}
    )


print("🚀 LH2 Research Bot active (True Divider-Based Split Mode)...")

while True:
    try:
        ensure_memory_freshness()

        tasks = notion.databases.query(
            database_id=DATABASE_ID,
            filter={"property": "Research", "select": {"equals": "Research Now"}}
        ).get("results")

        for task in tasks:
            page_id = task["id"]
            domain = task["properties"]["Domain"]["title"][0]["text"]["content"]

            print(f"🔍 Researching: {domain}...")
            research_data = deep_research_agent(domain, cached_context)
            write_to_notion(page_id, research_data)
            print(f"✅ Finished {domain}")

    except Exception as e:
        print(f"⚙️ Status: {e}")

    time.sleep(30)
