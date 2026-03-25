from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config import CONTACT_STATUSES, CONTACT_STATUS_LABELS, normalize_contact_status
from db_ops import (
    get_all_companies,
    get_contacts,
    get_or_create_shadow_company_for_advisor,
    get_advisor_parents_with_children,
    merge_advisor_company_extra_data,
    get_contact,
    update_contact_status,
)
from api.deps import get_current_user

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class PipelineStatusIn(BaseModel):
    status: str


@router.get("/independent")
def pipeline_independent(user: dict = Depends(get_current_user)):
    del user
    pipeline = {s: [] for s in CONTACT_STATUSES}
    for cd in get_all_companies(exclude_advisor_shadows=True):
        for ctd in get_contacts(cd["id"]):
            status = normalize_contact_status(ctd.get("status", "not_contacted"))
            if status in pipeline:
                pipeline[status].append({"contact": ctd, "company": cd})
    kpi = {s: len(pipeline[s]) for s in CONTACT_STATUSES}
    return {"columns": CONTACT_STATUSES, "labels": CONTACT_STATUS_LABELS, "pipeline": pipeline, "kpi": kpi}


@router.get("/media-houses")
def pipeline_media(user: dict = Depends(get_current_user)):
    del user
    parents_with_children = get_advisor_parents_with_children()
    pipeline_advisor = {s: 0 for s in CONTACT_STATUSES}
    groups_enriched = []
    for group in parents_with_children:
        parent_name = group["parent_name"]
        display_parent = (parent_name or "(No parent)").strip()
        enriched = []
        for ch in group["children"]:
            status = normalize_contact_status((ch.get("extra_data") or {}).get("pipeline_status", "not_contacted"))
            shadow_cid, shadow_ctid = get_or_create_shadow_company_for_advisor(ch["id"])
            spoc_name = "—"
            if shadow_ctid:
                ct = get_contact(shadow_ctid)
                if ct:
                    spoc_name = ct.get("name") or "Leadership"
            if status in pipeline_advisor:
                pipeline_advisor[status] += 1
            enriched.append({"child": ch, "spoc_name": spoc_name, "status": status})
        groups_enriched.append({"parent_name": display_parent, "children": enriched})
    kpi = {s: pipeline_advisor[s] for s in CONTACT_STATUSES}
    return {
        "columns": CONTACT_STATUSES,
        "labels": CONTACT_STATUS_LABELS,
        "groups": groups_enriched,
        "kpi": kpi,
    }


@router.patch("/media-houses/companies/{advisor_company_id}")
def patch_advisor_pipeline(
    advisor_company_id: int,
    body: PipelineStatusIn,
    user: dict = Depends(get_current_user),
):
    del user
    if body.status not in CONTACT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    merge_advisor_company_extra_data(advisor_company_id, {"pipeline_status": body.status})
    return {"ok": True}


@router.patch("/contacts/{contact_id}/status")
def patch_contact_pipeline_status(
    contact_id: int,
    body: PipelineStatusIn,
    user: dict = Depends(get_current_user),
):
    del user
    if body.status not in CONTACT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    update_contact_status(contact_id, body.status)
    return {"ok": True}
