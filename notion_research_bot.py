import os
import io
import time
import re
import logging
import threading
import requests as http_requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# --- LOGGING (thread-safe) ---
log = logging.getLogger("LH2-Research")
log.setLevel(logging.INFO)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
log.addHandler(console)

file_handler = logging.FileHandler("research_bot.log", encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
log.addHandler(file_handler)

# --- CONFIGURATION ---
DRIVE_FOLDER_ID = "1qntM9z5wasM2C5hRkYaKEaIJrm2DJTCH"
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
MAX_PARALLEL = 5

# Initialize Clients
notion = Client(auth=NOTION_TOKEN)
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

cached_context = ""
last_sync_time = None


# ============================================================
# NOTION DATABASE QUERY (version-safe)
# ============================================================

def query_database(database_id, filter_obj):
    """Query a Notion database using the REST API directly.
    This bypasses the SDK to avoid version-specific attribute errors."""
    resp = http_requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"filter": filter_obj},
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


# ============================================================
# GOOGLE DRIVE MEMORY SYNC
# ============================================================

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
            request = service.files().get_media(fileId=item['id'])
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

            reader = PdfReader(fh)
            doc_text = ""
            for page in reader.pages:
                doc_text += page.extract_text() + "\n"

            full_text += f"\n\n--- {item['name']} ---\n{doc_text}"

        log.info(f"Drive sync complete — {len(items)} PDFs loaded, {len(full_text):,} chars")
        return full_text
    except Exception as e:
        log.error(f"Drive Sync Error: {e}")
        return cached_context


def ensure_memory_freshness():
    global cached_context, last_sync_time
    now = datetime.now()

    if last_sync_time is None or (now - last_sync_time) > timedelta(hours=12):
        cached_context = sync_drive_memory()
        last_sync_time = now


# ============================================================
# NOTION STATUS HELPERS
# ============================================================

def set_status(page_id, status_name):
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"Research": {"select": {"name": status_name}}}
        )
        log.info(f"  Status -> '{status_name}'")
        return True
    except Exception as e:
        log.error(f"  Failed to set status '{status_name}': {e}")
        return False


def claim_task(page_id):
    try:
        page = notion.pages.retrieve(page_id=page_id)
        current = page["properties"]["Research"]["select"]
        if not current or current["name"] != "Research Now":
            log.warning(f"  Status is '{current['name'] if current else 'None'}', not 'Research Now' — skip")
            return False
    except Exception as e:
        log.error(f"  Could not verify status: {e}")
        return False

    return set_status(page_id, "In Progress")


# ============================================================
# RESEARCH PROMPT BUILDER
# ============================================================

