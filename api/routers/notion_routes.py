from fastapi import APIRouter, Depends, HTTPException

from integrations.notion.sync import sync_notion_to_db
from integrations.notion.sync_advisor import sync_notion_advisor_to_db
from api.deps import get_current_user

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/notion/independent")
def sync_independent(user: dict = Depends(get_current_user)):
    del user
    r = sync_notion_to_db()
    if r.get("error"):
        raise HTTPException(status_code=502, detail=r["error"])
    return r


@router.post("/notion/media-houses")
def sync_media_houses(user: dict = Depends(get_current_user)):
    del user
    r = sync_notion_advisor_to_db()
    if r.get("error"):
        raise HTTPException(status_code=502, detail=r["error"])
    return r
