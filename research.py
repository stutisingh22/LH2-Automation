"""
research.py — Deep research agent for company analysis

Extracted from notion_research_bot.py. Runs the full institutional-grade
research prompt via Claude, reads existing Notion research, and writes back.
"""

import logging
import time
import requests
from anthropic import Anthropic

from config import (
    ANTHROPIC_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID,
)

log = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
NOTION_TEXT_LIMIT = 1900
NOTION_BATCH_LIMIT = 100

# Research model — use the best available for deep analysis
RESEARCH_MODEL = "claude-sonnet-4-5-20250514"
RESEARCH_MAX_TOKENS = 16000


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ══════════════════════════════════════════════════════════════
# READ EXISTING RESEARCH FROM NOTION
# ══════════════════════════════════════════════════════════════

def fetch_research_from_notion(page_id):
    """
    Fetch all child pages (research notes) from a Notion page.
    Returns list of {title, content} dicts, or empty list.
    """
    if not page_id or not NOTION_TOKEN:
        return []

    notes = []
    try:
        r = requests.get(
            f"{NOTION_API}/blocks/{page_id}/children?page_size=100",
            headers=_headers(),
        )
        r.raise_for_status()
        children = r.json()

        for block in children.get("results", []):
            if block["type"] == "child_page":
                title = block["child_page"]["title"]
                # Fetch the child page's content
                content = _fetch_block_text(block["id"])
                if content.strip():
                    notes.append({"title": title, "content": content})

    except Exception as e:
        log.warning("Failed to fetch research for page %s: %s", page_id, e)

    return notes


def _fetch_block_text(block_id, max_depth=2, depth=0):
    """Recursively fetch text content from a block and its children."""
    if depth > max_depth:
        return ""

    texts = []
    try:
        r = requests.get(
            f"{NOTION_API}/blocks/{block_id}/children?page_size=100",
            headers=_headers(),
        )
        r.raise_for_status()
        blocks = r.json().get("results", [])

        for block in blocks:
            btype = block["type"]
            # Extract text from common block types
            if btype in ("paragraph", "bulleted_list_item", "numbered_list_item",
                         "heading_1", "heading_2", "heading_3", "quote", "callout"):
                parts = block[btype].get("rich_text", [])
                text = " ".join(p["plain_text"] for p in parts)
                if text.strip():
                    if btype == "bulleted_list_item":
                        texts.append(f"• {text}")
                    elif btype == "numbered_list_item":
                        texts.append(f"- {text}")
                    elif btype.startswith("heading"):
                        texts.append(f"\n{text}\n")
                    else:
                        texts.append(text)

            elif btype == "table":
                table_text = _fetch_table_text(block["id"])
                if table_text:
                    texts.append(table_text)

            # Recurse into blocks that have children
            if block.get("has_children") and btype != "child_page":
                child_text = _fetch_block_text(block["id"], max_depth, depth + 1)
                if child_text.strip():
                    texts.append(child_text)

    except Exception as e:
        log.debug("Block text fetch error for %s: %s", block_id, e)

    return "\n".join(texts)


def _fetch_table_text(table_id):
    """Fetch table rows as pipe-formatted text."""
    try:
        r = requests.get(
            f"{NOTION_API}/blocks/{table_id}/children?page_size=100",
            headers=_headers(),
        )
        r.raise_for_status()
        rows = r.json().get("results", [])
        lines = []
        for row in rows:
            if row["type"] == "table_row":
                cells = row["table_row"]["cells"]
                cell_texts = []
                for cell in cells:
                    text = " ".join(p["plain_text"] for p in cell) if cell else ""
                    cell_texts.append(text)
                lines.append("| " + " | ".join(cell_texts) + " |")
        return "\n".join(lines)
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════
# WRITE RESEARCH TO NOTION
# ══════════════════════════════════════════════════════════════

def write_research_to_notion(page_id, research_text, domain):
    """Write research output as child pages in Notion."""
    if not research_text or len(research_text.strip()) < 500:
        log.error("Research too short to write for %s", domain)
        _set_research_status(page_id, "Failed")
        return False

    notes = _split_research(research_text)
    log.info("Writing %d research notes for %s", len(notes), domain)

    created = 0
    for title, body in notes:
        try:
            # Create child page
            r = requests.post(
                f"{NOTION_API}/pages",
                headers=_headers(),
                json={
                    "parent": {"page_id": page_id},
                    "properties": {
                        "title": [{"text": {"content": title}}]
                    },
                },
            )
            r.raise_for_status()
            note_id = r.json()["id"]

            # Append content blocks
            blocks = _text_to_blocks(body)
            for i in range(0, len(blocks), NOTION_BATCH_LIMIT):
                batch = blocks[i:i + NOTION_BATCH_LIMIT]
                rb = requests.patch(
                    f"{NOTION_API}/blocks/{note_id}/children",
                    headers=_headers(),
                    json={"children": batch},
                )
                rb.raise_for_status()

            created += 1
        except Exception as e:
            log.error("Failed to write note '%s': %s", title, e)

    status = "Completed" if created == len(notes) else "Failed"
    _set_research_status(page_id, status)
    return created > 0


