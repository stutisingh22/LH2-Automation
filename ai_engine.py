"""
ai_engine.py  -  AI strategist, memory layers (L2-L5), lead scoring, export

FIX LOG:
  - Fixed duplicate user message bug
  - Added auto_brief() for instant analysis on contact select
  - Added streaming support for chat
  - Smarter context assembly with research integration
  - Optimized all AI prompts for clarity and token efficiency
  - Bumped max_tokens on constrained outputs
"""

import re
import json
import csv
import io
import logging
from datetime import datetime

import pytz
from anthropic import Anthropic

from anthropic_limiter import create_with_retry, stream_slot
from config import (
    ANTHROPIC_API_KEY, AI_MODEL, AI_MAX_TOKENS, SUMMARY_INTERVAL,
    MAX_CONTEXT_MESSAGES, TIMEZONE_COUNTRIES, US_TZ, normalize_domain,
)
from db_ops import (
    get_message_count, get_session_summary, save_session_summary,
    load_messages, get_memory_events, save_memory_event,
    get_global_insights, get_all_global_insights, save_global_insight, get_session_actions,
    get_pipeline_stats, get_all_companies, get_all_sessions,
)
from drive_sync import build_drive_context, get_lh2_knowledge_context, get_context_folder_files
from services.outreach_strategy import build_strategy_profile, format_strategy_profile_block

log = logging.getLogger(__name__)
_PREF_PREFIX = "PREFMEM|"


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
        return "-", None
    try:
        tz = pytz.timezone(tz_str)
        return datetime.now(tz).strftime("%I:%M %p %Z"), tz_str
    except Exception:
        return "-", None


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


def _infer_intent_tags(text):
    t = (text or "").lower()
    tags = set()
    if any(k in t for k in ("acquire", "acquisition", "buyout", "term sheet", "due diligence")):
        tags.add("acquisition")
    if any(k in t for k in ("accelerate", "pod", "full-stack", "full stack", "rev share", "revenue share")):
        tags.add("accelerate")
    if "mediaos" in t:
        tags.add("mediaos")
    if any(k in t for k in ("tone", "voice", "wording", "style", "operator-to-operator", "operator to operator")):
        tags.add("tone")
    if any(k in t for k in ("email", "sequence", "subject", "cta", "follow-up", "follow up", "linkedin")):
        tags.add("messaging")
    return sorted(tags)