def build_prompt(domain, context):
    return f"""{context}

You are producing institutional-grade strategic account research for LH2 Corp Dev & Partnerships. Produce the full research from scratch — do not extract or use any existing research content from Notion or elsewhere. This research is produced by you (Claude) only, using only the target domain and the guidelines below. No external search APIs. Completely ignore any contact details or SPOC data stored in Notion. Use only your knowledge of publicly available information about the company and its stakeholders.
This document must read like an internal Investment Committee memorandum — the kind a PE associate would write before a first meeting with a target.
It must demonstrate analytical rigor, structural clarity, economic reasoning, and decisive judgment.
This is not descriptive research.
This is structural diagnosis, economic mapping, and execution strategy.
Audience:
Founder
Corporate Development
Partnerships
Strategic Leadership
Core Mandate:

Diagnose the structural truth of the business — ownership, economics, constraints, and trajectory.
Identify the single most decisive strategic signal (a hiring decision, a capital event, a competitive shift — something that materially changes LH2's motion).
Map economic authority precisely. Distinguish who controls the P&L from who controls editorial. They are rarely the same person.
Determine real Accelerate fit — not theoretical fit. Be honest about friction.
Construct a sharp, operator-level wedge grounded in the target's specific execution gap.
Define exact outreach sequencing based on where power actually sits.

Research Depth Requirements:

Trace career arcs of key stakeholders. Prior employers, fund affiliations, deal backgrounds, and LinkedIn signals matter. A CFO hired from a PE-backed digital media roll-up is a fundamentally different counterpart than a CFO promoted from accounting. Say so.
Identify hiring patterns that signal strategic intent. Recent C-suite additions, board changes, or advisor appointments often reveal whether a company is professionalizing for growth, positioning for sale, or simply backfilling.
Connect dots across data points. Do not merely list facts. Synthesize them into a structural read. If someone's LinkedIn headline says "Operations / Investments / M&A" and they previously worked at a fund that acquired 24 digital media brands, that is a strategic signal — explain what it means for LH2's approach.
Cross-reference competitive scale. Use traffic estimates, content volume, and staffing to quantify execution gaps against primary competitors. Translate those gaps into revenue implications.

Analytical Standards:

Quantify wherever possible. Traffic ranges, headcount, article output, revenue directional ranges.
Translate execution gaps into revenue impact. "They publish 150 articles/month; their competitor publishes 400" is descriptive. "The 250-article gap represents approximately X thousand uncaptured organic visits per month at their observed pages-per-visit ratio" is analytical.
Distinguish control from influence. The person who signs checks is not always the person who decides strategy.
Separate editorial authority from economic authority. Editorial leaders often resist partnership conversations; economic leaders evaluate them.
Identify constraints, not surface symptoms. "Small team" is a symptom. "6 writers covering an entire legal news cycle that generates hundreds of new filings per month, with zero weekend coverage and no video strategy" is a constraint.
Tie every recommendation to leverage and probability of close.

Formatting Rules (MANDATORY):

Use section dividers exactly as written below (the === and --- lines).
Use proper paragraph spacing between all sections and subsections.
TABLES — Every table in the document must be a proper Markdown table: (1) header row with pipe-separated column names, (2) immediately on the next line a separator row with dashes (e.g. |-----------|------------|-----------|), (3) then one data row per item. This applies to ALL tables: Eligibility (0), Business Model (1C), Output Metrics (2B), Content Mix (2C), Stakeholder Table (3C), and any other. Never output a table without the separator row.
No markdown bold (no ** or __).
No emojis.
No decorative commentary.
No filler language ("It is worth noting that..." / "Interestingly..." / "Notably...").
Every section must contain substance. If a section would be speculative filler, say so in one sentence and move on.
Keep bullet points tight. Each bullet should deliver one discrete insight. No run-on bullets.

Target Domain: {domain}

============================================================
0. ELIGIBILITY CHECK (MANDATORY GATE)
============================================================

Provide a proper Markdown table — header row, then separator row (|---|---|), then exactly one data row per criterion:

| Criterion | Assessment | Pass/Fail |
|-----------|------------|-----------|
| Content behind a paywall or subscription meter? | [Your assessment] | Pass/Fail |
| Content style: factual, attribution-led, POV-light? | [Your assessment] | Pass/Fail |
| Category alignment with LH2's supported universe? | [Your assessment] | Pass/Fail |
| Can a 5–15 person pod plausibly move revenue? | [Your assessment] | Pass/Fail |

Evaluate these four criteria:

Paywall exposure: Is content free and open, or gated behind subscription/registration?
Content structure: Is the content factual, attribution-led, and POV-light? Or is it opinion-driven, editorial, personality-dependent?
Category alignment with LH2 doctrine: Does the domain's primary content category fall within LH2's supported universe? Be specific about which category.
Pod revenue plausibility: Can a 5-15 person deployment plausibly move revenue? Consider the revenue model (programmatic vs. lead-gen vs. affiliate vs. subscription) and whether more content = more revenue in a roughly linear way.

After the table:
Eligibility Verdict: Proceed / Proceed with Caveats / Stop
Provide one short paragraph (3-5 sentences) explaining structural caveats. If the revenue model is not purely programmatic, explain how this changes the Accelerate framing. If the content carries elevated accuracy requirements (legal, medical, financial), note the operational implications.
If Verdict = Stop, note the disqualifying factor but COMPLETE THE FULL ANALYSIS REGARDLESS. LH2 needs the research for internal records even if the target is not a fit.

============================================================
1. COMPANY & OPERATING CONTEXT
============================================================

1A. Ownership & Structure
Provide concise bullets covering:

Legal operating entity (name, year founded, state of incorporation if findable)
Parent company or holding structure (if any)
Capital structure inference: public, PE-backed, founder-owned, bootstrapped. Support the inference with evidence.
Estimated headcount (with breakdown by function if discoverable: editorial, engineering, marketing, admin)
Degree of operational autonomy (relevant if subsidiary or portfolio company)
Who controls monetization infrastructure (ad ops, lead-gen partnerships, affiliate relationships)

1B. Scale Indicators
Provide structured bullets:

Monthly visits (range + confidence level + source: SimilarWeb, SEMrush, etc.)
Pages per visit (if available)
Revenue estimate (directional range with logic)
Primary revenue mix (rank order: programmatic, lead-gen, affiliate, sponsored, subscription)
Revenue concentration risks
Platform dependency exposure

1C. Business Model Breakdown
Provide a proper Markdown table (header, separator row, then data rows):
| Revenue Stream | Est. Weight | Mechanics | Margin Profile | Structural Risk |
|----------------|-------------|-----------|----------------|-----------------|
| [row] | ... | ... | ... | ... |
After the table, provide one paragraph (3-5 sentences) explaining the critical nuance for Accelerate.

1D. Operational Pressure Points
Provide 4-6 economically grounded bullets.
Each must follow this structure:
[Constraint] > [Why it exists] > [Economic consequence]

------------------------------------------------------------
CRITICAL STRATEGIC SIGNAL
------------------------------------------------------------

Identify ONE structural signal that materially changes LH2's motion toward this target.

For this signal, explain:
What the signal is (factual, sourced)
Why it matters for LH2 specifically
What it implies about power distribution and decision-making at the target
How it changes outreach sequencing
How it changes probability of close

If the signal involves a person, provide a career arc table:
| Period | Role | Entity | Location |
Then provide 2-3 paragraphs of synthesis.

============================================================
2. ARTICLE FREQUENCY & EXECUTION CAPACITY
============================================================

2A. Methodology (3-4 sentences)
2B. Output Metrics table — proper Markdown (header, separator row, data rows): | Metric | Estimate | Confidence |
2C. Content Mix table — proper Markdown (header, separator row, data rows): | Section | Volume Share | Content Type | Templated? | Pod-Compatible? |
2D. Execution Gap Diagnosis with subsections:
Under-covered Categories (3-5 bullets)
Throughput Constraints (3-4 bullets)
Competitor Delta (quantified)
Revenue Implication
Pod Expansion Model
Single Biggest Bottleneck (one sentence)

============================================================
3. POWER MAPPING & STAKEHOLDER PRIORITIZATION
============================================================

Conduct proper SPOC research using your knowledge of publicly available information (LinkedIn, company pages, press, executive profiles, job postings). Completely ignore any contact details or SPOC data stored in Notion. This section must be highly aligned to the company context: ownership structure, content type, strategic signal, and execution gaps already identified in this research. Do not use generic placeholders — name real individuals where knowable, with titles and roles that match this specific company's structure. The stakeholder table must reflect who actually holds economic and editorial authority at this target, based on your knowledge of the web.

3A. Organizational Reality bullets
3B. Identified Power Center (1-2 paragraphs)
3C. Ranked Stakeholder Table — proper Markdown (header, separator row, data rows): | Priority | Name | Title | Economic Authority | Why They Matter | Core Incentive |. Each row must be a real SPOC aligned to this company's context.
3D. Contact Logic bullets including Who to Avoid

============================================================
4. INDIVIDUAL STRATEGIC INTERPRETATION
============================================================

Profile TOP TWO stakeholders with: Background, Incentive Structure, Likely Objections, Deal Sensitivity, How They Will Perceive LH2, Personalization Angle

============================================================
5. FIT ASSESSMENT
============================================================

Rating: Strong / Medium-Strong / Medium / Weak
Why Not Strong (2-4 points)
Why Attractive (4-6 points)
Single Most Acute Pain (one sentence)
Decision Integrity

============================================================
6. OUTREACH SEQUENCING STRATEGY
============================================================

Primary Entry, Secondary Path, Escalation Path, Internal Champion, Dual-Track Note

============================================================
7. ACCELERATE POSITIONING
============================================================

Correct Framing (4-6 bullets)
Language to Avoid (4-6 items)
Most Compelling Entry Vertical
Secondary Vertical

============================================================
FINAL SECTION: STRATEGIC WEDGE
============================================================

6-8 sentence wedge paragraph.
6-8 bullet doctrine alignment list with 1-2 risk bullets.

Tone: Institutional. Measured. Decisive. Economically grounded. Zero fluff.
"""


