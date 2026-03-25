from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db_ops import (
    delete_advisor_conversation,
    get_all_advisor_conversations,
    load_advisor_conversation,
    save_advisor_conversation,
)
from integrations.ai.ai_engine import advisor_chat, learn_user_preference
from api.deps import get_current_user, user_has_perm

router = APIRouter(prefix="/advisor-chat", tags=["advisor-chat"])


class AdvisorMessage(BaseModel):
    role: str
    content: str


class AdvisorTurnIn(BaseModel):
    messages: list[AdvisorMessage] = Field(default_factory=list)
    sender_email: str | None = None


@router.get("/conversations")
def list_conversations(user: dict = Depends(get_current_user)):
    del user
    return get_all_advisor_conversations(limit=30)


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: int, user: dict = Depends(get_current_user)):
    del user
    row = load_advisor_conversation(conv_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.delete("/conversations/{conv_id}")
def remove_conversation(conv_id: int, user: dict = Depends(get_current_user)):
    del user
    delete_advisor_conversation(conv_id)
    return {"ok": True}


@router.post("/turn")
def advisor_turn(body: AdvisorTurnIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    msgs = [m.model_dump() for m in body.messages]
    if not msgs or msgs[-1].get("role") != "user":
        raise HTTPException(status_code=400, detail="Last message must be user")
    user_msg = msgs[-1]["content"]
    hist = [{"role": m["role"], "content": m["content"]} for m in msgs[:-1]]
    reply = advisor_chat(user["id"], user_msg, hist, sender_email=body.sender_email or user.get("email"))
    out = msgs + [{"role": "assistant", "content": reply or ""}]
    save_advisor_conversation(user["id"], out)
    learn_user_preference(user["id"], user_msg, reply or "", source="advisor_chat")
    return {"reply": reply, "messages": out}
