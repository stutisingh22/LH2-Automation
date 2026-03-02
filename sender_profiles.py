"""
sender_profiles.py — Sender profiles for outreach generation

Defines hardcoded sender profiles (Upneet, Aman) and merges with active users from database.
Used by Strategist, Actions, and Advisor tabs for tone/voice adaptation in AI-generated outreach.
"""

import logging
from typing import Dict, List

log = logging.getLogger(__name__)

# ── Hardcoded Sender Profiles ────────────────────────────────────

HARDCODED_PROFILES = {
    "upneet.grover@lh2holdings.com": {
        "name": "Upneet Grover",
        "title": "Founder & CEO, LH2 Holdings",
        "email": "upneet.grover@lh2holdings.com",
        "phone": None,
        "linkedin": "https://www.linkedin.com/in/upneetgrover/",
        "voice_type": "founder_first_person",
        "voice_notes": (
            "VOICE: First-person founder. Warm, direct, and strategic — never pushy or salesy. "
            "References own exits naturally: 'I built and sold Getmyuni to CarDekho Group.' "
            "Uses 'we' for LH2 team, 'I' for personal experience and credibility. "
            "Big-picture thinker — connects the contact's specific situation to a larger opportunity. "
            "\n\n"
            "TONE RULES FOR OUTREACH DRAFTS: "
            "The opening should acknowledge the contact's specific work, not just flatter them. "
            "One brief warm line, then immediately into substance. "
            "Use specific numbers and outcomes — no vague claims. "
            "End with a low-pressure, open-ended invitation, never a hard ask. "
            "Closing phrase: 'Happy to connect whenever it works for you.' or similar. "
            "\n\n"
            "FORBIDDEN PHRASES (never use): "
            "'I'd love to connect', 'hope this finds you well', 'excited to reach out', "
            "'I came across your profile', 'synergy', 'leverage', 'at your earliest convenience', "
            "'I was wondering if', 'would you be open to', 'just following up'. "
            "\n\n"
            "EXAMPLE LINKEDIN (cold, under 300 chars): "
            "'Hi [Name] — saw [specific thing about their work]. I'm Upneet, built and sold Getmyuni. "
            "Now running LH2, we've taken publishers like Inquisitr from $X to 6x revenue. "
            "Think there's a fit here — happy to share details.'"
            "\n\n"
            "EXAMPLE EMAIL OPENER (cold outreach): "
            "'Hi [Name],\n\nSaw [specific research finding about their site/content]. "
            "Reached out because [specific reason tied to their situation].\n\n"
            "I'm Upneet — I built and sold Getmyuni to CarDekho. Now running LH2 Holdings, "
            "where we partner with digital media brands to grow revenue. No upfront fees — "
            "we only participate in incremental profits we generate together.\n\n"
            "Across live partnerships, we've averaged 2.5–3x revenue growth in year one. "
            "[One specific case study number relevant to their niche].\n\n"
            "Worth a quick conversation? Happy to connect whenever it works for you.'"
        ),
        "signature": (
            "Best,\n"
            "Upneet Grover\n"
            "Founder & CEO, LH2 Holdings\n"
            "Website | LinkedIn"
        ),
    },
    "aman.taneja@lh2holdings.com": {
        "name": "Aman Taneja",
        "title": "Head — Corp Dev & Partnerships, LH2 Holdings",
        "email": "aman.taneja@lh2holdings.com",
        "phone": "+91-7702022785",
        "linkedin": "https://www.linkedin.com/in/aman-taneja/",
        "voice_type": "corp_dev_third_person",
        "voice_notes": (
            "VOICE: Professional corp dev. Warm, analytical, and measured — never pushy or salesy. "
            "References founders in third person: 'Founded by Upneet and Shobhit — both second-time founders.' "
            "Comfortable with specific numbers and multiples. Structured thinker — uses numbered sections "
            "and bold headers in longer emails. Short messages are crisp and to the point. "
            "\n\n"
            "TONE RULES FOR OUTREACH DRAFTS: "
            "Brief, warm opener that acknowledges something specific about the contact or their work. "
            "Then quickly into substance with concrete data points. "
            "For cold outreach, lead with a specific observation about their site/traffic/content strategy, "
            "then connect it to what LH2 does. "
            "End with a no-pressure, generous close — offer to share more, not demand a meeting. "
            "Closing phrases: 'Happy to walk through any of this whenever it works for you.' or "
            "'Let me know what questions come up.' — never 'Looking forward to hearing from you.' "
            "\n\n"
            "FORBIDDEN PHRASES (never use): "
            "'I'd love to connect', 'hope this finds you well', 'excited to reach out', "
            "'I came across your profile', 'synergy', 'leverage', 'at your earliest convenience', "
            "'I was wondering if', 'would you be open to', 'just following up', "
            "'I think we could add value', 'looking forward to hearing from you'. "
            "\n\n"
            "EXAMPLE LINKEDIN (cold, under 300 chars): "
            "'Hi [Name] — noticed [specific thing about their publisher/traffic trend]. "
            "At LH2 we've helped similar publishers 3–6x their revenue with no upfront cost. "
            "Worth a quick look? Happy to share details.'"
            "\n\n"
            "EXAMPLE EMAIL OPENER (cold outreach): "
            "'Hi [Name],\n\nReached out because [specific observation about their site — "
            "e.g. traffic growth, content niche, monetization gap].\n\n"
            "At LH2 we partner with digital media brands to grow revenue — no upfront fees, "
            "we participate only in the incremental profits we generate together. "
            "Founded by Upneet and Shobhit, both second-time founders. "
            "Behind them is a 150+ person editorial desk and an in-house content engine (MediaOS) "
            "built from the ground up.\n\n"
            "Across live partnerships we've averaged 2.5–3x revenue growth in year one — "
            "[one specific case study number relevant to their niche].\n\n"
            "Happy to share full details whenever it works for you. "
            "Let me know what questions come up.'"
        ),
        "signature": (
            "Best,\n"
            "Aman Taneja\n"
            "Head — Corp Dev & Partnerships, LH2 Holdings\n"
            "Website | LinkedIn\n"
            "+91-7702022785"
        ),
    },
}


