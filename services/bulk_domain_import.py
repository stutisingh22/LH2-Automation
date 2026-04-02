"""
Bulk Domain Import — CSV/XLSX upload, classify independent vs media house, add to DB only.

Flow:
- Parse file → extract domains (with www trimming / normalization for existence check).
- If domain already exists (main companies or advisor/media house DB) → flag only.
- If new: AI classifies independent vs media_house.
- Independent → add to companies table.
- Media house → if parent matches existing media house → add as child to advisor_companies; else flag for review.
"""

import io
import logging
import os
import sys
import threading
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import List

import pandas as pd

# Avoid importing Streamlit under uvicorn (it produces noisy runtime warnings).
if any("uvicorn" in (a or "").lower() for a in sys.argv):
    st = None  # type: ignore
else:
    import streamlit as st

from config import normalize_domain
from db_ops import (
    get_company_by_domain,
    upsert_company,
    delete_company_by_domain,
    get_campaign,
    merge_company_extra_data,
    merge_advisor_company_extra_data,
)
from db_ops import SessionFactory
from db_ops import (
    get_advisor_company_by_domain, upsert_advisor_company, delete_advisor_company,
    get_or_create_advisor_company_for_parent, delete_advisor_parent,
)
from integrations import (
    classify_domains_independent_or_media_house,
    classify_entries_independent_or_media_house,
    verify_parent_from_web,
)
from services.ui_data_loader import clear_ui_cache, load_advisor_parents_with_children
from services.cache_registry import invalidate_group
from models import AdvisorCompany, Company

log = logging.getLogger(__name__)

# Column names we treat as domain (first match wins)
DOMAIN_COLUMN_CANDIDATES = ("domain", "Domain", "website", "Website", "url", "URL", "site", "Site", "domain_name")
SAFE_MIN_PARENT_KEY_LEN = 6
DEFAULT_JUDGMENT_STAGE = os.getenv("BULK_IMPORT_JUDGMENT_STAGE", "stage_b").strip().lower()
WEB_VERIFY_WORKERS = int(os.getenv("BULK_WEB_VERIFY_WORKERS", "20"))
# When a classifier row is uncertain (low confidence or flagged), we route it to web verification.
# Keep this threshold consistent across both analyze_from_domains and analyze_from_entries.
WEB_VERIFY_CONF_TRIGGER = float(os.getenv("BULK_WEB_VERIFY_CONF_TRIGGER", "0.65"))
# Run web verification for every new row by default. This is accuracy-first and avoids
# over-confident model misclassifications skipping verification.
BULK_VERIFY_ALL_NEW_ROWS = os.getenv("BULK_VERIFY_ALL_NEW_ROWS", "true").strip().lower() in {"1", "true", "yes", "on"}
PARENT_ALIAS_MAP = {
    "conde nast": "Condé Nast",
    "conde nast media": "Condé Nast",
    "vox media group": "Vox Media",
    "dow jones and company": "Dow Jones",
    "ny times": "The New York Times Company",
    "new york times": "The New York Times Company",
}

def _norm_parent_key(name: str) -> str:
    """Normalize parent/company names for matching (case/space/punctuation-insensitive)."""
    s = (name or "").strip().lower()
    return "".join(ch for ch in s if ch.isalnum())


def _as_float(val, default: float = 0.0) -> float:
    try:
        out = float(val)
    except (TypeError, ValueError):
        return default
    if out < 0:
        return 0.0
    if out > 1:
        return 1.0
    return out


def _extract_evidence_urls(ver: dict | None) -> str:
    if not isinstance(ver, dict):
        return ""
    ev = ver.get("evidence") or []
    return "\n".join([e.get("href", "") for e in ev[:5] if isinstance(e, dict) and e.get("href")])


def _suggest_parent_from_data(ai_parent_name: str, ai_parent_match: str, existing_parents: List[str]) -> tuple[str, str, str, float, bool]:
    """
    Suggest a parent from the existing list only.
    Returns (suggested_parent, reason, match_method, match_score, requires_review).
    Never returns a value not present in existing_parents.
    """
    if not existing_parents:
        return "", "no existing parents", "none", 0.0, True
    existing = list(existing_parents)
    by_display = {p: _norm_parent_key(p) for p in existing if p}
    by_key = {_norm_parent_key(p): p for p in existing if p}

    # Deterministic exact parent_match from model (if it is truly present) is safe.
    pm = (ai_parent_match or "").strip()
    if pm and pm in by_display:
        return pm, f"exact_allowed_parent_match({pm})", "exact_allowed_match", 1.0, False

    # Do NOT trust model-provided freeform matches by themselves; they must be deterministic.

    k = _norm_parent_key(ai_parent_name or "")
    if k and k in by_key:
        return by_key[k], f"normalized_key_match({ai_parent_name}→{by_key[k]})", "normalized_key_match", 0.98, False

    # Alias map supports high-signal common parent naming variants.
    alias_target = PARENT_ALIAS_MAP.get((ai_parent_name or "").strip().lower())
    if alias_target and alias_target in by_display:
        return alias_target, f"alias_map_match({ai_parent_name}→{alias_target})", "alias_map_match", 0.95, False

    # Containment is ambiguous by nature: return candidate but force review.
    if k:
        for ek, disp in by_key.items():
            if not ek:
                continue
            if min(len(ek), len(k)) < SAFE_MIN_PARENT_KEY_LEN:
                continue
            if ek in k or k in ek:
                return disp, f"ambiguous_contains({ai_parent_name}→{disp})", "contains_ambiguous", 0.55, True

    return "", "no_safe_match", "none", 0.0, True


