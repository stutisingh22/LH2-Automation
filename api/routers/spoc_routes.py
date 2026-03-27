from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db_ops import delete_spoc_contact, update_action, update_spoc_contact
from api.spoc_data import build_spoc_tracking_rows
from api.deps import get_current_user

router = APIRouter(prefix="/spoc", tags=["spoc"])


class RescheduleIn(BaseModel):
    contact_id: int
    new_date: str  # YYYY-MM-DD


class SpocContactPatchIn(BaseModel):
    email: str = ""


@router.get("/rows")
def spoc_rows(
    user: dict = Depends(get_current_user),
    q: str = Query("", description="Client-side filter optional; server returns all when empty"),
):
    del user
    rows = build_spoc_tracking_rows()
    if (q or "").strip():
        qq = q.strip().lower()
        rows = [r for r in rows if qq in r.get("search_text", "")]
    return {"rows": rows}


@router.delete("/contacts/{spoc_contact_id}")
def remove_spoc(spoc_contact_id: int, user: dict = Depends(get_current_user)):
    del user
    if delete_spoc_contact(spoc_contact_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Not found")


@router.patch("/contacts/{spoc_contact_id}")
def patch_spoc_contact(
    spoc_contact_id: int,
    body: SpocContactPatchIn,
    user: dict = Depends(get_current_user),
):
    del user
    if update_spoc_contact(spoc_contact_id, email=(body.email or "").strip()):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Not found")


@router.post("/reschedule")
def reschedule_overdue(body: RescheduleIn, user: dict = Depends(get_current_user)):
    del user
    rows = build_spoc_tracking_rows()
    row = next((r for r in rows if r["contact"]["id"] == body.contact_id), None)
    if not row or not row.get("first_overdue_action"):
        raise HTTPException(status_code=400, detail="No overdue action for this contact")
    _fo = row["first_overdue_action"]
    planned_sorted = sorted(row["planned"], key=lambda x: (x.get("scheduled_at") or ""))
    try:
        _old_first = datetime.fromisoformat(_fo["scheduled_at"].replace("Z", "+00:00"))
        new_d = datetime.fromisoformat(body.new_date).date()
        _new_first = _old_first.replace(year=new_d.year, month=new_d.month, day=new_d.day)
        _offset = _new_first - _old_first
        for _a in planned_sorted:
            _s = _a.get("scheduled_at")
            if not _s:
                continue
            _dt = datetime.fromisoformat(_s.replace("Z", "+00:00"))
            if _dt < _old_first:
                continue
            if _a["id"] == _fo["id"]:
                update_action(_a["id"], scheduled_at=_new_first.isoformat())
            else:
                update_action(_a["id"], scheduled_at=(_dt + _offset).isoformat())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}
