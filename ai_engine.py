"""
ai_engine.py — AI strategist, memory layers (L2-L5), lead scoring, export

FIX LOG:
  - Fixed duplicate user message bug
  - Added auto_brief() for instant analysis on contact select
  - Added streaming support for chat
  - Smarter context assembly with research integration
  - Optimized all AI prompts for clarity and token efficiency
  - Bumped max_tokens on constrained outputs
"""

import re
import csv
import io
import logging
from datetime import datetime

import pytz
from anthropic import Anthropic

from anthropic_limiter import create_with_retry, stream_slot
from config import (
    ANTHROPIC_API_KEY, AI_MODEL, AI_MAX_TOKENS, SUMMARY_INTERVAL,
    MAX_CONTEXT_MESSAGES, TIMEZONE_COUNTRIES, US_TZ,
)
from db_ops import (
    get_message_count, get_session_summary, save_session_summary,
    load_messages, get_memory_events, save_memory_event,
    get_global_insights, get_all_global_insights, save_global_insight, get_session_actions,
    get_pipeline_stats, get_all_companies, get_all_sessions,
)
from drive_sync import build_drive_context, get_lh2_knowledge_context, get_context_folder_files

log = logging.getLogger(__name__)


# ── AI Client ─────────────────────────────────────────────────

def _ai():
    return Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Timezone Helpers ──────────────────────────────────────────

def get_local_time(country, state=None):
    c = (country or "").lower().strip()
    s = (state or "").lower().strip()
    tz_str = None
    if any(x in c for x in ["us", "united states", "america"]):
        tz_str = "US/Eastern"
        for zone, states in US_TZ.items():
            if s in states:
                tz_str = zone
                break
    else:
        for key, tz in TIMEZONE_COUNTRIES.items():
            if key in c:
                tz_str = tz
                break
    if not tz_str:
        return "—", None
    try:
        tz = pytz.timezone(tz_str)
        return datetime.now(tz).strftime("%I:%M %p %Z"), tz_str
    except Exception:
        return "—", None


# ── Lead Scoring ──────────────────────────────────────────────

def compute_lead_score(comp, ct=None):
    score = 0
    v = comp.get("monthly_visits") or 0
    if v >= 10_000_000:
        score += 30
    elif v >= 1_000_000:
        score += 25
    elif v >= 500_000:
        score += 20
    elif v >= 100_000:
        score += 15
    elif v > 0:
        score += 10

    if comp.get("research_status") == "Completed":
        score += 25
    elif comp.get("research_status") == "In Progress":
        score += 10

    extra = comp.get("extra_data") or {}
    score += min(len(extra.get("research_notes", [])) * 5, 20)

    if ct:
        if ct.get("email"):
            score += 10
        if ct.get("linkedin"):
            score += 5

    pt = (comp.get("publisher_type") or "").lower()
    if "niche" in pt or "independent" in pt:
        score += 10
    elif "digital media" in pt:
        score += 8

    return min(score, 100)


# ── Product context helper ─────────────────────────────────────────

def _get_product_context_block(product_key: str) -> str:
    """Return the PRODUCT FOCUS block for the given product_key, or empty string."""
    if not product_key:
        return ""
    try:
        from product_profiles import format_product_context_block
        return format_product_context_block(product_key)
    except Exception as e:
        log.warning("Failed to load product profile '%s': %s", product_key, e)
        return ""


# ── Build System Prompt (shared between strategist and auto-brief) ──

