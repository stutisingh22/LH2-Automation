"""
product_profiles.py — LH2 product definitions for AI-driven outreach

Defines the two outreach products: LH2 Accelerate and MediaOS.
Used by ai_engine.py to inject a focused PRODUCT FOCUS block into
the system prompt, so the AI knows exactly what it's pitching and
how to frame it — rather than blending both into a generic blob.

Each profile contains:
  - label:         Display name (shown in the UI selector)
  - tagline:       One-line description
  - pitch_summary: Full pitch the AI should understand and internalize
  - target_signals: What makes a prospect a good fit for this product
  - key_facts:     Specific facts/numbers to anchor claims
  - pitch_angle:   HOW to lead the conversation — the strategic frame
  - tone_notes:    Tone register — how the sender should come across
  - opener_hooks:  Observation-based openers that work for this product
  - avoid:         Framings that undermine this product's positioning
"""

PRODUCT_PROFILES = {
    "accelerate": {
        "label": "LH2 Accelerate",
        "tagline": "Revenue growth partnership — no upfront fees",
        "pitch_summary": (
            "LH2 Accelerate is a revenue partnership for digital media publishers. "
            "LH2 brings editorial resources (150+ person desk), content infrastructure "
            "(MediaOS), distribution, and operational know-how. "
            "There is no upfront cost — LH2 participates only in the incremental revenue "
            "it generates together with the publisher. "
            "Across live partnerships, the average outcome is 2.5–3x revenue growth in year one. "
            "Founded by Upneet and Shobhit — both second-time founders. "
            "This is a partnership, not a service. LH2 only wins when the publisher wins."
        ),
        "target_signals": [
            "Publisher with strong traffic or audience but undermonetised relative to size",
            "Site growing editorially but revenue hasn't kept pace with content output",
            "Founder or owner looking for growth capital/resources without hiring risk or upfront cost",
            "Media brand in a clear niche with loyal audience but thin margins",
            "Publisher who has tried monetisation tools (programmatic, affiliates) but hit a ceiling",
        ],
        "key_facts": [
            "No upfront cost — pure profit-share on incremental revenue generated",
            "150+ person editorial desk ready to deploy immediately",
            "MediaOS content engine included in the partnership",
            "2.5–3x average revenue growth in year one across live partnerships",
            "Founded by Upneet Grover and Shobhit — both second-time founders",
            "LH2 only participates in profits it generates — zero risk to the publisher",
        ],
        "pitch_angle": (
            "Lead with a specific observation about their monetisation situation — "
            "not just that they exist, but a specific signal: traffic without revenue, "
            "niche audience without premium CPMs, content scaling without distribution. "
            "Frame LH2 as a partner who has skin in the game, not a vendor selling a service. "
            "Anchor to the no-upfront-cost structure early — it removes the capital risk objection "
            "before it can form. Include one specific case study number relevant to their niche "
            "to make the outcome concrete. "
            "Close with an offer to share more — never with a meeting demand."
        ),
        "tone_notes": (
            "Partnership and financial alignment. The sender is a potential co-investor "
            "in the publisher's growth — not a salesperson. "
            "Collaborative, not transactional. Grounded, not hype-driven. "
            "Emphasise incentive alignment: 'We only win when you win.' "
            "Let the math speak — 2.5-3x is the number; no need to hype it further. "
            "Avoid any framing that sounds like outsourcing or 'handing over' the brand."
        ),
        "opener_hooks": [
            "traffic growing but revenue hasn't kept pace with content output",
            "strong niche audience with clear monetisation upside not yet captured",
            "editorial output scaling well — distribution and revenue lagging behind",
            "programmatic floor has been hit — growth needs a new lever",
        ],
        "avoid": [
            "Don't frame it as 'content creation services' or 'outsourcing editorial'",
            "Don't open with LH2's size or team before anchoring to their specific situation",
            "Don't mention MediaOS as a separate product — in Accelerate it's part of the deal",
            "Don't use 'partnership opportunity' as an opener — too vague",
            "Don't lead with founder credentials before establishing relevance to the prospect",
        ],
    },

    "mediaos": {
        "label": "MediaOS",
        "tagline": "Content operations platform built for digital media at scale",
        "pitch_summary": (
            "MediaOS is LH2's proprietary content operations platform — built from the ground up "
            "to run digital media businesses at scale. It handles content workflow, "
            "editorial operations, publishing automation, distribution, and performance tracking. "
            "It was built for and battle-tested across LH2's own portfolio of publishers, "
            "not designed in theory. Now available to external publishers who want "
            "the infrastructure LH2 uses internally without having to build it themselves."
        ),
        "target_signals": [
            "Publisher with operational or workflow pain points at the editorial layer",
            "Media brand scaling content output but infrastructure (CMS, workflows) is holding them back",
            "Founder running content ops across multiple properties with coordination overhead",
            "Site with tech debt in CMS or publishing workflow who needs modern infrastructure",
            "Team spending time on manual operational tasks that should be automated",
        ],
        "key_facts": [
            "Built in-house, battle-tested across LH2's own portfolio — not a theoretical product",
            "Handles content workflow, editorial ops, publishing automation, distribution, and tracking",
            "Designed for scale — currently powers a 150+ person editorial operation",
            "Purpose-built for digital media revenue operations — not a generic CMS",
            "Founded by Upneet Grover and Shobhit — both operators who built this out of necessity",
        ],
        "pitch_angle": (
            "Lead with a specific operational observation — publishing frequency, "
            "content volume, or a workflow signal visible from outside (team size, "
            "number of properties, content cadence). "
            "Frame MediaOS as infrastructure they don't have to build — "
            "the credibility comes from the fact it runs real operations at scale, "
            "not from a feature list. "
            "The hook: 'We built this for ourselves, so you get what actually works in production, "
            "not what sounds good in a deck.' "
            "Don't pitch features — pitch the operational outcome: less overhead, more output, "
            "better visibility across the content operation."
        ),
        "tone_notes": (
            "Tech-forward but practical. The sender is a builder talking to another builder. "
            "No buzzwords about 'AI-powered content' or 'intelligent workflows.' "
            "Specific about what the platform does operationally — not vague about 'solutions.' "
            "Credibility comes from production use at scale. "
            "The tone is: 'We had this problem too, we built the solution, here it is.' "
            "Not: 'We have an exciting product we think you might be interested in.'"
        ),
        "opener_hooks": [
            "publishing at high volume — workflow looks manual from the outside",
            "content ops running across multiple properties with obvious coordination overhead",
            "editorial team scaling but publishing infrastructure hasn't kept up",
            "strong content output but distribution and tracking seem fragmented",
        ],
        "avoid": [
            "Don't pitch MediaOS as 'AI-powered' — that's not the credibility angle",
            "Don't list features without connecting them to a specific operational pain",
            "Don't conflate MediaOS with the Accelerate partnership model",
            "Don't use SaaS-style language: 'pricing plans', 'onboarding', 'free trial'",
            "Don't open with the product name before establishing the operational problem",
        ],
    },
}


