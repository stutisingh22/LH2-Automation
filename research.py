"""
research.py — Deep research agent for company analysis

Extracted from notion_research_bot.py. Runs the full institutional-grade
research prompt via Claude, reads existing Notion research, and writes back.
"""

import logging
import time
import requests
from anthropic import Anthropic

from anthropic_limiter import create_with_retry, stream_slot
from config import (
    ANTHROPIC_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID,
)

log = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
NOTION_TEXT_LIMIT = 1900
NOTION_BATCH_LIMIT = 100

# Research model — use the best available for deep analysis
RESEARCH_MODEL = "claude-sonnet-4-5"
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
    """Write research output as child pages in Notion. Accepts plain text or HTML (normalized to text for sectioning)."""
    if not research_text or len(research_text.strip()) < 500:
        log.error("Research too short to write for %s", domain)
        _set_research_status(page_id, "Failed")
        return False
    from ui_helpers import is_html_content, html_to_plain_text
    body_for_split = html_to_plain_text(research_text) if is_html_content(research_text) else research_text
    notes = _split_research(body_for_split)
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


def write_brief_to_notion(page_id, brief_text, domain):
    """Append the brief as blocks to the company page in Notion. Accepts plain text or HTML."""
    if not page_id or not NOTION_TOKEN or not (brief_text or "").strip():
        return False
    from ui_helpers import is_html_content
    try:
        blocks = [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Brief"}}]},
            },
        ]
        bt = (brief_text or "").strip()
        blocks.extend(_html_to_blocks(bt) if is_html_content(bt) else _text_to_blocks(bt))
        for i in range(0, len(blocks), NOTION_BATCH_LIMIT):
            batch = blocks[i : i + NOTION_BATCH_LIMIT]
            r = requests.patch(
                f"{NOTION_API}/blocks/{page_id}/children",
                headers=_headers(),
                json={"children": batch},
            )
            r.raise_for_status()
        log.info("Brief written to Notion for %s", domain)
        return True
    except Exception as e:
        log.error("Failed to write brief to Notion: %s", e)
        return False


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