# ============================================================
# CLAUDE API CALL WITH STREAMING
# ============================================================

def deep_research_agent(domain, context):
    MAX_RETRIES = 2
    MAX_CONTEXT_CHARS = 60000

    if len(context) > MAX_CONTEXT_CHARS:
        log.warning(f"  [{domain}] Context {len(context):,} chars — trimming to {MAX_CONTEXT_CHARS:,}")
        context = context[:MAX_CONTEXT_CHARS] + "\n\n[... trimmed ...]\n"

    prompt = build_prompt(domain, context)
    prompt_tokens_est = len(prompt) // 4
    log.info(f"  [{domain}] Prompt: ~{prompt_tokens_est:,} tokens est.")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"  [{domain}] API call {attempt}/{MAX_RETRIES} — streaming...")
            start = time.time()

            # Use streaming to get progress visibility
            collected = []
            chars_received = 0
            last_log_time = start

            with claude.messages.stream(
                model="claude-sonnet-4-5",
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                for text in stream.text_stream:
                    collected.append(text)
                    chars_received += len(text)

                    # Log progress every 30s so it doesn't look hung
                    now = time.time()
                    if now - last_log_time >= 30:
                        elapsed = now - start
                        log.info(f"  [{domain}] ... {chars_received:,} chars received ({elapsed:.0f}s)")
                        last_log_time = now

            result = "".join(collected)
            elapsed = time.time() - start

            # Get final message for usage stats
            final = stream.get_final_message()
            token_in = final.usage.input_tokens
            token_out = final.usage.output_tokens
            stop = final.stop_reason

            log.info(f"  [{domain}] Done — {elapsed:.0f}s, in:{token_in:,} out:{token_out:,} stop={stop}")

            if stop == "max_tokens":
                log.warning(f"  [{domain}] TRUNCATED at {token_out:,} tokens")

            if "STRATEGIC WEDGE" in result:
                log.info(f"  [{domain}] Sanity: PASS")
            else:
                log.warning(f"  [{domain}] Sanity: FAIL — Strategic Wedge missing")

            return result

        except Exception as e:
            elapsed = time.time() - start
            log.error(f"  [{domain}] Attempt {attempt} failed after {elapsed:.0f}s: {type(e).__name__}: {e}")
            if attempt < MAX_RETRIES:
                wait = 15 * attempt
                log.info(f"  [{domain}] Retry in {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"  [{domain}] All attempts failed.")
                return None


# ============================================================
# NOTION BLOCK HANDLING
# ============================================================

NOTION_TEXT_LIMIT = 1900
NOTION_BATCH_LIMIT = 100


def text_to_blocks(text):
    blocks = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        for i in range(0, len(stripped), NOTION_TEXT_LIMIT):
            chunk = stripped[i:i + NOTION_TEXT_LIMIT]
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": chunk}
                    }]
                }
            })
    return blocks


