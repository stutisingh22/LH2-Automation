from fastapi import APIRouter, Depends, HTTPException

from db_ops import (
    delete_session,
    get_all_actions,
    get_all_global_insights,
    get_all_sessions,
    get_message_count,
    get_session_actions,
)
from api.deps import get_current_user, user_has_perm

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/summary")
def history_summary(user: dict = Depends(get_current_user)):
    del user
    ginsights = get_all_global_insights(limit=8)
    all_ua = get_all_actions(limit=200)
    analytics = None
    if all_ua:
        total = len(all_ua)
        sent = sum(
            1
            for a in all_ua
            if a["status"]
            in (
                "sent",
                "completed",
                "replied_positive",
                "replied_negative",
                "meeting_booked",
                "deal_closed",
            )
        )
        pos = sum(1 for a in all_ua if a["status"] in ("replied_positive", "meeting_booked", "deal_closed"))
        mtg = sum(1 for a in all_ua if a["status"] == "meeting_booked")
        nr = sum(1 for a in all_ua if a["status"] == "no_response")
        rate = f"{(pos / sent * 100):.0f}%" if sent else "—"
        analytics = {"total": total, "sent": sent, "positive_rate": rate, "meetings": mtg, "no_response": nr}
    sessions = get_all_sessions()
    enriched = []
    for sx in sessions:
        enriched.append(
            {
                **sx,
                "message_count": get_message_count(sx["id"]),
                "action_count": len(get_session_actions(sx["id"])),
            }
        )
    return {"global_insights": ginsights, "analytics": analytics, "sessions": enriched}


@router.delete("/sessions/{session_id}")
def remove_session(session_id: int, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_delete_sessions"):
        raise HTTPException(status_code=403, detail="Forbidden")
    delete_session(session_id)
    return {"ok": True}