# ── Dynamic Sender List ──────────────────────────────────────────

def get_all_sender_profiles() -> List[Dict]:
    """
    Get combined list of sender profiles: hardcoded + database users.
    Returns list of dicts with keys: email, name, title, voice_type, signature, etc.
    """
    from db_ops import get_all_users
    
    profiles = []
    
    # Add hardcoded profiles first
    for email, profile in HARDCODED_PROFILES.items():
        profiles.append({
            "email": email,
            "name": profile["name"],
            "title": profile.get("title", ""),
            "phone": profile.get("phone"),
            "linkedin": profile.get("linkedin"),
            "voice_type": profile.get("voice_type", "professional"),
            "voice_notes": profile.get("voice_notes", ""),
            "signature": profile.get("signature", f"Best,\n{profile['name']}"),
            "source": "hardcoded",
        })
    
    # Add database users (if they have email and aren't already in hardcoded)
    try:
        db_users = get_all_users()
        for user in db_users:
            email = (user.get("email") or "").strip().lower()
            if not email:
                continue
            
            # Skip if already in hardcoded profiles
            if email in HARDCODED_PROFILES:
                continue
            
            name = user.get("username", "User")
            profiles.append({
                "email": email,
                "name": name,
                "title": f"{user.get('role', 'Team Member').title()}, LH2 Holdings",
                "phone": None,
                "linkedin": None,
                "voice_type": "professional",
                "voice_notes": f"Professional voice for {name}.",
                "signature": f"Best,\n{name}\nLH2 Holdings",
                "source": "database",
            })
    except Exception as e:
        log.warning("Failed to load users from database for sender profiles: %s", e)
    
    return profiles


def get_sender_profile(email: str) -> Dict:
    """
    Get sender profile by email address.
    Returns profile dict or None if not found.
    """
    if not email:
        return None
    
    email = email.strip().lower()
    
    # Check hardcoded profiles first
    if email in HARDCODED_PROFILES:
        profile = HARDCODED_PROFILES[email].copy()
        profile["email"] = email
        profile["source"] = "hardcoded"
        return profile
    
    # Check database users
    from db_ops import get_all_users
    try:
        db_users = get_all_users()
        for user in db_users:
            user_email = (user.get("email") or "").strip().lower()
            if user_email == email:
                name = user.get("username", "User")
                return {
                    "email": user_email,
                    "name": name,
                    "title": f"{user.get('role', 'Team Member').title()}, LH2 Holdings",
                    "phone": None,
                    "linkedin": None,
                    "voice_type": "professional",
                    "voice_notes": f"Professional voice for {name}.",
                    "signature": f"Best,\n{name}\nLH2 Holdings",
                    "source": "database",
                }
    except Exception as e:
        log.warning("Failed to load user profile from database: %s", e)
    
    return None


def format_sender_display_name(profile: Dict) -> str:
    """Format sender profile for display in dropdown."""
    if not profile:
        return "Select sender..."
    return f"{profile.get('name', 'Unknown')} ({profile.get('title', 'LH2 Holdings')})"