def _build_system_prompt(comp, ct, uid, sid, research_notes=None, sender_email=None, product_key=None, recipient_research=None):
    """Build the full context system prompt. If sender_email provided, includes training docs and sender profile.
    If product_key provided ('accelerate' or 'mediaos'), injects focused product pitch context.
    If recipient_research provided, includes social research on the recipient."""
    summary, _ = get_session_summary(sid)
    summary_block = f"\n{summary}" if summary else "(None yet.)"

    events = get_memory_events(sid, min_imp=4)
    events_block = (
        "\n".join([
            f"- [{e['event_type']}] {e['summary']} (imp: {e['importance_score']}/10)"
            for e in events[:8]
        ]) or "(None yet.)"
    )

    ginsights = get_all_global_insights(limit=8)
    global_block = (
        "\n".join([f"- {g['insight']} (conf: {g['confidence_score']:.1f})" for g in ginsights])
        or "(None yet.)"
    )

    # Research context — prefer Command Center research_full when set; else Notion notes/overrides
    research_context = ""
    extra = comp.get("extra_data") or {}
    if extra.get("research_full"):
        raw = (extra["research_full"] or "")[:18000]
        try:
            from ui_helpers import is_html_content, html_to_plain_text
            if is_html_content(raw):
                raw = html_to_plain_text(raw, max_length=18000)
        except Exception:
            pass
        research_context = "\n--- Research Document ---\n" + raw
    if not research_context:
        overrides = extra.get("research_overrides") or {}
        if research_notes:
            for note in research_notes:
                title = note.get("title", "").strip() or "Untitled"
                ovr = overrides.get(title)
                if isinstance(ovr, str) and ovr.strip():
                    research_context += f"\n--- {title} (LOCAL OVERRIDE) ---\n{ovr[:3000]}\n"
                else:
                    research_context += f"\n--- {title} ---\n{(note.get('content') or '')[:3000]}\n"
        if not research_context:
            for note in extra.get("research_notes", []):
                title = note.get("title", "").strip() or "Untitled"
                ovr = overrides.get(title)
                if isinstance(ovr, str) and ovr.strip():
                    research_context += f"\n--- {title} (LOCAL OVERRIDE) ---\n{ovr[:3000]}\n"
                else:
                    research_context += f"\n--- {title} ---\n{(note.get('content') or '')[:3000]}\n"

    drive_ctx = build_drive_context(comp.get("name", ""), comp.get("domain", ""))
    lh2_knowledge = get_lh2_knowledge_context()
    
    # Load sender-specific context if sender_email is provided
    sender_context = ""
    if sender_email:
        from sender_profiles import get_sender_profile
        sender_profile = get_sender_profile(sender_email)
        if sender_profile:
            sender_context = f"""
== SENDER CONTEXT ==
You are drafting on behalf of: {sender_profile.get('name', 'Unknown')} ({sender_profile.get('title', 'LH2 Holdings')})
Voice type: {sender_profile.get('voice_type', 'professional')}
Voice notes: {sender_profile.get('voice_notes', '')}

Signature to use:
{sender_profile.get('signature', '')}
"""
        
        # Load training docs from Context folder (includes outreach guidelines)
        training_docs = get_context_folder_files()
        if training_docs:
            sender_context += f"""
== OUTREACH TRAINING & GUIDELINES ==
{training_docs}
"""

    actions = get_session_actions(sid)
    action_history = (
        "\n".join([
            f"- [{a['status']}] {a['action_type']} via {a['channel']}: {a['description'][:100]}"
            + (f" (scheduled: {a['scheduled_at']})" if a.get('scheduled_at') else "")
            + (f" → Response: {a['response_content'][:80]}" if a.get('response_content') else "")
            for a in actions[:15]
        ]) or "(None.)"
    )

    local_time, tz_str = get_local_time(ct.get("country", ""), ct.get("state", ""))
    score = compute_lead_score(comp, ct)
    
    # Build recipient research context if available
    recipient_context = ""
    if recipient_research and recipient_research.get("status") == "completed":
        brief = recipient_research.get("brief", "")
        linkedin_data = recipient_research.get("linkedin", {})
        twitter_data = recipient_research.get("twitter", {})
        blog_posts = recipient_research.get("blog_posts", [])
        
        recipient_context = "\n== RECIPIENT SOCIAL RESEARCH ==\n"
        if brief:
            recipient_context += f"Brief: {brief}\n\n"
        
        if linkedin_data.get("recent_activity"):
            topics = ", ".join(linkedin_data.get("recent_activity", [])[:3])
            recipient_context += f"LinkedIn Activity: Recently engaged with {topics}.\n"
            if linkedin_data.get("professional_focus"):
                recipient_context += f"Professional Focus: {linkedin_data['professional_focus']}\n"
        
        if twitter_data.get("handle") and twitter_data.get("topics"):
            topics = ", ".join(twitter_data.get("topics", [])[:3])
            recipient_context += f"Twitter ({twitter_data['handle']}): Discusses {topics}.\n"
        
        if blog_posts:
            recipient_context += f"Recent Content: {blog_posts[0].get('title', 'Untitled')} ({blog_posts[0].get('topic', '')})"
            if len(blog_posts) > 1:
                recipient_context += f", {blog_posts[1].get('title', 'Untitled')}"
            recipient_context += "\n"
        
        recipient_context += "\nUSE THIS: Reference these topics/interests when drafting messages. Personalize based on their recent activity and professional focus.\n"

    system = f"""You are the LH2 Strategic Outreach Advisor — a digital media partnership strategist with persistent memory.

Your job: Give decisive, research-backed outreach recommendations. Every response must reference specific data from the context below.

== TARGET ==
Domain: {comp.get('domain','?')} | Type: {comp.get('publisher_type','?')} | Content: {comp.get('content_type','?')}
Monthly Visits: {comp.get('monthly_visits','?')} | Research Status: {comp.get('research_status','?')} | Lead Score: {score}/100

== CONTACT ==
Name: {ct.get('name','?')} | Role: {ct.get('role','?')} | Email: {ct.get('email','N/A')}
LinkedIn: {ct.get('linkedin','N/A')} | Location: {ct.get('city','')}, {ct.get('state','')} {ct.get('country','')}
Local Time: {local_time} ({tz_str or '?'}) | Outreach Status: {ct.get('status','not_contacted')}
{recipient_context}
{f'''== CHILD COMPANY RULE ==
This target is a child company/brand. Stick to the leadership of THIS child company (the target domain). Prioritize decision-makers at the child; do not default to parent-company leadership. All outreach and strategy must focus on the child's leadership.''' if comp.get('_child_company_focus') else ''}

== SESSION SUMMARY ==
{summary_block}

== KEY EVENTS ==
{events_block}

== RESEARCH ==
{research_context if research_context else '(None available.)'}

{"== DRIVE RESEARCH ==" + chr(10) + drive_ctx if drive_ctx else ""}
{"== LH2 OFFERINGS (reference when positioning value) ==\n" + lh2_knowledge[:12000] if lh2_knowledge else ""}
{_get_product_context_block(product_key)}
{sender_context}

== ACTION HISTORY ==
{action_history}

== GLOBAL PATTERNS ==
{global_block}

== OUTPUT RULES ==
1. Cite research: Name the specific finding, data point, or pattern you're basing each recommendation on.
2. YOUR STRATEGIC ADVICE (recommendations, action plans, analysis): Be directive. State what to do and why. Never say "you could" or "consider" — say "do this because."
3. MESSAGE DRAFTS (emails, LinkedIn messages written on behalf of the sender): Completely different register. Warm, human, professional — never pushy, never rude, never demanding. The sender sounds like a real person, not a sales machine.
   - Opener: ONE brief line acknowledging something specific about the contact or their work. Not flattery — a real observation.
   - Substance: Lead with a concrete data point or specific research finding. Numbers over vague claims.
   - Close: Low-pressure, generous, open-ended. E.g. "Happy to share more whenever it works for you." or "Let me know what questions come up." NEVER "Looking forward to hearing from you" or "Would you be open to a quick call?"
   - Forbidden in ALL message drafts: "I'd love to connect", "hope this finds you well", "excited to reach out", "synergy", "leverage", "at your earliest convenience", "would you be open to", "just following up", "I think we could add value".
4. CHANNEL INDEPENDENCE: Each channel is self-contained. A LinkedIn message must never reference an email sent, and an email must never reference a LinkedIn message sent. Every message reads as if it is the first time the person is hearing from us on that channel. LinkedIn follow-ups follow up on LinkedIn only. Email follow-ups follow up on email only.
5. Timing: Always specify exact day, date, time, and timezone (use contact's local TZ).
6. Emails: Always include "Subject:" line. LinkedIn requests: Hard limit 300 characters.
7. Action format: ACTION: [channel] [day, date, time TZ] — [what to send and why]"""

    return system


