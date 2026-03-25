from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from db_ops import get_timeline_actions
from api.deps import get_current_user

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/upcoming")
def upcoming(
    user: dict = Depends(get_current_user),
    source: str = Query("independent", description="independent | media"),
    days: int = Query(90, le=365),
):
    del user
    tl = get_timeline_actions(limit=2000)
    if source == "media":
        tl = [a for a in tl if a.get("source_advisor_company_id")]
    else:
        tl = [a for a in tl if not a.get("source_advisor_company_id")]

    now = datetime.utcnow()
    horizon = now + timedelta(days=days)
    upcoming = []
    for a in tl:
        sa = a.get("scheduled_at")
        if not sa:
            continue
        try:
            dt = datetime.fromisoformat(str(sa).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt < now or dt > horizon:
            continue
        if a.get("status") in ("completed", "deal_closed", "deal_lost"):
            continue
        upcoming.append({"at": dt.isoformat(), "action": a})

    upcoming.sort(key=lambda x: x["at"])
    return {"items": upcoming}
