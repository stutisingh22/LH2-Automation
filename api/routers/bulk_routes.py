from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from services.bulk_domain_import import analyze_from_file, analyze_from_text, commit_rows, undo_created
from api.deps import get_current_user


class FileWrap:
    def __init__(self, raw: bytes, name: str):
        self._raw = raw
        self.name = name

    def read(self):
        return self._raw


class BulkCommitIn(BaseModel):
    rows: list[dict]
    allow_new_parents: bool = False


class BulkUndoIn(BaseModel):
    created: list


router = APIRouter(prefix="/bulk", tags=["bulk"])


@router.post("/analyze-file")
async def bulk_analyze_file(
    user: dict = Depends(get_current_user),
    file: UploadFile = File(...),
    verify_ownership: bool = Form(True),
):
    del user
    raw = await file.read()
    name = file.filename or "upload.csv"
    preview = analyze_from_file(FileWrap(raw, name), verify_ownership=verify_ownership)
    return preview


@router.post("/analyze-text")
def bulk_analyze_text(
    user: dict = Depends(get_current_user),
    text: str = Form(""),
    verify_ownership: bool = Form(True),
):
    del user
    return analyze_from_text(text, verify_ownership=verify_ownership)


@router.post("/commit")
def bulk_commit(body: BulkCommitIn, user: dict = Depends(get_current_user)):
    del user
    return commit_rows(body.rows, allow_new_parents=body.allow_new_parents)


@router.post("/undo")
def bulk_undo(body: BulkUndoIn, user: dict = Depends(get_current_user)):
    del user
    return undo_created(body.created or [])
