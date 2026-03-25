from fastapi import APIRouter, Depends, Query

from db_ops import (
    get_all_advisor_companies,
    get_all_companies,
    get_overdue_followups,
    get_pipeline_stats,
    get_recent_activity,
    get_todays_scheduled,
)
from api.spoc_data import (
    build_publisher_crm_brand_detail_parent_group,
    build_publisher_crm_group_detail,
    paginate_publisher_crm_list,
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


@router.get("/publisher-crm")
def publisher_crm(
    source: str = Query("independent", description="independent | media"),
    offer_tab: str = Query("", description="indication | general_acq"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=3000),
    search: str = Query("", description="Server-side filter on domains / SPOC names / emails"),
    kpi: str = Query(
        "total",
        description="total | in_campaign | responded | no_response | won | due",
    ),
    due_dates: str = Query(
        "",
        description="Pipe-separated next-step display dates for Due KPI/filter, e.g. Mar 25, 2026|Mar 26, 2026",
    ),
    compact: bool = Query(
        False,
        description="Omit heavy list-only fields (contact_ids, advisor_company_ids, planned_preview, etc.) for a smaller JSON body.",
    ),
    user: dict = Depends(get_current_user),
):
    del user

    due_list = [p.strip() for p in (due_dates or "").split("|") if p.strip()]
    kpis, groups, total_filtered = paginate_publisher_crm_list(
        source,
        offer_tab or None,
        page=page,
        page_size=page_size,
        search=search,
        kpi=kpi,
        due_dates=due_list or None,
        compact=compact,
    )

    return {
        "kpis": kpis,
        "groups": groups,
        "total": total_filtered,
        "page": page,
        "page_size": page_size,
    }


@router.get("/publisher-crm/detail")
def publisher_crm_detail(
    source: str = Query("independent"),
    offer_tab: str = Query(""),
    company_id: int | None = Query(None),
    advisor_company_id: int | None = Query(None),
    parent_key: str = Query(
        "",
        description='Optional media parent group key, e.g. "parent:Acme Corp" for full household',
    ),
    user: dict = Depends(get_current_user),
):
    del user
    if parent_key.strip():
        g = build_publisher_crm_brand_detail_parent_group(
            source, offer_tab or None, parent_key=parent_key.strip()
        )
        return {"group": g}

    g = build_publisher_crm_group_detail(
        source,
        offer_tab or None,
        company_id=company_id,
        advisor_company_id=advisor_company_id,
    )
    return {"group": g}