# ── Auto-Brief (triggered on contact select) ──────────────────

def auto_brief(comp, ct, uid, sid, research_notes=None, sender_email=None, product_key=None, recipient_research=None):
    """
    Generate an instant strategic brief when a contact is selected.
    Output includes a SPOC table (3-5 C-suite/founder only; 1-3 if company <100 employees)
    and standard brief sections. All in proper tabular/structured format.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key, recipient_research=recipient_research)
    domain = comp.get("domain") or "this company"

    prompt = f"""Produce a strategic outreach brief for {comp.get('domain','this company')}.

MANDATORY — SPOC TABLE (first section):
- Recommend SPOCs (single points of contact) for outreach. Include ONLY C-suite, founder, or equivalent economic decision-makers. Do NOT include editors, content leads, or mid-level roles unless they hold equity or P&L authority.
- If the company has fewer than 100 employees (or is clearly small/regional), recommend 1-3 SPOCs. Otherwise recommend 3-5 SPOCs.
- Output as a proper Markdown table with a header row, a separator row (e.g. |---||---||---|), then one data row per SPOC.
- Table columns: Name | Title | Why entry point | Preferred channel (LinkedIn/Email)
- Example format:
| Name | Title | Why entry point | Preferred channel |
|------|-------|-----------------|-------------------|
| Jane Doe | CEO & Founder | Controls P&L; final sign-off | LinkedIn |
| John Smith | COO | Owns operations; pod rollout | Email |

Then use this exact structure for the rest of the brief:

SITUATION: [2-3 sentences. What does this company do, why are they an LH2 target, and what's their current state based on research?]
CONTACT ASSESSMENT: [1-2 sentences. Who from the SPOC table is the best first contact and why.]
RECOMMENDED NEXT MOVE: [One specific action. State: channel, exact timing with timezone, and the angle/hook to use.]
KEY TALKING POINT: [One concrete, research-backed hook. Cite a specific finding that would resonate.]
RISK: [One specific obstacle: e.g. gatekeeper, competitor relationship, bad timing, missing info.]

Use proper Markdown tables only where specified. Do not use or cite Notion for contact or SPOC information — use only your knowledge of publicly available information. No "Unknown contact"."""

    try:
        r = create_with_retry(
            model=AI_MODEL, max_tokens=6000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text.strip()
    except Exception as e:
        log.error("Auto-brief failed: %s", e)
        return None


# ── L2: Rolling Summary ──────────────────────────────────────

def maybe_update_summary(sid, comp, ct):
    cnt = get_message_count(sid)
    _, last = get_session_summary(sid)
    if cnt - last < SUMMARY_INTERVAL:
        return
    try:
        msgs = load_messages(sid)
        if len(msgs) < 6:
            return
        convo = "\n".join(
            [f"[{m['role']}] {m['content'][:500]}" for m in msgs[-30:]]
        )
        r = create_with_retry(
            model=AI_MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": f"""Compress this outreach conversation into a structured memory document. Preserve every decision, commitment, objection, and status change. Drop small talk and repetition.

Target: {comp.get('domain','?')} | Contact: {ct.get('name','?')}

Conversation:
{convo}

Output exactly this structure (max 400 words total):

STRATEGIC DIRECTION: [1-2 sentences. What approach did we decide on and why?]
POSITIONING: [How are we framing LH2's value prop to this specific target?]
KEY OBJECTIONS: [List any concerns raised, anticipated blockers, or pushback — be specific.]
TONE CALIBRATION: [What communication style works for this contact? Formal/casual/technical/etc.]
DECISIONS MADE: [Every concrete choice: channels picked, messaging angles chosen, timing agreed, content approved.]
CURRENT STATUS: [Where does this outreach stand right now? What is the immediate next action?]"""}],
        )
        save_session_summary(sid, r.content[0].text.strip(), cnt)
    except Exception as e:
        log.error("Summary update failed for session %s: %s", sid, e)


# ── L3: Episodic Extraction ──────────────────────────────────

def extract_episodic_memories(sid, ai_resp, comp):
    try:
        r = create_with_retry(
            model=AI_MODEL, max_tokens=5000,
            messages=[{"role": "user", "content": f"""Extract 0-3 events from this strategist response about {comp.get('domain','this target')} that are worth storing for future outreach sessions.

Only extract events that would change how we approach this target next time — skip routine advice.

Response:
{ai_resp[:2000]}

Output format — one event per line, pipe-delimited:
EVENT|<type>|<one-sentence summary>|<importance 1-10>

Valid types: decision, objection, insight, rejection, positive_signal, budget_info, timing_info, contact_change

Importance guide: 1-3 = minor preference, 4-6 = tactical detail, 7-8 = strategic shift, 9-10 = deal-changing.

If nothing worth storing, output exactly: NONE

No other text."""}],
        )
        text = r.content[0].text.strip()
        if text == "NONE":
            return
        for line in text.split("\n"):
            parts = line.strip().split("|")
            if len(parts) == 4 and parts[0] == "EVENT":
                try:
                    sc = int(parts[3].strip())
                except ValueError:
                    sc = 5
                save_memory_event(sid, parts[1].strip(), parts[2].strip(), min(sc, 10))
    except Exception as e:
        log.error("Episodic extraction failed for session %s: %s", sid, e)


# ── L4: Insight from Outcome ─────────────────────────────────

def generate_insight_from_outcome(uid, sid, action, comp):
    try:
        r = create_with_retry(
            model=AI_MODEL, max_tokens=2500,
            messages=[{"role": "user", "content": f"""Derive one reusable outreach lesson (1-2 sentences) from this outcome. The insight must apply beyond this specific target — it should help with similar companies.

Target: {comp.get('domain','?')} ({comp.get('publisher_type','?')}, {comp.get('content_type','?')})
Action: {action.get('action_type','')} via {action.get('channel','')}
Result: {action.get('status','')}
Details: {action.get('description','')}

Output only the insight sentence(s). No preamble, no labels."""}],
        )
        ins = r.content[0].text.strip()
        save_global_insight(uid, ins, 0.6)
        return ins
    except Exception as e:
        log.error("Insight generation failed: %s", e)
        return None


# ── Intelligence request (research suggestions / what's missing) ──

def intelligence_request(comp, research_notes, user_message):
    """
    User says something is missing, suggests an addition, or asks for research.
    Returns a markdown string: suggested text or guidance. research_notes: list of {title, content}.
    """
    if not user_message or not user_message.strip():
        return None
    ctx = f"Company: {comp.get('domain','?')} | {comp.get('publisher_type','')} | {comp.get('content_type','')}\n"
    if research_notes:
        for n in research_notes[:8]:
            ctx += f"\n--- {n.get('title','')} ---\n{(n.get('content') or '')[:2000]}\n"
    else:
        ctx += "\n(No research sections loaded yet.)\n"
    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=15000,
            messages=[{"role": "user", "content": f"""You are augmenting strategic research for LH2 outreach. Match the institutional tone of existing research sections.

Existing research:
{ctx}

User request:
{user_message.strip()}

Respond based on the request type:

A) "Something is missing" / suggested addition → Write the exact text to insert. Use the same tone and depth as the existing sections. Be specific — include names, data points, and sources where possible. For leads or contacts, use only your knowledge of publicly available information (LinkedIn, company pages, press). Completely ignore any contact details in Notion — do not reference the user's Notion database or contact lists.

B) "Research this topic/person" (no URL provided) → Draft a 1-2 paragraph research section based on what you can infer from context. Flag what you're inferring vs. what needs verification. For person/lead suggestions, base recommendations only on publicly findable information; do not use Notion or contact lists.