def append_blocks(block_id, blocks):
    for i in range(0, len(blocks), NOTION_BATCH_LIMIT):
        batch = blocks[i:i + NOTION_BATCH_LIMIT]
        try:
            notion.blocks.children.append(block_id=block_id, children=batch)
        except Exception as e:
            log.error(f"  Block append error (batch {i // NOTION_BATCH_LIMIT + 1}): {e}")


# ============================================================
# SECTION SPLITTING — 5 INTERNAL NOTES
# ============================================================

NOTE_DEFINITIONS = [
    (
        "0-1: Eligibility & Operating Context",
        ["0. ELIGIBILITY CHECK", "1. COMPANY & OPERATING CONTEXT", "CRITICAL STRATEGIC SIGNAL"]
    ),
    (
        "2: Article Frequency & Execution",
        ["2. ARTICLE FREQUENCY & EXECUTION CAPACITY"]
    ),
    (
        "3: Power Mapping & Stakeholders",
        ["3. POWER MAPPING & STAKEHOLDER PRIORITIZATION"]
    ),
    (
        "4-5: Stakeholder Interpretation & Fit",
        ["4. INDIVIDUAL STRATEGIC INTERPRETATION", "5. FIT ASSESSMENT"]
    ),
    (
        "6-Final: Outreach & Strategic Wedge",
        ["6. OUTREACH SEQUENCING STRATEGY", "7. ACCELERATE POSITIONING", "FINAL SECTION: STRATEGIC WEDGE"]
    ),
]


