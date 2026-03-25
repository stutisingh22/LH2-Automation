from fastapi import APIRouter, Depends, HTTPException

from db_ops import get_company, get_contact, get_session, load_messages
from api.deps import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{session_id}")
def session_detail(session_id: int, user: dict = Depends(get_current_user)):
    del user
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    out: dict = {"session": sess}
    cid = sess.get("contact_id")
    if cid:
        ct = get_contact(cid)
        if ct:
            out["contact"] = {
                "id": ct.get("id"),
                "email": ct.get("email"),
                "name": ct.get("name"),
            }
    comp_id = sess.get("company_id")
    if comp_id:
        comp = get_company(comp_id)
        if comp:
            out["company"] = {"domain": comp.get("domain"), "name": comp.get("name")}
    return out


@router.get("/{session_id}/messages")
def session_messages(session_id: int, user: dict = Depends(get_current_user)):
    del user
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = load_messages(session_id)
    return {"session": sess, "messages": msgs}
