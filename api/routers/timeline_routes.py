from fastapi import APIRouter, Depends, Query

from db_ops import get_timeline_actions
from api.deps import get_current_user

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("")
def timeline(
    user: dict = Depends(get_current_user),
    company_id: int | None = None,
    contact_id: int | None = None,
    limit: int = Query(50, le=200),
):
    del user
    return get_timeline_actions(company_id=company_id, contact_id=contact_id, limit=limit)