def _is_preference_instruction(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    pref_markers = (
        "always", "never", "must", "strictly", "do not", "don't", "should",
        "use this tone", "use tone", "my ask", "my approach", "keep context",
        "for acquisition", "for accelerate", "when", "if", "do this",
        "avoid", "no ", "please make sure",
    )
    return any(m in t for m in pref_markers) and len(t) >= 20


def _parse_pref_insight(insight_text):
    txt = (insight_text or "").strip()
    if not txt.startswith(_PREF_PREFIX):
        return None
    payload = txt[len(_PREF_PREFIX):].strip()
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _extract_preference_rationale(text):
    t = (text or "").strip()
    if not t:
        return ""
    m = re.search(r"(?i)\b(?:because|so that|so we can|so that we can)\b(.+)$", t)
    if m:
        return m.group(1).strip()[:220]
    return ""


def learn_user_preference(uid, user_text, assistant_text="", source="strategist"):
    """
    Persist user preference signals as tagged global insights.
    Additive only; deduped to avoid noise.
    """
    try:
        raw = (user_text or "").strip()
        if not _is_preference_instruction(raw):
            return False
        pref_text = re.sub(r"\s+", " ", raw)[:420]
        intent_tags = _infer_intent_tags(pref_text)
        record = {
            "kind": "user_preference",
            "source": source,
            "intent_tags": intent_tags or ["general"],
            "preference": pref_text,
            "rationale": _extract_preference_rationale(pref_text),
            "captured_at": datetime.utcnow().isoformat(),
        }
        existing = get_all_global_insights(limit=120)
        norm_pref = pref_text.lower()
        for row in existing:
            parsed = _parse_pref_insight(row.get("insight") or "")
            if not parsed:
                continue
            if (parsed.get("preference") or "").strip().lower() == norm_pref:
                return False
        save_global_insight(uid, _PREF_PREFIX + json.dumps(record, ensure_ascii=True), 0.75)
        return True
    except Exception as e:
        log.warning("learn_user_preference failed: %s", e)
        return False


def get_relevant_preference_memories(query, limit=5):
    q = (query or "").strip().lower()
    if not q:
        return []
    q_tags = set(_infer_intent_tags(q))
    q_words = set(re.findall(r"\b[a-z]{4,}\b", q))
    rows = get_all_global_insights(limit=120)
    ranked = []
    for row in rows:
        parsed = _parse_pref_insight(row.get("insight") or "")
        if not parsed:
            continue
        pref = (parsed.get("preference") or "").strip()
        if not pref:
            continue
        tags = set(parsed.get("intent_tags") or [])
        pref_words = set(re.findall(r"\b[a-z]{4,}\b", pref.lower()))
        overlap = len(q_words & pref_words)
        tag_overlap = len(q_tags & tags) if q_tags else 0
        if q_tags and tag_overlap == 0 and overlap == 0:
            continue
        score = float(row.get("confidence_score") or 0.5) + (0.8 * tag_overlap) + (0.15 * overlap)
        ranked.append({
            "preference": pref[:260],
            "rationale": (parsed.get("rationale") or "").strip()[:180],
            "tags": sorted(tags),
            "score": score,
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    out = []
    seen = set()
    for item in ranked:
        key = item["preference"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _build_preference_memory_block(query):
    prefs = get_relevant_preference_memories(query, limit=5)
    if not prefs:
        return "== USER PREFERENCE MEMORY ==\n(None captured yet for this ask.)"
    lines = []
    for p in prefs:
        why = f" | why: {p['rationale']}" if p.get("rationale") else ""
        lines.append(f"- [{', '.join(p.get('tags') or ['general'])}] {p['preference']}{why}")
    return "== USER PREFERENCE MEMORY ==\n" + "\n".join(lines)


def _extract_platform_presence(comp):
    """
    Read platform presence from company extra_data.
    Prefer platform_presence_analytics (fresh snapshot), fallback to platform_presence.
    """
    extra = (comp or {}).get("extra_data") or {}
    analytics = extra.get("platform_presence_analytics") or {}
    analytics_presence = analytics.get("platform_presence") or {}
    base_presence = extra.get("platform_presence") or {}

    def _coerce_bool(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"true", "yes", "1", "present"}:
                return True
            if s in {"false", "no", "0", "absent"}:
                return False
        return None

    out = {}
    source = "none"
    for platform in ("msn", "yahoo", "newsbreak"):
        val = _coerce_bool(analytics_presence.get(platform, None))
        if val is not None:
            out[platform] = val
            source = "platform_presence_analytics"
            continue
        val = _coerce_bool(base_presence.get(platform, None))
        if val is not None:
            out[platform] = val
            if source == "none":
                source = "platform_presence"
            continue
        # Fallback: many snapshots store presence as top-level booleans.
        val = _coerce_bool(analytics.get(f"{platform}_presence", None))
        if val is not None:
            out[platform] = val
            if source == "none":
                source = "platform_presence_analytics"
            continue
        # Fallback: infer from positioning evidence.
        p = (analytics.get("platform_positioning") or {}).get(platform) or {}
        if int(p.get("result_count") or 0) > 0:
            out[platform] = True
            if source == "none":
                source = "platform_positioning"
            continue
        out[platform] = None

    # Final fallback: use the same CSV-backed source as Companies navigation.
    # This is only used when no deterministic platform signal exists in extra_data.
    if all(v is None for v in out.values()):
        try:
            from tracker_sources import load_domain_to_platforms, get_presence_for_domain
            domain = (comp or {}).get("domain") or ""
            tracker_map = load_domain_to_platforms()
            csv_presence = get_presence_for_domain(domain, tracker_map) or {}
            for platform in ("msn", "yahoo", "newsbreak"):
                val = _coerce_bool(csv_presence.get(platform, None))
                if val is not None:
                    out[platform] = val
            if any(v is not None for v in out.values()):
                source = "tracker_sources_csv_fallback"
        except Exception:
            pass
    return out, source, analytics


def _build_platform_presence_block(comp):
    presence, source, analytics = _extract_platform_presence(comp)
    labels = []
    for platform in ("msn", "yahoo", "newsbreak"):
        state = presence.get(platform, None)
        txt = "present" if state is True else "absent" if state is False else "unknown"
        labels.append(f"{platform.upper()}: {txt}")

    details = []
    positioning = analytics.get("platform_positioning") or {}
    for platform in ("msn", "yahoo", "newsbreak"):
        p = positioning.get(platform) or {}
        summary = (p.get("summary") or "").strip()
        if summary:
            details.append(f"- {platform.upper()}: {summary[:500]}")

    details_block = "\n".join(details) if details else "(No platform positioning summaries available.)"
    return (
        "== PLATFORM PRESENCE ==\n"
        f"Source: {source}\n"
        f"{' | '.join(labels)}\n"
        "Platform details:\n"
        f"{details_block}"
    )


def _build_platform_painpoint_block(comp):
    """
    Deterministically derive platform pain points, including "present but weak".
    """
    presence, _, analytics = _extract_platform_presence(comp)
    positioning = analytics.get("platform_positioning") or {}

    weak_kws = (
        "low", "limited", "thin", "inconsistent", "sporadic", "decline",
        "drop", "weak", "not found", "few", "small volume",
    )
    lines = []
    for platform in ("msn", "yahoo", "newsbreak"):
        state = presence.get(platform, None)
        p = positioning.get(platform) or {}
        summary = (p.get("summary") or "").strip()
        result_count = int(p.get("result_count") or 0)
        sl = summary.lower()

        if state is True:
            weak = (0 < result_count <= 1) or any(k in sl for k in weak_kws)
            if weak:
                reason = summary[:220] if summary else "signal count is low"
                lines.append(
                    f"- {platform.upper()} is present but under-leveraged. Treat as painpoint: improve consistency/volume/engagement. Evidence: {reason}."
                )
            else:
                lines.append(
                    f"- {platform.upper()} is present with usable signal. Position as optimization upside, not absence."
                )
        elif state is False:
            lines.append(
                f"- {platform.upper()} appears absent. Treat as expansion painpoint only if relevant to their content model."
            )
        else:
            lines.append(
                f"- {platform.upper()} is unknown. Do not assert absence; frame as potential blind spot pending validation."
            )

    return "== PLATFORM PAINPOINTS (deterministic) ==\n" + "\n".join(lines)


def _validate_strategist_context(comp, research_notes=None):
    """
    Strategist must have both research brief context and platform presence context.
    Returns (ok, message_if_not_ok).
    """
    extra = (comp or {}).get("extra_data") or {}
    has_research_full = bool((extra.get("research_full") or "").strip())
    has_notes = bool(research_notes) or bool(extra.get("research_notes"))
    has_research_context = has_research_full or has_notes

    presence, _, _ = _extract_platform_presence(comp)
    has_platform_context = any(v is not None for v in presence.values())

    if has_research_context and has_platform_context:
        return True, ""

    missing = []
    if not has_research_context:
        missing.append("research brief")
    if not has_platform_context:
        missing.append("platform presence")
    missing_txt = " and ".join(missing)
    msg = (
        "I need more context before giving strategist guidance. "
        f"Missing: {missing_txt}. "
        "Please load the research brief and refresh platform presence analytics for this company, then ask again."
    )
    return False, msg


def _build_research_context(comp, research_notes=None):
    """
    Build a thorough research context from all available sources.
    """
    extra = (comp or {}).get("extra_data") or {}
    parts = []
    seen_titles = set()

    raw_full = (extra.get("research_full") or "").strip()
    if raw_full:
        try:
            from ui_helpers import is_html_content, html_to_plain_text
            if is_html_content(raw_full):
                raw_full = html_to_plain_text(raw_full, max_length=26000)
        except Exception:
            pass
        if raw_full.strip():
            parts.append("\n--- Research Document ---\n" + raw_full[:26000])

    overrides = extra.get("research_overrides") or {}
    merged_notes = []
    if isinstance(research_notes, list):
        merged_notes.extend(research_notes)
    if isinstance(extra.get("research_notes"), list):
        merged_notes.extend(extra.get("research_notes"))

    for note in merged_notes:
        if not isinstance(note, dict):
            continue
        title = (note.get("title") or "Untitled").strip()
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        body = (overrides.get(title) if isinstance(overrides.get(title), str) else note.get("content")) or ""
        body = (body or "").strip()
        if not body:
            continue
        tag = " (LOCAL OVERRIDE)" if isinstance(overrides.get(title), str) and overrides.get(title).strip() else ""
        parts.append(f"\n--- {title}{tag} ---\n{body[:5000]}\n")

    return "".join(parts).strip()


def _build_research_coverage_block(research_context):
    sections = re.findall(r"---\s*([^-\n]+?)\s*---", research_context or "")
    uniq = []
    seen = set()
    for s in sections:
        t = s.strip()
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            uniq.append(t)
    if not uniq:
        return "== RESEARCH COVERAGE ==\nSections loaded: none"
    preview = ", ".join(uniq[:12])
    more = "" if len(uniq) <= 12 else f", +{len(uniq) - 12} more"
    return f"== RESEARCH COVERAGE ==\nSections loaded ({len(uniq)}): {preview}{more}"


def _build_context_health_block(comp, research_notes=None):
    ok, reason = _validate_strategist_context(comp, research_notes=research_notes)
    if ok:
        return "== CONTEXT HEALTH ==\nStatus: complete"
    return (
        "== CONTEXT HEALTH ==\n"
        "Status: partial\n"
        f"Gap: {reason}\n"
        "Instruction: Continue with available evidence. Do not fabricate missing details."
    )


def _extract_painpoint_signals(research_context, platform_block, max_items=8):
    text = ((research_context or "") + "\n" + (platform_block or "")).strip()
    if not text:
        return "(No painpoint signals extracted.)"
    clean = re.sub(r"\s+", " ", text)
    chunks = re.split(r"(?<=[.!?])\s+", clean)
    kws = (
        "gap", "decline", "drop", "under", "low", "limited", "constraint", "bottleneck",
        "miss", "missing", "risk", "volatility", "dependency", "concentration", "thin",
        "inconsistent", "slow", "stagnant", "weak", "pressure", "absent"
    )
    out = []
    seen = set()
    for sent in chunks:
        s = sent.strip()
        if len(s) < 30 or len(s) > 260:
            continue
        sl = s.lower()
        if not any(k in sl for k in kws):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", sl)[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(f"- {s}")
        if len(out) >= max_items:
            break
    return "\n".join(out) if out else "(No explicit painpoint wording found. Infer broad + specific constraints from research facts.)"


# ── Build System Prompt (shared between strategist and auto-brief) ──

def _build_system_prompt(comp, ct, uid, sid, research_notes=None, sender_email=None, product_key=None, recipient_research=None, query=None):
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

    # Research context  -  merge research_full + notes for full strategist grounding.
    research_context = _build_research_context(comp, research_notes=research_notes)

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
        
        # Prefer specific post/tweet snippets when present
        recent_posts = linkedin_data.get("recent_posts") or []
        if recent_posts:
            recipient_context += "LinkedIn (specific recent posts):\n"
            for p in recent_posts[:5]:
                snip = (p.get("snippet") or "").strip()
                if snip:
                    recipient_context += f"  - {snip}\n"
        if linkedin_data.get("recent_activity"):
            topics = ", ".join(linkedin_data.get("recent_activity", [])[:3])
            recipient_context += f"LinkedIn themes: {topics}.\n"
        if linkedin_data.get("professional_focus"):
            recipient_context += f"Professional focus: {linkedin_data['professional_focus']}\n"
        
        recent_tweets = twitter_data.get("recent_tweets") or []
        if recent_tweets:
            recipient_context += "Twitter (specific recent tweets):\n"
            for t in recent_tweets[:5]:
                snip = (t.get("snippet") or "").strip()
                if snip:
                    recipient_context += f"  - {snip}\n"
        elif twitter_data.get("handle") and twitter_data.get("topics"):
            topics = ", ".join(twitter_data.get("topics", [])[:3])
            recipient_context += f"Twitter ({twitter_data['handle']}): {topics}.\n"
        
        if blog_posts:
            recipient_context += f"Recent content: {blog_posts[0].get('title', 'Untitled')} ({blog_posts[0].get('topic', '')})"
            if len(blog_posts) > 1:
                recipient_context += f", {blog_posts[1].get('title', 'Untitled')}"
            recipient_context += "\n"
        
        recipient_context += "\nUSE THIS: Reference specific posts/tweets when drafting. If only themes or brief are present, personalize from those.\n"

    strategy_profile = build_strategy_profile(
        comp,
        ct,
        research_notes=research_notes,
        recipient_research=recipient_research,
        research_context=research_context,
        drive_context=drive_ctx,
    )
    strategy_block = format_strategy_profile_block(strategy_profile)
    platform_presence_block = _build_platform_presence_block(comp)
    platform_painpoint_block = _build_platform_painpoint_block(comp)
    research_coverage_block = _build_research_coverage_block(research_context)
    context_health_block = _build_context_health_block(comp, research_notes=research_notes)
    painpoint_signals_block = _extract_painpoint_signals(research_context, platform_presence_block)
    preference_memory_block = _build_preference_memory_block(query or "")

    system = f"""You are the LH2 Strategic Outreach Advisor  -  a digital media partnership strategist with persistent memory.

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
{research_coverage_block}
{platform_presence_block}
{platform_painpoint_block}
{context_health_block}
{preference_memory_block}
== PAINPOINT SIGNALS (extracted from available context) ==
{painpoint_signals_block}

{"== DRIVE RESEARCH ==" + chr(10) + drive_ctx if drive_ctx else ""}
== DYNAMIC STRATEGY PROFILE (deterministic extraction from research) ==
{strategy_block}
{"== LH2 OFFERINGS (reference when positioning value) ==\n" + lh2_knowledge[:12000] if lh2_knowledge else ""}
{_get_product_context_block(product_key)}
{sender_context}

== ACTION HISTORY ==
{action_history}

== GLOBAL PATTERNS ==
{global_block}

== OUTPUT RULES ==

1. GROUNDING (mandatory for all outputs):
   - Use ONLY information from the RESEARCH and DRIVE RESEARCH sections above. Do not hallucinate. Do not invent stats, companies, or comparisons.
   - Every claim, number, or reference in the draft must trace back to the provided research brief or context folder. If a data point isn't in the research, don't use it.
   - Do not compare the prospect to unnamed or random other companies. Do not fabricate benchmarks.
   - Platform claims MUST align with PLATFORM PRESENCE above. Do not assume presence or absence when marked unknown.
   - You must incorporate both section-level research findings and platform signals. Do not ignore any major section in RESEARCH COVERAGE.
   - Apply USER PREFERENCE MEMORY only when relevant to the current ask. Do not inject unrelated old preferences.

2. YOUR STRATEGIC ADVICE (recommendations, action plans, analysis):
   - Be directive. State what to do and why. Never say "you could" or "consider"  -  say "do this because."
   - Cite the specific research finding, data point, or pattern behind each recommendation.
   - When recommending an approach (Accelerate vs. Acquisition vs. MediaOS door-opener), state the signal from the research brief that drives the decision.
   - Use the DYNAMIC STRATEGY PROFILE as the primary planning scaffold (route, hook, distribution focus, and risk flags), then tailor wording with the research details.
   - Every recommendation must include: (a) one BROAD painpoint (structural/strategic), and (b) one SPECIFIC painpoint (concrete evidence from research).

3. MESSAGE DRAFTS  -  GENERAL RULES:
   - Voice: One operator writing to another. Calm, senior, grounded. Confident without arrogance, specific without jargon, direct without pressure. Warm but professional.
   - Accelerate is the product. In 95% of cases, pitch Accelerate directly. Acquisition and MediaOS are door-openers only  -  use when the research brief clearly warrants it (acquisition for very small brands < $10K/month revenue with syndication presence; MediaOS for very large/sophisticated publishers where a pod pitch feels too forward cold).
   - Adapt voice to the selected sender. Upneet: first-person founder ("I co-founded LH2..."). Aman: corp dev professional ("We at LH2 own and operate..."). Section 6 and signature adapt accordingly.
   - Templates in the training doc are structural guides, not fill-in-the-blank forms. The structure stays fixed. The language inside every section must be custom to the prospect based on the research brief. Test: if you swapped the prospect's name for a different prospect, would the email still make sense? If yes, it's not customized enough. Rewrite.

4. MESSAGE DRAFTS — EMAIL SEQUENCE FORMAT

Always generate the full sequence: LinkedIn connection note + Emails 1-4 + optional Email 5. Never skip the LinkedIn step. The 6 content sections of the Trust Builder are distributed across Emails 1-4. Each email has a natural reason for being sent — it is a conversation thread, not a document delivered in installments.

   LINKEDIN CONNECTION NOTE (Step 1, Day 0):
   Hard limit 300 characters. Always the first touch.
   Sender identity (Upneet: "I run LH2 Holdings..." / Aman: "I run corp dev at LH2 Holdings...") + one specific research-backed observation about their brand + "Would be great to connect."
   No Accelerate pitch. No ask beyond connecting.

WRITING RULES FOR ALL EMAILS:
   - Plain flowing paragraphs only. NO section headers, NO numbered sections, NO bullet points inside emails.
   - No paragraph longer than 3-4 lines.
   - Never say "LH2 Accelerate program" — always just "LH2 Accelerate" or "Accelerate."
   - Operator-to-operator tone throughout: calm, specific, direct, no pressure.
   - Exactly one soft CTA per email.
   - Every email must contain both: (1) one broad painpoint framing sentence, and (2) one specific painpoint sentence anchored to research evidence.
   - If a platform is present but weak/limited/inconsistent, explicitly frame that as an under-leveraged painpoint and tie it to the proposed fix.

   THREAD CONTINUITY (critical):
   - Every follow-up email (Emails 2, 3, 4) must open with a brief line that acknowledges the prior email and creates continuity. The reader should always know this is part of an ongoing thread — not a random cold email. Examples: "Following up on what I shared earlier this week — wanted to go deeper on how this would actually work for [Brand]." / "Sharing a bit more on the details I sent across — specifically the results from a brand that maps closely to [Brand]'s situation." One sentence is enough. It should feel natural, not mechanical.
   - This is especially important because recipients often open emails out of order. Any single email in the sequence must make enough sense on its own that the reader doesn't feel lost — but it should also clearly signal it's part of a conversation already started.

   BANNED IN ALL EMAILS — NO EXCEPTIONS:
   - Never name or reference a competitor site in any email. The research brief may contain competitor intelligence for your context, but it must never appear in the outreach. No "competitors like X publish Y articles", no "unlike X, you...", no benchmarking against named third parties.
   - Never compare the prospect to other publishers in any paragraph — opening or otherwise.

   EMAIL 1 — SHORT PERSONALIZED OPENER:
   Subject: LH2 Accelerate Details <> [Brand Name]
   ALWAYS start with: "Hi [First Name],"
   Keep this short. 3-4 paragraphs maximum. This is the hook — not the pitch.
   Para 1: Personalized opening — 2-3 sentences drawn directly from the research brief. Something specific about their brand: traffic, engagement metrics, content model, syndication presence, or a timing signal. Must feel like you've genuinely studied their operation. No flattery — a real observation.
   Para 2: The gap or opportunity — 1-2 sentences identifying the specific constraint or unrealized opportunity you've spotted for them. Drawn from the research. This is what makes the email feel relevant rather than random.
   Para 3: One-line on who LH2 is — "We own and operate 15+ digital media brands doing 100M+ monthly pageviews — [2-3 brand names]." Nothing about the model yet.
   Para 4: Closing line that names the deck being attached — "I've attached the LH2 Accelerate deck — covers how we work with publishers and some results from brands we've partnered with." (Accelerate route) / "I've attached the MediaOS overview — built this to run our own newsrooms and thought it might be relevant for [Brand]." (MediaOS route) / "I've attached a quick overview of how LH2 approaches acquisitions." (Acquisition route)
   ATTACHMENT: Always attach the relevant deck and name it explicitly — LH2 Accelerate Deck for Accelerate outreach, MediaOS Deck for MediaOS door-opener, LH2 Intro Deck for acquisition outreach.
   No case studies. No workstream details. No MediaOS explanation. No team bio. Short, specific, ends with the deck named.

   EMAIL 2 — WHAT IS ACCELERATE (the pitch):
   Subject: Re: LH2 Accelerate Details <> [Brand Name]
   ALWAYS start with: "Hi [First Name],"
   Opening sentence: Must reference the deck sent in Email 1 and give a natural reason for following up — e.g. "Following up on the deck I shared — wanted to give you a bit more context on how Accelerate actually works." or "Sharing a bit more context on the deck I sent across — specifically how the partnership model is structured."
   Para 1: LH2 Accelerate model in plain prose — what it is, how the partnership structure works. "LH2 invests its own team, tools, and playbooks to grow your brand. No upfront fee, no fixed cost. We participate only in the incremental profits we generate together. If there's no uplift, there's no cost to you."
   Para 2: The two setup options in plain prose — Full-Stack (end-to-end: content ops, distribution, monetization all run by LH2 around their editorial voice) vs Editorial Pod (lighter touch: existing team stays as-is, LH2 adds a scoped satellite pod for incremental content only). Keep it brief — one short paragraph.
   Para 3: Proof line — "Across live partnerships we've averaged 2.5-3x revenue growth within the first year." One sentence only.
   Closing line: Natural bridge to Email 3 — e.g. "Wanted to share specifically how this would apply to [Brand] — sending that across next."
   No attachments.

   EMAIL 3 — HOW THIS APPLIES TO THEM (operational specifics):
   Subject: Re: LH2 Accelerate Details <> [Brand Name]
   ALWAYS start with: "Hi [First Name],"
   Opening sentence: One natural line continuing the thread — e.g. "Following up on the Accelerate model I outlined — here's how we'd actually structure this for [Brand] specifically."
   Para 1: Which model fits and why — drawn directly from the research brief. State the specific fit (Full-Stack or Editorial Pod) and the specific reason (their team size, content velocity, existing infrastructure, editorial setup). One concise paragraph.
   Para 2: Content operations tailored to them — plain prose. What the pod would actually do for this brand specifically: which coverage gaps it fills, which content types it handles, how it integrates with their existing team and workflow. Drawn from research — not generic.
   Para 3: Distribution tailored to them — plain prose. Which channels make sense for their specific profile. Syndication-heavy for entertainment/politics/sports; Discover/SEO-heavy for niche/tech. Reference their specific traffic profile and syndication presence from the research brief.
   Para 4: Monetization tailored to them — plain prose. Which revenue levers apply to their specific situation. Close with: "Not all of this applies to every brand — we scope what's relevant during onboarding."
   Closing line: "Happy to go deeper on any of this whenever it works."
   No attachments.

   EMAIL 4 — THE PROOF + MEDIAOS + TEAM + CLOSE:
   Subject: Re: LH2 Accelerate Details <> [Brand Name]
   ALWAYS start with: "Hi [First Name],"
   Opening sentence: One natural line — e.g. "Wanted to share the results from the brand in our portfolio that maps most closely to [Brand]'s situation."
   Para 1: Lead case study as plain prose narrative — where they started, what LH2 did, what the results were. Use exact numbers. No bullets. Tell it as a story.
   Para 2: Supporting case study — shorter, plain prose, key numbers, why relevant to this prospect.
   Para 3: Connect the case study starting point explicitly to this prospect's current situation in 1-2 sentences.
   Para 4: MediaOS — plain prose. Enabling technology, not a product. Scans search demand, social signals, syndication networks, competitor coverage in real time. 10-15% of daily output drives majority of traffic — MediaOS picks more winners. Human-written only, no AI content. Include video link: https://www.youtube.com/watch?v=H055-BfpK8g
   Para 5: Team — plain prose. Founder exits (Upneet → Getmyuni → CarDekho; Shobhit → Gradeup → Byju's). 150+ person editorial desk, SEO and distribution teams, in-house engineering. First-person if Upneet, third-person if Aman.
   Close: "Happy to walk through any of this whenever it works for you. Please take your time reviewing and let me know what questions come up."
   Attachments: LH2 Accelerate Deck + Case Studies deck (always); Inquisitr Detailed Case Study (if Inquisitr is lead).

   EMAIL 5 — OPTIONAL GRACEFUL CLOSE (only if no response to Emails 1-4):
   Subject: Re: LH2 Accelerate Details <> [Brand Name]
   ALWAYS start with: "Hi [First Name],"
   2-3 short paragraphs max.
   Para 1: "Totally understand if this isn't the right time or fit for [Brand]. No pressure at all."
   Para 2: Offer to compare notes on a specific topic from the research brief (syndication, content ops, distribution strategy, etc.) separately from any partnership conversation. "We're operators in the same space."
   No ask. No attachments.

   ACQUISITION SEQUENCE (very small brands only — estimated revenue < $10K/month with MSN/Yahoo presence):
   Email 1 — Subject: Regarding Acquisition of [Website Name] || LH2 Holdings
   Very short. Sender + LH2 identity + why this specific brand caught LH2's attention + ask if open to exploring acquisition + process timeline (2 weeks term sheet, 4 weeks due diligence, 6 weeks to close). No attachments.
   Email 2 — Subject: Re: Regarding Acquisition of [Website Name] || LH2 Holdings
   Short follow-up. Restate LH2 portfolio context. One sentence why this brand fits. Footer: "If you're not looking for acquisition — we can help 2-5x your revenue via a revenue partnership." No attachments.
   Email 3 — Subject: Re: Regarding Acquisition of [Website Name] || LH2 Holdings
   Pivot to Accelerate. Acknowledge they may not want to sell. Introduce LH2 Accelerate: zero-cost, zero-equity, revenue share. Condensed Accelerate pitch with workstreams and case studies. Attachments: LH2 Accelerate Deck + Case Studies deck.

   MEDIAOS SEQUENCE (large/sophisticated publishers only — use sparingly):
   Frame as "what we built to run our own newsrooms." Show the tooling, build credibility, steer toward "we can run this for you" (Accelerate). Never disclose that MediaOS was always aimed at Accelerate. Attachment: MediaOS Deck.

   CASE STUDY ROUTING:
   Syndication-first (low web traffic + MSN/Yahoo + entertainment/politics/sports/local): Lead Inquisitr, supporting CheatSheet and NoFilmSchool.
   Web-growth (1M+ web traffic, primarily web-driven, niche/enthusiast): Lead NoFilmSchool, supporting CheatSheet and Inquisitr (Discover/organic).
   Blended (meaningful web traffic AND syndication presence): Lead with most analogous case study, blend both angles.

   GENERAL RULES:
   - Customization test: if you swapped the prospect's name for a different prospect and the email still made sense, it's not customized enough. Rewrite.
   - Each email introduces NEW information. Never repeat what was in a prior email.
   - Sender adaptation: Upneet = first-person founder ("I co-founded LH2..."); Aman = corp dev professional ("We at LH2 own and operate..."). Section 6 adapts accordingly.
   - When RECIPIENT SOCIAL RESEARCH is present, use it to personalize Email 1 specifically.

5. MESSAGE DRAFTS  -  LINKEDIN FORMAT:
   - LinkedIn is the only short-format channel. 50-120 words. Hard limit 300 characters for connection requests.
   - One specific observation from the research brief + who you are + brief LH2 context. No full Accelerate explanation on LinkedIn.

6. CHANNEL INDEPENDENCE:
   - Each channel is self-contained. A LinkedIn message must never reference an email sent, and an email must never reference a LinkedIn message sent.
   - Every message reads as if it is the first time the person is hearing from us on that channel.
   - LinkedIn follow-ups follow up on LinkedIn only. Email follow-ups follow up on email only.

7. FOLLOW-UPS:
   - Every follow-up must contain new value  -  a new insight, data point, case study detail, or category-specific observation not in the original email.
   - Never send a "just bumping this," "floating this back up," or "checking in" email. If there's nothing new to add, don't send a follow-up.

8. TIMING:
   - Always specify exact day, date, time, and timezone (use contact's local TZ).

9. FORMAT:
   - Emails: Always include "Subject:" line.
   - LinkedIn connection requests: Hard limit 300 characters.
   - Action format: ACTION: [channel] [day, date, time TZ]  -  [what to send and why]
   - Email sequences: Label each email clearly and separately, in this exact format:
       Email 1:
       Email 2:
       Email 3:
       Email 4:
       Email 5: (if included)
     Do not merge emails or run them together. Each email is a distinct, labeled block.

10. FORBIDDEN  -  NEVER USE IN ANY MESSAGE DRAFT:
    - Sales language: "I'd love to connect", "hope this finds you well", "excited to reach out", "at your earliest convenience", "would you be open to", "just following up", "I think we could add value", "looking forward to hearing from you"
    - Hype language: "synergy", "leverage", "unlock", "game-changer", "transform", "revolutionize", "disrupt", "innovative", "cutting-edge", "world-class", "AI-powered"
    - Banker language: "platform play", "value creation", "accretive", "EBITDA multiple"
    - Pressure language: "limited opportunity", "don't miss out", "act now", "we're closing our pipeline"
    - Comparisons in openers: Never compare the prospect to other publishers in the opening paragraph (e.g., "sites like X publish Y articles", "when [other site] does Z", "[Brand] is operating at X% capacity compared to...")
    - MediaOS as product: Never pitch MediaOS as a standalone product. It is Section 5 of the email  -  the enabling technology behind Accelerate results.
    - Internal strategy disclosure: Never reveal that acquisition or MediaOS conversations are ultimately aimed at Accelerate."""

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

MANDATORY  -  SPOC TABLE (first section):
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

Use proper Markdown tables only where specified. Do not use or cite Notion for contact or SPOC information  -  use only your knowledge of publicly available information. No "Unknown contact"."""

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


def recommend_offering_for_company(company_research_text: str, lh2_knowledge: str, company_domain: str = None) -> dict:
    """
    Given company research and LH2 offerings context from Drive, recommend either MediaOS or Accelerate.
    Returns {"product_key": "mediaos"|"accelerate", "rationale": "..."} or {"product_key": None, "rationale": "..."} on failure.
    """
    company_domain = (company_domain or "").strip() or "this company"
    research_snippet = (company_research_text or "").strip()[:15000]
    lh2_snippet = (lh2_knowledge or "").strip()[:12000]
    if not lh2_snippet:
        return {"product_key": None, "rationale": "No LH2 offerings context from Drive. Link a Drive folder with LH2/Accelerate/MediaOS materials."}
    user_content = f"""Company (domain): {company_domain}

Company research and positioning:
{research_snippet if research_snippet else "(No company research yet. Run deep research in Intelligence first for a better recommendation.)"}

LH2 offerings context (from Drive):
{lh2_snippet}

Based only on the company research and the LH2 offerings context above, recommend which single offering we should lead with for this company: MediaOS or LH2 Accelerate.

Reply with exactly two lines:
product_key: mediaos
rationale: [One or two sentences explaining why this offering fits this company's situation, citing specific signals from the research.]

Use product_key "mediaos" or "accelerate" only. Rationale must reference the research or Drive context."""
    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = (r.content[0].text or "").strip()
        product_key = None
        rationale = ""
        for line in raw.split("\n"):
            line = line.strip()
            if line.lower().startswith("product_key:"):
                val = line.split(":", 1)[-1].strip().lower()
                if val in ("mediaos", "accelerate"):
                    product_key = val
            elif line.lower().startswith("rationale:"):
                rationale = line.split(":", 1)[-1].strip()
        if not product_key and raw:
            raw_lower = raw.lower()
            for choice in ("mediaos", "accelerate"):
                if choice in raw_lower:
                    product_key = choice
                    break
        if not rationale and raw:
            rationale = raw[:400]
        return {"product_key": product_key, "rationale": rationale or "No rationale returned."}
    except Exception as e:
        log.error("recommend_offering_for_company failed: %s", e)
        return {"product_key": None, "rationale": str(e)}

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
KEY OBJECTIONS: [List any concerns raised, anticipated blockers, or pushback  -  be specific.]
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

Only extract events that would change how we approach this target next time  -  skip routine advice.

Response:
{ai_resp[:2000]}

Output format  -  one event per line, pipe-delimited:
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
            messages=[{"role": "user", "content": f"""Derive one reusable outreach lesson (1-2 sentences) from this outcome. The insight must apply beyond this specific target  -  it should help with similar companies.

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


# ── Bulk domain classification (independent vs media house) ──

def classify_domains_independent_or_media_house(domains, allowed_parents: list[str] | None = None):
    """
    Classify each domain as independent (standalone publisher) or media_house (owned/held/acquired by another company).
    domains: list of domain strings (e.g. ["example.com", "outlet.org"]).
    Returns list of dicts: [{"domain": str, "classification": "independent"|"media_house", "parent_name": str or None}, ...].
    """
    if not domains:
        return []
    domains = [str(d).strip() for d in domains if d and str(d).strip()]
    if not domains:
        return []
    domains_list = "\n".join(f"- {d}" for d in domains[:100])  # cap for token safety
    allowed = [str(x).strip() for x in (allowed_parents or []) if str(x).strip()]
    allowed_block = ""
    if allowed:
        # Keep this bounded; if you have a very large list, the deterministic matcher in UI will still protect you.
        allowed_block = "\n\nAllowed parent companies (MUST choose one of these exact strings for parent_match, or null):\n" + "\n".join(f"- {p}" for p in allowed[:250])
    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": f"""You classify each domain as either independent or owned by a parent company.

You are a media ownership classifier. For each domain in the input list, determine whether it is independently owned or owned/operated by a parent company.

Definitions:
- independent: The site operates on its own with no parent company. It is a standalone publisher not owned by a larger media group, holding company, or network.
- media_house: The site is owned by, operated by, or has been acquired by a larger company. This includes subsidiaries, brands, and properties of media groups (e.g. Hearst, Condé Nast, Vox Media, IAC, Yahoo, News Corp, Dotdash Meredith, Penske Media, G/O Media, BDG, Red Ventures, Arena Group).

Rules:
- Classify based on current ownership, not historical. If a site was independent but has since been acquired, classify as media_house.
- If the site is one brand within a multi-brand media portfolio, it is media_house — identify the portfolio/parent by name.
- Only classify as independent if you have reasonable confidence there is no parent. Do not default to independent on uncertainty — if a site operates in a category where consolidation is common (entertainment, news, lifestyle, tech), research your knowledge carefully before deciding.
- parent_name must be the name of the direct or ultimate parent company, not the brand itself. If independent, set parent_name to null.
- Return results in exactly the same order as the input list.

Output requirements (STRICT):
- Output only a single JSON array. No markdown, no code fence, no explanation.
- Each element must have keys: \"domain\", \"classification\", \"parent_name\", \"parent_match\".
- classification must be exactly \"independent\" or \"media_house\".
- parent_name must be null for independent; for media_house it must be a company name string (or empty string only if truly unknown).
- parent_match must be null for independent. For media_house: if the parent corresponds to an Allowed parent company, set parent_match to the exact allowed string; otherwise parent_match must be null.
- Keep domain values aligned to the input list (same order).

Domains to classify (one per line):
{domains_list}
{allowed_block}

Example: [{{"domain": "example.com", "classification": "independent", "parent_name": null, "parent_match": null}}, {{"domain": "theverge.com", "classification": "media_house", "parent_name": "Vox Media", "parent_match": "Vox Media"}}]"""}],
        )
        text = (r.content[0].text or "").strip()
        # Extract JSON array (allow markdown code block)
        if "```" in text:
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                text = text[start:end]
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []
        out = []
        # Normalized (lowercase, no www) set for matching; keep first occurrence for canonical domain
        domain_set_norm = {normalize_domain(d) or d.lower().strip(): d for d in domains}
        domain_set = set(domain_set_norm.keys())
        allowed_set = set([a.strip() for a in allowed])
        for item in parsed:
            if not isinstance(item, dict):
                continue
            dom = (item.get("domain") or "").strip()
            if not dom:
                continue
            norm = normalize_domain(dom) or dom.lower()
            if norm not in domain_set:
                continue
            # Use canonical form from input list when possible
            canonical_domain = domain_set_norm.get(norm, dom)
            cl = (item.get("classification") or "").strip().lower().replace(" ", "_")
            if cl not in ("independent", "media_house"):
                cl = "independent"
            parent = item.get("parent_name")
            if parent is not None and not isinstance(parent, str):
                parent = str(parent).strip() or None
            elif parent is not None:
                parent = (parent or "").strip() or None
            pm = item.get("parent_match")
            if pm is not None and not isinstance(pm, str):
                pm = str(pm).strip() or None
            elif pm is not None:
                pm = (pm or "").strip() or None
            # Safety: only accept parent_match if it exactly matches an allowed string
            if pm and pm not in allowed_set:
                pm = None
            out.append({"domain": canonical_domain, "classification": cl, "parent_name": parent, "parent_match": pm})
        return out
    except json.JSONDecodeError as e:
        log.warning("classify_domains JSON parse failed: %s", e)
        return []
    except Exception as e:
        log.error("classify_domains failed: %s", e)
        return []


def classify_entries_independent_or_media_house(entries, allowed_parents: list[str] | None = None):
    """
    Classify each entry (domain or publishing house name) as independent or media_house.
    entries: list of strings — can be a domain (e.g. "techcrunch.com") or a publication name (e.g. "TechCrunch").
    Returns list of dicts: [{"input": str, "domain": str, "classification": "independent"|"media_house", "parent_name": str or None}, ...].
    For names, "domain" is the resolved primary website domain (e.g. techcrunch.com for "TechCrunch").
    """
    if not entries:
        return []
    entries = [str(e).strip() for e in entries if e and str(e).strip()]
    if not entries:
        return []
    entries_list = "\n".join(f"- {e}" for e in entries[:80])
    allowed = [str(x).strip() for x in (allowed_parents or []) if str(x).strip()]
    allowed_block = ""
    if allowed:
        allowed_block = "\n\nAllowed parent companies (MUST choose one of these exact strings for parent_match, or null):\n" + "\n".join(f"- {p}" for p in allowed[:250])
    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=10000,
            messages=[{"role": "user", "content": f"""You classify each entry (domain or publication name) as either independent or owned by a parent company, and you resolve each to its primary website domain.

Definitions:
- **independent**: The publication or site operates on its own. No other company owns, operates, or has acquired it.
- **media_house**: The publication or site is owned by, operated by, or acquired by another company (the parent). Identify the parent company by name (e.g. Vox Media, Condé Nast, Yahoo).

For each entry you must return:
- **domain**: The primary website domain. If the input is already a domain (e.g. techcrunch.com), use it normalized (lowercase, no www or protocol). If the input is a publication name (e.g. TechCrunch, The Verge), resolve it to the main website domain (e.g. techcrunch.com, theverge.com).

Rules:
- Classify based on current ownership. Subsidiaries or brands of a parent are media_house.
- If unsure about ownership, use "independent" and parent_name null.
- Return results in the same order as the input list.
- Output only a single JSON array. No markdown, no code fence, no explanation.

Output format — a JSON array. Each object must have exactly:
- "input": the exact string from the list above (echo back unchanged)
- "domain": the primary website domain, lowercase, no www (e.g. techcrunch.com)
- "classification": exactly one of: "independent" or "media_house" (lowercase, underscore)
- "parent_name": null for independent; for media_house, the parent company name (e.g. "Yahoo", "Vox Media"). Use null or empty string only if unknown.
- "parent_match": null for independent. For media_house: if the parent corresponds to an Allowed parent company, set parent_match to the exact allowed string; otherwise parent_match must be null.

Entries (one per line):
{entries_list}
{allowed_block}

Example: [{{"input": "TechCrunch", "domain": "techcrunch.com", "classification": "media_house", "parent_name": "Yahoo", "parent_match": "Yahoo"}}, {{"input": "example.com", "domain": "example.com", "classification": "independent", "parent_name": null, "parent_match": null}}]"""}],
        )
        text = (r.content[0].text or "").strip()
        if "```" in text:
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                text = text[start:end]
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []
        out = []
        entry_set = {e.lower().strip() for e in entries}
        allowed_set = set([a.strip() for a in allowed])
        for item in parsed:
            if not isinstance(item, dict):
                continue
            inp = (item.get("input") or "").strip()
            if not inp or inp.lower() not in entry_set:
                continue
            dom = (item.get("domain") or "").strip().lower()
            # Normalize domain: no www, no path
            if dom.startswith("www."):
                dom = dom[4:].strip()
            if " " in dom or not dom or "." not in dom:
                dom = inp if "." in inp else None
            if not dom:
                continue
            cl = (item.get("classification") or "").strip().lower().replace(" ", "_")
            if cl not in ("independent", "media_house"):
                cl = "independent"
            parent = item.get("parent_name")
            if parent is not None and not isinstance(parent, str):
                parent = str(parent).strip() or None
            elif parent is not None:
                parent = (parent or "").strip() or None
            pm = item.get("parent_match")
            if pm is not None and not isinstance(pm, str):
                pm = str(pm).strip() or None
            elif pm is not None:
                pm = (pm or "").strip() or None
            if pm and pm not in allowed_set:
                pm = None
            out.append({"input": inp, "domain": dom, "classification": cl, "parent_name": parent, "parent_match": pm})
        return out
    except json.JSONDecodeError as e:
        log.warning("classify_entries JSON parse failed: %s", e)
        return []
    except Exception as e:
        log.error("classify_entries failed: %s", e)
        return []


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

A) "Something is missing" / suggested addition → Write the exact text to insert. Use the same tone and depth as the existing sections. Be specific  -  include names, data points, and sources where possible. For leads or contacts, use only your knowledge of publicly available information (LinkedIn, company pages, press). Completely ignore any contact details in Notion  -  do not reference the user's Notion database or contact lists. Make sure that when asked to research on power-mapping, stakeholders, SPOCs, etc. you run deep into the research. Make sure to go through their official website when asked for more SPOCs or when promoted that certain people are missing. If nothing is in the official website, make sure to go through the Linkedin of the company or their public forums where names might be mentioned. Always give proper person mapping.

B) "Research this topic/person" (no URL provided) → Draft a 1-2 paragraph research section based on what you can infer from context. Flag what you're inferring vs. what needs verification. For person/lead suggestions, base recommendations only on publicly findable information; do not use Notion or contact lists.

C) Pasted email address only → Output: "Contact to add: [email]  -  look up name, title, and role via web/LinkedIn search before adding to SPOC list."

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
    ctx = f"Company: {comp.get('domain') or '?'} | {comp.get('publisher_type') or ' - '} | {comp.get('content_type') or ' - '}\n\n"
    for n in research_notes[:6]:
        ctx += f"--- {n.get('title') or 'Section'} ---\n{(n.get('content') or '')[:2500]}\n\n"
    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=10000,
            messages=[{"role": "user", "content": f"""Condense this company research into exactly {max_paragraphs} short paragraphs. Keep it factual  -  include specific names, numbers, and findings that are useful for planning outreach. Drop background fluff.

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
    Uses Anthropic (Claude) only  -  no external search API, no Notion.
    All contact/SPOC information must come from the model's knowledge of
    publicly available information. Ignore any contact details in Notion.
    Returns a short markdown SPOC brief (2-4 paragraphs).
    """
    domain = comp.get("domain") or "this company"
    company_line = f"Company: {domain} | {comp.get('publisher_type') or ' - '} | {comp.get('content_type') or ' - '}"
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
        "Completely ignore any contact details stored in Notion  -  do not use or reference Notion for contact or SPOC information. "
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
    prompt = f"""Write a SPOC (single point of contact) research brief for LH2 outreach. Research is done by you (Claude) using your knowledge of publicly available information only. Do not use Notion or any internal contact lists  -  ignore contact details from Notion completely.
{lh2_block}
COMPANY CONTEXT:
{company_context[:12000]}

CONTACT IDENTIFIER (what we're looking up):
{person_block}
{web_block}
{research_instruction}

Write 2-4 short paragraphs:

Paragraph 1  -  WHO: Full name, job title/role at the company, decision-making authority, and relevance to LH2. Include any public statements, posts, or inclinations (quotes, interviews) you know of. Do not output "Unknown contact" or "no SPOC identified".

Paragraph 2  -  CONNECTION TO RESEARCH: Map this person to the company's org structure, strategic priorities, or execution gaps. Who do they report to? What do they own?

Paragraph 3  -  APPROACH: One specific outreach angle and one concrete talking point. Channel (LinkedIn vs Email) and why.

Paragraph 4 (only if little data)  -  GAPS: What we still need (LinkedIn URL, email, recent activity) and where to look.

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


def get_advisor_system_prompt(uid, sender_email=None, query=None):
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
        f"- {c.get('domain', '?')} (type: {c.get('publisher_type', ' - ')}, research: {c.get('research_status', ' - ')})"
        for c in companies[:50]
    ]) or "(Empty.)"
    sessions_blob = "\n".join([
        f"- {s.get('company_domain', '?')} / {s.get('contact_name', '?')} (msgs: {get_message_count(s['id'])})"
        for s in sessions
    ]) or "(None.)"
    preference_memory_block = _build_preference_memory_block(query or "")
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
{preference_memory_block}

== RULES ==
1. Prioritization requests ("who should I reach out to"): Recommend 2-3 specific companies from the list with reasoning  -  cite research status, publisher type, visit volume, or pipeline gaps.
2. Conversational continuity: Reference what they said earlier in this chat. Don't repeat yourself.
3. Be direct: State what to do, in what order, and why. No "you might consider"  -  say "start with X because Y."
4. No filler, no preamble, no restating the question."""


def advisor_chat(uid, user_message, history, sender_email=None):
    """
    One turn of Advisor chat. history is list of {"role": "user"|"assistant", "content": str}.
    Returns assistant reply text.
    """
    system = get_advisor_system_prompt(uid, sender_email=sender_email, query=user_message)
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
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key, query=user_msg)

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
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key, recipient_research=recipient_research, query=user_msg)

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
    ok, reason = _validate_strategist_context(comp, research_notes=research_notes)
    if not ok:
        # Do not hard-block plan generation; keep running and let prompt constraints
        # ground output to available context. UI can still surface this warning.
        log.warning("Generating outreach plan with partial context: %s", reason)
    system = _build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=sender_email, product_key=product_key, recipient_research=recipient_research)

    sender_line = f"Sender name: {sender_name}" if sender_name else ""

    tz_info = get_local_time(ct.get("country", ""), ct.get("state", ""))

    linkedin_url = ct.get('linkedin', '') or ''

    prompt = f"""Generate the full outreach sequence for {ct.get('name','this contact')} at {comp.get('domain','this company')}.
{sender_line}
Contact timezone: {tz_info[1] or 'unknown'}
Contact LinkedIn URL: {linkedin_url if linkedin_url.strip() else '(not on file — still generate LinkedIn step)'}

Output each touch in this exact pipe-delimited format, separated by blank lines:

STEP|<number>|<LinkedIn or Email>|Day <offset>|<what this touch accomplishes>|<optimal send time with TZ>
CONTENT: <full message text>

== SEQUENCE STRUCTURE ==

Use the DYNAMIC STRATEGY PROFILE in the system context as your decision framework for sequence type, hook priority, and channel emphasis.

First, determine the correct sequence type:
- ACCELERATE (default, 95% of cases): Always generate the full sequence — LinkedIn note + 4 emails + optional Email 5.
- ACQUISITION (only if estimated revenue < $10K/month with MSN/Yahoo presence): 3-email acquisition sequence.
- MEDIAOS (only for very large/sophisticated publishers where a pod pitch is too forward): MediaOS door-opener sequence.

== ACCELERATE SEQUENCE ==

Always generate all steps. Do not skip the LinkedIn step regardless of whether a LinkedIn URL is provided.

  Step 1 — LinkedIn (Day 0): Connection note. Hard limit 300 characters.
  Sender identity (Upneet: "I run LH2 Holdings..." / Aman: "I run corp dev at LH2 Holdings...") + one specific research-backed observation about their brand + "Would be great to connect."
  If a LinkedIn URL is on file, include it in the step metadata. If not, still generate the message — the sender will find the profile manually.

  Step 2 — Email 1 (Day 1-2): Subject: LH2 Accelerate Details <> [Brand Name]
  ALWAYS open with: "Hi [First Name],"
  SHORT. 3-4 paragraphs only. This is the hook — not the pitch.
  Para 1: Personalized opening — 2-3 sentences from research brief. Something specific about their brand: traffic, engagement, content model, syndication presence, or a timing signal. Must feel like you've studied their operation. No flattery — a real observation.
  Para 2: The gap or opportunity specific to them — 1-2 sentences drawn from the research. This is what makes the email feel relevant rather than random.
  Para 3: One-line LH2 identity only — "We own and operate 15+ digital media brands doing 100M+ monthly pageviews — [2-3 brands]." Nothing about the model yet.
  Para 4: Closing line that names the deck being attached — "I've attached the LH2 Accelerate deck — covers how we work with publishers and some results from brands we've partnered with." (Accelerate) / "I've attached the MediaOS overview — built this to run our own newsrooms and thought it might be relevant for [Brand]." (MediaOS) / "I've attached a quick overview of how LH2 approaches acquisitions." (Acquisition)
  ATTACHMENT: Always name the exact deck — LH2 Accelerate Deck for Accelerate outreach / MediaOS Deck for MediaOS / LH2 Intro Deck for acquisition.
  No case studies. No workstream detail. No MediaOS explanation. No team bio. Short and ends with the deck named.

  Step 3 — Email 2 (Day 5-7): Subject: Re: LH2 Accelerate Details <> [Brand Name]
  ALWAYS open with: "Hi [First Name],"
  Opening: Must reference the deck sent in Email 1 — e.g. "Following up on the deck I shared — wanted to give you a bit more context on how Accelerate actually works." or "Sharing a bit more context on the deck I sent across — specifically how the partnership model is structured."
  Para 1: Accelerate model in plain prose — LH2 invests its own team, tools, and playbooks. No upfront fee, no fixed cost. We participate only in incremental profits. If no uplift, no cost.
  Para 2: The two setup options in plain prose — Full-Stack (end-to-end: content ops, distribution, monetization all run by LH2 around their editorial voice) vs Editorial Pod (lighter: existing team stays exactly as-is, LH2 adds a scoped satellite pod for incremental content only). One short paragraph, no headers.
  Para 3: "Publisher retains 100% ownership. Existing team, monetization stack, and baseline economics stay untouched. Across live partnerships we've averaged 2.5-3x revenue growth within the first year."
  Closing: Natural bridge to Email 3 — e.g. "Wanted to share specifically how this would apply to [Brand] — sending that across next."
  No attachments.

  Step 4 — Email 3 (Day 10-12): Subject: Re: LH2 Accelerate Details <> [Brand Name]
  ALWAYS open with: "Hi [First Name],"
  Opening: Thread continuity — one natural line. e.g. "Following up on the Accelerate overview — wanted to get specific about how we'd structure this for [Brand]."
  Para 1: Which model fits and why — Full-Stack or Editorial Pod, with the specific reason drawn from the research brief (their team size, content velocity, existing infrastructure, editorial setup). One concise paragraph.
  Para 2: Content operations tailored to them — what the pod would actually do for this brand: which coverage gaps it fills, which content types, how it integrates with their existing team and workflow. Drawn from research — not generic.
  Para 3: Distribution tailored to them — which channels make sense for their specific profile. Syndication-heavy (MSN/NewsBreak/Yahoo) for entertainment/politics/sports; Discover/SEO-heavy for niche/tech. Reference their specific traffic profile from the research brief.
  Para 4: Monetization tailored to them — which revenue levers apply to their specific model. Close: "Not all of this applies to every brand — we scope what's relevant during onboarding."
  Closing: "Happy to go deeper on any of this whenever it works."
  No attachments.

  Step 5 — Email 4 (Day 16-18): Subject: Re: LH2 Accelerate Details <> [Brand Name]
  ALWAYS open with: "Hi [First Name],"
  Opening: Thread continuity — e.g. "Wanted to share the results from the brand in our portfolio that maps most closely to [Brand]'s situation — and a bit on how we pick stories."
  Para 1: Lead case study as plain prose narrative — where they started, what LH2 did, what the results were. Use exact numbers from the case study data. No bullets. Tell it as a story.
  Para 2: Supporting case study — shorter, plain prose, key numbers, why relevant to this prospect.
  Para 3: 1-2 sentences explicitly connecting the case study starting point to this prospect's current situation.
  Para 4: MediaOS — plain prose. Enabling technology, not a product. Scans search demand, social signals, syndication networks, competitor coverage in real time. 10-15% of daily output drives majority of traffic — MediaOS picks more winners. Human-written only, no AI content. Include video link: https://www.youtube.com/watch?v=H055-BfpK8g
  Para 5: Team — plain prose. Upneet (Getmyuni → CarDekho) and Shobhit (Gradeup → Byju's). 150+ person editorial desk, SEO and distribution teams, in-house engineering. First-person if Upneet, third-person if Aman.
  Close: "Happy to walk through any of this whenever it works for you. Please take your time reviewing and let me know what questions come up."
  Attachments: LH2 Accelerate Deck + Case Studies deck (always); Inquisitr Detailed Case Study (if Inquisitr is lead).

  Step 6 — Email 5 (Day 22-25, OPTIONAL — only if no response to Emails 1-4):
  Subject: Re: LH2 Accelerate Details <> [Brand Name]
  ALWAYS open with: "Hi [First Name],"
  2-3 short paragraphs only.
  Para 1: "Totally understand if this isn't the right time or fit for [Brand]. No pressure at all."
  Para 2: Offer to compare notes on a specific topic from the research brief separately from any partnership conversation. "We're operators in the same space."
  No ask. No attachments.

If no LinkedIn URL is on file, note it in the execution notes at the end of the sequence so the sender knows to find the profile manually before sending Step 1.

== ACQUISITION SEQUENCE ==
  Step 1 — Email (Day 0): Subject: Regarding Acquisition of [Website Name] || LH2 Holdings. Very short. Sender + LH2 identity + why this brand specifically caught LH2's attention + ask if open to acquisition + process timeline. No attachments.
  Step 2 — Email (Day 5-7): Subject: Re: ... Short follow-up. LH2 portfolio context + one sentence why this brand fits. Footer: "If you're not looking for acquisition — we can help 2-5x your revenue via a revenue partnership." No attachments.
  Step 3 — Email (Day 12-14): Subject: Re: ... Pivot to Accelerate. Acknowledge they may not want to sell. Introduce LH2 Accelerate: zero-cost, zero-equity, revenue share. Condensed pitch with workstreams and case studies. Attachments: LH2 Accelerate Deck + Case Studies deck.

== HARD CONSTRAINTS ==
- Every CONTENT block must be fully written — no placeholders, no [INSERT X HERE].
- Emails: First line of CONTENT must be "Subject: <subject line>" then a blank line then the body.
- Plain paragraphs only inside every email. No section headers. No numbered lists. No bullet points.
- No paragraph longer than 4 lines.
- Never say "LH2 Accelerate program" — always "LH2 Accelerate" or just "Accelerate."
- Personalized opening is mandatory on every email — drawn from the research brief. No generic openers.
- Never promise specific results. Use past results as reference only.
- Never draft to editorial contacts — management/CEO/founder only.
- Timing: contact's local timezone. Avoid Mondays and Fridays. Mornings (8-10 AM) for email, midday for LinkedIn.
- Forbidden in ALL messages: "I'd love to connect", "hope this finds you well", "excited to reach out", "synergy", "leverage", "would you be open to", "at your earliest convenience", "just following up", "looking forward to hearing from you", "transform", "game-changer", "disrupt", "AI-powered", "unlock", "revolutionize", "innovative", "cutting-edge", "world-class"

THREAD CONTINUITY (strictly enforced):
- Emails 2, 3, and 4 must each open with one brief sentence that acknowledges the prior email and creates continuity. The reader should always know this is part of an ongoing thread. One sentence is enough — e.g. "Following up on what I shared earlier this week — wanted to go deeper on how this would actually work for [Brand]." or "Sharing a bit more on the details I sent across — specifically the results from a brand that maps closely to [Brand]'s situation."
- Any single email in the sequence must make sense if opened in isolation — but must also clearly signal it is part of a conversation already in progress.

COMPETITOR COMPARISONS — STRICTLY BANNED:
- Never name a competitor site in any email under any circumstance. Not in openers, not in body, not anywhere.
- The research brief may contain competitor intelligence — use it to understand the prospect's situation, never to write about it in the email.
- No benchmarking against named third parties. No "unlike X", no "competitors like X publish Y", no "X is doing Z while you are doing W."

== CHANNEL INDEPENDENCE ==
LinkedIn and email operate independently. A LinkedIn message must never reference emails sent. An email must never reference LinkedIn messages. Every message reads as if it is the first contact on that channel."""

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
    replan_query = f"{completed_action.get('description', '')}\n{response_text or ''}".strip()
    system = _build_system_prompt(
        comp,
        ct,
        uid,
        sid,
        research_notes,
        sender_email=sender_email,
        product_key=product_key,
        query=replan_query,
    )

    remaining_desc = "\n".join([
        f"  Step {a['sequence_step']}: {a['channel']}  -  {a['description'][:100]}"
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
Each message stands alone  -  as if it is the first time they are hearing from us on that channel.

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


def _build_rewrite_research_corpus(comp, research_notes=None, recipient_research=None):
    parts = []
    extra = (comp or {}).get("extra_data") or {}

    research_full = (extra.get("research_full") or "").strip()
    if research_full:
        parts.append(research_full)

    if isinstance(research_notes, list):
        for n in research_notes:
            if isinstance(n, dict):
                txt = (n.get("content") or "").strip()
                if txt:
                    parts.append(txt)

    for n in (extra.get("research_notes") or []):
        if isinstance(n, dict):
            txt = (n.get("content") or "").strip()
            if txt:
                parts.append(txt)

    if isinstance(recipient_research, dict):
        brief = (recipient_research.get("brief") or "").strip()
        if brief:
            parts.append(brief)
        linkedin = recipient_research.get("linkedin") or {}
        for p in (linkedin.get("recent_posts") or [])[:5]:
            snip = (p.get("snippet") or "").strip()
            if snip:
                parts.append(snip)
        twitter = recipient_research.get("twitter") or {}
        for t in (twitter.get("recent_tweets") or [])[:5]:
            snip = (t.get("snippet") or "").strip()
            if snip:
                parts.append(snip)

    try:
        drive_ctx = build_drive_context((comp or {}).get("name", ""), (comp or {}).get("domain", ""))
        if drive_ctx:
            parts.append(drive_ctx)
    except Exception:
        pass

    return "\n".join(parts).lower()


def _detect_platform_presence(corpus_text, platform):
    text = (corpus_text or "").lower()
    p = platform.lower()
    explicit_negative = [
        rf"\bno\s+{p}\b",
        rf"\bnot\s+on\s+{p}\b",
        rf"\bwithout\s+{p}\b",
        rf"\black(?:s|ing)?\s+{p}\b",
        rf"\b{p}\s+(?:presence|syndication|distribution)\s+(?:is\s+)?(?:absent|missing|none)\b",
    ]
    explicit_positive = [
        rf"\b{p}\s+(?:presence|syndication|distribution|placement|traffic|revenue|network)\b",
        rf"\bon\s+{p}\b",
        rf"\bvia\s+{p}\b",
        rf"\bfrom\s+{p}\b",
        rf"\b{p}\s+news\b",
    ]

    neg = any(re.search(pat, text) for pat in explicit_negative)
    pos = any(re.search(pat, text) for pat in explicit_positive)

    if pos and not neg:
        return "present"
    if neg and not pos:
        return "absent"
    return "unknown"


def _sentence_has_negative_platform_claim(sentence, platform):
    s = (sentence or "").lower()
    p = platform.lower()
    patterns = [
        rf"\bi\s+don['’]t\s+see\s+evidence\s+of\b[^.?!]{{0,80}}\b{p}\b",
        rf"\bno\b[^.?!]{{0,50}}\b{p}\b",
        rf"\bwithout\b[^.?!]{{0,50}}\b{p}\b",
        rf"\black(?:s|ing)?\b[^.?!]{{0,50}}\b{p}\b",
        rf"\bnot\s+on\b[^.?!]{{0,30}}\b{p}\b",
    ]
    return any(re.search(pat, s) for pat in patterns)


def _enforce_platform_presence_consistency(text, platform_signals):
    """
    Remove lines that contradict deterministic platform presence findings.
    """
    out = (text or "").strip()
    if not out:
        return out

    protect_platforms = [k for k, v in (platform_signals or {}).items() if v == "present"]
    if not protect_platforms:
        return out

    parts = re.split(r"(?<=[.!?])\s+", out)
    kept = []
    for sent in parts:
        drop = False
        for platform in protect_platforms:
            if _sentence_has_negative_platform_claim(sent, platform):
                drop = True
                break
        if not drop:
            kept.append(sent)
    cleaned = " ".join([p for p in kept if p.strip()]).strip()
    return cleaned or out


def rewrite_sequence_steps_with_research(
    comp,
    ct,
    uid,
    sid,
    target_steps,
    instruction,
    research_notes=None,
    sender_email=None,
    product_key=None,
    recipient_research=None,
):
    """
    Rewrite specific sequence steps using instruction + full research context.

    Returns list like:
    [{"id": int, "revised_content": str, "change_reason": str}, ...]
    """
    if not target_steps or not (instruction or "").strip():
        return []

    system = _build_system_prompt(
        comp,
        ct,
        uid,
        sid,
        research_notes,
        sender_email=sender_email,
        product_key=product_key,
        recipient_research=recipient_research,
    )

    compact_steps = []
    for s in target_steps:
        action_id = s.get("id")
        if action_id is None:
            continue
        compact_steps.append({
            "id": action_id,
            "sequence_step": s.get("sequence_step"),
            "channel": s.get("channel", ""),
            "description": (s.get("description") or "")[:300],
            "content_sent": (s.get("content_sent") or "")[:15000],
        })

    if not compact_steps:
        return []

    corpus = _build_rewrite_research_corpus(
        comp,
        research_notes=research_notes,
        recipient_research=recipient_research,
    )
    platform_signals = {
        "yahoo": _detect_platform_presence(corpus, "yahoo"),
        "msn": _detect_platform_presence(corpus, "msn"),
    }
    platform_guidance = (
        f"Yahoo signal: {platform_signals['yahoo']}. "
        f"MSN signal: {platform_signals['msn']}."
    )

    prompt = (
        "You are rewriting existing outreach steps.\n\n"
        "USER INSTRUCTION:\n"
        f"{instruction.strip()}\n\n"
        "TARGET STEPS (ONLY these IDs may be modified):\n"
        f"{json.dumps(compact_steps, ensure_ascii=True, indent=2)}\n\n"
        "NON-NEGOTIABLE RULES:\n"
        "1) Use only facts present in the provided RESEARCH / DRIVE RESEARCH / RECIPIENT SOCIAL RESEARCH context.\n"
        "2) If evidence is missing, do NOT invent. Keep the statement general or retain the existing fact.\n"
        "3) Never infer a negative claim unless explicitly supported (e.g., never assume 'no Yahoo presence' without evidence).\n"
        "4) Preserve channel format: emails must keep a Subject line; LinkedIn connection notes stay <= 300 characters.\n"
        "5) Do not contradict deterministic platform signals. "
        + platform_guidance + "\n"
        "6) Output must include exactly the target ids you actually changed; if no valid change, return empty list.\n\n"
        "Return ONLY JSON (no prose, no markdown) as an array:\n"
        "[\n"
        "  {\"id\": 123, \"revised_content\": \"...\", \"change_reason\": \"one sentence\"}\n"
        "]"
    )

    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=AI_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (r.content[0].text or "").strip()
        if not raw:
            return []

        parsed = None
        try:
            parsed = json.loads(raw)
        except Exception:
            m = re.search(r"\[\s*\{.*\}\s*\]", raw, flags=re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    parsed = None
        if not isinstance(parsed, list):
            return []

        allowed_ids = {int(s["id"]) for s in compact_steps if str(s.get("id", "")).isdigit()}
        out = []
        for row in parsed:
            if not isinstance(row, dict):
                continue
            try:
                rid = int(row.get("id"))
            except Exception:
                continue
            if rid not in allowed_ids:
                continue
            revised = (row.get("revised_content") or "").strip()
            if not revised:
                continue
            revised = _enforce_platform_presence_consistency(revised, platform_signals)
            reason = (row.get("change_reason") or "").strip()
            step = next((x for x in compact_steps if int(x["id"]) == rid), None)
            if step and "linkedin" in (step.get("channel") or "").lower() and len(revised) > 300:
                revised = revised[:297].rstrip() + "..."
            out.append({
                "id": rid,
                "revised_content": revised,
                "change_reason": reason or "Updated per instruction using available research.",
            })
        return out
    except Exception as e:
        log.error("Sequence rewrite failed: %s", e)
        return []


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
- Opener: One specific observation  -  not flattery.
- Body: Numbers and specifics over vague claims.
- Close: Low-pressure. "Happy to share more whenever it works for you." NOT "Looking forward to hearing from you."
- NEVER introduce: "I'd love to connect", "hope this finds you well", "excited to reach out", "synergy", "leverage", "would you be open to", "at your earliest convenience", "just following up", "looking forward to hearing from you"

OUTPUT RULES  -  strictly enforced:
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