def get_product_profile(product_key: str) -> dict:
    """Get a product profile by key. Returns None if not found."""
    if not product_key:
        return None
    return PRODUCT_PROFILES.get(product_key.lower())


def get_product_options() -> list:
    """Return list of (key, label) tuples for UI selectors."""
    return [(k, v["label"]) for k, v in PRODUCT_PROFILES.items()]


def format_product_context_block(product_key: str) -> str:
    """
    Build the PRODUCT FOCUS block to inject into the system prompt.
    Returns empty string if no product selected.
    """
    profile = get_product_profile(product_key)
    if not profile:
        return ""

    facts = "\n".join(f"  - {f}" for f in profile["key_facts"])
    signals = "\n".join(f"  - {s}" for s in profile["target_signals"])
    hooks = "\n".join(f"  - {h}" for h in profile["opener_hooks"])
    avoid = "\n".join(f"  - {a}" for a in profile["avoid"])

    return f"""== PRODUCT FOCUS: {profile['label'].upper()} ==
Tagline: {profile['tagline']}

WHAT WE'RE PITCHING:
{profile['pitch_summary']}

WHY THIS PROSPECT IS A FIT (signals to reference):
{signals}

KEY FACTS TO ANCHOR CLAIMS:
{facts}

PITCH ANGLE (how to frame the conversation):
{profile['pitch_angle']}

TONE FOR THIS PRODUCT:
{profile['tone_notes']}

STRONG OPENER HOOKS:
{hooks}

WHAT TO AVOID IN THIS PITCH:
{avoid}"""
