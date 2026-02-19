"""
ai_engine.py — AI strategist, memory layers (L2-L5), lead scoring, export

FIX LOG:
  - Fixed duplicate user message bug
  - Added auto_brief() for instant analysis on contact select
  - Added streaming support for chat
  - Smarter context assembly with research integration
"""

import re
import csv
import io
import logging
from datetime import datetime

import pytz
from anthropic import Anthropic

from config import (
    ANTHROPIC_API_KEY, AI_MODEL, AI_MAX_TOKENS, SUMMARY_INTERVAL,
    MAX_CONTEXT_MESSAGES, TIMEZONE_COUNTRIES, US_TZ,
)
from db_ops import (
    get_message_count, get_session_summary, save_session_summary,
    load_messages, get_memory_events, save_memory_event,
    get_global_insights, save_global_insight, get_session_actions,
    get_pipeline_stats, get_all_companies, get_user_sessions,
)
from drive_sync import build_drive_context, get_lh2_knowledge_context

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


# ── Build System Prompt (shared between strategist and auto-brief) ──

def _build_system_prompt(comp, ct, uid, sid, research_notes=None):
    """Build the full context system prompt."""
    summary, _ = get_session_summary(sid)
    summary_block = f"\n{summary}" if summary else "(No summary yet.)"

    events = get_memory_events(sid, min_imp=4)
    events_block = (
        "\n".join([
            f"- [{e['event_type']}] {e['summary']} (importance: {e['importance_score']}/10)"
            for e in events[:8]
        ]) or "(No key events yet.)"
    )

    ginsights = get_global_insights(uid, limit=8)
    global_block = (
        "\n".join([f"- {g['insight']} (conf: {g['confidence_score']:.1f})" for g in ginsights])
        or "(No global insights yet.)"
    )

    # Research context — from passed notes or extra_data
    research_context = ""
    overrides = (comp.get("extra_data") or {}).get("research_overrides") or {}
    if research_notes:
        for note in research_notes:
            title = note.get("title", "").strip() or "Untitled"
            ovr = overrides.get(title)
            if isinstance(ovr, str) and ovr.strip():
                research_context += f"\n--- {title} (LOCAL OVERRIDE) ---\n{ovr[:3000]}\n"
            else:
                research_context += f"\n--- {title} ---\n{(note.get('content') or '')[:3000]}\n"
    else:
        extra = comp.get("extra_data") or {}
        for note in extra.get("research_notes", []):
            title = note.get("title", "").strip() or "Untitled"
            ovr = overrides.get(title)
            if isinstance(ovr, str) and ovr.strip():
                research_context += f"\n--- {title} (LOCAL OVERRIDE) ---\n{ovr[:3000]}\n"
            else:
                research_context += f"\n--- {title} ---\n{(note.get('content') or '')[:3000]}\n"

    drive_ctx = build_drive_context(comp.get("name", ""), comp.get("domain", ""))
    lh2_knowledge = get_lh2_knowledge_context()

    actions = get_session_actions(sid)
    action_history = (
        "\n".join([
            f"- [{a['status']}] {a['action_type']} via {a['channel']}: {a['description'][:100]}"
            + (f" (scheduled: {a['scheduled_at']})" if a.get('scheduled_at') else "")
            + (f" → Response: {a['response_content'][:80]}" if a.get('response_content') else "")
            for a in actions[:15]
        ]) or "(No prior actions.)"
    )

    local_time, tz_str = get_local_time(ct.get("country", ""), ct.get("state", ""))
    score = compute_lead_score(comp, ct)

    system = f"""You are the LH2 Strategic Outreach Advisor for digital media partnerships.
You have layered memory — you remember everything about this target across the entire conversation, plus patterns from all past outreach.

== COMPANY PROFILE ==
Domain: {comp.get('domain','?')} | Type: {comp.get('publisher_type','?')} | Content: {comp.get('content_type','?')}
Monthly Visits: {comp.get('monthly_visits','?')} | Research: {comp.get('research_status','?')} | Score: {score}/100

== CONTACT PROFILE ==
Name: {ct.get('name','?')} | Role: {ct.get('role','?')} | Email: {ct.get('email','N/A')}
LinkedIn: {ct.get('linkedin','N/A')} | Location: {ct.get('city','')}, {ct.get('state','')} {ct.get('country','')}
Local Time: {local_time} ({tz_str or '?'}) | Status: {ct.get('status','not_contacted')}

== SESSION MEMORY ==
{summary_block}

== KEY EVENTS ==
{events_block}

== RESEARCH CONTEXT ==
{research_context if research_context else '(No research available.)'}

{"== DRIVE RESEARCH ==" + chr(10) + drive_ctx if drive_ctx else ""}
{"== LH2 / ACCELERATE / OFFERINGS (use this to position our value) ==\n" + lh2_knowledge[:12000] if lh2_knowledge else ""}

== INTERACTION HISTORY ==
{action_history}

== GLOBAL OUTREACH PATTERNS ==
{global_block}

== RULES ==
- Reference research findings and memory in every recommendation.
- Messages: human, professional, smart. No jargon. No filler.
- Specify exact send times with timezone.
- Emails: include subject line. LinkedIn requests: under 300 chars.
- Be decisive. State what to do, not what could be done.
- When suggesting actions, format: ACTION: [channel] [day, date, time TZ] — [description]"""

    return system


