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
    title_q: str = Query("", description="Substring match for Website / Media House column"),
    spoc_q: str = Query("", description="Substring match for SPOC name column (independent)"),
    status_filter: str = Query("", description="Exact pipeline status filter (independent only)"),
    next_step_q: str = Query("", description="Next step date filter: YYYY-MM-DD or 'Mon DD, YYYY'"),
    relevance_filter: str = Query("", description="Exact relevance filter"),
    traffic_min: int | None = Query(None, ge=0, description="Min traffic filter (monthly_visits)"),
    traffic_max: int | None = Query(None, ge=0, description="Max traffic filter (monthly_visits)"),
    syndication_filter: str = Query("", description="Exact syndication tag filter (msn|yahoo|newsbreak|smartnews)"),
    brands_min: int | None = Query(None, ge=0, description="Min brand count (media only)"),
    contacted_min: int | None = Query(None, ge=0, description="Min contacted brand count (media only)"),
    ceo_q: str = Query("", description="Substring match for CEO / Decision Maker column (media only)"),
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
        title_q=title_q,
        spoc_q=spoc_q,
        status_filter=status_filter,
        next_step_q=next_step_q,
        relevance_filter=relevance_filter,
        traffic_min=traffic_min,
        traffic_max=traffic_max,
        syndication_filter=syndication_filter,
        brands_min=brands_min,
        contacted_min=contacted_min,
        ceo_q=ceo_q,
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
