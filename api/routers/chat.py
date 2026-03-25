from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from db_ops import get_company, get_contact, get_session, load_messages, save_message
from api.company_resolve import enrich_company_for_strategist
from integrations.ai.ai_engine import (
    extract_episodic_memories,
    get_strategist_chat_response_stream,
    get_strategist_response_stream,
    maybe_update_summary,
)
from api.deps import get_current_user
from api.helpers import is_placeholder_contact, research_notes_from_company
from api.schemas import ChatStreamIn

router = APIRouter(prefix="/sessions", tags=["chat"])


@router.post("/{session_id}/chat/stream")
def strategist_chat_stream(
    session_id: int,
    body: ChatStreamIn,
    user: dict = Depends(get_current_user),
):
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    comp = enrich_company_for_strategist(get_company(sess["company_id"]))
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    ct = get_contact(sess["contact_id"]) if sess.get("contact_id") else None
    if ct is None:
        ct = {
            "id": None,
            "name": (comp.get("name") or comp.get("domain") or "Company").strip() or "Company",
            "role": "",
            "email": "",
            "linkedin": "",
            "country": "",
            "state": "",
            "city": "",
        }
    elif is_placeholder_contact(ct, comp):
        raise HTTPException(
            status_code=400,
            detail="Select a contact with a real name, email, or LinkedIn before messaging the strategist.",
        )

    uid = user["id"]
    sender_email = (body.sender_email or user.get("email") or "").strip() or None
    rn = research_notes_from_company(comp)
    recipient_research = body.recipient_research

    save_message(session_id, "user", body.message)
    messages = load_messages(session_id)
    chat_for_ai = [{"role": m["role"], "content": m["content"]} for m in messages]

    def stream_with_persist():
        chunks: list[str] = []
        try:
            if body.mode == "plan":
                gen = get_strategist_response_stream(
                    comp,
                    ct,
                    chat_for_ai,
                    body.message,
                    uid,
                    session_id,
                    research_notes=rn,
                    sender_email=sender_email,
                    product_key=body.product_key,
                    recipient_research=recipient_research,
                )
            else:
                gen = get_strategist_chat_response_stream(
                    comp,
                    ct,
                    chat_for_ai,
                    body.message,
                    uid,
                    session_id,
                    research_notes=rn,
                    sender_email=sender_email,
                    product_key=body.product_key,
                    recipient_research=recipient_research,
                )
            for text in gen:
                chunks.append(text)
                yield text
        finally:
            response = "".join(chunks).strip()
            if response:
                save_message(session_id, "assistant", response)
                try:
                    maybe_update_summary(session_id, comp, ct)
                except Exception:
                    pass
                try:
                    extract_episodic_memories(session_id, response, comp)
                except Exception:
                    pass

    return StreamingResponse(
        stream_with_persist(),
        media_type="text/plain; charset=utf-8",
    )
