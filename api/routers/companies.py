from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db_ops import (
    get_company,
    get_contact,
    get_contacts,
    get_or_create_session,
    get_spoc_contacts_by_company,
    merge_company_extra_data,
    search_companies,
    search_advisor_companies,
)
from integrations.ai.ai_engine import compute_lead_score
from api.deps import get_current_user
from api.schemas import SessionEnsureIn

router = APIRouter(prefix="/companies", tags=["companies"])


class CompanyExtraPatch(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


@router.get("")
def list_companies(
    user: dict = Depends(get_current_user),
    q: str = Query("", description="Search domain or name"),
    source: str = Query("independent", description="independent | media"),
):
    del user
    if source == "media":
        return search_advisor_companies(q or "")
    return search_companies(q or "", exclude_advisor_shadows=True)


@router.get("/{company_id}")
def company_detail(company_id: int, user: dict = Depends(get_current_user)):
    del user
    c = get_company(company_id)
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    return c


@router.get("/{company_id}/contacts")
def company_contacts(company_id: int, user: dict = Depends(get_current_user)):
    del user
    if not get_company(company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    return get_contacts(company_id)


@router.get("/{company_id}/workspace")
def company_workspace(company_id: int, user: dict = Depends(get_current_user)):
    del user
    comp = get_company(company_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    return {
        "company": comp,
        "contacts": get_contacts(company_id),
        "spoc_contacts": get_spoc_contacts_by_company(company_id),
    }


@router.post("/{company_id}/session")
def ensure_session(
    company_id: int,
    body: SessionEnsureIn,
    user: dict = Depends(get_current_user),
):
    comp = get_company(company_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    if body.contact_id is None:
        sid = get_or_create_session(company_id, None, user["id"], score=0)
        return {"session_id": sid}
    ct = get_contact(body.contact_id)
    if not ct or ct.get("company_id") != company_id:
        raise HTTPException(status_code=400, detail="Contact not found for this company")
    score = 0
    try:
        score = compute_lead_score(comp, ct)
    except Exception:
        pass
    sid = get_or_create_session(company_id, body.contact_id, user["id"], score=score)
    return {"session_id": sid}


@router.patch("/{company_id}/extra")
def patch_company_extra_data(
    company_id: int,
    body: CompanyExtraPatch,
    user: dict = Depends(get_current_user),
):
    del user
    if not body.patch:
        raise HTTPException(status_code=400, detail="Empty patch")
    if not merge_company_extra_data(company_id, body.patch):
        raise HTTPException(status_code=404, detail="Company not found")
    return {"ok": True}
