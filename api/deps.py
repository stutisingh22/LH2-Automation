from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db_ops import get_user
from api.security import decode_token
from config import ROLE_PERMISSIONS
from config import is_allowed_user_email

_bearer = HTTPBearer(auto_error=False)


def user_has_perm(user: dict, key: str) -> bool:
    role = (user.get("role") or "viewer").strip()
    return bool(ROLE_PERMISSIONS.get(role, {}).get(key))


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    if not creds or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(creds.credentials)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        uid = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token subject")
    user = get_user(uid)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not is_allowed_user_email(user.get("email")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not allowed")
    return user