# ── Auto-Brief (triggered on contact select) ──────────────────

def auto_brief(comp, ct, uid, sid, research_notes=None):
    """
    Generate an instant strategic brief when a contact is selected.
    Returns a concise analysis with recommended next steps.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes)

    prompt = f"""Give me a quick strategic brief for reaching out to {ct.get('name','this contact')} at {comp.get('domain','this company')}.

Format your response exactly like this:
SITUATION: [2-3 sentences on what we know about this target]
CONTACT ASSESSMENT: [1-2 sentences on this specific person's role and influence]
RECOMMENDED NEXT MOVE: [Specific action with channel, timing, and angle]
KEY TALKING POINT: [The single strongest hook based on research]
RISK: [One thing to watch out for]"""

    try:
        r = _ai().messages.create(
            model=AI_MODEL, max_tokens=500,
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
        r = _ai().messages.create(
            model=AI_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": f"""Compress this outreach strategy conversation into a structured memory summary. Be concise but preserve all decisions.
TARGET: {comp.get('domain','?')} / {ct.get('name','?')}
CONVERSATION:
{convo}
Output format (under 400 words):
STRATEGIC DIRECTION: [1-2 sentences]
POSITIONING: [how we frame LH2]
KEY OBJECTIONS: [concerns]
TONE CALIBRATION: [style]
DECISIONS MADE: [specific choices]
CURRENT STATUS: [where we are]"""}],
        )
        save_session_summary(sid, r.content[0].text.strip(), cnt)
    except Exception as e:
        log.error("Summary update failed for session %s: %s", sid, e)


# ── L3: Episodic Extraction ──────────────────────────────────

def extract_episodic_memories(sid, ai_resp, comp):
    try:
        r = _ai().messages.create(
            model=AI_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": f"""From this AI strategist response, extract 0-3 important episodic events worth remembering for future interactions with {comp.get('domain','this target')}.
RESPONSE:
{ai_resp[:2000]}
For each event, output one line: EVENT|type|summary|importance_score(1-10)
Types: decision, objection, insight, rejection, positive_signal, budget_info, timing_info, contact_change
If nothing noteworthy, output NONE."""}],
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
        r = _ai().messages.create(
            model=AI_MODEL, max_tokens=200,
            messages=[{"role": "user", "content": f"""Generate ONE concise strategic insight (1-2 sentences) from this outreach outcome.
Target: {comp.get('domain','?')} ({comp.get('publisher_type','?')}, {comp.get('content_type','?')})
Action: {action.get('action_type','')} via {action.get('channel','')}
Outcome: {action.get('status','')}
Description: {action.get('description','')}
Return ONLY the insight. Make it generalizable."""}],
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
        r = _ai().messages.create(
            model=AI_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": f"""You are helping improve strategic research for LH2 outreach.

CURRENT RESEARCH CONTEXT:
{ctx}

USER REQUEST:
{user_message.strip()}

Respond in one of these ways:
1. If they say something is missing or suggest an addition: provide concrete suggested text to add (or a short section). Be specific and use the same institutional tone as the research.
2. If they ask to "research" a topic or person (without a URL): suggest what to add or how to find it; or draft a short research paragraph if you can infer from context.
3. If they only pasted an email: suggest a one-line note like "Contact to add: [email] — consider looking up name/role on LinkedIn."

Keep the response focused and usable. If the request is unclear, ask one short clarifying question."""}],
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
        r = _ai().messages.create(
            model=AI_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": f"""Turn this company research into a short context summary ({max_paragraphs} short paragraphs max). Keep it factual and useful for outreach. No preamble.

{ctx}"""}],
        )
        return (r.content[0].text or "").strip()
    except Exception as e:
        log.error("Summarize research failed: %s", e)
        return None


def research_person_for_company(comp, research_notes, linkedin_url=None, email=None, notion_lead=None):
    """
    Run AI research on a person (SPOC) in the context of this company.
    Uses company research notes (shortened to context), optional Notion lead data, and/or LinkedIn/email.
    Returns a short markdown SPOC brief (2–4 paragraphs) to present alongside company notes.
    """
    domain = comp.get("domain") or "this company"
    company_line = f"Company: {domain} | {comp.get('publisher_type') or '—'} | {comp.get('content_type') or '—'}"
    # Shortened company context from notes (cap total so we leave room for response)
    ctx_parts = [company_line]
    if research_notes:
        for n in research_notes[:6]:
            title = n.get("title") or "Section"
            content = (n.get("content") or "")[:1800]
            if content.strip():
                ctx_parts.append(f"\n--- {title} ---\n{content}")
    company_context = "\n".join(ctx_parts) if len(ctx_parts) > 1 else company_line + "\n(No detailed research notes yet.)"

    person_desc = []
    if notion_lead:
        person_desc.append(f"Notion record: {notion_lead.get('name') or '—'} | {notion_lead.get('email') or '—'} | {notion_lead.get('role') or '—'} | LinkedIn: {notion_lead.get('linkedin') or '—'} | {notion_lead.get('city') or ''}, {notion_lead.get('state') or ''} {notion_lead.get('country') or ''}.")
    if linkedin_url:
        person_desc.append(f"LinkedIn URL provided: {linkedin_url}")
    if email:
        person_desc.append(f"Email provided: {email}")
    if not person_desc:
        person_desc.append("No specific contact identifier; produce a generic 'key contact' brief for this company.")

    person_block = " ".join(person_desc)
    lh2_knowledge = get_lh2_knowledge_context()
    lh2_block = ""
    if lh2_knowledge:
        lh2_block = f"""
WHAT LH2 / ACCELERATE / OUR OFFERINGS ARE (use this to recommend the best-fit person and angle):
{lh2_knowledge[:10000]}
"""
    prompt = f"""You are producing a short SPOC (single point of contact) research brief for LH2 outreach.
{lh2_block}
COMPANY CONTEXT (shortened from existing research):
{company_context[:12000]}

CONTACT IDENTIFIER / DATA:
{person_block}

Produce a concise research brief (2–4 short paragraphs) that:
1. Summarizes who this person is in the context of the company (role, relevance, authority).
2. Connects them to the company research above (power mapping, stakeholder section, or execution gaps).
3. Suggests how to approach them and one concrete talking point or wedge.
4. If we have no real data on the person (only URL/email), say so briefly and recommend what to find (e.g. name, title, recent activity) and how they likely fit the company.

Tone: institutional, concise. No filler. Output only the brief, no preamble."""

    try:
        r = _ai().messages.create(
            model=AI_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return (r.content[0].text or "").strip()
    except Exception as e:
        log.error("research_person_for_company failed: %s", e)
        return None


def get_advisor_system_prompt(uid):
    """Build system prompt for the Advisor bot: LH2 knowledge + pipeline + companies + recent sessions."""
    lh2 = get_lh2_knowledge_context()
    stats = get_pipeline_stats()
    companies = get_all_companies()
    sessions = get_user_sessions(uid)[:20]
    companies_blob = "\n".join([
        f"- {c.get('domain', '?')} (type: {c.get('publisher_type', '—')}, research: {c.get('research_status', '—')})"
        for c in companies[:50]
    ]) or "(No companies yet.)"
    sessions_blob = "\n".join([
        f"- {s.get('company_domain', '?')} / {s.get('contact_name', '?')} (messages: {get_message_count(s['id'])})"
        for s in sessions
    ]) or "(No recent sessions.)"
    return f"""You are the LH2 Command Center Advisor. You understand what the user is doing (outreach, research, pipeline) and give targeted advice. You remember the conversation and suggest next steps: which companies to reach out to, which plans to run, and how to prioritize.

== WHAT LH2 / ACCELERATE / OUR OFFERINGS ARE ==
{lh2[:14000] if lh2 else '(No Drive context loaded. Suggest they link a Drive folder with LH2/Accelerate materials.)'}

== PIPELINE SNAPSHOT ==
Companies: {len(companies)} | Active outreach: {stats.get('outreach_sent', 0) + stats.get('awaiting_reply', 0)} | Meetings: {stats.get('meeting_booked', 0)} | Partnerships: {stats.get('partnership_active', 0)}

== COMPANIES IN DB ==
{companies_blob}

== RECENT SESSIONS (what they are working on) ==
{sessions_blob}

== RULES ==
- Use the above to suggest specific companies to prioritize and why; suggest outreach plans when asked.
- Retain context from the chat: reference what they said and what they're focused on.
- Be concise and actionable. When they ask for advice, give clear next steps.
- If they ask "who should I reach out to" or "what companies", use pipeline and company list to recommend.
- No filler or preamble."""


def advisor_chat(uid, user_message, history):
    """
    One turn of Advisor chat. history is list of {"role": "user"|"assistant", "content": str}.
    Returns assistant reply text.
    """
    system = get_advisor_system_prompt(uid)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-24:]]
    messages.append({"role": "user", "content": user_message})
    try:
        r = _ai().messages.create(
            model=AI_MODEL,
            max_tokens=2000,
            system=system,
            messages=messages,
        )
        return (r.content[0].text or "").strip()
    except Exception as e:
        log.error("advisor_chat failed: %s", e)
        return f"Sorry, I couldn’t process that: {e}"



def summarize_advisor_title(messages):
    """Generate a concise 3-5 word title for an advisor conversation."""
    if not messages:
        return "New Conversation"
    
    first_exchange = []
    for m in messages[:4]:
        first_exchange.append(f"{m['role']}: {m['content'][:200]}")
    
    context = "\n".join(first_exchange)
    
    try:
        r = _ai().messages.create(
            model=AI_MODEL,
            max_tokens=20,
            system="Generate a concise 3-5 word title summarizing the main topic of this conversation. Return ONLY the title, no quotes or explanation.",
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

    for g in get_global_insights(uid, limit=20):
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

def get_strategist_response(comp, ct, messages, user_msg, uid, sid, research_notes=None):
    """
    FIX: The user_msg is already in `messages` (appended by the caller).
    We do NOT append again to avoid sending it twice.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes)

    # Add retrieved memories for this specific query
    relevant = retrieve_relevant_memories(sid, uid, user_msg)
    if relevant:
        mem_block = "\n".join([f"- [{m['type']}] {m['text']}" for m in relevant])
        system += f"\n\n== RELEVANT RETRIEVED MEMORIES ==\n{mem_block}"

    api_msgs = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-MAX_CONTEXT_MESSAGES:]
    ]

    try:
        r = _ai().messages.create(
            model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
            system=system, messages=api_msgs,
        )
        return r.content[0].text
    except Exception as e:
        log.error("Strategist API call failed: %s", e)
        return f"Error: {e}"


