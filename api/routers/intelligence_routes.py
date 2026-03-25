import json
import queue
import re
import html as html_module
import threading
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db_ops import (
    get_advisor_company,
    get_company,
    get_company_by_domain,
    get_or_create_session,
    get_or_create_shadow_company_for_advisor,
    get_session_summary,
    get_sessions_for_company,
    merge_advisor_company_extra_data,
    merge_company_extra_data,
    save_session_summary,
    is_advisor_parent_company,
)
from integrations import build_platform_analytics_for_domain, get_presence_for_domain, load_domain_to_platforms
from integrations.ai.research import (
    RESEARCH_MAX_TOKENS,
    fetch_research_from_notion,
    format_appended_section,
    get_next_section_number,
    research_command_ai,
    run_research,
    split_research_into_sections,
)
from integrations.ai.ai_engine import (
    intelligence_request,
    recommend_offering_for_company,
    research_person_for_company,
)
from integrations.drive import create_doc_in_drive, get_lh2_knowledge_context
from ui_helpers import (
    html_to_plain_text,
    is_html_content,
    looks_like_html_fragment,
    markdown_to_html,
    redocument_with_lh2_markers,
    research_display_to_editor_html,
    split_html_into_canonical_sections,
    split_html_into_sections,
)
from api.helpers import research_notes_from_company
from api.deps import get_current_user, user_has_perm

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def _independent_should_use_canonical_html(display: str, plain: str, pairs: list) -> bool:
    """First-load docs: few top-level <h2>s but long LH2 template in HTML → split like Streamlit advisor."""
    if "lh2:section:" in (display or ""):
        return False
    p = (plain or "").strip()
    if len(p) < 4000:
        return False
    if len(pairs) > 5:
        return False
    return bool(
        "0. ELIGIBILITY" in p
        or "ELIGIBILITY CHECK" in p
        or "1. COMPANY & OPERATING" in p
        or "2. ARTICLE FREQUENCY" in p
        or "3. POWER MAPPING" in p
        or "6. OUTREACH" in p
        or "CRITICAL STRATEGIC SIGNAL" in p
    )


class DeepResearchIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    contact_id: Optional[int] = None
    session_id: Optional[int] = None


class OfferingIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    research_text: str = ""
    session_id: Optional[int] = None


class ResearchSectionsIn(BaseModel):
    display: str = ""
    advisor_canonical: bool = False


class SaveResearchIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    session_id: Optional[int] = None
    merged_html: str = ""
    sections: Optional[list[dict]] = None


class DeleteResearchIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    session_id: Optional[int] = None


class ResearchCommandIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    current_text: str = ""
    command: str = ""


class ResearchAppendIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    session_id: Optional[int] = None
    current_merged_html: str = ""
    new_content: str = ""
    title: str = "Extended research"


class RefreshAnalyticsIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None


class IntelRequestIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    raw: str = ""


