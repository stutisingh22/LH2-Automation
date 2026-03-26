from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from db_ops import (
    delete_action,
    get_action_by_id,
    get_session,
    get_active_sequences,
    get_sequence_actions,
    get_session_actions,
    log_action,
    update_action,
)
from integrations.ai.ai_engine import export_session_csv, generate_insight_from_outcome
from api.deps import get_current_user, user_has_perm

router = APIRouter(tags=["session-actions"])


class LogActionIn(BaseModel):
    action_type: str = "Outreach"
    channel: str = "Email"
    description: str = ""
    status: str = "planned"
    content_sent: Optional[str] = None
    context_note: Optional[str] = None
    scheduled_at: Optional[str] = None


class PatchActionIn(BaseModel):
    status: Optional[str] = None
    response_content: Optional[str] = None
    description: Optional[str] = None
    scheduled_at: Optional[str] = None
    shift_following_in_sequence: bool = False
    content_sent: Optional[str] = None
    draft_revisions: Optional[list[Any]] = None
    draft_original: Optional[str] = None


def _parse_scheduled_dt(val: str) -> Optional[datetime]:
    if not (val or "").strip():
        return None
    s = str(val).strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        s = f"{s}T12:00:00"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dt_from_action_sched(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_naive_utc(dt: datetime) -> datetime:
    """DB often stores naive times; client may send Zulu. Make subtraction safe."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class BulkDeleteIn(BaseModel):
    action_ids: list[int] = Field(default_factory=list)


@router.get("/sessions/{session_id}/action-board")
def action_board(session_id: int, user: dict = Depends(get_current_user)):
    del user
    if not get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    sequences = get_active_sequences(session_id)
    all_a = get_session_actions(session_id)
    non_seq = [a for a in all_a if not a.get("sequence_id")]
    return {"sequences": sequences, "standalone": non_seq, "all": all_a}


@router.post("/sessions/{session_id}/actions")
def post_log_action(
    session_id: int,
    body: LogActionIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    aid = log_action(
        session_id,
        user["id"],
        body.action_type,
        body.channel,
        body.description,
        body.status,
        content_sent=body.content_sent,
        context_note=body.context_note,
        scheduled_at=body.scheduled_at,
    )
    if body.status in (
        "replied_positive",
        "replied_negative",
        "meeting_booked",
        "deal_closed",
        "deal_lost",
        "no_response",
    ):
        from db_ops import get_company

        comp = get_company(sess["company_id"]) or {}
        generate_insight_from_outcome(
            user["id"],
            session_id,
            {
                "action_type": body.action_type,
                "channel": body.channel,
                "description": body.description,
                "status": body.status,
            },
            comp,
        )
    return {"id": aid}


@router.patch("/actions/{action_id}")
def patch_action(
    action_id: int,
    body: PatchActionIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    patch = body.model_dump(exclude_unset=True)
    shift_follow = bool(patch.pop("shift_following_in_sequence", False))
    sched_in = patch.get("scheduled_at")

    if sched_in is not None and shift_follow:
        act = get_action_by_id(action_id)
        if not act:
            raise HTTPException(status_code=404, detail="Action not found")
        seq_id = (act.get("sequence_id") or "").strip()
        new_dt = _parse_scheduled_dt(str(sched_in)) if sched_in else None
        if seq_id and new_dt:
            old_dt = _dt_from_action_sched(act.get("scheduled_at"))
            if old_dt is not None:
                delta = _as_naive_utc(new_dt) - _as_naive_utc(old_dt)
                my_step = int(act.get("sequence_step") or 0)
                sid = act.get("session_id")
                if sid and delta.total_seconds() != 0:
                    for st in get_sequence_actions(int(sid), seq_id):
                        st_step = int(st.get("sequence_step") or 0)
                        if st_step <= my_step:
                            continue
                        odt = _dt_from_action_sched(st.get("scheduled_at"))
                        if odt is None:
                            continue
                        nid = st.get("id")
                        if nid:
                            update_action(
                                int(nid),
                                scheduled_at=(_as_naive_utc(odt) + delta).isoformat(),
                            )

    if not patch:
        raise HTTPException(status_code=400, detail="No fields")
    update_action(action_id, **patch)
    return {"ok": True}


@router.delete("/actions/{action_id}")
def remove_action(action_id: int, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    delete_action(action_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/actions/bulk-delete")
def bulk_delete_actions(
    session_id: int,
    body: BulkDeleteIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    for aid in body.action_ids:
        try:
            delete_action(int(aid))
        except Exception:
            continue
    return {"ok": True, "deleted": len(body.action_ids)}


@router.get("/sessions/{session_id}/export.csv")
def export_csv(session_id: int, user: dict = Depends(get_current_user)):
    del user
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    from db_ops import get_company

    comp = get_company(sess["company_id"]) or {}
    domain = comp.get("domain") or "export"
    data = export_session_csv(session_id, domain)
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="lh2_{domain}_export.csv"'},
    )