def get_strategist_response_stream(comp, ct, messages, user_msg, uid, sid, research_notes=None):
    """
    Streaming version of the strategist. Yields text chunks.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes)

    relevant = retrieve_relevant_memories(sid, uid, user_msg)
    if relevant:
        mem_block = "\n".join([f"- [{m['type']}] {m['text']}" for m in relevant])
        system += f"\n\n== RELEVANT RETRIEVED MEMORIES ==\n{mem_block}"

    api_msgs = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-MAX_CONTEXT_MESSAGES:]
    ]

    try:
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

def generate_outreach_plan(comp, ct, uid, sid, sender_name="", research_notes=None):
    """
    Generate a structured outreach sequence.
    Returns list of dicts: [{step, channel, day_offset, description, content_draft, timing_note}, ...]
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes)

    sender_line = f"The sender is {sender_name}." if sender_name else ""

    prompt = f"""Create a structured outreach sequence for {ct.get('name','this contact')} at {comp.get('domain','this company')}.
{sender_line}

You MUST return EXACTLY this format — one block per step, separated by blank lines:

STEP|1|LinkedIn|Day 0|[description of this touch]|[timing note e.g. "Tuesday 9am EST"]
CONTENT: [exact message text for LinkedIn connection request, under 300 chars]

STEP|2|Email|Day 2|[description]|[timing note]
CONTENT: Subject: [subject line]
[full email body]

STEP|3|Email|Day 5|[description]|[timing note]
CONTENT: Subject: [subject line]
[full email body]

STEP|4|LinkedIn|Day 8|[description]|[timing note]
CONTENT: [LinkedIn follow-up message]

Rules:
- 4-5 steps. Mix LinkedIn and Email.
- Step 1 is ALWAYS a LinkedIn connection request (under 300 chars).
- Each subsequent step builds on the previous. Reference research.
- Include exact message content for every step.
- Messages must be human, smart, and specific to this target. No jargon. No filler.
- Timing notes should account for their timezone and optimal send windows.
- If email, always include Subject: line at the start of CONTENT."""

    try:
        r = _ai().messages.create(
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


def replan_on_response(comp, ct, uid, sid, completed_action, response_text, remaining_actions, research_notes=None):
    """
    When a response is received, replan remaining sequence steps.
    Returns updated action dicts for remaining steps.
    """
    system = _build_system_prompt(comp, ct, uid, sid, research_notes)

    remaining_desc = "\n".join([
        f"  Step {a['sequence_step']}: {a['channel']} — {a['description'][:100]}"
        for a in remaining_actions
    ])

    prompt = f"""A response was received in our outreach sequence to {ct.get('name','?')} at {comp.get('domain','?')}.

COMPLETED ACTION:
  Channel: {completed_action.get('channel','')}
  What we sent: {completed_action.get('content_sent','')[:500]}
  Response received: {response_text}

REMAINING PLANNED STEPS:
{remaining_desc}

Based on this response, should we:
1. Continue with the remaining steps (adjusted)?
2. Skip some steps and escalate?
3. Change the approach entirely?

For each remaining step, provide updated guidance in this format:
REVISED|[step_number]|[keep/skip/modify]|[new description if modified]|[new content if modified]

Then provide a brief STRATEGY NOTE explaining your reasoning."""

    try:
        r = _ai().messages.create(
            model=AI_MODEL, max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text
    except Exception as e:
        log.error("Replan failed: %s", e)
        return None