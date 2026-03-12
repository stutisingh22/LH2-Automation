"""
deck_links.py — Load deck URLs from links.csv and inject hyperlinks into email body.
Used when saving to Gmail so words like 'portfolio', 'deck', 'case studies' link to the right deck.
"""

import html
import logging
import os
import re

log = logging.getLogger(__name__)

_LINKS_CACHE = None


_DEFAULT_CLOSE_LINE = (
    "Happy to walk through any of this whenever it works for you. "
    "Please take your time reviewing and let me know what questions come up."
)

_PORTFOLIO_NUMBERS_IMG_TOKEN = "__LH2_PORTFOLIO_NUMBERS_IMAGE__"

def _direct_image_url(url: str) -> str:
    """
    Convert share URLs into direct image URLs suitable for <img src=...>.
    Gmail often won't render images that require redirects/cookies; Dropbox share links need raw=1.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if "dropbox.com" in u:
        # remove dl=0/1 if present, then append raw=1
        u2 = re.sub(r"([&?])dl=\\d+", r"\\1", u).replace("?&", "?").rstrip("&?")
        if "raw=1" in u2:
            return u2
        return u2 + ("&raw=1" if "?" in u2 else "?raw=1")
    return u


def _maybe_insert_portfolio_numbers_image(body_plain: str, step_index=None) -> str:
    """
    For Email 1 only, insert a token after the portfolio sentence; token is later rendered as an <img>.
    This preserves the exact email text (no wording changes), and only adds an image in Gmail drafts.
    """
    step = step_index or 0
    if step != 2:  # Step 2 in our sequences = Email 1 (LinkedIn is Step 1)
        return body_plain
    if _PORTFOLIO_NUMBERS_IMG_TOKEN in (body_plain or ""):
        return body_plain

    links = _load_links_csv()
    img_url = _direct_image_url((links.get("Portfolio Numbers") or "").strip())
    if not img_url:
        return body_plain

    # Find the specific portfolio sentence (we insert immediately after it).
    marker = "We own and operate 15+ digital media brands doing 100M+ monthly pageviews"
    if marker not in (body_plain or ""):
        return body_plain

    lines = (body_plain or "").split("\n")
    out = []
    inserted = False
    for ln in lines:
        out.append(ln)
        if (not inserted) and marker in ln:
            # Insert token on its own line; rendering happens after escaping/linking.
            out.append("")
            out.append(_PORTFOLIO_NUMBERS_IMG_TOKEN)
            out.append("")
            inserted = True
    return "\n".join(out)


def _maybe_replace_close_with_attachment_sentence(body_plain: str, product_key=None, step_index=None, lead_brand=None) -> str:
    """
    If the body contains the default close line and this step is an 'attachments' step,
    replace only that line with: "Attaching the <Deck A>, <Deck B>, and <Deck C> for your reference."
    Deck names themselves are later hyperlinked by the injector.
    """
    if _DEFAULT_CLOSE_LINE not in (body_plain or ""):
        return body_plain

    pk = (product_key or "").strip().lower()
    step = step_index or 0
    lead = (lead_brand or "").strip().lower() if lead_brand else ""
    is_attachments_step = False
    if pk in ("accelerate", "mediaos") and step >= 5:
        is_attachments_step = True
    if pk == "acquisition" and step >= 5:
        is_attachments_step = True
    if not is_attachments_step:
        return body_plain

    # Build deck list based on what we can infer from the body/context.
    decks = []
    if pk == "acquisition" and step < 5:
        decks = ["Intro Deck"]
    else:
        # Partnership proof / case-study step
        decks.append("LH2 Accelerate Deck")
        decks.append("Case Studies Deck")
        body_l = (body_plain or "").lower()
        lead_candidates = [
            ("inquisitr", "Inquisitr Detailed Case Study"),
            ("nofilmschool", "NoFilmSchool Detailed Case Study"),
            ("cheatsheet", "CheatSheet Detailed Case Study"),
            ("vintage aviation news", "Vintage Aviation News Detailed Case Study"),
        ]
        for key, deck_name in lead_candidates:
            if key in body_l or key in lead:
                decks.append(deck_name)
                break

    if not decks:
        return body_plain

    if len(decks) == 1:
        attach_line = f"Attaching the {decks[0]} for your reference."
    elif len(decks) == 2:
        attach_line = f"Attaching the {decks[0]} and {decks[1]} for your reference."
    else:
        attach_line = f"Attaching the {', '.join(decks[:-1])}, and {decks[-1]} for your reference."

    return body_plain.replace(_DEFAULT_CLOSE_LINE, attach_line)


def _strip_attachment_footer_lines(body_plain: str) -> str:
    """
    Remove redundant footer-style attachment lines like:
      "ATTACHMENT: LH2 Accelerate Deck"
    We rely on in-text hyperlinks instead.
    """
    if not (body_plain or "").strip():
        return body_plain
    lines = (body_plain or "").split("\n")
    kept = []
    for ln in lines:
        # Handle variants like:
        # - ATTACHMENT: ...
        # - **ATTACHMENT:** ...
        # - Attachments: ...
        if re.match(r"^\s*(\*{1,3}\s*)?attachments?(\s*\*{1,3})?\s*:\s*.+\s*$", ln, flags=re.IGNORECASE):
            continue
        kept.append(ln)
    # Trim excessive trailing blank lines introduced by removal
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def _load_links_csv():
    """Load links.csv from project root. Returns dict: label (normalized) -> url."""
    global _LINKS_CACHE
    if _LINKS_CACHE is not None:
        return _LINKS_CACHE
    out = {}
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "links.csv")
        if not os.path.isfile(path):
            log.warning("deck_links: links.csv not found at %s", path)
            _LINKS_CACHE = out
            return out
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "," not in line:
                    continue
                parts = line.split(", ", 1)
                if len(parts) < 2:
                    continue
                label, url = parts[0].strip(), parts[1].strip()
                if label and url:
                    out[label] = url
        _LINKS_CACHE = out
    except Exception as e:
        log.warning("deck_links: failed to load links.csv: %s", e)
        _LINKS_CACHE = {}
    return _LINKS_CACHE


def get_deck_links_for_context(product_key=None, step_index=None, lead_brand=None):
    """
    Return list of (phrase, url) to inject for this pitch context.
    phrase is matched case-insensitively in body; longest phrases first.
    """
    links = _load_links_csv()
    if not links:
        return []

    # Map CSV labels to URLs (use exact keys from CSV)
    def u(label):
        return links.get(label) or links.get(label.strip())

    acc = u("Accelerate Deck")
    perf = u("Portfolio Performance")
    intro = u("Intro Deck")
    cheatsheet = u("Cheatsheet")
    inquisitr = u("Inquisitr")
    vintage = u("Vintage Avaiation News")  # CSV spelling
    nofilmschool = u("No Film School")
    portfolio_nums = u("Portfolio Numbers")

    pairs = []
    pk = (product_key or "").strip().lower()
    step = step_index
    lead = (lead_brand or "").strip().lower() if lead_brand else ""

    if pk == "accelerate" or pk == "mediaos":
        if acc:
            pairs.extend([
                ("LH2 Accelerate deck", acc),
                ("LH2 Accelerate Deck", acc),
                ("Accelerate deck", acc),
                ("Accelerate Deck", acc),
            ])
        if step and step >= 4 and perf:
            pairs.extend([
                ("case studies", perf),
                ("Case studies", perf),
                ("Case Studies", perf),
                ("Portfolio Performance", perf),
                ("portfolio", perf),
                ("Case Studies Deck", perf),  # attachment label variant
            ])
        if step and step >= 4 and lead and "inquisitr" in lead and inquisitr:
            pairs.append(("Inquisitr", inquisitr))
        if step and step >= 4 and inquisitr:
            pairs.append(("Inquisitr Detailed Case Study", inquisitr))  # attachment label variant
        if step and step >= 4 and cheatsheet:
            pairs.append(("CheatSheet Detailed Case Study", cheatsheet))
        if step and step >= 4 and nofilmschool:
            pairs.append(("NoFilmSchool Detailed Case Study", nofilmschool))
        if step and step >= 4 and vintage:
            pairs.append(("Vintage Aviation News Detailed Case Study", vintage))
    elif pk == "acquisition":
        if intro:
            pairs.extend([
                ("Intro deck", intro),
                ("intro deck", intro),
            ])
        if step and step >= 5 and acc:
            pairs.extend([
                ("LH2 Accelerate deck", acc),
                ("Accelerate deck", acc),
                ("the Accelerate deck", acc),
            ])
        if step and step >= 5 and perf:
            pairs.extend([
                ("case studies", perf),
                ("Case studies", perf),
                ("portfolio", perf),
            ])
    else:
        # Default: offer common links (e.g. when product not selected)
        if acc:
            pairs.extend([
                ("LH2 Accelerate deck", acc),
                ("LH2 Accelerate Deck", acc),
                ("Accelerate deck", acc),
                ("Accelerate Deck", acc),
            ])
        if perf:
            pairs.extend([("case studies", perf), ("portfolio", perf), ("Case Studies Deck", perf)])
        if intro:
            pairs.extend([("Intro deck", intro), ("intro deck", intro)])
        if inquisitr:
            pairs.append(("Inquisitr Detailed Case Study", inquisitr))
        if cheatsheet:
            pairs.append(("CheatSheet Detailed Case Study", cheatsheet))
        if nofilmschool:
            pairs.append(("NoFilmSchool Detailed Case Study", nofilmschool))
        if vintage:
            pairs.append(("Vintage Aviation News Detailed Case Study", vintage))

    # Dedupe by phrase (first wins); sort by -len(phrase) so longer matches first
    seen = set()
    unique = []
    for phrase, url in pairs:
        key = phrase.lower()
        if key in seen or not url:
            continue
        seen.add(key)
        unique.append((phrase, url))
    unique.sort(key=lambda x: -len(x[0]))
    return unique


def inject_deck_links_into_email_body(body_plain, product_key=None, step_index=None, lead_brand=None):
    """
    Replace deck/portfolio phrases in body_plain with hyperlinks. Returns HTML body.
    Matches phrases in the original text (before escaping) so e.g. "I've attached the LH2 Accelerate deck" is found.
    """
    if not (body_plain or "").strip():
        return ""

    # If the model generated Markdown links like [Intro deck](https://...), strip them to plain text.
    # We intentionally control hyperlinking via our own phrase->URL injector so output stays consistent.
    try:
        body_plain = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", body_plain)
    except Exception:
        pass

    # Remove redundant footer attachment lines (we use in-text links instead).
    body_plain = _strip_attachment_footer_lines(body_plain)

    # Optional: insert portfolio numbers image for Email 1 (rendered only in Gmail drafts).
    body_plain = _maybe_insert_portfolio_numbers_image(body_plain, step_index=step_index)

    # Optional: replace the standard closing line with an attachment sentence (deck names are linked later).
    body_plain = _maybe_replace_close_with_attachment_sentence(
        body_plain, product_key=product_key, step_index=step_index, lead_brand=lead_brand
    )

    pairs = get_deck_links_for_context(product_key, step_index, lead_brand)
    if not pairs:
        return html.escape(body_plain).replace("\n", "<br>\n")

    # Find all phrase matches in original body (case-insensitive); collect (start, end, matched_text, url)
    matches = []
    for phrase, url in pairs:
        pattern = re.escape(phrase)
        for m in re.finditer(pattern, body_plain, re.IGNORECASE):
            matches.append((m.start(), m.end(), m.group(0), url))
    # Sort by start; drop overlapping (keep earlier/longer)
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    non_overlapping = []
    for m in matches:
        if non_overlapping and m[0] < non_overlapping[-1][1]:
            continue
        non_overlapping.append(m)

    # Build HTML: text segments escaped, link segments as <a href="...">matched</a>
    out = []
    pos = 0
    for start, end, matched_text, url in non_overlapping:
        if start > pos:
            out.append(html.escape(body_plain[pos:start]))
        out.append('<a href="' + html.escape(url, quote=True) + '">' + html.escape(matched_text) + "</a>")
        pos = end
    if pos < len(body_plain):
        out.append(html.escape(body_plain[pos:]))
    rendered = "".join(out).replace("\n", "<br>\n")

    # Render the portfolio numbers image token as an <img> tag (URL is HTML-escaped).
    if _PORTFOLIO_NUMBERS_IMG_TOKEN in (body_plain or ""):
        links = _load_links_csv()
        img_url = _direct_image_url((links.get("Portfolio Numbers") or "").strip())
        if img_url:
            img_html = (
                '<img src="' + html.escape(img_url, quote=True) + '" '
                'alt="Portfolio numbers" '
                'style="max-width:100%;height:auto;border:1px solid #e5e7eb;border-radius:8px;margin:8px 0 2px 0;" />'
            )
            rendered = rendered.replace(html.escape(_PORTFOLIO_NUMBERS_IMG_TOKEN), img_html)
        else:
            rendered = rendered.replace(html.escape(_PORTFOLIO_NUMBERS_IMG_TOKEN), "")
    return rendered
