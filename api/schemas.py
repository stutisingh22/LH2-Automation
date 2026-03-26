from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    username: str
    password: str


class RegisterIn(BaseModel):
    username: str
    password: str
    email: Optional[str] = None


class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    role: str


class SessionEnsureIn(BaseModel):
    """contact_id null = company-level session (shared thread, no specific recipient)."""
    contact_id: Optional[int] = None


class ChatStreamIn(BaseModel):
    message: str = Field(..., min_length=1)
    mode: Literal["chat", "plan"] = "chat"
    product_key: Optional[str] = None
    sender_email: Optional[str] = None
    recipient_research: Optional[Any] = None
    strategist_rules: Optional[str] = None
