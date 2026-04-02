from typing import Any
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from config import normalize_domain
from db_ops import (
    get_company,
    get_company_by_domain,
    get_contact,
    get_contacts,
    get_or_create_session,
    upsert_spoc_contact,
    get_spoc_contacts_by_company,
    merge_company_extra_data,
    search_companies,
    search_advisor_companies,
    upsert_company,
    upsert_contact,
)
from integrations.ai.ai_engine import compute_lead_score
from integrations.analytics import (
    apply_similarweb_traffic_to_company,
    apply_syndication_refresh_to_company,
)
from api.deps import get_current_user, user_has_perm
from api.schemas import SessionEnsureIn
from services.company_reclassification_service import reclassify_company

router = APIRouter(prefix="/companies", tags=["companies"])


class CompanyExtraPatch(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class PublisherContactIn(BaseModel):
    name: str = Field(..., min_length=1)
    email: str | None = None
    role: str | None = None
    linkedin: str | None = None


class CreatePublisherCompanyIn(BaseModel):
    domain: str = Field(..., min_length=1)
    name: str | None = None
    contacts: list[PublisherContactIn] = Field(default_factory=list)


class AddPublisherContactsIn(BaseModel):
    """Add extra SPOCs/contacts for an existing independent publisher company."""

    contacts: list[PublisherContactIn] = Field(default_factory=list)


class ReclassifyCompanyIn(BaseModel):
    source_type: str = Field(..., description="independent | media_house")
    source_id: int = Field(..., ge=1)
    target_type: str = Field(..., description="independent | media_house")
    parent_name: str | None = None
    reason: str | None = None


@router.post("")
def create_publisher_company(
    body: CreatePublisherCompanyIn,
    user: dict = Depends(get_current_user),
):
    """
    Create an independent publisher (Company + optional Contacts) for Publisher CRM.
    Rejects duplicates by normalized domain (409 with existing company_id).
    """
    if not user_has_perm(user, "can_edit_company"):
        raise HTTPException(status_code=403, detail="Forbidden")
    raw = (body.domain or "").strip()
    dom = normalize_domain(raw) or raw.strip().lower().lstrip(".")
    if not dom:
        raise HTTPException(status_code=400, detail="Invalid domain")
    if dom.startswith("__advisor_"):
        raise HTTPException(status_code=400, detail="Reserved domain pattern")
    existing = get_company_by_domain(dom)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"domain_exists": True, "company_id": existing.get("id")},
        )
    display_name = (body.name or "").strip() or dom
    company_id = upsert_company(dom, name=display_name)
    contact_ids: list[int] = []
    first_contact_id: int | None = None
    for c in body.contacts:
        nm = (c.name or "").strip()
        if not nm:
            continue
        cid = upsert_contact(
            company_id,
            nm,
            role=(c.role or "").strip() or None,
            email=(c.email or "").strip() or None,
            linkedin=(c.linkedin or "").strip() or None,
        )
        contact_ids.append(cid)
        if first_contact_id is None:
            first_contact_id = cid
    session_id: int | None = None
    if first_contact_id is not None:
        score = 0
        try:
            comp = get_company(company_id)
            ct = get_contact(first_contact_id)
            if comp and ct:
                score = compute_lead_score(comp, ct)
        except Exception:
            pass
        session_id = get_or_create_session(company_id, first_contact_id, user["id"], score=score)
    # Best-effort async syndication refresh for newly created independent publishers.
    # Keep create API fast; refresh persists in background.
    def _refresh_syndication_async():
        try:
            apply_syndication_refresh_to_company(company_id, dom, publisher_name=display_name)
            apply_similarweb_traffic_to_company(company_id, dom)
        except Exception:
            pass

    threading.Thread(target=_refresh_syndication_async, daemon=True).start()
    return {
        "ok": True,
        "company_id": company_id,
        "contact_ids": contact_ids,
        "session_id": session_id,
    }


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


@router.post("/{company_id}/contacts/add")
def add_publisher_contacts(
    company_id: int,
    body: AddPublisherContactsIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_edit_company"):
        raise HTTPException(status_code=403, detail="Forbidden")
    comp = get_company(company_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")

    added_ids: list[int] = []
    contacts = body.contacts or []
    for c in contacts:
        nm = (c.name or "").strip()
        if not nm:
            continue
        raw_email = (c.email or "").strip()
        raw_role = (c.role or "").strip()
        # Defensive fix:
        # If the UI accidentally sends the email into "role" (role contains '@'),
        # treat it as email instead.
        if (not raw_email) and raw_role and "@" in raw_role:
            raw_email = raw_role
            raw_role = ""
        email = raw_email or None
        role = raw_role or None
        linkedin = (c.linkedin or "").strip() or None

        cid = upsert_contact(company_id, nm, email=email, role=role, linkedin=linkedin)
        if cid is None:
            continue
        added_ids.append(int(cid))

        # Ensure a session exists so planned actions for that contact can surface in the dashboard.
        score = 0
        try:
            ct = get_contact(int(cid))
            if ct:
                score = compute_lead_score(comp, ct)
        except Exception:
            pass
        get_or_create_session(company_id, int(cid), user["id"], score=score)

        # Ensure the new Contact is also represented as a SPOC in Strategist.
        # Strategist "SPOC tracking" is driven by `spoc_contacts` rows keyed by spoc_contact_id.
        try:
            upsert_spoc_contact(
                company_id=company_id,
                spoc_contact_id=int(cid),
                name=nm,
                role=role,
                email=email,
                linkedin_url=linkedin,
            )
        except Exception:
            # Non-fatal: contact/session still created even if spoc row insert fails.
            pass

    return {"ok": True, "contact_ids": added_ids}


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


@router.post("/reclassify")
def reclassify_company_route(
    body: ReclassifyCompanyIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_edit_company"):
        raise HTTPException(status_code=403, detail="Forbidden")

    source_type = (body.source_type or "").strip().lower()
    target_type = (body.target_type or "").strip().lower()
    valid = {"independent", "media_house"}
    if source_type not in valid or target_type not in valid:
        raise HTTPException(status_code=400, detail="Invalid source_type or target_type")
    if source_type == target_type:
        raise HTTPException(status_code=400, detail="source_type and target_type must be different")
    if target_type == "media_house" and not (body.parent_name or "").strip():
        raise HTTPException(status_code=400, detail="parent_name is required for media_house target")

    try:
        return reclassify_company(
            source_type=source_type,  # type: ignore[arg-type]
            source_id=body.source_id,
            target_type=target_type,  # type: ignore[arg-type]
            parent_name=body.parent_name,
            reason=body.reason,
            actor_user_id=user.get("id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