C) Pasted email address only → Output: "Contact to add: [email] — look up name, title, and role via web/LinkedIn search before adding to SPOC list."

If the request is ambiguous, ask one specific clarifying question. No preamble."""}],
        )
        return (r.content[0].text or "").strip()
    except Exception as e:
        log.error("Intelligence request failed: %s", e)
        return None


def summarize_research_to_context(comp, research_notes, max_paragraphs=3):
    """
    Shorten company research notes into a few paragraphs of context.
    Returns markdown string or None.
    """
    if not research_notes:
        return None
    ctx = f"Company: {comp.get('domain') or '?'} | {comp.get('publisher_type') or '—'} | {comp.get('content_type') or '—'}\n\n"
    for n in research_notes[:6]:
        ctx += f"--- {n.get('title') or 'Section'} ---\n{(n.get('content') or '')[:2500]}\n\n"
    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=10000,
            messages=[{"role": "user", "content": f"""Condense this company research into exactly {max_paragraphs} short paragraphs. Keep it factual — include specific names, numbers, and findings that are useful for planning outreach. Drop background fluff.

Start directly with the summary. No preamble or labels.

{ctx}"""}],
        )
        return (r.content[0].text or "").strip()
    except Exception as e:
        log.error("Summarize research failed: %s", e)
        return None


def research_person_for_company(comp, research_notes, linkedin_url=None, email=None, person_name=None, web_search_results=None):
    """
    Run AI research on a person (SPOC) in the context of this company.
    Uses Anthropic (Claude) only — no external search API, no Notion.
    All contact/SPOC information must come from the model's knowledge of
    publicly available information. Ignore any contact details in Notion.
    Returns a short markdown SPOC brief (2–4 paragraphs).
    """
    domain = comp.get("domain") or "this company"
    company_line = f"Company: {domain} | {comp.get('publisher_type') or '—'} | {comp.get('content_type') or '—'}"
    ctx_parts = [company_line]
    if research_notes:
        for n in research_notes[:6]:
            title = n.get("title") or "Section"
            content = (n.get("content") or "")[:1800]
            if content.strip():
                ctx_parts.append(f"\n--- {title} ---\n{content}")
    company_context = "\n".join(ctx_parts) if len(ctx_parts) > 1 else company_line + "\n(No detailed research notes yet.)"

    person_desc = []
    if person_name and (person_name or "").strip():
        person_desc.append(f"Person to look up: {person_name.strip()}")
    if linkedin_url:
        person_desc.append(f"LinkedIn URL: {linkedin_url}")
    if email:
        person_desc.append(f"Email: {email}")
    if not person_desc:
        person_desc.append("No contact identifier provided. Produce a generic key-contact brief for this company.")

    person_block = " ".join(person_desc)
    web_block = ""
    if web_search_results and web_search_results.strip():
        web_block = f"""