def _decision_stage() -> str:
    stage = (DEFAULT_JUDGMENT_STAGE or "stage_b").lower()
    if stage not in {"stage_a", "stage_b", "stage_c"}:
        return "stage_b"
    return stage


def _build_feedback_event(row: dict, final_classification: str, final_parent: str, campaign_id: int | None) -> dict:
    return {
        "source": "bulk_import",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": (row.get("input") or "").strip(),
        "domain": (row.get("domain") or "").strip(),
        "predicted_classification": (row.get("classification") or "").strip(),
        "final_classification": final_classification,
        "predicted_parent": (row.get("parent_name") or "").strip(),
        "final_parent": final_parent,
        "decision_reason": (row.get("decision_reason") or "").strip(),
        "decision_confidence": _as_float(row.get("decision_confidence"), 0.0),
        "campaign_id": int(campaign_id) if campaign_id is not None else None,
    }


def resolve_import_judgment(
    *,
    classified_row: dict,
    exists_db: bool,
    existing_advisor: dict | None,
    existing_parent: str,
    existing_parents: List[str],
    verify_result: dict | None = None,
) -> dict:
    """
    Deterministic policy layer for analyze output.
    """
    decision = {
        "classification": "independent",
        "parent_name": "",
        "parent_suggested": "",
        "parent_confirmed": "",
        "match_reason": "",
        "match_method": "none",
        "match_score": 0.0,
        "evidence": _extract_evidence_urls(verify_result),
        "new_parent_candidate": "",
        "requires_review": False,
        "decision_reason": "",
        "decision_confidence": 0.0,
        "ownership_rationale": "",
        "needs_review": False,
        "is_parent_company": False if exists_db else bool(classified_row.get("is_parent_company")),
    }

    if exists_db:
        if existing_advisor:
            decision["classification"] = "media_house"
            decision["parent_name"] = existing_parent
            decision["parent_suggested"] = existing_parent
            decision["parent_confirmed"] = existing_parent
            decision["match_reason"] = "existing_in_db"
            decision["match_method"] = "db_override"
            decision["match_score"] = 1.0
            decision["decision_reason"] = "db_authoritative_media_house"
            decision["decision_confidence"] = 1.0
            return decision
        decision["classification"] = "independent"
        decision["decision_reason"] = "db_authoritative_independent"
        decision["decision_confidence"] = 1.0
        decision["match_reason"] = "existing_in_db (independent)"
        decision["match_method"] = "db_override"
        decision["match_score"] = 1.0
        return decision

    cl = (classified_row.get("classification") or "").strip().lower().replace(" ", "_")
    if cl not in ("independent", "media_house"):
        cl = "independent"
        decision["needs_review"] = True
    parent = (classified_row.get("parent_name") or "").strip()
    parent_match = (classified_row.get("parent_match") or "").strip()
    confidence = _as_float(classified_row.get("ownership_confidence"), 0.0)
    model_needs_review = bool(classified_row.get("needs_review"))
    rationale = (classified_row.get("ownership_rationale") or "").strip()

    decision["classification"] = cl
    decision["parent_name"] = parent
    decision["decision_confidence"] = confidence
    decision["ownership_rationale"] = rationale
    decision["needs_review"] = model_needs_review

    # Web evidence override: if the verifier explicitly finds this site is independently owned/self-owned,
    # treat it as independent regardless of classifier guess.
    if verify_result and verify_result.get("explicit_independent"):
        direct_parent = verify_result.get("direct_parent")
        ultimate_parent = verify_result.get("ultimate_parent")
        parent_match_ev = verify_result.get("parent_match")
        parent_evidence = bool(direct_parent) or bool(ultimate_parent) or bool(parent_match_ev)
        if not parent_evidence:
            decision["classification"] = "independent"
            decision["parent_name"] = ""
            decision["parent_suggested"] = ""
            decision["parent_confirmed"] = ""
            decision["match_reason"] = "web_explicit_independent"
            decision["match_method"] = "web_explicit_independent"
            decision["requires_review"] = False
            decision["decision_reason"] = "web_explicit_independent"
            ev_conf = _as_float(verify_result.get("explicit_independent_confidence"), 0.0)
            decision["decision_confidence"] = max(confidence, ev_conf)
            return decision
        decision["requires_review"] = True
        decision["decision_reason"] = "conflicting_web_explicit_independent_vs_parent"
        return decision

    is_parent_company = bool(classified_row.get("is_parent_company"))
    if is_parent_company and parent:
        decision["parent_suggested"] = parent
        decision["parent_confirmed"] = parent
        decision["match_reason"] = "parent_company (add as new parent)"
        decision["match_method"] = "parent_company"
        decision["match_score"] = 1.0
        decision["decision_reason"] = "parent_company_candidate"
        decision["requires_review"] = False
        decision["classification"] = "media_house"
        decision["decision_confidence"] = max(confidence, 0.9)
        return decision

    if cl == "media_house":
        if verify_result and str(verify_result.get("match_reason") or "").strip().lower() == "error":
            decision["requires_review"] = True
            decision["decision_reason"] = "web_verification_error"
            decision["match_method"] = "web_verification_error"
            decision["match_reason"] = "web_verification_error"
            return decision
        if verify_result and verify_result.get("parent_match"):
            matched = str(verify_result["parent_match"]).strip()
            decision["parent_suggested"] = matched
            decision["parent_confirmed"] = matched
            decision["parent_name"] = matched
            decision["match_reason"] = f"web_verified({verify_result.get('match_reason','')},conf={_as_float(verify_result.get('confidence'), 0.0):.2f})"
            decision["match_method"] = "web_verified"
            web_conf = _as_float(verify_result.get("confidence"), 0.0)
            decision["match_score"] = web_conf
            decision["decision_confidence"] = max(confidence, web_conf)
            # Tighten auto-acceptance: low-confidence "web verified" still needs manual confirmation.
            decision["requires_review"] = web_conf < 0.8
            decision["decision_reason"] = (
                "web_verified_parent_match" if web_conf >= 0.8 else "web_verified_low_confidence_parent_match"
            )
            return decision

        # Web verifier says there is no parent detected for this domain.
        # In this case, don't let model-provided parent strings drive the result.
        if verify_result is not None:
            web_conf = _as_float(verify_result.get("confidence"), 0.0)
            direct_parent = (verify_result.get("direct_parent") or "").strip()
            ultimate_parent = (verify_result.get("ultimate_parent") or "").strip()
            if not direct_parent and not ultimate_parent:
                decision["classification"] = "independent"
                decision["parent_name"] = ""
                decision["parent_suggested"] = ""
                decision["parent_confirmed"] = ""
                decision["match_reason"] = "web_no_parent_detected"
                decision["match_method"] = "web_no_parent_detected"
                decision["match_score"] = web_conf
                decision["ownership_rationale"] = (rationale or decision["ownership_rationale"]).strip()
                decision["decision_reason"] = "web_no_parent_detected"
                # Hard stop: "no parent found" is unverified, never auto-commit.
                decision["requires_review"] = True
                decision["decision_confidence"] = max(confidence, web_conf)
                return decision

        if verify_result and (verify_result.get("direct_parent") or verify_result.get("ultimate_parent")):
            decision["parent_name"] = str(verify_result.get("ultimate_parent") or verify_result.get("direct_parent") or "").strip()
            decision["new_parent_candidate"] = str(verify_result.get("new_parent_candidate") or "").strip()

        # Use verifier's parent strings as the primary signal for mapping into allowed parents.
        # This avoids mis-assignments when the classifier guessed a wrong parent_name string.
        verifier_parent_name = (verify_result.get("parent_name") or "").strip() if isinstance(verify_result, dict) else ""
        verifier_parent_match = (verify_result.get("parent_match") or "").strip() if isinstance(verify_result, dict) else ""
        ai_parent_name = verifier_parent_name or parent
        ai_parent_match = verifier_parent_match or parent_match

        suggested, reason, method, score, ambiguous = _suggest_parent_from_data(ai_parent_name, ai_parent_match, existing_parents)
        decision["parent_suggested"] = suggested
        decision["parent_confirmed"] = suggested
        decision["match_reason"] = reason
        decision["match_method"] = method
        decision["match_score"] = score
        if suggested:
            decision["parent_name"] = suggested
        if ambiguous:
            decision["requires_review"] = True
            decision["decision_reason"] = "ambiguous_parent_match"
        elif not suggested:
            decision["requires_review"] = True
            decision["decision_reason"] = "no_parent_evidence"
        else:
            # Even when parent matching is deterministic, require adequate model confidence
            # to avoid auto-committing questionable parent mappings.
            decision["requires_review"] = confidence < 0.65
            decision["decision_reason"] = (
                "deterministic_parent_match" if confidence >= 0.65 else "low_confidence_media_house_parent_match"
            )
            decision["decision_confidence"] = max(confidence, score)
        if model_needs_review:
            decision["requires_review"] = True
            decision["decision_reason"] = "model_marked_uncertain"
        return decision

    # independent branch
    if verify_result is not None:
        if str(verify_result.get("match_reason") or "").strip().lower() == "error":
            decision["classification"] = "independent"
            decision["decision_reason"] = "web_verification_error"
            decision["requires_review"] = True
            return decision
        web_conf = _as_float(verify_result.get("confidence"), 0.0)
        direct_parent = (verify_result.get("direct_parent") or "").strip()
        ultimate_parent = (verify_result.get("ultimate_parent") or "").strip()
        parent_match_ev = (verify_result.get("parent_match") or "").strip() if verify_result.get("parent_match") is not None else ""

        parent_evidence = bool(direct_parent) or bool(ultimate_parent) or bool(parent_match_ev)
        if not parent_evidence:
            # Hard stop: "no parent found" during web verification is unverified → never auto-commit.
            decision["classification"] = "independent"
            decision["decision_reason"] = "web_no_parent_detected_unverified"
            decision["requires_review"] = True
            decision["decision_confidence"] = max(confidence, web_conf)
            return decision

        # Parent evidence exists, so flip to media_house even if the classifier guessed independent.
        verifier_parent_name = (
            (verify_result.get("parent_name") or "").strip()
            or (verify_result.get("ultimate_parent") or "").strip()
            or (verify_result.get("direct_parent") or "").strip()
        )
        verifier_parent_match = parent_match_ev or ""

        suggested, reason, method, score, ambiguous = _suggest_parent_from_data(
            verifier_parent_name or parent,
            verifier_parent_match or parent_match,
            existing_parents,
        )
        decision["classification"] = "media_house"
        decision["parent_suggested"] = suggested
        decision["parent_confirmed"] = suggested
        decision["match_reason"] = reason
        decision["match_method"] = method
        decision["match_score"] = score
        if suggested:
            decision["parent_name"] = suggested

        if verifier_parent_match:
            decision["decision_reason"] = "web_verified_parent_match" if web_conf >= 0.8 else "web_verified_low_confidence_parent_match"
            decision["requires_review"] = web_conf < 0.8 or ambiguous or not suggested
        else:
            decision["decision_reason"] = (
                "deterministic_parent_match" if (suggested and not ambiguous) else "web_parent_detected_requires_review"
            )
            decision["requires_review"] = (not suggested) or ambiguous or web_conf < 0.8

        decision["decision_confidence"] = max(confidence, web_conf, score)
        if model_needs_review:
            decision["requires_review"] = True
            decision["decision_reason"] = "model_marked_uncertain"
        return decision

    decision["decision_reason"] = "model_independent"
    if model_needs_review or confidence < 0.65:
        decision["requires_review"] = True
        decision["decision_reason"] = "low_confidence_independent"
    return decision


