from fastapi import APIRouter, Depends, Query

from db_ops import (
    get_all_advisor_companies,
    get_all_companies,
    get_overdue_followups,
    get_pipeline_stats,
    get_recent_activity,
    get_todays_scheduled,
)
from api.spoc_data import build_publisher_crm_rows
from api.deps import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary(
    user: dict = Depends(get_current_user),
    source: str = Query("independent", description="independent | media"),
):
    del user
    stats = get_pipeline_stats()
    if source == "media":
        companies_count = len(get_all_advisor_companies())
    else:
        companies_count = len(get_all_companies(exclude_advisor_shadows=True))
    return {
        "stats": stats,
        "companies_count": companies_count,
        "overdue": get_overdue_followups(3)[:5],
        "todays": get_todays_scheduled()[:5],
        "recent": get_recent_activity(10),
    }


@router.get("/publisher-crm")
def publisher_crm(
    source: str = Query("independent", description="independent | media"),
    offer_tab: str = Query("", description="indication | general_acq"),
    user: dict = Depends(get_current_user),
):
    del user

    rows = build_publisher_crm_rows(source=source, offer_tab=offer_tab or None)

    # KPI tiles (based on normalized contact status)
    total = len(rows)
    in_campaign = sum(1 for r in rows if r.get("status") == "outreach_sent")
    responded = sum(1 for r in rows if r.get("status") in ("replied_positive", "meeting_booked"))
    no_response = sum(1 for r in rows if r.get("status") == "not_contacted")
    won = sum(1 for r in rows if r.get("status") == "partnership_active")
    due_today = sum(1 for r in rows if bool(r.get("due_today")))

    kpis = {
        "total": total,
        "in_campaign": in_campaign,
        "responded": responded,
        "no_response": no_response,
        "won": won,
        "due_today": due_today,
    }

    # Light sorting: due-today first, then next-step date as display string.
    rows_sorted = sorted(rows, key=lambda r: (not bool(r.get("due_today")), str(r.get("next_step_date") or "")))
    return {"kpis": kpis, "rows": rows_sorted}