def split_research_into_notes(text):
    lines = text.split("\n")

    header_to_note = {}
    for note_idx, (_, patterns) in enumerate(NOTE_DEFINITIONS):
        for pat in patterns:
            header_to_note[pat] = note_idx

    boundaries = []
    matched_patterns = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r'[=\-]+', stripped):
            continue
        for pat, note_idx in header_to_note.items():
            if pat in matched_patterns:
                continue
            if pat in stripped:
                start = i
                for back in range(1, 4):
                    if i - back >= 0 and re.fullmatch(r'[=\-]+', lines[i - back].strip()):
                        start = i - back
                    else:
                        break
                boundaries.append((start, note_idx))
                matched_patterns.add(pat)
                break

    seen_lines = set()
    unique_boundaries = []
    for line_idx, note_idx in boundaries:
        if line_idx not in seen_lines:
            seen_lines.add(line_idx)
            unique_boundaries.append((line_idx, note_idx))
    boundaries = sorted(unique_boundaries, key=lambda x: x[0])

    if not boundaries:
        log.warning("No section headers detected — falling back to equal splits")
        return _fallback_split(text)

    detected_notes = set(note_idx for _, note_idx in boundaries)
    for idx in range(len(NOTE_DEFINITIONS)):
        status = "FOUND" if idx in detected_notes else "MISSING"
        log.info(f"  Section: {NOTE_DEFINITIONS[idx][0]} — {status}")

    note_bodies = [""] * len(NOTE_DEFINITIONS)

    for idx, (line_start, note_idx) in enumerate(boundaries):
        if idx + 1 < len(boundaries):
            line_end = boundaries[idx + 1][0]
        else:
            line_end = len(lines)

        chunk = "\n".join(lines[line_start:line_end]) + "\n"
        note_bodies[note_idx] += chunk

    first_boundary_line = boundaries[0][0]
    if first_boundary_line > 0:
        preamble = "\n".join(lines[:first_boundary_line]) + "\n"
        note_bodies[0] = preamble + note_bodies[0]

    for i, body in enumerate(note_bodies):
        if not body.strip():
            note_bodies[i] = "(No content matched for this section.)"

    return [(NOTE_DEFINITIONS[i][0], note_bodies[i]) for i in range(len(NOTE_DEFINITIONS))]


def _fallback_split(text):
    chunk_size = len(text) // 5
    notes = []
    for i in range(5):
        start = i * chunk_size
        end = (i + 1) * chunk_size if i < 4 else len(text)
        title = NOTE_DEFINITIONS[i][0] if i < len(NOTE_DEFINITIONS) else f"Section {i + 1}"
        notes.append((title, text[start:end]))
    return notes


# ============================================================
# WRITE RESEARCH TO NOTION
# ============================================================

def write_to_notion(page_id, research_text, domain):
    if research_text is None:
        log.error(f"  [{domain}] No data — Failed")
        set_status(page_id, "Failed")
        return False

    if len(research_text.strip()) < 500:
        log.error(f"  [{domain}] Too short ({len(research_text.strip())} chars) — Failed")
        set_status(page_id, "Failed")
        return False

    log.info(f"  [{domain}] Splitting {len(research_text):,} chars into notes...")
    text = research_text.replace("\r\n", "\n")
    notes = split_research_into_notes(text)

    log.info(f"  [{domain}] Writing {len(notes)} notes...")
    created_count = 0
    for idx, (title, body) in enumerate(notes, 1):
        try:
            note = notion.pages.create(
                parent={"page_id": page_id},
                properties={
                    "title": [{"text": {"content": title}}]
                }
            )
            blocks = text_to_blocks(body)
            append_blocks(note["id"], blocks)
            created_count += 1
            body_len = len(body) if body.strip() and body.strip() != "(No content matched for this section.)" else 0
            log.info(f"  [{domain}] Note {idx}/5: {title} — {len(blocks)} blocks, {body_len:,} chars")
        except Exception as e:
            log.error(f"  [{domain}] ERROR note '{title}': {e}")

    if created_count > 0:
        set_status(page_id, "Completed")
        if created_count < len(notes):
            log.warning(f"  [{domain}] Partial: {created_count}/{len(notes)}")
    else:
        set_status(page_id, "Failed")

    return created_count == len(notes)


# ============================================================
# PROCESS ONE TASK (thread-safe)
# ============================================================

