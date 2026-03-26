from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from db_ops import bulk_log_actions, get_contact, get_session, get_company
from services.strategist_add_to_action import scheduled_at_iso_for_strategist_step
from api.company_resolve import enrich_company_for_strategist
from integrations.ai.ai_engine import generate_outreach_plan, replan_on_response
from api.helpers import research_notes_from_company
from api.utils.ics import build_ics
from api.deps import get_current_user, user_has_perm

router = APIRouter(tags=["plans"])


class OutreachPlanRequest(BaseModel):
    sender_name: str = ""
    sender_email: Optional[str] = None
    product_key: Optional[str] = None
    recipient_research: Optional[Any] = None


class PlanStep(BaseModel):
    model_config = {"extra": "allow"}


class CommitPlanRequest(BaseModel):
    steps: list[dict] = Field(default_factory=list)
    product_key: Optional[str] = None
    sender_user_id: int


class ReplanRequest(BaseModel):
    trigger_action_id: int
    response_text: str = ""
    research_notes: Optional[list] = None
    sender_email: Optional[str] = None
    product_key: Optional[str] = None


class IcsExportIn(BaseModel):
    steps: list[dict] = Field(default_factory=list)
    title: str = "LH2 Outreach Plan"


def _session_party(session_id: int):
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    comp = enrich_company_for_strategist(get_company(sess["company_id"]))
    ct = get_contact(sess["contact_id"]) if sess.get("contact_id") else None
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    if not ct:
        ct = {
            "id": None,
            "name": comp.get("domain", "Company"),
            "role": "",
            "email": "",
            "linkedin": "",
            "country": "",
            "state": "",
            "city": "",
        }
    return sess, comp, ct


@router.post("/sessions/{session_id}/outreach-plan")
def create_outreach_plan(
    session_id: int,
    body: OutreachPlanRequest,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    _, comp, ct = _session_party(session_id)
    rn = research_notes_from_company(comp)
    plan = generate_outreach_plan(
        comp,
        ct,
        user["id"],
        session_id,
        body.sender_name,
        rn,
        sender_email=body.sender_email or user.get("email"),
        product_key=body.product_key,
        recipient_research=body.recipient_research,
    )
    return {"plan": plan or []}


@router.post("/sessions/{session_id}/outreach-plan/commit")
def commit_outreach_plan(
    session_id: int,
    body: CommitPlanRequest,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    _pk = (body.product_key or "").strip().lower()
    _offering_label = None
    if _pk == "accelerate":
        _offering_label = "Accelerate"
    elif _pk == "mediaos":
        _offering_label = "MediaOS"
    elif _pk == "acquisition":
        _offering_label = "Acquisition"
    actions_for_db = []
    for idx, step in enumerate(body.steps):
        base_desc = f"Step {step.get('step')}: {step.get('description', '')}"
        desc = f"{base_desc} — {_offering_label}" if _offering_label else base_desc
        sched = step.get("scheduled_at")
        if not sched:
            sched = scheduled_at_iso_for_strategist_step(idx)
        actions_for_db.append(
            {
                "action_type": "Outreach",
                "channel": step.get("channel", "Email"),
                "description": desc,
                "status": "planned",
                "scheduled_at": sched,
                "content_sent": step.get("content_draft"),
                "context_note": step.get("timing_note"),
            }
        )
    ids, seq_id = bulk_log_actions(session_id, body.sender_user_id, actions_for_db)
    return {"action_ids": ids, "sequence_id": seq_id}


@router.post("/sessions/{session_id}/outreach-plan/ics")
def download_plan_ics(
    session_id: int,
    body: IcsExportIn,
    user: dict = Depends(get_current_user),
):
    del user, session_id
    ics = build_ics(body.steps, title=body.title)
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="lh2_plan.ics"'},
    )


@router.post("/sessions/{session_id}/replan")
def replan_remaining(
    session_id: int,
    body: ReplanRequest,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    sess, comp, ct = _session_party(session_id)
    from db_ops import get_session_actions

    all_steps = [a for a in get_session_actions(session_id) if a.get("sequence_id")]
    trigger = next((a for a in all_steps if a["id"] == body.trigger_action_id), None)
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger action not found")
    remaining = [a for a in all_steps if a["status"] in ("planned", "scheduled", "drafted")]
    rn = body.research_notes
    if rn is None:
        rn = research_notes_from_company(comp)
    advice = replan_on_response(
        comp,
        ct,
        user["id"],
        session_id,
        trigger,
        body.response_text,
        remaining,
        research_notes=rn,
        sender_email=body.sender_email or user.get("email"),
        product_key=body.product_key,
    )
    return {"advice": advice or ""}