EXTERNAL CONTEXT (use for the person's name, role, and public statements):
{web_search_results[:10000]}
"""
    research_instruction = (
        " Using your knowledge of publicly available information (LinkedIn, company sites, press, professional profiles), "
        "research this person. State: full name, job title/role at the company, decision-making authority, and any public statements or inclinations you can infer. "
        "Completely ignore any contact details stored in Notion — do not use or reference Notion for contact or SPOC information. "
        "Do not say 'Unknown contact' or 'no SPOC identified'; if you have no information, say what would be needed to find more. "
        "Do not invent names or roles from domain registration or guesswork."
    )
    lh2_knowledge = get_lh2_knowledge_context()
    lh2_block = ""
    if lh2_knowledge:
        lh2_block = f"""
LH2 OFFERINGS (use to determine best-fit contact type and pitch angle):
{lh2_knowledge[:10000]}
"""
    prompt = f"""Write a SPOC (single point of contact) research brief for LH2 outreach. Research is done by you (Claude) using your knowledge of publicly available information only. Do not use Notion or any internal contact lists — ignore contact details from Notion completely.
{lh2_block}
COMPANY CONTEXT:
{company_context[:12000]}

CONTACT IDENTIFIER (what we're looking up):
{person_block}
{web_block}
{research_instruction}

Write 2-4 short paragraphs:

Paragraph 1 — WHO: Full name, job title/role at the company, decision-making authority, and relevance to LH2. Include any public statements, posts, or inclinations (quotes, interviews) you know of. Do not output "Unknown contact" or "no SPOC identified".

Paragraph 2 — CONNECTION TO RESEARCH: Map this person to the company's org structure, strategic priorities, or execution gaps. Who do they report to? What do they own?

Paragraph 3 — APPROACH: One specific outreach angle and one concrete talking point. Channel (LinkedIn vs Email) and why.

Paragraph 4 (only if little data) — GAPS: What we still need (LinkedIn URL, email, recent activity) and where to look.

Tone: institutional, factual. No filler. Output only the brief."""

    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=15000,
            messages=[{"role": "user", "content": prompt}],
        )
        return (r.content[0].text or "").strip()
    except Exception as e:
        log.error("research_person_for_company failed: %s", e)
        return None


def get_advisor_system_prompt(uid, sender_email=None):
    """Build system prompt for the Advisor bot: LH2 knowledge + pipeline + companies + recent sessions."""
    lh2 = get_lh2_knowledge_context()
    
    # Load sender-specific context if sender_email is provided
    sender_context = ""
    if sender_email:
        from sender_profiles import get_sender_profile
        from drive_sync import get_context_folder_files
        sender_profile = get_sender_profile(sender_email)
        if sender_profile:
            sender_context = f"""
== SENDER CONTEXT (for outreach drafts) ==
You are drafting on behalf of: {sender_profile.get('name', 'Unknown')} ({sender_profile.get('title', 'LH2 Holdings')})
Voice type: {sender_profile.get('voice_type', 'professional')}
Voice notes: {sender_profile.get('voice_notes', '')}

Signature to use:
{sender_profile.get('signature', '')}
"""
        
        # Load training docs from Context folder (includes outreach guidelines)
        training_docs = get_context_folder_files()
        if training_docs:
            sender_context += f"""
== OUTREACH TRAINING & GUIDELINES ==
{training_docs[:15000]}
"""
    
    stats = get_pipeline_stats()
    companies = get_all_companies()
    sessions = get_all_sessions()[:20]
    companies_blob = "\n".join([
        f"- {c.get('domain', '?')} (type: {c.get('publisher_type', '—')}, research: {c.get('research_status', '—')})"
        for c in companies[:50]
    ]) or "(Empty.)"
    sessions_blob = "\n".join([
        f"- {s.get('company_domain', '?')} / {s.get('contact_name', '?')} (msgs: {get_message_count(s['id'])})"
        for s in sessions
    ]) or "(None.)"
    return f"""You are the LH2 Command Center Advisor. You have full visibility into the user's pipeline, active sessions, and research status. Your job is to tell them exactly what to do next and why.

== LH2 OFFERINGS ==
{lh2[:14000] if lh2 else '(No Drive context loaded. Tell them to link a Drive folder with LH2/Accelerate materials.)'}
{sender_context}

== PIPELINE ==
Total companies: {len(companies)} | Active outreach: {stats.get('outreach_sent', 0)} | Meetings booked: {stats.get('meeting_booked', 0)} | Active partnerships: {stats.get('partnership_active', 0)}

== COMPANIES ==
{companies_blob}

== RECENT SESSIONS ==
{sessions_blob}

== RULES ==
1. Prioritization requests ("who should I reach out to"): Recommend 2-3 specific companies from the list with reasoning — cite research status, publisher type, visit volume, or pipeline gaps.
2. Conversational continuity: Reference what they said earlier in this chat. Don't repeat yourself.
3. Be direct: State what to do, in what order, and why. No "you might consider" — say "start with X because Y."
4. No filler, no preamble, no restating the question."""


def advisor_chat(uid, user_message, history, sender_email=None):
    """
    One turn of Advisor chat. history is list of {"role": "user"|"assistant", "content": str}.
    Returns assistant reply text.
    """
    system = get_advisor_system_prompt(uid, sender_email=sender_email)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-24:]]
    messages.append({"role": "user", "content": user_message})
    try:
        with stream_slot():
            client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=600.0)
            with client.messages.stream(
                model=AI_MODEL,
                max_tokens=25000,
                system=system,
                messages=messages,
            ) as stream:
                chunks = []
                for text in stream.text_stream:
                    chunks.append(text)
                result = ("".join(chunks) or "").strip()
                if not result:
                    final = stream.get_final_message()
                    if final and getattr(final, "content", None):
                        for block in final.content:
                            if getattr(block, "text", None):
                                result = block.text.strip()
                                break
        return result or ""
    except Exception as e:
        log.error("advisor_chat failed: %s", e)
        return f"Sorry, I couldn't process that: {e}"



def summarize_advisor_title(messages):
    """Generate a concise 3-5 word title for an advisor conversation."""
    if not messages:
        return "New Conversation"
    
    first_exchange = []
    for m in messages[:4]:
        first_exchange.append(f"{m['role']}: {m['content'][:200]}")
    
    context = "\n".join(first_exchange)
    
    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=200,
            system="Output a 3-5 word topic title. No quotes, no punctuation, no explanation.",
            messages=[{"role": "user", "content": context}],
        )
        title = (r.content[0].text or "").strip().strip('"').strip("'")
        return title if title else messages[0]["content"][:50]
    except Exception as e:
        log.error("summarize_advisor_title failed: %s", e)
        first_user_msg = next((m["content"] for m in messages if m["role"] == "user"), "Advisor Chat")
        return first_user_msg[:50] + ("..." if len(first_user_msg) > 50 else "")


_STOP_WORDS = {
    "what", "when", "where", "this", "that", "they", "their", "about",
    "should", "would", "could", "have", "with", "from", "been", "some",
    "more", "into", "also", "just", "like", "make", "want", "need",
    "will", "does", "than", "very", "most", "much",
}


def retrieve_relevant_memories(sid, uid, query, limit=5):
    kw = set(re.findall(r"\b[a-zA-Z]{4,}\b", query.lower())) - _STOP_WORDS
    if not kw:
        return []

    mems = []
    for e in get_memory_events(sid, min_imp=3):
        ew = set(re.findall(r"\b[a-zA-Z]{4,}\b", e["summary"].lower()))
        ol = len(kw & ew)
        if ol > 0:
            mems.append({"type": "episodic", "text": e["summary"], "score": ol + e["importance_score"] / 10})

    for g in get_all_global_insights(limit=20):
        gw = set(re.findall(r"\b[a-zA-Z]{4,}\b", g["insight"].lower()))
        ol = len(kw & gw)
        if ol > 0:
            mems.append({"type": "global", "text": g["insight"], "score": ol + g["confidence_score"]})

    mems.sort(key=lambda x: x["score"], reverse=True)
    return mems[:limit]


# ── Action Parsing ────────────────────────────────────────────

def parse_ai_actions(text):
    found = []
    for line in text.split("\n"):
        ll = line.lower().strip()
        if any(k in ll for k in ["send email", "send linkedin", "follow up",
                                   "schedule call", "send inmail", "action:"]):
            ch = "Email"
            if "linkedin" in ll or "inmail" in ll:
                ch = "LinkedIn"
            elif "call" in ll:
                ch = "Phone"
            tm = re.search(
                r"(\w+day)[,\s]+(\w+ \d{1,2})[,\s]*([\d:]+\s*[AP]M)",
                line, re.IGNORECASE,
            )
            sched = None
            if tm:
                try:
                    sched = datetime.strptime(
                        f"{tm.group(2)}, 2026 {tm.group(3)}", "%B %d, %Y %I:%M %p"
                    ).isoformat()
                except ValueError:
                    pass
            found.append({
                "type": "Outreach", "channel": ch,
                "description": line.strip()[:300],
                "scheduled_at": sched,
            })
    return found[:6]


# ── Main Strategist (with optional streaming) ─────────────────

def get_strategist_response(comp, ct, messages, user_msg, uid, sid, research_notes=None, sender_email=None, product_key=None):
    """
    FIX: The user_msg is already in `messages` (appended by the caller).
    We do NOT append again to avoid sending it twice.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key)

    # Add retrieved memories for this specific query
    relevant = retrieve_relevant_memories(sid, uid, user_msg)
    if relevant:
        mem_block = "\n".join([f"- [{m['type']}] {m['text']}" for m in relevant])
        system += f"\n\n== RECALLED MEMORIES (relevant to this query) ==\n{mem_block}"

    api_msgs = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-MAX_CONTEXT_MESSAGES:]
    ]

    try:
        r = create_with_retry(
            model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
            system=system, messages=api_msgs,
        )
        return r.content[0].text
    except Exception as e:
        log.error("Strategist API call failed: %s", e)
        return f"Error: {e}"


