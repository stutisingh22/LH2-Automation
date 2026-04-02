from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import get_current_user
from db_ops import create_campaign, get_campaign, list_campaigns

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignCreateIn(BaseModel):
    name: str = Field(..., min_length=1)
    status: str = Field(default="active")
    starts_at: str | None = None
    ends_at: str | None = None


@router.get("")
def list_campaigns_route(
    include_inactive: bool = Query(True),
    user: dict = Depends(get_current_user),
):
    del user
    return list_campaigns(include_inactive=include_inactive)


@router.get("/{campaign_id}")
def get_campaign_route(campaign_id: int, user: dict = Depends(get_current_user)):
    del user
    row = get_campaign(campaign_id)
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return row


@router.post("")
def create_campaign_route(body: CampaignCreateIn, user: dict = Depends(get_current_user)):
    del user
    starts = None
    ends = None
    try:
        if body.starts_at:
            starts = datetime.fromisoformat(body.starts_at.replace("Z", "+00:00"))
        if body.ends_at:
            ends = datetime.fromisoformat(body.ends_at.replace("Z", "+00:00"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}") from e
    try:
        return create_campaign(
            body.name,
            status=body.status,
            starts_at=starts,
            ends_at=ends,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