def _set_research_status(page_id, status):
    """Update the Research select property in Notion."""
    try:
        requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=_headers(),
            json={"properties": {"Research": {"select": {"name": status}}}},
        )
    except Exception as e:
        log.error("Failed to set research status: %s", e)


def _text_to_blocks(text):
    """Convert text to Notion paragraph blocks."""
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
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                },
            })
    return blocks


# ══════════════════════════════════════════════════════════════
# RESEARCH PROMPT
# ══════════════════════════════════════════════════════════════

def build_research_prompt(domain, context=""):
    """The full institutional-grade research prompt."""
    return f"""{context}

You are producing institutional-grade strategic account research for LH2 Corp Dev & Partnerships.
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
Translate execution gaps into revenue impact.
Distinguish control from influence. The person who signs checks is not always the person who decides strategy.
Separate editorial authority from economic authority.
Identify constraints, not surface symptoms.
Tie every recommendation to leverage and probability of close.

Formatting Rules (MANDATORY):

Use section dividers exactly as written below (the === and --- lines).
Use proper paragraph spacing between all sections and subsections.
Use clean, consistent pipe-formatted tables with column headers.
No markdown bold (no ** or __).
No emojis.
No decorative commentary.
No filler language.
Every section must contain substance. If a section would be speculative filler, say so in one sentence and move on.
Keep bullet points tight. Each bullet should deliver one discrete insight. No run-on bullets.

Target Domain: {domain}

============================================================
0. ELIGIBILITY CHECK
============================================================

Provide the following table:
| Criterion | Assessment | Pass/Fail |
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
1B. Scale Indicators
1C. Business Model Breakdown
1D. Operational Pressure Points

------------------------------------------------------------
CRITICAL STRATEGIC SIGNAL
------------------------------------------------------------

Identify ONE structural signal that materially changes LH2's motion toward this target.

============================================================
2. ARTICLE FREQUENCY & EXECUTION CAPACITY
============================================================

2A. Methodology
2B. Output Metrics table
2C. Content Mix table
2D. Execution Gap Diagnosis

============================================================
3. POWER MAPPING & STAKEHOLDER PRIORITIZATION
============================================================

3A. Organizational Reality
3B. Identified Power Center
3C. Ranked Stakeholder Table
3D. Contact Logic

============================================================
4. INDIVIDUAL STRATEGIC INTERPRETATION
============================================================

Profile TOP TWO stakeholders with: Background, Incentive Structure, Likely Objections, Deal Sensitivity, How They Will Perceive LH2, Personalization Angle

============================================================
5. FIT ASSESSMENT
============================================================

Rating: Strong / Medium-Strong / Medium / Weak
Why Not Strong / Why Attractive / Single Most Acute Pain / Decision Integrity

============================================================
6. OUTREACH SEQUENCING STRATEGY
============================================================

Primary Entry, Secondary Path, Escalation Path, Internal Champion, Dual-Track Note

============================================================
7. ACCELERATE POSITIONING
============================================================

Correct Framing / Language to Avoid / Most Compelling Entry Vertical / Secondary Vertical

============================================================
FINAL SECTION: STRATEGIC WEDGE
============================================================

6-8 sentence wedge paragraph.
6-8 bullet doctrine alignment list with 1-2 risk bullets.

Tone: Institutional. Measured. Decisive. Economically grounded. Zero fluff.
"""


# ══════════════════════════════════════════════════════════════
# SECTION SPLITTING (for Notion write-back)
# ══════════════════════════════════════════════════════════════

