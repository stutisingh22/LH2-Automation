from fastapi import APIRouter, Depends, Query

from db_ops import (
    get_all_advisor_companies,
    get_all_companies,
    get_overdue_followups,
    get_pipeline_stats,
    get_recent_activity,
    get_todays_scheduled,
)
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