def _html_to_blocks(html):
    """
    Convert HTML to Notion blocks (paragraph, heading_1/2/3, bulleted_list_item).
    Tables are emitted as one paragraph per row (pipe-separated cells).
    """
    from html.parser import HTMLParser
    import re

    class _BlockParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.blocks = []
            self._current = []
            self._tag = None
            self._in_table = False
            self._row_cells = []
            self._cell_text = []
            self._last_tag = None

        def _flush_text(self):
            if not self._current:
                return
            text = "".join(self._current).strip()
            self._current = []
            if not text or not text.strip():
                return
            text = re.sub(r"\s+", " ", text).strip()
            level = self._tag or "paragraph"
            if len(text) > NOTION_TEXT_LIMIT:
                for i in range(0, len(text), NOTION_TEXT_LIMIT):
                    self.blocks.append((level, text[i:i + NOTION_TEXT_LIMIT]))
            else:
                self.blocks.append((level, text))

        def handle_starttag(self, tag, attrs):
            self._last_tag = tag
            if tag in ("p", "div", "h1", "h2", "h3", "li"):
                self._flush_text()
                self._tag = {"h1": "heading_1", "h2": "heading_2", "h3": "heading_3", "li": "bulleted_list_item"}.get(tag, "paragraph")
            elif tag == "tr":
                self._flush_text()
                self._tag = None
                self._in_table = True
                self._row_cells = []
                self._cell_text = []
            elif tag in ("td", "th") and self._in_table:
                self._cell_text = []
            elif tag == "br":
                self._current.append("\n")

        def handle_endtag(self, tag):
            if tag in ("p", "div", "h1", "h2", "h3"):
                self._flush_text()
                self._tag = None
            elif tag == "li":
                self._flush_text()
                self._tag = None
            elif tag == "tr" and self._in_table:
                row_text = " | ".join(c.strip() for c in self._row_cells if c.strip())
                if row_text:
                    self.blocks.append(("paragraph", row_text))
                self._row_cells = []
            elif tag in ("td", "th") and self._in_table:
                cell = "".join(self._cell_text).strip()
                self._row_cells.append(cell)
            elif tag == "table":
                self._in_table = False

        def handle_data(self, data):
            if self._in_table and (self._last_tag in ("td", "th")):
                self._cell_text.append(data)
            else:
                self._current.append(data)

    try:
        p = _BlockParser()
        p.feed(html)
        p._flush_text()
    except Exception as e:
        log.warning("_html_to_blocks parse failed, falling back to text: %s", e)
        from ui_helpers import html_to_plain_text
        return _text_to_blocks(html_to_plain_text(html))

    out = []
    for block_type, content in p.blocks:
        if not (content or "").strip():
            continue
        content = (content or "").strip()[:NOTION_TEXT_LIMIT]
        if block_type == "heading_1":
            out.append({"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        elif block_type == "heading_2":
            out.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        elif block_type == "heading_3":
            out.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        elif block_type == "bulleted_list_item":
            out.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        else:
            out.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
    return out


# ══════════════════════════════════════════════════════════════
# RESEARCH PROMPT
# ══════════════════════════════════════════════════════════════

def build_research_prompt(domain, context=""):
    """The full institutional-grade research prompt. Research is produced from scratch by Claude; no extraction from Notion."""
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
Translate execution gaps into revenue impact.
Distinguish control from influence. The person who signs checks is not always the person who decides strategy.
Separate editorial authority from economic authority.
Identify constraints, not surface symptoms.
Tie every recommendation to leverage and probability of close.

Formatting Rules (MANDATORY):

Use section dividers exactly as written below (the === and --- lines).
Use proper paragraph spacing between all sections and subsections.
TABLES — Every table in the document must be a proper Markdown table so it renders correctly:
  - Line 1: header row with pipe-separated column names (e.g. | Criterion | Assessment | Pass/Fail |)
  - Line 2: separator row with dashes under each column (e.g. |-----------|------------|-----------|)
  - Line 3+: one data row per item, same pipe format. No table may omit the separator row.
This applies to ALL tables: Eligibility (0), Business Model (1C), Output Metrics (2B), Content Mix (2C), Stakeholder Table (3C), and any other table. Output only proper Markdown tables.
No markdown bold (no ** or __).
No emojis.
No decorative commentary.
No filler language.
Every section must contain substance. If a section would be speculative filler, say so in one sentence and move on.
Keep bullet points tight. Each bullet should deliver one discrete insight. No run-on bullets.

Target Domain: {domain}

============================================================
0. ELIGIBILITY CHECK (MANDATORY GATE)
============================================================

Provide a proper Markdown table — header row, then separator row (|---|---|), then exactly one data row per criterion. Example format:

| Criterion | Assessment | Pass/Fail |
|-----------|------------|-----------|
| Content behind a paywall or subscription meter? | [Your assessment] | Pass/Fail |
| Content style: factual, attribution-led, POV-light? | [Your assessment] | Pass/Fail |
| Category alignment with LH2's supported universe? | [Your assessment] | Pass/Fail |
| Can a 5–15 person pod plausibly move revenue? | [Your assessment] | Pass/Fail |
| Content type: Evergreen (search-based) vs Everyday (news, latest updates)? | [Your assessment] | Pass/Fail |

Evaluate these five criteria:

Paywall exposure: Is content free and open, or gated behind subscription/registration?
Content structure: Is the content factual, attribution-led, and POV-light? Or is it opinion-driven, editorial, personality-dependent?
Category alignment with LH2 doctrine: Does the domain's primary content category fall within LH2's supported universe? Be specific about which category.
Pod revenue plausibility: Can a 5-15 person deployment plausibly move revenue? Consider the revenue model (programmatic vs. lead-gen vs. affiliate vs. subscription) and whether more content = more revenue in a roughly linear way.
Content type: Is the site primarily Evergreen (search-driven, evergreens, how-to, reference) or Everyday (news, latest updates, breaking, time-sensitive)? Pass = Everyday. Fail = Evergreen. Base this on the type of content they actually publish.

After the table, on a new line:
Eligibility Verdict: Proceed / Proceed with Caveats / Stop
Verdict must be Proceed or Proceed with Caveats only when the content type is Everyday. If the site is primarily Evergreen (search-based), Verdict = Stop.
Then provide one short paragraph (3-5 sentences) explaining structural caveats. If the revenue model is not purely programmatic, explain how this changes the Accelerate framing. If the content carries elevated accuracy requirements (legal, medical, financial), note the operational implications.
If Verdict = Stop, note the disqualifying factor but COMPLETE THE FULL ANALYSIS REGARDLESS. LH2 needs the research for internal records even if the target is not a fit.

============================================================
1. COMPANY & OPERATING CONTEXT
============================================================

1A. Ownership & Structure
1B. Scale Indicators
1C. Business Model Breakdown — use a proper Markdown table (header, separator row |---|, then data rows): Revenue Stream, Est. Weight, Mechanics, etc.
1D. Operational Pressure Points

------------------------------------------------------------
CRITICAL STRATEGIC SIGNAL
------------------------------------------------------------

Identify ONE structural signal that materially changes LH2's motion toward this target.

============================================================
2. ARTICLE FREQUENCY & EXECUTION CAPACITY
============================================================

2A. Methodology
2B. Output Metrics — proper Markdown table (header, separator row, data rows)
2C. Content Mix — proper Markdown table (header, separator row, data rows)
2D. Execution Gap Diagnosis

============================================================
3. POWER MAPPING & STAKEHOLDER PRIORITIZATION
============================================================

You are conducting stakeholder mapping using publicly available information only.

You must perform exhaustive research before producing output. Do not stop after identifying 2–3 executives. You must return 5–6 high-leverage stakeholders unless the company is demonstrably too small to support that many (in which case explicitly justify why).

You are strictly prohibited from referencing internal CRM systems, Notion, or private databases.

Before moving to mid-level or secondary roles, you must prioritize identifying the highest-ranking authority figures first. Prioritization order:

Founder / Co-Founder

Owner / Principal

CEO / President

Publisher / GM

COO / CFO

CTO / Chief Product Officer

Chief Content Officer / Editor-in-Chief

VP-level leadership (Operations, Product, Audience, Revenue, Technology, Editorial)

Director / Head-level roles (only if higher authority roles are unavailable or insufficient)

You must exhaust all attempts to identify individuals in tiers 1–7 before expanding into Director or Head-level roles.

Research Scope (Mandatory Sources)

You must systematically check the following:

Company official website:

About
Team / Leadership
Masthead
Press
Careers
Footer / disclosures
Investor Relations (if applicable)

Company LinkedIn page:

Employees tab
Filter by seniority: Owner, Founder, CXO, VP, Director, Head
Cross-check tenure and scope via individual LinkedIn profiles

Press mentions, interviews, and bylines

Podcast appearances

Conference speaker listings

Job postings (to infer missing leadership roles)

Parent company / network / holding structure pages (if applicable)

Organizational filings (if PE-backed or part of a larger group)

You may not finalize output until all relevant public sources have been reviewed.

Required Research Sequence

Step 1: Establish ownership structure
Determine whether the company is:

Independent
PE-backed
Family-owned
Network-affiliated
Subsidiary

If network-affiliated or PE-backed:

Determine whether economic authority sits locally or at the parent level.

Escalate stakeholder search to parent-level executives or operating partners where relevant.

When a parent or holding company exists, you must prioritize identifying decision-makers at the level where economic authority truly resides (local vs parent). Do not assume decisions are local without verification.

Step 2: Identify Economic Authority
Identify who controls:

Budget
Vendor relationships
Technology spend
Strategic partnerships

Acceptable titles include:
Founder, CEO, Publisher, GM, COO, CFO, VP Operations, Operating Partner (PE), Network Executive

You must identify at least one economic buyer.

Economic authority roles must be prioritized over purely operational or advisory roles.

Step 3: Identify Editorial Authority
Identify who controls:

Content strategy
Publishing workflow
Distribution decisions
Editorial roadmap

Acceptable titles include:
Editor-in-Chief, Head of Content, Chief Content Officer, Managing Editor, Director of Audience, Founder (if lean organization)

You must identify at least one editorial authority.

If editorial leadership appears consolidated under a founder or CEO, explicitly note that overlap.

Step 4: Identify Technical Authority
Identify who manages:

CMS
Ad stack
Analytics
Product or engineering teams
In-house tooling

Acceptable titles include:
CTO, VP Engineering, Head of Product, Director of Technology, Product Lead

You must prioritize CTO or executive-level technical leadership before considering Director-level roles.

If no technical authority is publicly visible:

Explicitly flag the gap
Infer responsibility overlap
Mark as requiring enrichment

You must identify at least one technical authority unless company size clearly prevents it.

Step 5: Distinguish Champion vs Decision-Maker
For each authority dimension:

Identify who signs contracts (decision-maker)
Identify who likely feels operational pain (champion)

Decision-makers must be senior economic or executive authorities.

Champions may be VP or Director-level roles with operational ownership.

At least one stakeholder must be identified as an operational champion.

Coverage Requirement

Return 5–6 real individuals with verifiable public signals.

Stakeholders must collectively cover:

Economic authority
Editorial authority
Technical authority

At least three stakeholders must be executive-level (Founder, Owner, CXO, Publisher, GM, or VP-level).

Do not pad the output with low-leverage or irrelevant names.

If fewer than 5 stakeholders are identified:

Explicitly explain why (company size, lack of public data, founder-led structure)

Identify expected role titles that likely hold authority

Flag those roles as requiring enrichment

Hard Constraints

No speculative org chart assumptions.
No generic filler.
No internal data references.
No stopping after identifying only top 2–3 executives.
Must check parent or holding structure before concluding research.
Must prioritize highest-ranking authority roles before mapping secondary stakeholders.

If verification_checklist cannot be fully satisfied, continue research before producing final output.

3A. Organizational Reality
3B. Identified Power Center
3C. Ranked Stakeholder Table — proper Markdown table (header, separator row, data rows). Each row must be a real SPOC: name, exact title, economic authority, why they matter, aligned to this company's context.
3D. Contact Logic

============================================================
4. INDIVIDUAL STRATEGIC INTERPRETATION
============================================================

Profile TOP THREE stakeholders with: Background, Incentive Structure, Likely Objections, Deal Sensitivity, How They Will Perceive LH2, Personalization Angle

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


def _split_extra_sections(body):
    """
    Split the last section body into content before '8.', '9.', etc. and extra (title, body) pairs.
    Only numbers >= 8 are treated as user-appended sections; 6 and 7 are part of the template
    (6-Final: Outreach & Strategic Wedge). Returns (first_part_text, [(title, body), ...]).
    """
    import re
    if not (body or "").strip():
        return "", []
    lines = body.split("\n")
    extra = []
    current_title = None
    current_body = []
    first_part_lines = []
    for line in lines:
        m = re.match(r"^\s*(\d+)[.:]\s+(.+)$", line)
        if m and int(m.group(1)) >= 8:
            if current_title is not None:
                extra.append((current_title, "\n".join(current_body).strip()))
            current_title = m.group(2).strip()
            current_body = []
        else:
            if current_title is not None:
                current_body.append(line)
            else:
                first_part_lines.append(line)
    if current_title is not None:
        extra.append((current_title, "\n".join(current_body).strip()))
    return "\n".join(first_part_lines).strip(), extra


def _split_research(text):
    """Split research text into 5 fixed sections plus any 8., 9., ... user-appended sections for expanders."""
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
        # Fallback: split evenly into 5, then split last chunk for 8., 9., ...
        chunk = len(text) // 5
        base = [
            (NOTE_SECTIONS[i][0], text[i * chunk:(i + 1) * chunk if i < 4 else len(text)])
            for i in range(5)
        ]
        first_4, extra = _split_extra_sections(base[4][1])
        if extra:
            base[4] = (NOTE_SECTIONS[4][0], first_4 or "(No content for this section.)")
            base.extend(extra)
        return base

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

    result = [(NOTE_SECTIONS[i][0], note_bodies[i]) for i in range(len(NOTE_SECTIONS))]
    first_part_4, extra_sections = _split_extra_sections(note_bodies[4])
    if extra_sections:
        result[-1] = (NOTE_SECTIONS[4][0], first_part_4 or "(No content for this section.)")
        result.extend(extra_sections)
    return result


def split_research_into_sections(text):
    """Split full research text into 5 Notion-style sections plus any 8., 9., ... user-appended sections. Returns list of (section_title, body)."""
    if not (text or "").strip():
        return [(NOTE_SECTIONS[i][0], "") for i in range(len(NOTE_SECTIONS))]
    return _split_research(text)


def get_next_section_number(research_text):
    """
    Return the next section number (8, 9, ...) to use when appending extended research.
    Sections 6 and 7 are reserved for the template (6-Final). Scans for existing "N." or "N:"
    with N>=8 and returns max+1, or 8 if none.
    """
    import re
    if not (research_text or "").strip():
        return 8
    max_n = 7
    for m in re.finditer(r"(?m)^\s*(\d+)[.:]\s+", research_text):
        n = int(m.group(1))
        if n >= 8:
            max_n = max(max_n, n)
    return max_n + 1


def format_appended_section(content, title, section_number):
    """Format extended research for append: 'N. TITLE' then content so it becomes a new section and shows in expanders."""
    return f"{section_number}. {title}\n\n{content.strip()}"


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

            with stream_slot():
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


def research_command_ai(domain, current_research_text, user_command):
    """
    Run an AI command on the research document: extend, add section, or revise.
    Returns ONLY the new content (the section or addition for the user's query).
    The UI then lets the user add this to the full research and save.
    """
    if not (user_command or "").strip() or not ANTHROPIC_API_KEY:
        return None
    current = (current_research_text or "").strip()
    # Send last 20k chars for context (style/structure) so we don't overload the prompt
    context = (current[-20000:] if len(current) > 20000 else current) if current else "(No existing research.)"
    prompt = f"""You are writing institutional-grade strategic research for {domain}.

EXISTING RESEARCH (for style/structure only — do not repeat this in your answer):
{context}

USER REQUEST:
{user_command.strip()}

Instructions:
- Answer ONLY the user's request: write the new section, the addition, or the revised snippet they asked for.
- Do NOT return the full document. Do NOT repeat existing content. Output only what should be added or the revised part.
- Use the same style: institutional, structured, pipe tables where appropriate. Same section formatting (===, ---). No markdown bold, no emojis.
- No preamble like "Here is the section". Output only the research content."""
    try:
        r = create_with_retry(
            model=RESEARCH_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        return (r.content[0].text or "").strip()
    except Exception as e:
        log.error("Research command AI failed: %s", e)
        return None


def get_research_brief(domain, research_text):
    """
    Generate a quick 5-line executive brief from full research.
    Used for the auto-strategize panel.
    """
    if not research_text or not ANTHROPIC_API_KEY:
        return None

    try:
        r = create_with_retry(
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