def run_task(task):
    """Full pipeline for one task. Called from thread pool."""
    page_id = task["id"]

    try:
        domain = task["properties"]["Domain"]["title"][0]["text"]["content"]
    except (KeyError, IndexError):
        log.error(f"  Could not read Domain from {page_id}")
        return False

    log.info(f"  >>> START: {domain}")
    start_time = time.time()

    try:
        research_data = deep_research_agent(domain, cached_context)
    except Exception as e:
        log.error(f"  [{domain}] Research crash: {type(e).__name__}: {e}")
        research_data = None

    elapsed = time.time() - start_time
    if research_data:
        log.info(f"  [{domain}] Research: {elapsed:.0f}s, {len(research_data):,} chars")
    else:
        log.error(f"  [{domain}] Research: None after {elapsed:.0f}s")

    try:
        success = write_to_notion(page_id, research_data, domain)
    except Exception as e:
        log.error(f"  [{domain}] Notion crash: {type(e).__name__}: {e}")
        set_status(page_id, "Failed")
        success = False

    total = time.time() - start_time
    tag = "DONE" if success else "FAILED"
    log.info(f"  <<< {tag}: {domain} — {total:.0f}s total")
    return success


# ============================================================
# MAIN LOOP
# ============================================================

log.info("=" * 60)
log.info("LH2 Research Bot — Parallel + Streaming Mode")
log.info(f"Database: {DATABASE_ID}")
log.info(f"Max parallel: {MAX_PARALLEL}")
log.info("=" * 60)

# --- STARTUP DIAGNOSTIC ---
try:
    db_info = notion.databases.retrieve(database_id=DATABASE_ID)
    db_title = "".join([t.get("plain_text", "") for t in db_info.get("title", [])])
    log.info(f"Connected: '{db_title}'")
    props = db_info.get("properties", {})

    if "Research" in props:
        rtype = props["Research"].get("type", "?")
        if rtype == "select":
            opts = [o["name"] for o in props["Research"].get("select", {}).get("options", [])]
            log.info(f"  Research options: {opts}")
            for needed in ["Research Now", "In Progress", "Completed", "Failed"]:
                if needed not in opts:
                    log.warning(f"  >>> '{needed}' NOT in options — add it!")
    else:
        log.warning(f"  >>> 'Research' property NOT FOUND!")

    if "Domain" not in props:
        log.warning(f"  >>> 'Domain' property NOT FOUND!")
except Exception as e:
    log.error(f"Startup diagnostic failed: {e}")

log.info("Polling every 30s...")

poll_count = 0

while True:
    try:
        ensure_memory_freshness()
        poll_count += 1

        tasks = query_database(
            DATABASE_ID,
            {"property": "Research", "select": {"equals": "Research Now"}}
        )

        if tasks:
            log.info(f"Poll #{poll_count}: {len(tasks)} task(s) found")
        elif poll_count <= 5 or poll_count % 20 == 0:
            log.info(f"Poll #{poll_count}: idle")

        if tasks:
            # Phase 1: Claim all tasks (fast, sequential)
            claimed = []
            for task in tasks:
                try:
                    domain = task["properties"]["Domain"]["title"][0]["text"]["content"]
                except (KeyError, IndexError):
                    continue
                if claim_task(task["id"]):
                    claimed.append(task)
                    log.info(f"  Claimed: {domain}")

            if not claimed:
                log.warning(f"  No tasks could be claimed")
                time.sleep(30)
                continue

            # Phase 2: Run all claimed tasks in parallel
            batch_start = time.time()
            log.info(f"  Launching {len(claimed)} task(s) in parallel...")

            with ThreadPoolExecutor(max_workers=min(len(claimed), MAX_PARALLEL)) as pool:
                futures = {}
                for task in claimed:
                    domain = task["properties"]["Domain"]["title"][0]["text"]["content"]
                    futures[pool.submit(run_task, task)] = domain

                for future in as_completed(futures):
                    domain = futures[future]
                    try:
                        success = future.result()
                    except Exception as e:
                        log.error(f"  THREAD CRASH: {domain} — {type(e).__name__}: {e}")

            batch_time = time.time() - batch_start
            log.info(f"  Batch done: {len(claimed)} tasks in {batch_time:.0f}s")
            continue  # Re-poll immediately

    except Exception as e:
        log.error(f"Loop error: {e}", exc_info=True)

    time.sleep(30)