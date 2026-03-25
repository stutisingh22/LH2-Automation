from fastapi import APIRouter, Depends, HTTPException, Query

from typing import Any

from pydantic import BaseModel, Field

from db_ops import (
    get_advisor_company,
    get_advisor_parents_with_children,
    merge_advisor_company_extra_data,
    get_company,
    get_contact,
    get_or_create_session,
    get_or_create_shadow_company_for_advisor,
    search_advisor_companies,
)
from integrations.ai.ai_engine import compute_lead_score
from api.deps import get_current_user

router = APIRouter(prefix="/advisor", tags=["advisor"])


class AdvisorExtraPatch(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


@router.get("/media-houses")
def media_houses_tree(user: dict = Depends(get_current_user)):
    del user
    return get_advisor_parents_with_children()


@router.get("/companies/search")
def advisor_search(
    user: dict = Depends(get_current_user),
    q: str = Query("", description="Search domain or parent"),
):
    del user
    return search_advisor_companies(q)


@router.get("/companies/{advisor_company_id}")
def advisor_company_detail(advisor_company_id: int, user: dict = Depends(get_current_user)):
    del user
    row = get_advisor_company(advisor_company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.patch("/companies/{advisor_company_id}/extra")
def advisor_patch_extra(
    advisor_company_id: int,
    body: AdvisorExtraPatch,
    user: dict = Depends(get_current_user),
):
    del user
    if not body.patch:
        raise HTTPException(status_code=400, detail="Empty patch")
    merge_advisor_company_extra_data(advisor_company_id, body.patch)
    return {"ok": True}


@router.post("/companies/{advisor_company_id}/session")
def advisor_shadow_session(advisor_company_id: int, user: dict = Depends(get_current_user)):
    """Map a Media Houses child to shadow company + Leadership contact; return session for strategist."""
    cid, ctid = get_or_create_shadow_company_for_advisor(advisor_company_id)
    if not cid or not ctid:
        raise HTTPException(status_code=404, detail="Advisor company not found")
    comp = get_company(cid)
    ct = get_contact(ctid)
    score = 0
    try:
        score = compute_lead_score(comp, ct)
    except Exception:
        pass
    sid = get_or_create_session(cid, ctid, user["id"], score=score)
    return {
        "session_id": sid,
        "company_id": cid,
        "contact_id": ctid,
        "advisor_company_id": advisor_company_id,
    }
