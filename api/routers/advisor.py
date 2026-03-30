from fastapi import APIRouter, Depends, HTTPException, Query

from typing import Any

from pydantic import BaseModel, Field

from config import normalize_domain
from db_ops import (
    get_advisor_company,
    get_advisor_company_by_domain_and_parent,
    get_advisor_parents_with_children,
    merge_advisor_company_extra_data,
    get_company,
    get_contact,
    get_or_create_session,
    get_or_create_shadow_company_for_advisor,
    search_advisor_companies,
    update_contact,
    upsert_spoc_contact,
    upsert_advisor_company,
    upsert_contact,
)
from integrations.ai.ai_engine import compute_lead_score
from api.deps import get_current_user, user_has_perm
from api.routers.companies import PublisherContactIn

router = APIRouter(prefix="/advisor", tags=["advisor"])

_MAX_BRAND_CONTACTS = 25


class AdvisorExtraPatch(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class CreateAdvisorBrandIn(BaseModel):
    domain: str = Field(..., min_length=1)
    parent_name: str = Field(default="", description="Media house name; empty → (No parent)")
    leadership_name: str | None = None
    leadership_email: str | None = None
    leadership_role: str | None = None
    leadership_linkedin: str | None = None
    contacts: list[PublisherContactIn] = Field(
        default_factory=list,
        description="SPOCs for shadow workspace: first maps to Leadership row, rest are additional contacts.",
    )


class AddAdvisorBrandContactsIn(BaseModel):
    """Add extra SPOCs/contacts to an existing media shadow workspace."""

    contacts: list[PublisherContactIn] = Field(default_factory=list)


def _media_brand_contact_rows(body: CreateAdvisorBrandIn) -> list[PublisherContactIn]:
    rows = list(body.contacts or [])
    if not rows and (
        (body.leadership_name or "").strip()
        or (body.leadership_email or "").strip()
        or (body.leadership_role or "").strip()
        or (body.leadership_linkedin or "").strip()
    ):
        rows = [
            PublisherContactIn(
                name=(body.leadership_name or "").strip() or "Leadership",
                email=(body.leadership_email or "").strip() or None,
                role=(body.leadership_role or "").strip() or None,
                linkedin=(body.leadership_linkedin or "").strip() or None,
            )
        ]
    out: list[PublisherContactIn] = []
    for c in rows[:_MAX_BRAND_CONTACTS]:
        nm = (c.name or "").strip()
        if not nm:
            continue
        out.append(c)
    return out


@router.post("/companies")
def create_advisor_brand(
    body: CreateAdvisorBrandIn,
    user: dict = Depends(get_current_user),
):
    """
    Create a media child brand (advisor_companies row) + shadow company + Leadership contact.
    Duplicate (domain, parent_name) → 409.
    """
    if not user_has_perm(user, "can_edit_company"):
        raise HTTPException(status_code=403, detail="Forbidden")
    raw = (body.domain or "").strip()
    dom = normalize_domain(raw) or raw.strip().lower().lstrip(".")
    if not dom or dom.startswith("__"):
        raise HTTPException(status_code=400, detail="Invalid domain")
    p = (body.parent_name or "").strip() or "(No parent)"
    existing = get_advisor_company_by_domain_and_parent(dom, p)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"advisor_domain_exists": True, "advisor_company_id": existing.get("id")},
        )
    advisor_id = upsert_advisor_company(dom, p)
    merge_advisor_company_extra_data(
        advisor_id,
        {"pipeline_status": "not_contacted"},
    )
    cid, ctid = get_or_create_shadow_company_for_advisor(advisor_id)
    if not cid or not ctid:
        raise HTTPException(status_code=500, detail="Could not create shadow workspace")
    contact_rows = _media_brand_contact_rows(body)
    contact_ids: list[int] = [int(ctid)]
    for i, c in enumerate(contact_rows):
        nm = (c.name or "").strip()
        em = (c.email or "").strip() or None
        role = (c.role or "").strip() or None
        li = (c.linkedin or "").strip() or None
        if i == 0:
            patch = {k: v for k, v in {"name": nm, "email": em, "role": role, "linkedin": li}.items() if v is not None}
            if patch:
                update_contact(ctid, **patch)
        else:
            new_id = upsert_contact(
                cid,
                nm,
                role=role,
                email=em,
                linkedin=li,
            )
            contact_ids.append(int(new_id))
    score = 0
    try:
        comp = get_company(cid)
        ct = get_contact(ctid)
        if comp and ct:
            score = compute_lead_score(comp, ct)
    except Exception:
        pass
    session_id = get_or_create_session(cid, ctid, user["id"], score=score)
    return {
        "ok": True,
        "advisor_company_id": advisor_id,
        "company_id": cid,
        "contact_id": ctid,
        "contact_ids": contact_ids,
        "session_id": session_id,
        "parent_key": f"parent:{p}",
    }


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


@router.post("/companies/{advisor_company_id}/contacts/add")
def add_advisor_brand_contacts(
    advisor_company_id: int,
    body: AddAdvisorBrandContactsIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_edit_company"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not get_advisor_company(advisor_company_id):
        raise HTTPException(status_code=404, detail="Advisor company not found")

    cid, _ctid = get_or_create_shadow_company_for_advisor(advisor_company_id)
    if not cid:
        raise HTTPException(status_code=404, detail="Shadow workspace not found")

    comp = get_company(cid) or {}
    added_ids: list[int] = []
    for c in body.contacts or []:
        nm = (c.name or "").strip()
        if not nm:
            continue
        raw_email = (c.email or "").strip()
        raw_role = (c.role or "").strip()
        # Defensive fix: if UI accidentally sends email into "role" (contains '@'),
        # treat it as email.
        if (not raw_email) and raw_role and "@" in raw_role:
            raw_email = raw_role
            raw_role = ""
        email = raw_email or None
        role = raw_role or None
        linkedin = (c.linkedin or "").strip() or None

        cid_row = upsert_contact(cid, nm, role=role, email=email, linkedin=linkedin)
        if cid_row is None:
            continue
        added_ids.append(int(cid_row))

        score = 0
        try:
            ct = get_contact(int(cid_row))
            if comp and ct:
                score = compute_lead_score(comp, ct)
        except Exception:
            pass
        get_or_create_session(cid, int(cid_row), user["id"], score=score)

        # Ensure the new Contact is also represented as a SPOC in Strategist.
        # Strategist "SPOC tracking" is driven by `spoc_contacts` rows keyed by spoc_contact_id.
        try:
            upsert_spoc_contact(
                company_id=cid,
                spoc_contact_id=int(cid_row),
                name=nm,
                role=role,
                email=email,
                linkedin_url=linkedin,
            )
        except Exception:
            pass

    return {"ok": True, "contact_ids": added_ids}


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