NOTE_SECTIONS = [
    ("0-1: Eligibility & Operating Context",
     ["0. ELIGIBILITY CHECK", "1. COMPANY & OPERATING CONTEXT", "CRITICAL STRATEGIC SIGNAL"]),
    ("2: Article Frequency & Execution",
     ["2. ARTICLE FREQUENCY & EXECUTION CAPACITY"]),
    ("3: Power Mapping & Stakeholders",
     ["3. POWER MAPPING & STAKEHOLDER PRIORITIZATION"]),
    ("4-5: Stakeholder Interpretation & Fit",
     ["4. INDIVIDUAL STRATEGIC INTERPRETATION", "5. FIT ASSESSMENT"]),
    ("6-Final: Outreach & Strategic Wedge",
     ["6. OUTREACH SEQUENCING STRATEGY", "7. ACCELERATE POSITIONING", "FINAL SECTION: STRATEGIC WEDGE"]),
]


def _split_research(text):
    """Split research text into 5 sections for Notion child pages."""
    import re
    lines = text.split("\n")

    header_to_note = {}
    for idx, (_, patterns) in enumerate(NOTE_SECTIONS):
        for pat in patterns:
            header_to_note[pat] = idx

    boundaries = []
    matched = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r'[=\-]+', stripped):
            continue
        for pat, note_idx in header_to_note.items():
            if pat in matched:
                continue
            if pat in stripped:
                start = i
                for back in range(1, 4):
                    if i - back >= 0 and re.fullmatch(r'[=\-]+', lines[i - back].strip()):
                        start = i - back
                    else:
                        break
                boundaries.append((start, note_idx))
                matched.add(pat)
                break

    boundaries = sorted(set(boundaries), key=lambda x: x[0])

    if not boundaries:
        # Fallback: split evenly
        chunk = len(text) // 5
        return [
            (NOTE_SECTIONS[i][0], text[i * chunk:(i + 1) * chunk if i < 4 else len(text)])
            for i in range(5)
        ]

    note_bodies = [""] * len(NOTE_SECTIONS)

    for idx, (line_start, note_idx) in enumerate(boundaries):
        line_end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        note_bodies[note_idx] += "\n".join(lines[line_start:line_end]) + "\n"

    # Preamble goes to first note
    if boundaries[0][0] > 0:
        note_bodies[0] = "\n".join(lines[:boundaries[0][0]]) + "\n" + note_bodies[0]

    for i, body in enumerate(note_bodies):
        if not body.strip():
            note_bodies[i] = "(No content for this section.)"

    return [(NOTE_SECTIONS[i][0], note_bodies[i]) for i in range(len(NOTE_SECTIONS))]


# ══════════════════════════════════════════════════════════════
# RUN RESEARCH (streaming with progress)
# ══════════════════════════════════════════════════════════════

def run_research(domain, context="", progress_callback=None):
    """
    Run the full research prompt against Claude.
    Returns the research text, or None on failure.

    progress_callback(chars_received, elapsed_seconds) is called periodically.
    """
    if not ANTHROPIC_API_KEY:
        return None

    MAX_CONTEXT = 60000
    if len(context) > MAX_CONTEXT:
        context = context[:MAX_CONTEXT] + "\n\n[... trimmed ...]\n"

    prompt = build_research_prompt(domain, context)
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    for attempt in range(1, 3):
        try:
            start = time.time()
            collected = []
            chars = 0

            with client.messages.stream(
                model=RESEARCH_MODEL,
                max_tokens=RESEARCH_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    collected.append(text)
                    chars += len(text)
                    if progress_callback and chars % 500 < len(text):
                        progress_callback(chars, time.time() - start)

            result = "".join(collected)
            elapsed = time.time() - start

            final = stream.get_final_message()
            log.info(
                "[%s] Research done — %.0fs, in:%d out:%d stop=%s",
                domain, elapsed, final.usage.input_tokens,
                final.usage.output_tokens, final.stop_reason,
            )

            if final.stop_reason == "max_tokens":
                log.warning("[%s] Research truncated at max_tokens", domain)

            return result

        except Exception as e:
            log.error("[%s] Research attempt %d failed: %s", domain, attempt, e)
            if attempt < 2:
                time.sleep(15)

    return None


def get_research_brief(domain, research_text):
    """
    Generate a quick 5-line executive brief from full research.
    Used for the auto-strategize panel.
    """
    if not research_text or not ANTHROPIC_API_KEY:
        return None

    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        r = client.messages.create(
            model=RESEARCH_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": f"""From this research on {domain}, extract a 5-line executive brief:
Line 1: Eligibility verdict (Proceed/Proceed with Caveats/Stop) + one-line reason
Line 2: Key strategic signal
Line 3: Power center (who to contact first and why)
Line 4: Biggest execution gap LH2 can fill
Line 5: Recommended first move

RESEARCH:
{research_text[:8000]}"""}],
        )
        return r.content[0].text.strip()
    except Exception as e:
        log.error("Brief generation failed: %s", e)
        return None