def _resolve_intel_context(body: DeepResearchIn, user_id: int):
    if body.advisor_company_id is not None:
        adv = get_advisor_company(body.advisor_company_id)
        if not adv:
            raise HTTPException(status_code=404, detail="Advisor company not found")
        _is_parent = is_advisor_parent_company(adv)
        research_domain = (
            (adv.get("parent_name") or adv.get("domain") or "")
            if _is_parent
            else (adv.get("domain") or "")
        )
        research_domain = (research_domain or "").strip() or (adv.get("domain") or "")
        comp_like = {
            "domain": adv.get("domain", "—"),
            "extra_data": adv.get("extra_data") or {},
            "research_domain": research_domain,
        }

        def merge_fn(p: dict):
            merge_advisor_company_extra_data(body.advisor_company_id, p)

        shadow_cid, shadow_ctid = get_or_create_shadow_company_for_advisor(body.advisor_company_id)
        sid = body.session_id
        if sid is None and shadow_cid:
            use_ct = body.contact_id if body.contact_id is not None else shadow_ctid
            sid = get_or_create_session(shadow_cid, use_ct, user_id)
        return research_domain, comp_like, merge_fn, sid

    if body.company_id is None:
        raise HTTPException(status_code=400, detail="company_id or advisor_company_id required")
    comp = get_company(body.company_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")

    def merge_fn(p: dict):
        merge_company_extra_data(body.company_id, p)

    sid = body.session_id
    if sid is None:
        sid = get_or_create_session(body.company_id, body.contact_id, user_id)
    domain = (comp.get("domain") or "").strip()
    return domain, comp, merge_fn, sid


def _normalize_extra(raw):
    if isinstance(raw, str) and (raw or "").strip():
        try:
            import json

            return json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _merge_platform_presence_for_display(comp: dict) -> dict:
    _pp_ed = dict(comp.get("extra_data") or {}) if isinstance(comp.get("extra_data"), dict) else {}
    dom = (comp.get("domain") or "").strip()
    if dom:
        main = get_company_by_domain(dom)
        if main and isinstance(main.get("extra_data"), dict):
            med = main.get("extra_data") or {}
            if med.get("platform_presence"):
                _pp_ed["platform_presence"] = med["platform_presence"]
            if med.get("social_from_search"):
                _pp_ed["social_from_search"] = med["social_from_search"]
    return _pp_ed


def _load_intel_company(company_id: Optional[int], advisor_company_id: Optional[int]):
    if advisor_company_id is not None:
        adv = get_advisor_company(advisor_company_id)
        if not adv:
            raise HTTPException(status_code=404, detail="Advisor company not found")
        _is_parent = is_advisor_parent_company(adv)
        adv_extra = _normalize_extra(adv.get("extra_data"))
        _display_name = (
            (adv.get("parent_name") or adv.get("domain") or "—")
            if _is_parent
            else (adv.get("domain") or adv.get("parent_name") or "—")
        )
        _research_target = (
            (adv.get("parent_name") or adv.get("domain") or "")
            if _is_parent
            else (adv.get("domain") or "")
        )
        comp = {
            "id": adv["id"],
            "domain": adv.get("domain", "—"),
            "name": _display_name,
            "publisher_type": adv.get("parent_name") or "—",
            "content_type": "—",
            "monthly_visits": None,
            "research_status": "—",
            "notion_page_id": adv.get("notion_page_id"),
            "extra_data": adv_extra,
            "research_domain": (_research_target or adv.get("domain") or "").strip(),
        }
        comp["extra_data"] = _merge_platform_presence_for_display(comp)
        return comp, (comp.get("research_domain") or comp.get("domain") or "").strip()
    if company_id is None:
        raise HTTPException(status_code=400, detail="company_id or advisor_company_id required")
    c = get_company(company_id)
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    c2 = dict(c)
    if isinstance(c2.get("extra_data"), str):
        c2["extra_data"] = _normalize_extra(c2.get("extra_data"))
    elif not isinstance(c2.get("extra_data"), dict):
        c2["extra_data"] = {}
    return c2, (c2.get("domain") or "").strip()


# Rough upper bound for streamed output (tokens → chars) for UI progress; not a hard limit.
_ESTIMATED_RESEARCH_CHARS = max(32000, int(RESEARCH_MAX_TOKENS * 3.4))


def _pct_from_chars(chars: int) -> int:
    return min(94, int((chars / _ESTIMATED_RESEARCH_CHARS) * 100)) if _ESTIMATED_RESEARCH_CHARS else 0


def _finalize_deep_research(
    session_id: int,
    comp: dict,
    merge_fn,
    result: str,
) -> tuple[str, str]:
    """Persist research + analytics; returns (research_display_html, research_full)."""
    if not session_id:
        raise ValueError("session_id required")
    if not (result or "").strip():
        raise ValueError("empty research")
    save_session_summary(session_id, result, msg_count=0)
    try:
        _ed_local = comp.get("extra_data") if isinstance(comp.get("extra_data"), dict) else {}
        _presence_local = (_ed_local.get("platform_presence") or {}) if isinstance(_ed_local, dict) else {}
        platform_analytics_snapshot = build_platform_analytics_for_domain(
            comp.get("domain", ""),
            platform_presence=_presence_local,
        )
    except Exception as _pae:
        platform_analytics_snapshot = {
            "status": "error",
            "error": str(_pae),
            "updated_at": datetime.utcnow().isoformat(),
        }
    _disp_html = research_display_to_editor_html(result) if result else ""
    merge_fn(
        {
            "research_full": result,
            "research_full_updated_at": datetime.utcnow().isoformat(),
            "platform_presence_analytics": platform_analytics_snapshot,
            "research_display_html": _disp_html,
        }
    )
    return _disp_html, result


def _research_notes_for_intel(comp: dict) -> list:
    rn = []
    pid = comp.get("notion_page_id")
    if pid:
        try:
            rn = fetch_research_from_notion(pid)
        except Exception:
            rn = []
    if not rn:
        rn = research_notes_from_company(comp)
    return rn


@router.post("/deep-research")
def deep_research(body: DeepResearchIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    domain, comp, merge_fn, session_id = _resolve_intel_context(body, user["id"])
    if not domain:
        raise HTTPException(status_code=400, detail="Could not resolve domain")
    _pp_ed = (comp.get("extra_data") or {}) if isinstance(comp.get("extra_data"), dict) else {}
    _platform_presence = (_pp_ed.get("platform_presence") or {}) if isinstance(_pp_ed, dict) else {}
    if not _platform_presence:
        try:
            _tracker_map = load_domain_to_platforms()
            _target = comp.get("research_domain", comp.get("domain") or "")
            _platform_presence = get_presence_for_domain(_target, _tracker_map) or {}
        except Exception:
            _platform_presence = {}
    try:
        result = run_research(domain, progress_callback=None, platform_presence=_platform_presence or None)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not result:
        raise HTTPException(status_code=502, detail="Research returned empty")
    if not session_id:
        raise HTTPException(status_code=400, detail="Could not resolve session")
    try:
        _disp_html, _ = _finalize_deep_research(session_id, comp, merge_fn, result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) from ve
    return {
        "session_id": session_id,
        "chars": len(result or ""),
        "research_full": result,
        "research_display_html": _disp_html,
    }


@router.post("/deep-research-stream")
def deep_research_stream(body: DeepResearchIn, user: dict = Depends(get_current_user)):
    """Same as deep-research but streams SSE progress (chars + estimated %)."""
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        domain, comp, merge_fn, session_id = _resolve_intel_context(body, user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not domain:
        raise HTTPException(status_code=400, detail="Could not resolve domain")
    if not session_id:
        raise HTTPException(status_code=400, detail="Could not resolve session")

    _pp_ed = (comp.get("extra_data") or {}) if isinstance(comp.get("extra_data"), dict) else {}
    _platform_presence = (_pp_ed.get("platform_presence") or {}) if isinstance(_pp_ed, dict) else {}
    if not _platform_presence:
        try:
            _tracker_map = load_domain_to_platforms()
            _target = comp.get("research_domain", comp.get("domain") or "")
            _platform_presence = get_presence_for_domain(_target, _tracker_map) or {}
        except Exception:
            _platform_presence = {}

    q: queue.Queue = queue.Queue()
    holder: dict = {"result": None, "error": None}

    def progress_cb(chars: int, elapsed: float) -> None:
        q.put(("progress", chars, elapsed))

    def worker() -> None:
        try:
            res = run_research(domain, progress_callback=progress_cb, platform_presence=_platform_presence or None)
            holder["result"] = res
        except Exception as e:
            holder["error"] = str(e)
        finally:
            q.put(("done", None, None))

    threading.Thread(target=worker, daemon=True).start()

    def sse_gen():
        wall0 = time.time()
        last_emit = 0.0
        last_chars = -1
        yield f"data: {json.dumps({'type': 'start', 'estimated_max_chars': _ESTIMATED_RESEARCH_CHARS})}\n\n"
        while True:
            try:
                kind, a, b = q.get(timeout=0.45)
            except queue.Empty:
                elapsed = time.time() - wall0
                yield f"data: {json.dumps({'type': 'pulse', 'elapsed': round(elapsed, 1)})}\n\n"
                continue
            if kind == "progress":
                chars, elapsed = int(a), float(b)
                now = time.time()
                if chars - last_chars < 1400 and (now - last_emit) < 0.35:
                    continue
                last_emit = now
                last_chars = chars
                pct = _pct_from_chars(chars)
                payload = {
                    "type": "progress",
                    "chars": chars,
                    "elapsed": round(elapsed, 1),
                    "pct": pct,
                    "remaining_pct": max(0, 100 - pct),
                }
                yield f"data: {json.dumps(payload)}\n\n"
            elif kind == "done":
                break

        err = holder.get("error")
        if err:
            yield f"data: {json.dumps({'type': 'error', 'detail': err})}\n\n"
            return
        result = holder.get("result")
        if not result or not str(result).strip():
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Research returned empty'})}\n\n"
            return
        try:
            _finalize_deep_research(session_id, comp, merge_fn, str(result))
        except ValueError as ve:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(ve)})}\n\n"
            return
        done_payload = {
            "type": "complete",
            "session_id": session_id,
            "chars": len(result),
            "pct": 100,
            "remaining_pct": 0,
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return StreamingResponse(
        sse_gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/offering-recommendation")
def offering_recommendation(body: OfferingIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    research = (body.research_text or "").strip()
    if not research and body.session_id:
        summ, _ = get_session_summary(body.session_id)
        research = (summ or "").strip()
    if not research:
        ed = None
        if body.company_id:
            c = get_company(body.company_id)
            ed = (c.get("extra_data") or {}) if c and isinstance(c.get("extra_data"), dict) else {}
        elif body.advisor_company_id:
            a = get_advisor_company(body.advisor_company_id)
            ed = _normalize_extra(a.get("extra_data")) if a else {}
        if isinstance(ed, dict):
            research = (ed.get("research_full") or "").strip()
    if not research:
        raise HTTPException(status_code=400, detail="No research text; run deep research or paste summary")
    try:
        lh2 = get_lh2_knowledge_context()
    except Exception:
        lh2 = ""
    domain = None
    if body.company_id:
        c = get_company(body.company_id)
        domain = c.get("domain") if c else None
    elif body.advisor_company_id:
        a = get_advisor_company(body.advisor_company_id)
        domain = a.get("domain") if a else None
    result = recommend_offering_for_company(research, lh2, company_domain=domain)
    pk = (result.get("product_key") or "").strip().lower() if result else None
    if pk in ("mediaos", "accelerate"):
        _ro = {
            "product_key": pk,
            "rationale": (result.get("rationale") or "").strip() or "No rationale.",
            "updated_at": datetime.utcnow().isoformat(),
        }
        if body.advisor_company_id:
            merge_advisor_company_extra_data(body.advisor_company_id, {"recommended_offering": _ro})
        elif body.company_id:
            merge_company_extra_data(body.company_id, {"recommended_offering": _ro})
    return result or {}


@router.get("/notion-research")
def notion_research(page_id: str, user: dict = Depends(get_current_user)):
    del user
    if not page_id.strip():
        raise HTTPException(status_code=400, detail="page_id required")
    return {"notes": fetch_research_from_notion(page_id.strip())}


@router.get("/workspace")
def intelligence_workspace(
    company_id: Optional[int] = Query(None),
    advisor_company_id: Optional[int] = Query(None),
    session_id: Optional[int] = Query(None),
    user: dict = Depends(get_current_user),
):
    del user
    comp, _domain = _load_intel_company(company_id, advisor_company_id)
    ed = comp.get("extra_data") if isinstance(comp.get("extra_data"), dict) else {}
    rf = (ed.get("research_full") or "").strip()
    rdh = (ed.get("research_display_html") or "").strip()
    rs = ed.get("research_sections") if isinstance(ed.get("research_sections"), list) else []
    used_session_fallback = False
    if not rf and session_id:
        summ, _ = get_session_summary(session_id)
        if summ and str(summ).strip():
            rf = str(summ).strip()
            used_session_fallback = True
    if rf and not rdh:
        rdh = research_display_to_editor_html(rf)
    if not rs and (rdh or rf):
        display = rdh or rf
        if advisor_company_id is not None:
            if is_html_content(display):
                pairs = split_html_into_canonical_sections(display)
            else:
                pairs = split_research_into_sections(display)
        elif is_html_content(display):
            plain_for_split = html_to_plain_text(display)
            pairs = split_html_into_sections(display)
            if len(pairs) == 1 and pairs[0][0] == "Research":
                pairs = split_research_into_sections(plain_for_split)
            elif _independent_should_use_canonical_html(display, plain_for_split, pairs):
                pairs = split_html_into_canonical_sections(display)
        else:
            pairs = split_research_into_sections(display)
        rs = []
        for title, chunk in pairs:
            if chunk and not looks_like_html_fragment(chunk):
                body_html = research_display_to_editor_html(chunk)
            else:
                body_html = (chunk or "").strip()
            rs.append({"title": title, "body_html": body_html})
        if advisor_company_id is not None:
            merge_advisor_company_extra_data(advisor_company_id, {"research_sections": rs})
        elif company_id is not None:
            merge_company_extra_data(company_id, {"research_sections": rs})
    # If workspace is currently relying on session summary fallback, persist it to company/advisor
    # so refresh/tab switches remain stable even when session_id changes.
    if used_session_fallback and rf:
        patch = {
            "research_full": rf,
            "research_full_updated_at": datetime.utcnow().isoformat(),
            "research_display_html": rdh or research_display_to_editor_html(rf),
            "research_sections": rs,
        }
        if advisor_company_id is not None:
            merge_advisor_company_extra_data(advisor_company_id, patch)
        elif company_id is not None:
            merge_company_extra_data(company_id, patch)
    notes = _research_notes_for_intel(comp)
    if rf:
        notes = [
            n
            for n in notes
            if not (
                isinstance(n, dict)
                and (n.get("title") or "").strip() == "Research Document"
                and (n.get("content") or "").strip() == rf
            )
        ]
    return {
        "profile": {
            "domain": comp.get("domain"),
            "name": comp.get("name"),
            "publisher_type": comp.get("publisher_type"),
            "content_type": comp.get("content_type"),
            "monthly_visits": comp.get("monthly_visits"),
            "research_status": comp.get("research_status"),
        },
        "research_full": rf,
        "research_display_html": rdh,
        "research_sections": rs,
        "recommended_offering": ed.get("recommended_offering"),
        "platform_presence": ed.get("platform_presence") or {},
        "social_from_search": ed.get("social_from_search") or {},
        "platform_presence_analytics": ed.get("platform_presence_analytics") or {},
        "notion_notes": notes,
        "notion_page_id": comp.get("notion_page_id"),
        "advisor_canonical": bool(advisor_company_id),
    }


@router.post("/research-sections")
def post_research_sections(body: ResearchSectionsIn, user: dict = Depends(get_current_user)):
    del user
    display = (body.display or "").strip()
    if not display:
        return {"sections": []}
    if body.advisor_canonical:
        if is_html_content(display):
            pairs = split_html_into_canonical_sections(display)
        else:
            pairs = split_research_into_sections(display)
    elif is_html_content(display):
        plain_for_split = html_to_plain_text(display)
        pairs = split_html_into_sections(display)
        if len(pairs) == 1 and pairs[0][0] == "Research":
            pairs = split_research_into_sections(plain_for_split)
        elif _independent_should_use_canonical_html(display, plain_for_split, pairs):
            pairs = split_html_into_canonical_sections(display)
    else:
        pairs = split_research_into_sections(display)
    out = []
    for title, chunk in pairs:
        if chunk and not looks_like_html_fragment(chunk):
            body_html = research_display_to_editor_html(chunk)
        else:
            body_html = (chunk or "").strip()
        out.append({"title": title, "body_html": body_html})
    return {"sections": out}


@router.post("/save-research")
def post_save_research(body: SaveResearchIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    merged = (body.merged_html or "").strip()
    if not merged:
        raise HTTPException(status_code=400, detail="merged_html required")
    if body.advisor_company_id is None and body.company_id is None:
        raise HTTPException(status_code=400, detail="company_id or advisor_company_id required")
    comp, _d = _load_intel_company(body.company_id, body.advisor_company_id)

    if is_html_content(merged):
        merged = redocument_with_lh2_markers(merged)
        _research_for_db = merged
        _disp_html = merged
    else:
        _research_for_db = merged
        _disp_html = research_display_to_editor_html(merged)
    _sections = []
    if isinstance(body.sections, list):
        for it in body.sections:
            if not isinstance(it, dict):
                continue
            t = str(it.get("title") or "").strip()
            b = str(it.get("body_html") or "").strip()
            if not t and not b:
                continue
            if b and not looks_like_html_fragment(b):
                b = research_display_to_editor_html(b)
            _sections.append({"title": t or "Section", "body_html": b})
    if not _sections:
        if body.advisor_company_id is not None:
            if is_html_content(_disp_html):
                pairs = split_html_into_canonical_sections(_disp_html)
            else:
                pairs = split_research_into_sections(_research_for_db)
        elif is_html_content(_disp_html):
            plain_for_split = html_to_plain_text(_disp_html)
            pairs = split_html_into_sections(_disp_html)
            if len(pairs) == 1 and pairs[0][0] == "Research":
                pairs = split_research_into_sections(plain_for_split)
            elif _independent_should_use_canonical_html(_disp_html, plain_for_split, pairs):
                pairs = split_html_into_canonical_sections(_disp_html)
        else:
            pairs = split_research_into_sections(_research_for_db)
        for title, chunk in pairs:
            if chunk and not looks_like_html_fragment(chunk):
                body_html = research_display_to_editor_html(chunk)
            else:
                body_html = (chunk or "").strip()
            _sections.append({"title": title or "Section", "body_html": body_html})
    _patch = {
        "research_full": _research_for_db,
        "research_full_updated_at": datetime.utcnow().isoformat(),
        "research_display_html": _disp_html,
        "research_sections": _sections,
    }
    if body.advisor_company_id is not None:
        merged_ok = merge_advisor_company_extra_data(body.advisor_company_id, _patch)
    else:
        merged_ok = merge_company_extra_data(body.company_id, _patch)
    if not merged_ok:
        raise HTTPException(
            status_code=500,
            detail="Failed to persist research to the database (record missing or DB error).",
        )
    _plain_session = (
        html_to_plain_text(_research_for_db) if is_html_content(_research_for_db) else _research_for_db
    )
    if body.session_id:
        save_session_summary(body.session_id, _plain_session or "", msg_count=0)
    doc_name = comp.get("domain") or comp.get("name") or "Company"
    _drive_body = _disp_html or merged
    fid, _link, err_drive = create_doc_in_drive(f"{doc_name} - Research", _drive_body)
    return {"ok": True, "drive_file_id": fid, "drive_error": err_drive}


@router.post("/delete-research")
def post_delete_research(body: DeleteResearchIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    cleared_ok = False
    if body.advisor_company_id is not None:
        shadow_cid, _ = get_or_create_shadow_company_for_advisor(body.advisor_company_id)
        if shadow_cid:
            for s in get_sessions_for_company(shadow_cid):
                save_session_summary(s["id"], "", msg_count=0)
        cleared_ok = bool(
            merge_advisor_company_extra_data(
            body.advisor_company_id,
            {
                "research_full": "",
                "research_full_updated_at": datetime.utcnow().isoformat(),
                "research_display_html": "",
                "research_sections": [],
            },
        )
        )
    elif body.company_id is not None:
        for s in get_sessions_for_company(body.company_id):
            save_session_summary(s["id"], "", msg_count=0)
        cleared_ok = bool(
            merge_company_extra_data(
            body.company_id,
            {
                "research_full": "",
                "research_full_updated_at": datetime.utcnow().isoformat(),
                "research_display_html": "",
                "research_sections": [],
            },
        )
        )
    else:
        raise HTTPException(status_code=400, detail="company_id or advisor_company_id required")
    if not cleared_ok:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete research from the database (record missing or DB error).",
        )
    if body.session_id:
        save_session_summary(body.session_id, "", msg_count=0)
    return {"ok": True}


@router.post("/research-command")
def post_research_command(body: ResearchCommandIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    comp, domain = _load_intel_company(body.company_id, body.advisor_company_id)
    cmd = (body.command or "").strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="command required")
    cur = (body.current_text or "").strip()
    current_for_ai = html_to_plain_text(cur) if is_html_content(cur) else cur
    dom = (comp.get("research_domain") or domain or "").strip()
    out = research_command_ai(dom, current_for_ai or "(No existing research.)", cmd)
    if not out:
        raise HTTPException(status_code=502, detail="Research command failed")
    return {"title": "Extended research", "content": out}


@router.post("/research-command-append")
def post_research_append(body: ResearchAppendIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    comp, _d = _load_intel_company(body.company_id, body.advisor_company_id)
    current = (body.current_merged_html or "").strip()
    cmd_content = (body.new_content or "").strip()
    if not cmd_content:
        raise HTTPException(status_code=400, detail="new_content required")
    if body.advisor_company_id is not None:

        def _merge(p: dict):
            merge_advisor_company_extra_data(body.advisor_company_id, p)

    else:

        def _merge(p: dict):
            merge_company_extra_data(body.company_id, p)

    plain_current = html_to_plain_text(current) if is_html_content(current) else current
    next_num = get_next_section_number(plain_current or "")
    title_safe = html_module.escape((body.title or "Extended research").strip() or "Extended research")
    if is_html_content(current):
        new_section_html = markdown_to_html(cmd_content)
        new_block = f"<h2>{next_num}. {title_safe}</h2>\n\n{new_section_html}"
        merged = f"{current}\n\n{new_block}" if current else new_block
    else:
        new_block = format_appended_section(cmd_content, body.title or "Extended research", next_num)
        merged = f"{current}\n\n{new_block}" if current else new_block
    if is_html_content(merged):
        merged = redocument_with_lh2_markers(merged)
        _disp_html = merged
    else:
        _disp_html = research_display_to_editor_html(merged)
    _merge(
        {
            "research_full": merged,
            "research_full_updated_at": datetime.utcnow().isoformat(),
            "research_display_html": _disp_html,
        }
    )
    _plain_session = html_to_plain_text(merged) if is_html_content(merged) else merged
    if body.session_id:
        save_session_summary(body.session_id, _plain_session or "", msg_count=0)
    return {"merged_html": merged, "research_display_html": _disp_html}


@router.post("/refresh-platform-analytics")
def post_refresh_platform_analytics(body: RefreshAnalyticsIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    comp, _d = _load_intel_company(body.company_id, body.advisor_company_id)
    ed = comp.get("extra_data") if isinstance(comp.get("extra_data"), dict) else {}
    presence = ed.get("platform_presence") or {}
    if not presence:
        try:
            tracker = load_domain_to_platforms()
            target = comp.get("research_domain") or comp.get("domain") or ""
            presence = get_presence_for_domain(target, tracker) or {}
        except Exception:
            presence = {}
    fresh = build_platform_analytics_for_domain(
        comp.get("domain", "") or "",
        platform_presence=presence if isinstance(presence, dict) else {},
    )
    if body.advisor_company_id is not None:
        merge_advisor_company_extra_data(body.advisor_company_id, {"platform_presence_analytics": fresh})
    else:
        merge_company_extra_data(body.company_id, {"platform_presence_analytics": fresh})
    return {"analytics": fresh}


@router.post("/intel-request")
def post_intel_request(body: IntelRequestIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    comp, _domain = _load_intel_company(body.company_id, body.advisor_company_id)
    raw = (body.raw or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="raw required")
    rn = _research_notes_for_intel(comp)
    linkedin_match = re.search(r"https?://[^\s]*linkedin\.com[^\s]*", raw, re.IGNORECASE)
    email_match = re.search(r"[\w.-]+@[\w.-]+\.\w+", raw)
    if linkedin_match:
        url = linkedin_match.group(0).strip().rstrip(".,;:)")
        brief = research_person_for_company(comp=comp, research_notes=rn, linkedin_url=url)
        return {
            "kind": "person",
            "brief": brief or "",
            "lead": {"linkedin": url, "name": "", "email": "", "role": "Contact", "domain": comp.get("domain", "")},
        }
    if email_match:
        em = email_match.group(0).strip()
        brief = research_person_for_company(comp=comp, research_notes=rn, email=em)
        return {
            "kind": "person",
            "brief": brief or "",
            "lead": {"email": em, "name": "", "linkedin": "", "role": "Contact", "domain": comp.get("domain", "")},
        }
    suggestion_phrases = (
        "missing",
        "add ",
        "add a",
        "expand",
        "section on",
        "suggest",
        "what's missing",
        "what to add",
    )
    is_suggestion = any(p in raw.lower() for p in suggestion_phrases)
    if is_suggestion:
        suggestion = intelligence_request(comp, rn, raw)
        return {"kind": "suggestion", "suggestion": suggestion or "", "query": raw}
    brief = research_person_for_company(comp=comp, research_notes=rn, person_name=raw)
    return {
        "kind": "person",
        "brief": brief or "",
        "lead": {"name": raw, "email": "", "linkedin": "", "role": "Contact", "domain": comp.get("domain", "")},
    }


