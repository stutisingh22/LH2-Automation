"""
Ownership verifier — grounded parent matching using web evidence.

We use DDG search snippets (no browsing automation) and ask the AI to choose a parent ONLY from allowed_parents.
This reduces incorrect parent selection (e.g. allrecipes.com should map to Dotdash Meredith / IAC, not Red Ventures).
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import List, Dict, Tuple

import requests

from integrations.ai import create_with_retry
from config import AI_MODEL

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SEC = 4
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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
    return None, "no_match"


def _ddg_html_search(query: str, max_results: int = 6) -> List[Dict]:
    """
    Lightweight DuckDuckGo HTML search (single endpoint).
    This avoids the ddgs library's provider fallbacks (yandex/yahoo/etc.) which can be blocked in some environments.
    """
    try:
        headers = {"user-agent": USER_AGENT, "accept-language": "en-US,en;q=0.9"}
        # DDG HTML endpoint is more stable with POST
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            headers=headers,
            data={"q": query},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        if r.status_code != 200:
            raise RuntimeError(f"http_{r.status_code}")
        html = r.text or ""

        import re

        # Extract result blocks: title + href + snippet.
        # This parser is intentionally forgiving: DDG markup changes occasionally.
        links = []
        for m in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
            href = re.sub(r"&amp;", "&", (m.group(1) or "").strip())
            title = re.sub(r"<[^>]+>", "", (m.group(2) or "")).strip()
            if href and title:
                links.append((href, title))
            if len(links) >= max_results:
                break

        snippets = []
        for m in re.finditer(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', html, re.I | re.S):
            body = re.sub(r"<[^>]+>", "", (m.group(1) or "")).strip()
            if body:
                snippets.append(body)
            if len(snippets) >= max_results:
                break

        out: List[Dict] = []
        for i, (href, title) in enumerate(links[:max_results]):
            out.append({"title": title, "href": href, "body": (snippets[i] if i < len(snippets) else "")})
        return out
    except Exception as e:
        log.warning("DDG html search failed for %s: %s", query, e)
        return []


def _ddg_search(query: str, max_results: int = 6) -> List[Dict]:
    # Keep bulk import responsive: one attempt + tiny jitter.
    out = _ddg_html_search(query, max_results=max_results)
    if out:
        return out
    time.sleep(0.15 + random.random() * 0.25)
    return _ddg_html_search(query, max_results=max_results)


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
        "explicit_independent": bool,                 # true when evidence explicitly says site is independently owned/self-owned/independent operator
        "explicit_independent_confidence": float,     # 0..1
        "evidence": [{"href","title","body"}, ...],
      }
    """
    domain = (domain or "").strip().lower()
    allowed = [a.strip() for a in (allowed_parents or []) if a and str(a).strip()]
    if not domain:
        return {"domain": domain, "direct_parent": None, "ultimate_parent": None, "parent_match": None, "match_reason": "no_domain", "parent_name": None, "new_parent_candidate": None, "confidence": 0.0, "evidence": []}

    # Domain-specific queries so evidence is about THIS site only (avoid confusion with other brands).
    # Keep this small: web search is the slowest step and can be blocked in some deployments.
    queries = [
        f'{domain} owned by',
        f'"{domain}" owner parent company',
        f'"{domain}" acquired by',
        f'site owner {domain}',
        f'site:{domain} independently owned',
    ]
    evidence = []
    seen = set()
    # Hard cap total time spent collecting snippets.
    t0 = time.time()
    for q in queries:
        if time.time() - t0 > 8.0:
            break
        for r in _ddg_search(q, max_results=max_results_per_query):
            href = r.get("href") or ""
            key = (href or r.get("title") or "")[:200]
            if not key or key in seen:
                continue
            seen.add(key)
            evidence.append(r)
        if len(evidence) >= 8:
            break
    evidence = evidence[:8]

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
- Output ONLY valid JSON with keys: domain, direct_parent, ultimate_parent, confidence (number 0 to 1), explicit_independent (boolean), explicit_independent_confidence (number 0 to 1).

Definition for explicit_independent:
- Set explicit_independent=true ONLY if evidence explicitly states the site is independently owned/self-owned/independent operator (i.e. no parent/ownership company is presented as the owner/operator for {domain}).
- If a parent company is explicitly named as owning/operating {domain}, explicit_independent must be false.
- If evidence is unclear/mixed, set explicit_independent=false and explicit_independent_confidence low.

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

        explicit_independent = bool(parsed.get("explicit_independent")) if "explicit_independent" in parsed else False
        econf = parsed.get("explicit_independent_confidence")
        try:
            econf = float(econf)
        except Exception:
            econf = 0.0
        econf = max(0.0, min(1.0, econf))
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
            "explicit_independent": explicit_independent,
            "explicit_independent_confidence": econf,
            "evidence": evidence,
        }
    except Exception as e:
        log.warning("verify_parent_from_web failed for %s: %s", domain, e)
        return {"domain": domain, "direct_parent": None, "ultimate_parent": None, "parent_match": None, "match_reason": "error", "parent_name": None, "new_parent_candidate": None, "confidence": 0.0, "explicit_independent": False, "explicit_independent_confidence": 0.0, "evidence": evidence}