def _append_audit_event(extra_data: dict, event: dict) -> dict:
    events = list(extra_data.get("bulk_judgment_events") or [])
    if len(events) >= 50:
        events = events[-49:]
    events.append(event)
    extra_data["bulk_judgment_events"] = events
    return extra_data


def get_bulk_judgment_baseline(days: int = 28) -> dict:
    """
    Baseline snapshot from stored judgment/reclassification metadata.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(days or 1), 1))
    session = SessionFactory()
    try:
        companies = session.query(Company).all()
        advisors = session.query(AdvisorCompany).all()
        imported = 0
        reclassified = 0
        overrides = 0
        review_required = 0
        wrong_parent = 0
        for c in companies:
            if not c.created_at or c.created_at.replace(tzinfo=timezone.utc) < cutoff:
                continue
            extra = c.extra_data if isinstance(c.extra_data, dict) else {}
            if extra.get("bulk_import_source") == "bulk_import":
                imported += 1
                if extra.get("bulk_requires_review"):
                    review_required += 1
                if extra.get("bulk_override_used"):
                    overrides += 1
            if extra.get("reclassified_from"):
                reclassified += 1
        for a in advisors:
            if not a.created_at or a.created_at.replace(tzinfo=timezone.utc) < cutoff:
                continue
            extra = a.extra_data if isinstance(a.extra_data, dict) else {}
            if extra.get("bulk_import_source") == "bulk_import":
                imported += 1
                if extra.get("bulk_requires_review"):
                    review_required += 1
                if extra.get("bulk_override_used"):
                    overrides += 1
            if extra.get("reclassified_from"):
                reclassified += 1
                if extra.get("reclassified_reason") and "parent" in str(extra.get("reclassified_reason")).lower():
                    wrong_parent += 1
        return {
            "window_days": max(int(days or 1), 1),
            "imported_rows": imported,
            "reclassified_rows": reclassified,
            "review_required_rows": review_required,
            "override_rows": overrides,
            "wrong_parent_reclassifications": wrong_parent,
            "reclassification_rate": round((reclassified / imported), 4) if imported else 0.0,
            "review_rate": round((review_required / imported), 4) if imported else 0.0,
            "override_rate": round((overrides / imported), 4) if imported else 0.0,
        }
    finally:
        session.close()


def _parse_domains_from_file(uploaded_file) -> List[str]:
    """Parse CSV or XLSX and return list of domain-like strings (raw, not yet normalized)."""
    raw = uploaded_file.read()
    name = (uploaded_file.name or "").lower()
    domains = []
    try:
        if name.endswith(".xlsx") or name.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
        else:
            df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        log.warning("Parse file failed: %s", e)
        return []
    if df.empty:
        return []
    # Find domain column
    col = None
    for c in DOMAIN_COLUMN_CANDIDATES:
        if c in df.columns:
            col = c
            break
    if col is None:
        col = df.columns[0]
    for v in df[col].dropna().astype(str).str.strip():
        v = (v or "").strip()
        if not v or v in ("nan", "NaN", ""):
            continue
        # Basic domain-like: has a dot and no spaces
        if "." in v and " " not in v:
            domains.append(v)
    return list(dict.fromkeys(domains))  # preserve order, dedupe


def _parse_entries_from_text(text: str) -> List[str]:
    """Parse manual input: newline or comma separated names/domains; strip and dedupe."""
    if not text or not text.strip():
        return []
    parts = []
    for line in text.replace(",", "\n").splitlines():
        for part in line.split(","):
            p = (part or "").strip()
            if p and p not in ("nan", "NaN"):
                parts.append(p)
    return list(dict.fromkeys(parts))


def get_existing_parent_media_houses() -> List[str]:
    """Return list of existing parent media house names (display names). Uses cached loader for performance."""
    out = []
    for group in load_advisor_parents_with_children():
        p = (group.get("parent_name") or "").strip()
        if p and p != "(No parent)":
            out.append(p)
    return sorted(list(dict.fromkeys(out)))


def analyze_from_file(uploaded_file, verify_ownership: bool = True, max_verify: int = 1000) -> dict:
    """Analyze a CSV/XLSX and return preview rows; no writes."""
    domains_raw = _parse_domains_from_file(uploaded_file)
    return analyze_from_domains(domains_raw, verify_ownership=verify_ownership, max_verify=max_verify)


def analyze_from_text(text: str, verify_ownership: bool = True, max_verify: int = 1000) -> dict:
    """Analyze manual input (names/domains) and return preview rows; no writes."""
    entries = _parse_entries_from_text(text or "")
    return analyze_from_entries(entries, verify_ownership=verify_ownership, max_verify=max_verify)


def analyze_from_domains(domains_raw: List[str], verify_ownership: bool = True, max_verify: int = 1000) -> dict:
    """Analyze list of domains (strings). Returns {"rows": [...], "errors": [...]}. No writes."""
    result = {"rows": [], "errors": []}
    if not domains_raw:
        result["errors"].append("No domains found.")
        return result

    # Normalize and dedupe
    seen = set()
    domains = []
    for d in domains_raw:
        norm = normalize_domain(d)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        domains.append(norm)

    existing_parents = get_existing_parent_media_houses()
    classified = classify_domains_independent_or_media_house(domains, allowed_parents=existing_parents)
    by_dom = {normalize_domain(c.get("domain") or ""): c for c in classified if c}

    # Precompute existence + classifier rows, then run web verification in parallel
    # for uncertain rows in a bounded worker pool.
    existence_by_domain: dict[str, dict] = {}
    for d in domains:
        existing_company = get_company_by_domain(d)
        existing_advisor = get_advisor_company_by_domain(d)
        exists_db = bool(existing_company or existing_advisor)
        exists = bool(exists_db)
        existing_parent = (existing_advisor or {}).get("parent_name") or ""
        existing_parent = (existing_parent or "").strip() if existing_parent else ""
        existence_by_domain[d] = {
            "exists_db": exists_db,
            "exists": exists,
            "existing_advisor": existing_advisor,
            "existing_parent": existing_parent,
        }

    def _row_needs_web_verify(c: dict) -> bool:
        # Trigger web verification for BOTH directions when uncertain:
        # low confidence or model-marked uncertain.
        if BULK_VERIFY_ALL_NEW_ROWS:
            return True
        model_needs_review = bool(c.get("needs_review"))
        conf = _as_float(c.get("ownership_confidence"), 0.0)
        return model_needs_review or conf < WEB_VERIFY_CONF_TRIGGER

    to_verify: list[str] = []
    ver_candidate_by_dom: dict[str, dict] = {}
    for d in domains:
        ctx = existence_by_domain.get(d) or {}
        if ctx.get("exists_db"):
            continue
        c = by_dom.get(d) or {}
        if not verify_ownership:
            continue
        if max_verify <= 0:
            break
        if _row_needs_web_verify(c):
            to_verify.append(d)
            ver_candidate_by_dom[d] = c
            max_verify -= 1

    # cap already applied; no need for more.
    verify_results: dict[str, dict] = {}
    if to_verify:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WEB_VERIFY_WORKERS) as ex:
            future_by_dom = {
                ex.submit(verify_parent_from_web, dom, existing_parents): dom for dom in to_verify
            }
            for fut in concurrent.futures.as_completed(future_by_dom):
                dom = future_by_dom[fut]
                try:
                    verify_results[dom] = fut.result()
                except Exception as e:
                    log.warning("web ownership verify failed for %s: %s", dom, e)
                    verify_results[dom] = {
                        "domain": dom,
                        "direct_parent": None,
                        "ultimate_parent": None,
                        "parent_match": None,
                        "match_reason": "error",
                        "parent_name": None,
                        "new_parent_candidate": None,
                        "confidence": 0.0,
                        "explicit_independent": False,
                        "explicit_independent_confidence": 0.0,
                        "evidence": [],
                    }

    for d in domains:
        ctx = existence_by_domain.get(d) or {}
        exists_db = bool(ctx.get("exists_db"))
        exists = bool(ctx.get("exists"))
        existing_advisor = ctx.get("existing_advisor")
        existing_parent = ctx.get("existing_parent") or ""

        c = by_dom.get(d) or {}
        ver = verify_results.get(d)
        decision = resolve_import_judgment(
            classified_row=c,
            exists_db=exists_db,
            existing_advisor=existing_advisor,
            existing_parent=existing_parent,
            existing_parents=existing_parents,
            verify_result=ver,
        )
        result["rows"].append({
            "do_import": not exists,
            "input": d,
            "domain": d,
            "exists": exists,
            "exists_source": ("db" if exists_db else ""),
            "classification": decision["classification"],
            "parent_name": decision["parent_name"],
            "parent_suggested": decision["parent_suggested"],
            "match_reason": decision["match_reason"],
            "match_method": decision["match_method"],
            "match_score": decision["match_score"],
            "evidence": decision["evidence"],
            "new_parent_candidate": decision["new_parent_candidate"],
            "parent_confirmed": decision["parent_confirmed"],
            "is_parent_company": decision["is_parent_company"],
            "requires_review": decision["requires_review"],
            "decision_reason": decision["decision_reason"],
            "decision_confidence": decision["decision_confidence"],
            "ownership_rationale": decision["ownership_rationale"],
            "needs_review": decision["needs_review"],
            "status": "exists" if exists else "new",
        })
    result["existing_parents"] = existing_parents
    result["quality_baseline"] = get_bulk_judgment_baseline(28)
    return result


def analyze_from_entries(entries: List[str], verify_ownership: bool = True, max_verify: int = 1000) -> dict:
    """Analyze list of names/domains. Returns preview rows; no writes."""
    result = {"rows": [], "errors": []}
    entries = [e.strip() for e in (entries or []) if e and str(e).strip()]
    if not entries:
        result["errors"].append("No names or domains entered.")
        return result

    existing_parents = get_existing_parent_media_houses()
    classified = classify_entries_independent_or_media_house(entries, allowed_parents=existing_parents)
    # map by exact input (lower) to keep order
    by_inp = {(c.get("input") or "").strip().lower(): c for c in (classified or []) if isinstance(c, dict)}
    # Build per-entry contexts; verify in parallel for uncertain domains.
    entry_ctx: list[dict] = []
    for inp in entries:
        c = by_inp.get(inp.strip().lower()) or {}
        dom = normalize_domain(c.get("domain") or inp)
        existing_company = get_company_by_domain(dom)
        existing_advisor = get_advisor_company_by_domain(dom)
        exists_db = bool(existing_company or existing_advisor)
        exists = bool(exists_db)
        existing_parent = (existing_advisor or {}).get("parent_name") or ""
        existing_parent = (existing_parent or "").strip() if existing_parent else ""
        entry_ctx.append({
            "inp": inp,
            "c": c,
            "dom": dom,
            "exists_db": exists_db,
            "exists": exists,
            "existing_advisor": existing_advisor,
            "existing_parent": existing_parent,
        })

    def _row_needs_web_verify(c: dict) -> bool:
        if BULK_VERIFY_ALL_NEW_ROWS:
            return True
        model_needs_review = bool(c.get("needs_review"))
        conf = _as_float(c.get("ownership_confidence"), 0.0)
        return model_needs_review or conf < WEB_VERIFY_CONF_TRIGGER

    to_verify: list[str] = []
    queued: set[str] = set()
    for row in entry_ctx:
        dom = row.get("dom")
        if not dom:
            continue
        if row.get("exists_db"):
            continue
        if not verify_ownership:
            continue
        if max_verify <= 0:
            break
        c = row.get("c") or {}
        if _row_needs_web_verify(c) and dom not in queued:
            queued.add(dom)
            to_verify.append(dom)
            max_verify -= 1

    verify_results: dict[str, dict] = {}
    if to_verify:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WEB_VERIFY_WORKERS) as ex:
            future_by_dom = {ex.submit(verify_parent_from_web, dom, existing_parents): dom for dom in to_verify}
            for fut in concurrent.futures.as_completed(future_by_dom):
                dom = future_by_dom[fut]
                try:
                    verify_results[dom] = fut.result()
                except Exception as e:
                    log.warning("web ownership verify failed for %s: %s", dom, e)
                    verify_results[dom] = {
                        "domain": dom,
                        "direct_parent": None,
                        "ultimate_parent": None,
                        "parent_match": None,
                        "match_reason": "error",
                        "parent_name": None,
                        "new_parent_candidate": None,
                        "confidence": 0.0,
                        "explicit_independent": False,
                        "explicit_independent_confidence": 0.0,
                        "evidence": [],
                    }

    for row in entry_ctx:
        inp = row["inp"]
        c = row["c"]
        dom = row["dom"]
        exists_db = bool(row["exists_db"])
        exists = bool(row["exists"])
        existing_advisor = row["existing_advisor"]
        existing_parent = row["existing_parent"] or ""
        ver = verify_results.get(dom)

        decision = resolve_import_judgment(
            classified_row=c,
            exists_db=exists_db,
            existing_advisor=existing_advisor,
            existing_parent=existing_parent,
            existing_parents=existing_parents,
            verify_result=ver,
        )
        result["rows"].append({
            "do_import": not exists,
            "input": inp,
            "domain": dom,
            "exists": exists,
            "exists_source": ("db" if exists_db else ""),
            "classification": decision["classification"],
            "parent_name": decision["parent_name"],
            "parent_suggested": decision["parent_suggested"],
            "match_reason": decision["match_reason"],
            "match_method": decision["match_method"],
            "match_score": decision["match_score"],
            "evidence": decision["evidence"],
            "new_parent_candidate": decision["new_parent_candidate"],
            "parent_confirmed": decision["parent_confirmed"],
            "is_parent_company": decision["is_parent_company"],
            "requires_review": decision["requires_review"],
            "decision_reason": decision["decision_reason"],
            "decision_confidence": decision["decision_confidence"],
            "ownership_rationale": decision["ownership_rationale"],
            "needs_review": decision["needs_review"],
            "status": "exists" if exists else "new",
        })
    result["existing_parents"] = existing_parents
    result["quality_baseline"] = get_bulk_judgment_baseline(28)
    return result


def _append_campaign_id_patch(current: dict, campaign_id: int) -> dict:
    ids = list(current.get("imported_campaign_ids") or [])
    if int(campaign_id) not in ids:
        ids.append(int(campaign_id))
    current["imported_campaign_ids"] = ids
    current["last_imported_campaign_id"] = int(campaign_id)
    return current


def commit_rows(rows: List[dict], allow_new_parents: bool = False, campaign_id: int | None = None) -> dict:
    """
    Commit reviewed rows into DB only. Requires explicit parent_confirmed for media_house rows.
    Returns summary + created items for undo.
    """
    result = {
        "added_independent": [],
        "added_media_house": [],
        "flagged_already_exists": [],
        "flagged_for_review": [],
        "errors": [],
        "created": [],  # for undo: list of dicts
        "overrides_used": [],
        "blocked_for_review_details": [],
        "judgment_stage": _decision_stage(),
    }
    if campaign_id is not None:
        if not get_campaign(int(campaign_id)):
            result["errors"].append("campaign_id not found")
            return result
    existing_parents = set(get_existing_parent_media_houses())
    syndication_targets_independent: list[tuple[int, str, str]] = []
    syndication_targets_media: list[tuple[int, str, str]] = []

    for r in rows or []:
        if not r.get("do_import"):
            continue
        dom = normalize_domain(r.get("domain") or "")
        if not dom:
            continue
        label = (r.get("input") or dom).strip()
        # existence check again at commit time
        if get_company_by_domain(dom) or get_advisor_company_by_domain(dom):
            result["flagged_already_exists"].append(label)
            continue
        cl = (r.get("classification") or "").strip().lower().replace(" ", "_")
        if cl not in ("independent", "media_house"):
            cl = "independent"
        requires_review = bool(r.get("requires_review"))
        decision_confidence = _as_float(r.get("decision_confidence"), 0.0)
        override_review_block = bool(r.get("override_review_block"))
        stage = _decision_stage()
        # Stage B keeps bulk import fast but still blocks low-confidence classifications for manual review.
        should_gate = (stage == "stage_c") or (stage == "stage_b" and requires_review)
        if should_gate and requires_review and not override_review_block:
            result["flagged_for_review"].append(label)
            result["blocked_for_review_details"].append({
                "label": label,
                "domain": dom,
                "classification": cl,
                "decision_reason": (r.get("decision_reason") or "").strip(),
                "decision_confidence": decision_confidence,
            })
            continue
        if override_review_block and requires_review:
            result["overrides_used"].append(label)

        if cl == "independent":
            try:
                company_id = upsert_company(dom, name=label, notion_page_id=None)
                if company_id:
                    syndication_targets_independent.append((int(company_id), dom, label))
                    existing = (get_company_by_domain(dom) or {}).get("extra_data") or {}
                    patch = dict(existing) if isinstance(existing, dict) else {}
                    if campaign_id is not None:
                        patch = _append_campaign_id_patch(patch, int(campaign_id))
                    patch["bulk_import_source"] = "bulk_import"
                    patch["bulk_requires_review"] = requires_review
                    patch["bulk_override_used"] = override_review_block
                    patch["bulk_judgment_stage"] = stage
                    patch["bulk_decision_confidence"] = decision_confidence
                    patch["bulk_decision_reason"] = (r.get("decision_reason") or "").strip()
                    patch = _append_audit_event(
                        patch,
                        _build_feedback_event(r, "independent", "", campaign_id),
                    )
                    merge_company_extra_data(int(company_id), patch)
                result["added_independent"].append(label)
                result["created"].append({
                    "type": "independent",
                    "domain": dom,
                    "company_id": company_id,
                    "notion_page_id": None,
                    "campaign_id": int(campaign_id) if campaign_id is not None else None,
                })
            except Exception as e:
                log.exception("Commit independent failed for %s: %s", dom, e)
                result["errors"].append(f"{label}: {e}")
        else:
            parent_confirmed = (r.get("parent_confirmed") or "").strip()
            is_parent_company = bool(r.get("is_parent_company"))
            # Safety: do not write media_house unless parent is explicitly selected and exists (or allow_new_parents)
            if not parent_confirmed:
                result["flagged_for_review"].append(label)
                continue
            if (parent_confirmed not in existing_parents) and (not allow_new_parents):
                result["flagged_for_review"].append(label)
                continue
            try:
                if is_parent_company:
                    # Add as new parent only (no child row); parent row is created so it appears in Media Houses list.
                    get_or_create_advisor_company_for_parent(parent_confirmed)
                    result["added_media_house"].append(label)
                    result["created"].append({
                        "type": "media_house_parent",
                        "parent_name": parent_confirmed,
                    })
                else:
                    advisor_id = upsert_advisor_company(dom, parent_confirmed, notion_page_id=None)
                    if advisor_id:
                        syndication_targets_media.append((int(advisor_id), dom, label))
                        existing_adv = (get_advisor_company_by_domain(dom) or {}).get("extra_data") or {}
                        patch_adv = dict(existing_adv) if isinstance(existing_adv, dict) else {}
                        if campaign_id is not None:
                            patch_adv = _append_campaign_id_patch(patch_adv, int(campaign_id))
                        patch_adv["bulk_import_source"] = "bulk_import"
                        patch_adv["bulk_requires_review"] = requires_review
                        patch_adv["bulk_override_used"] = override_review_block
                        patch_adv["bulk_judgment_stage"] = stage
                        patch_adv["bulk_decision_confidence"] = decision_confidence
                        patch_adv["bulk_decision_reason"] = (r.get("decision_reason") or "").strip()
                        patch_adv = _append_audit_event(
                            patch_adv,
                            _build_feedback_event(r, "media_house", parent_confirmed, campaign_id),
                        )
                        merge_advisor_company_extra_data(int(advisor_id), patch_adv)
                    result["added_media_house"].append(label)
                    result["created"].append({
                        "type": "media_house",
                        "domain": dom,
                        "parent_name": parent_confirmed,
                        "advisor_id": advisor_id,
                        "notion_page_id": None,
                        "campaign_id": int(campaign_id) if campaign_id is not None else None,
                    })
            except Exception as e:
                log.exception("Commit media_house failed for %s: %s", dom, e)
                result["errors"].append(f"{label}: {e}")

    # Post-commit async syndication + Similarweb traffic (rate-limited):
    # keeps bulk commit fast; persists platform presence and traffic after commit.
    def _run_post_import_analytics_async(
        ind_targets: list[tuple[int, str, str]],
        media_targets: list[tuple[int, str, str]],
    ):
        import random
        import time

        try:
            from integrations.analytics import (
                apply_syndication_refresh_to_company,
                apply_syndication_refresh_to_advisor_company,
            )
            from integrations.analytics.platform_analytics import (
                apply_similarweb_traffic_to_advisor_company,
                apply_similarweb_traffic_to_company,
            )

            for cid, dom, label in ind_targets:
                try:
                    apply_syndication_refresh_to_company(cid, dom, publisher_name=label)
                except Exception as e:
                    log.warning("Syndication refresh failed for company %s (%s): %s", cid, dom, e)
            for aid, dom, label in media_targets:
                try:
                    apply_syndication_refresh_to_advisor_company(aid, dom, publisher_name=label)
                except Exception as e:
                    log.warning("Syndication refresh failed for advisor company %s (%s): %s", aid, dom, e)

            traffic_jobs: list[tuple[str, tuple[int, str, str]]] = (
                [("independent", t) for t in ind_targets]
                + [("media", t) for t in media_targets]
            )
            for i, (kind, (eid, dom, _label)) in enumerate(traffic_jobs):
                try:
                    if kind == "independent":
                        _sw_ok, sw_err = apply_similarweb_traffic_to_company(eid, dom)
                    else:
                        _sw_ok, sw_err = apply_similarweb_traffic_to_advisor_company(eid, dom)
                    if sw_err == "cookie_expired":
                        log.warning("Stopping bulk-import Similarweb traffic run: cookie expired")
                        break
                except Exception as e:
                    log.warning(
                        "Similarweb traffic refresh failed for %s id=%s (%s): %s",
                        kind,
                        eid,
                        dom,
                        e,
                    )
                if i < len(traffic_jobs) - 1:
                    time.sleep(random.uniform(2.0, 5.0))
        except Exception as e:
            log.warning("Post-import analytics stage skipped: %s", e)
        finally:
            try:
                clear_ui_cache()
                invalidate_group("advisor")
            except Exception as e:
                log.warning("Cache refresh after bulk-import analytics failed: %s", e)

    if syndication_targets_independent or syndication_targets_media:
        threading.Thread(
            target=_run_post_import_analytics_async,
            args=(list(syndication_targets_independent), list(syndication_targets_media)),
            daemon=True,
        ).start()

    log.info(
        "bulk_import_commit_summary stage=%s added_independent=%s added_media_house=%s flagged_for_review=%s overrides=%s errors=%s",
        result.get("judgment_stage"),
        len(result.get("added_independent") or []),
        len(result.get("added_media_house") or []),
        len(result.get("flagged_for_review") or []),
        len(result.get("overrides_used") or []),
        len(result.get("errors") or []),
    )

    clear_ui_cache()
    invalidate_group("advisor")
    return result


def undo_created(created: List[dict]) -> dict:
    """Undo a previous commit: deletes DB rows only (no Notion side-effects)."""
    out = {"undone": 0, "errors": []}
    for item in created or []:
        try:
            t = item.get("type")
            if t == "independent":
                delete_company_by_domain(item.get("domain"))
                out["undone"] += 1
            elif t == "media_house":
                delete_advisor_company(item.get("domain"), item.get("parent_name"))
                out["undone"] += 1
            elif t == "media_house_parent":
                delete_advisor_parent(item.get("parent_name"))
                out["undone"] += 1
        except Exception as e:
            out["errors"].append(str(e))
    clear_ui_cache()
    invalidate_group("advisor")
    return out


def bulk_import_runtime_settings() -> dict:
    """Effective bulk-import env (for diagnosing prod vs local). No secrets."""
    return {
        "judgment_stage": DEFAULT_JUDGMENT_STAGE,
        "web_verify_workers": WEB_VERIFY_WORKERS,
        "web_verify_conf_trigger": WEB_VERIFY_CONF_TRIGGER,
        "verify_all_new_rows": BULK_VERIFY_ALL_NEW_ROWS,
    }


__all__ = [
    "_parse_entries_from_text",
    "analyze_from_file",
    "analyze_from_text",
    "analyze_from_domains",
    "analyze_from_entries",
    "commit_rows",
    "undo_created",
    "get_existing_parent_media_houses",
    "get_bulk_judgment_baseline",
    "resolve_import_judgment",
    "bulk_import_runtime_settings",
]
