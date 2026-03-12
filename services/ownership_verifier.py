"""
Ownership verifier — grounded parent matching using web evidence.

We use DDG search snippets (no browsing automation) and ask the AI to choose a parent ONLY from allowed_parents.
This reduces incorrect parent selection (e.g. allrecipes.com should map to Dotdash Meredith / IAC, not Red Ventures).
"""

from __future__ import annotations

import json
import logging
from typing import List, Dict, Tuple

from ddgs import DDGS

from anthropic_limiter import create_with_retry
from config import AI_MODEL

log = logging.getLogger(__name__)


def _norm_key(name: str) -> str:
    s = (name or "").strip().lower()
    return "".join(ch for ch in s if ch.isalnum())


def _match_allowed(name: str, allowed: List[str]) -> Tuple[str | None, str]:
    """Return (allowed_match, reason). Only returns a value that is exactly in allowed."""
    if not name or not allowed:
        return None, "no_input_or_allowed"
    if name in allowed:
        return name, "exact"
    nk = _norm_key(name)
    by_key = {_norm_key(a): a for a in allowed if a}
    if nk and nk in by_key:
        return by_key[nk], f"norm_key({name}→{by_key[nk]})"
    if nk:
        for k, a in by_key.items():
            if k and (k in nk or nk in k):
                return a, f"norm_contains({name}→{a})"
    return None, "no_match"


def _ddg_search(query: str, max_results: int = 6) -> List[Dict]:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        out = []
        for r in results or []:
            out.append({
                "title": (r.get("title") or "").strip(),
                "href": (r.get("href") or "").strip(),
                "body": (r.get("body") or "").strip(),
            })
        return out
    except Exception as e:
        log.warning("DDG search failed for %s: %s", query, e)
        return []


def verify_parent_from_web(domain: str, allowed_parents: List[str], max_results_per_query: int = 5) -> dict:
    """
    Verify ownership using web snippets and determine direct + ultimate parent.
    Then pick a parent_match only if it matches allowed_parents (exact string). Prefer ultimate if it matches.
    Returns:
      {
        "domain": str,
        "direct_parent": str|None,
        "ultimate_parent": str|None,
        "parent_match": str|None,   # exact allowed string or None
        "match_reason": str,
        "parent_name": str|None,    # ultimate if present else direct
        "new_parent_candidate": str|None,  # if no match, parent to consider adding (ultimate preferred)
        "confidence": float,        # 0..1
        "evidence": [{"href","title","body"}, ...],
      }
    """
    domain = (domain or "").strip().lower()
    allowed = [a.strip() for a in (allowed_parents or []) if a and str(a).strip()]
    if not domain:
        return {"domain": domain, "direct_parent": None, "ultimate_parent": None, "parent_match": None, "match_reason": "no_domain", "parent_name": None, "new_parent_candidate": None, "confidence": 0.0, "evidence": []}

    # Domain-specific queries so evidence is about THIS site only (avoid confusion with other brands).
    queries = [
        f'"{domain}" owner parent company',
        f'"{domain}" acquired by who owns',
        f'site owner {domain}',
        f'{domain} owned by',
        f'{domain} parent company acquisition',
    ]
    evidence = []
    seen = set()
    for q in queries:
        for r in _ddg_search(q, max_results=max_results_per_query + 2):
            href = r.get("href") or ""
            key = (href or r.get("title") or "")[:200]
            if not key or key in seen:
                continue
            seen.add(key)
            evidence.append(r)
        if len(evidence) >= 10:
            break
    evidence = evidence[:10]

    allowed_block = "\n".join(f"- {p}" for p in allowed[:300]) or "(none)"
    ev_block = "\n".join(
        f"- {e.get('title','')}\n  {e.get('href','')}\n  {e.get('body','')[:280]}"
        for e in evidence
    ) or "(no evidence found)"

    try:
        r = create_with_retry(
            model=AI_MODEL,
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": f"""You are verifying who owns or operates the specific website {domain} based ONLY on the evidence snippets below.

CRITICAL:
- Answer ONLY for the site {domain}. Do NOT confuse it with other sites or brands (e.g. do not mix up parade.com with a different publication).
- Use ONLY information that explicitly refers to {domain} or its parent in the snippets. If a snippet is about a different domain or company, ignore it.
- Return direct_parent = the company that directly owns/operates {domain}; ultimate_parent = the TOP-LEVEL / MAIN parent company (the holding company or group that ultimately owns the brand). Example: for polygon.com, direct might be a division but ultimate_parent must be Vox Media (the main parent), NOT a subsidiary like "Group 9 Media" if that is owned by Vox. Always prefer the highest-level parent in the ownership chain.
- When both a subsidiary and its parent appear in Allowed parents, prefer matching the PARENT (e.g. Vox Media over Group 9 Media when Group 9 is owned by Vox).
- Do NOT guess. If evidence is unclear or mixed, set confidence low and only set direct_parent/ultimate_parent when clearly stated for this domain.
- Output ONLY valid JSON with keys: domain, direct_parent, ultimate_parent, confidence (number 0 to 1).

Allowed parents (use these exact strings when matching evidence to your list; prefer the main/parent company when multiple apply):
{allowed_block}

Evidence (use only snippets that clearly refer to {domain}):
{ev_block}

JSON only."""
            }],
        )
        text = (r.content[0].text or "").strip()
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
        parsed = json.loads(text)
        direct = parsed.get("direct_parent")
        if direct is not None and not isinstance(direct, str):
            direct = str(direct).strip() or None
        elif isinstance(direct, str):
            direct = direct.strip() or None
        ultimate = parsed.get("ultimate_parent")
        if ultimate is not None and not isinstance(ultimate, str):
            ultimate = str(ultimate).strip() or None
        elif isinstance(ultimate, str):
            ultimate = ultimate.strip() or None
        conf = parsed.get("confidence")
        try:
            conf = float(conf)
        except Exception:
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        parent_match = None
        match_reason = "no_allowed"
        if allowed:
            parent_match, match_reason = _match_allowed(ultimate or "", allowed)
            if not parent_match:
                pm2, mr2 = _match_allowed(direct or "", allowed)
                if pm2:
                    parent_match = pm2
                    match_reason = f"direct_{mr2}"
                else:
                    match_reason = f"ultimate_{match_reason};direct_{mr2}"
        parent_name = ultimate or direct
        new_candidate = None if parent_match else (ultimate or direct)
        return {
            "domain": domain,
            "direct_parent": direct,
            "ultimate_parent": ultimate,
            "parent_match": parent_match,
            "match_reason": match_reason,
            "parent_name": parent_name,
            "new_parent_candidate": new_candidate,
            "confidence": conf,
            "evidence": evidence,
        }
    except Exception as e:
        log.warning("verify_parent_from_web failed for %s: %s", domain, e)
        return {"domain": domain, "direct_parent": None, "ultimate_parent": None, "parent_match": None, "match_reason": "error", "parent_name": None, "new_parent_candidate": None, "confidence": 0.0, "evidence": evidence}