def get_strategist_response_stream(comp, ct, messages, user_msg, uid, sid, research_notes=None, sender_email=None, product_key=None, recipient_research=None):
    """
    Streaming version of the strategist. Yields text chunks.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key, recipient_research=recipient_research)

    relevant = retrieve_relevant_memories(sid, uid, user_msg)
    if relevant:
        mem_block = "\n".join([f"- [{m['type']}] {m['text']}" for m in relevant])
        system += f"\n\n== RECALLED MEMORIES (relevant to this query) ==\n{mem_block}"

    api_msgs = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-MAX_CONTEXT_MESSAGES:]
    ]

    try:
        with stream_slot():
            with _ai().messages.stream(
                model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
                system=system, messages=api_msgs,
            ) as stream:
                for text in stream.text_stream:
                    yield text
    except Exception as e:
        log.error("Strategist stream failed: %s", e)
        yield f"Error: {e}"


# ── CSV Export ────────────────────────────────────────────────

def export_session_csv(sid, domain):
    msgs = load_messages(sid)
    acts = get_session_actions(sid)
    evts = get_memory_events(sid, min_imp=1)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["LH2 Export"])
    w.writerow(["Domain", domain, "Exported", datetime.utcnow().isoformat()])
    w.writerow([])

    w.writerow(["== CONVERSATION =="])
    w.writerow(["Time", "Role", "Content"])
    for m in msgs:
        w.writerow([m["created_at"], m["role"], m["content"]])

    w.writerow([])
    w.writerow(["== ACTIONS =="])
    w.writerow(["Time", "Type", "Channel", "Description", "Status",
                 "Outcome", "Content Sent", "Response"])
    for a in acts:
        w.writerow([
            a["created_at"], a["action_type"], a["channel"],
            a["description"], a["status"], a.get("outcome", ""),
            a.get("content_sent", ""), a.get("response_content", ""),
        ])

    w.writerow([])
    w.writerow(["== MEMORY EVENTS =="])
    w.writerow(["Time", "Type", "Summary", "Importance"])
    for e in evts:
        w.writerow([e["created_at"], e["event_type"], e["summary"], e["importance_score"]])

    return out.getvalue()


# ── Outreach Sequence Generation ──────────────────────────────

def generate_outreach_plan(comp, ct, uid, sid, sender_name="", research_notes=None, sender_email=None, product_key=None, recipient_research=None):
    """
    Generate a structured outreach sequence.
    Returns list of dicts: [{step, channel, day_offset, description, content_draft, timing_note}, ...]
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key, recipient_research=recipient_research)

    sender_line = f"Sender name: {sender_name}" if sender_name else ""

    tz_info = get_local_time(ct.get("country", ""), ct.get("state", ""))

    prompt = f"""Build a 4-5 step outreach sequence for {ct.get('name','this contact')} at {comp.get('domain','this company')}.
{sender_line}
Contact timezone: {tz_info[1] or 'unknown'}

Output each step in this exact pipe-delimited format, separated by blank lines:

STEP|<number>|<LinkedIn or Email>|Day <offset>|<what this touch accomplishes>|<optimal send time with TZ>
CONTENT: <full message text>

== TONE GUIDANCE FOR DRAFTS ==
Every message draft must match the sender's voice (see Sender Context above).
General rules regardless of sender:
- Opener: One brief, specific observation about the contact or their company. Not flattery — a real finding.
- Body: Lead with a concrete data point. Numbers and specifics beat vague claims every time.
- Close: Warm, low-pressure, open-ended. Offer to share more — don't demand a meeting.
- NEVER use: "I'd love to connect", "hope this finds you well", "excited to reach out", "synergy", "leverage", "would you be open to", "at your earliest convenience", "just following up", "looking forward to hearing from you"

== EXAMPLES OF CORRECT TONE ==

GOOD LinkedIn (cold, 280 chars):
"Hi [Name] — noticed [Company] has been growing editorial output fast over the last year. At LH2 we've helped similar publishers 2–3x revenue with no upfront fees. Worth a look? Happy to share details."

BAD LinkedIn (do NOT write like this):
"Hi [Name], I came across your profile and I'd love to connect! I'm excited to share how LH2 Holdings could potentially add value to your organization. Would you be open to a quick call?"

GOOD Email opener (cold outreach):
"Hi [Name],

Reached out because [specific observation — e.g. traffic growth in a particular niche, a content gap, or a monetization pattern we've seen before].

At LH2 we partner with digital media brands to grow revenue — no upfront fees, we participate only in the incremental profits we generate. Founded by Upneet and Shobhit, both second-time founders. Across live partnerships we've averaged 2.5–3x revenue growth in year one.

Happy to share full details whenever it works for you. Let me know what questions come up."

BAD Email opener (do NOT write like this):
"Hi [Name],

Hope this email finds you well! I'm excited to reach out because I believe there could be great synergy between our organizations. I'd love to connect and explore how we could potentially add value to your business. Would you be open to a quick 15-minute call at your earliest convenience?"

== FORMAT ==

STEP|1|LinkedIn|Day 0|Initial connection with research hook|Tuesday 9:00 AM EST
CONTENT: Hi [name], [one specific research finding about their company]. At LH2 we've helped similar publishers [specific outcome]. Happy to share details if useful.

Constraints:
- Step 1: Always LinkedIn connection request. Hard limit 300 characters.
- Steps 2-5: Mix Email and LinkedIn. Each step must advance the conversation — no repetition.
- Every step must have complete message text after CONTENT: — no placeholders.
- Emails: First line of CONTENT must be "Subject: <subject line>", then body.
- Reference specific research findings from the context above in at least 2 steps.
- Timing: Use contact's timezone. Avoid Mondays and Fridays. Mornings (8-10 AM) for Email, lunch hours for LinkedIn.

== CHANNEL INDEPENDENCE RULE (strictly enforced) ==
Each channel (LinkedIn and Email) operates independently. Messages must NEVER reference activity on a different channel.
- A LinkedIn message must NEVER mention "my email", "I sent you an email", "as I mentioned in my message", or any reference to email contact.
- An email must NEVER mention "my LinkedIn message", "I connected with you on LinkedIn", "I reached out on LinkedIn", or any reference to LinkedIn contact.
- Every message must stand entirely on its own — written as if it is the first time this person is hearing from you on that channel.
- LinkedIn follow-ups build on prior LinkedIn interaction only. Email follow-ups build on prior email interaction only.
- The reader of any single message should have no idea they were also contacted on another platform."""

    try:
        r = create_with_retry(
            model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_sequence_response(r.content[0].text)
    except Exception as e:
        log.error("Sequence generation failed: %s", e)
        return None


def _parse_sequence_response(text):
    """Parse the AI's structured sequence response into action dicts."""
    steps = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("STEP|"):
            parts = line.split("|")
            if len(parts) >= 5:
                step_num = parts[1].strip()
                channel = parts[2].strip()
                day_info = parts[3].strip()
                desc = parts[4].strip() if len(parts) > 4 else ""
                timing = parts[5].strip() if len(parts) > 5 else ""

                # Parse day offset
                day_offset = 0
                import re as _re
                day_match = _re.search(r'Day\s*(\d+)', day_info)
                if day_match:
                    day_offset = int(day_match.group(1))

                # Collect content
                content_lines = []
                i += 1
                while i < len(lines):
                    cl = lines[i]
                    if cl.strip().startswith("CONTENT:"):
                        content_lines.append(cl.strip()[8:].strip())
                        i += 1
                        # Collect rest of content until next STEP or end
                        while i < len(lines) and not lines[i].strip().startswith("STEP|"):
                            if lines[i].strip():
                                content_lines.append(lines[i].rstrip())
                            elif content_lines:
                                content_lines.append("")
                            i += 1
                        break
                    elif cl.strip().startswith("STEP|"):
                        break
                    else:
                        i += 1

                content = "\n".join(content_lines).strip()

                # Calculate scheduled date
                from datetime import timedelta as _td
                sched_date = datetime.utcnow() + _td(days=day_offset)

                steps.append({
                    "step": int(step_num) if step_num.isdigit() else len(steps) + 1,
                    "channel": channel,
                    "day_offset": day_offset,
                    "description": desc,
                    "timing_note": timing,
                    "content_draft": content,
                    "action_type": "Outreach",
                    "status": "planned",
                    "scheduled_at": sched_date.isoformat(),
                    "content_sent": content,
                })
                continue
        i += 1

    return steps if steps else None


def replan_on_response(comp, ct, uid, sid, completed_action, response_text, remaining_actions, research_notes=None, sender_email=None, product_key=None):
    """
    When a response is received, replan remaining sequence steps.
    Returns updated action dicts for remaining steps.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key)

    remaining_desc = "\n".join([
        f"  Step {a['sequence_step']}: {a['channel']} — {a['description'][:100]}"
        for a in remaining_actions
    ])

    prompt = f"""We got a reply in our outreach to {ct.get('name','?')} at {comp.get('domain','?')}. Replan the remaining steps.

WHAT WE SENT:
Channel: {completed_action.get('channel','')}
Message: {completed_action.get('content_sent','')[:500]}

THEIR REPLY:
{response_text}

REMAINING PLANNED STEPS:
{remaining_desc}

For each remaining step, output one line:
REVISED|<step_number>|<keep/skip/modify>|<new description if modified>|<new message content if modified>

CHANNEL INDEPENDENCE RULE: Any revised message draft must NEVER reference activity on a different channel.
A LinkedIn message must not mention emails sent. An email must not mention LinkedIn messages sent.
Each message stands alone — as if it is the first time they are hearing from us on that channel.

Then:
STRATEGY NOTE: [1-2 sentences. What does their reply signal? Are we accelerating to a meeting, pivoting angle, or staying course? Why?]"""

    try:
        r = create_with_retry(
            model=AI_MODEL, max_tokens=20000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text
    except Exception as e:
        log.error("Replan failed: %s", e)
        return None


def refine_email_draft(current_draft, instruction, draft_type="email"):
    """
    Revise an email or LinkedIn draft based on user instruction. Returns the revised draft text only.
    draft_type: "email" or "linkedin". LinkedIn drafts are kept under 300 characters.
    """
    if not (current_draft or "").strip():
        return None
    is_linkedin = (draft_type or "").lower() == "linkedin"
    system = """You are an outreach copy editor. Apply the user's edit instruction to the draft.

TONE PRINCIPLES (always maintain these, even when applying edits):
- Warm, human, professional. Never pushy, never demanding, never rude.
- Opener: One specific observation — not flattery.
- Body: Numbers and specifics over vague claims.
- Close: Low-pressure. "Happy to share more whenever it works for you." NOT "Looking forward to hearing from you."
- NEVER introduce: "I'd love to connect", "hope this finds you well", "excited to reach out", "synergy", "leverage", "would you be open to", "at your earliest convenience", "just following up", "looking forward to hearing from you"

OUTPUT RULES — strictly enforced:
- Output ONLY the rewritten draft text. Nothing else.
- No labels ("Revised:", "Here's the updated version:").
- No quotes around the output.
- No explanations of what you changed.
- No markdown code blocks.
- The rewrite MUST be noticeably different from the original in structure or phrasing.
- If the draft has a "Subject:" line, keep a Subject line in the rewrite."""
    if is_linkedin:
        system += "\n- This is a LinkedIn connection request. HARD LIMIT: 300 characters. Cut ruthlessly if over."

    prompt = f"""Edit instruction: {instruction.strip()}

Original:
{current_draft.strip()}"""

    try:
        r = create_with_retry(
            model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        initial_raw = (r.content[0].text or "").strip()
        if not initial_raw:
            log.warning("Refine draft returned empty response")
            return None
        raw = initial_raw
        prefixes = (
            "REVISED DRAFT:", "Revised draft:", "Here is the revised draft:",
            "Here's the revised draft:", "Revised LinkedIn message:",
            "Here is the revised connection request:", "Here's the revised message:",
            "Revised message:", "Revised:", "Here is the revised:", "Here's the revised:",
        )
        raw_upper = raw.upper()
        for prefix in prefixes:
            if raw_upper.startswith(prefix.upper()):
                raw = raw[len(prefix):].lstrip(" :\n\t")
                break
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'", "`"):
            raw = raw[1:-1].strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines)
        out = raw.strip() or initial_raw
        orig_norm = " ".join((current_draft or "").split())
        out_norm = " ".join((out or "").split())
        if orig_norm and out_norm and orig_norm == out_norm:
            retry_prompt = f"""Your output was identical to the original. That violates instructions. Rewrite with clearly different structure and phrasing.

Edit instruction: {instruction.strip()}

Original:
{current_draft.strip()}"""

            r2 = create_with_retry(
                model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            raw2 = (r2.content[0].text or "").strip()
            if raw2:
                raw = raw2
                raw_upper = raw.upper()
                for prefix in prefixes:
                    if raw_upper.startswith(prefix.upper()):
                        raw = raw[len(prefix):].lstrip(" :\n\t")
                        break
                if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'", "`"):
                    raw = raw[1:-1].strip()
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    if lines[0].strip().startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    raw = "\n".join(lines)
                out = raw.strip() or raw2
            out_norm = " ".join((out or "").split())
            if orig_norm and out_norm and orig_norm == out_norm:
                log.warning("Refine draft unchanged after second attempt.")
                return initial_raw
        if is_linkedin and len(out) > 300:
            out = out[:297].rstrip() + "..."
        log.info(
            "Draft refined successfully. Original length: %s, New length: %s",
            len(current_draft or ""),
            len(out or ""),
        )
        return out
    except Exception as e:
        log.error("Refine draft failed: %s", e)
        return None