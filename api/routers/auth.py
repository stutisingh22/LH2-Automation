from fastapi import APIRouter, Depends, HTTPException, status

from db_ops import authenticate, create_user, get_user
from api.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from api.security import create_access_token
from api.deps import get_current_user
from config import is_allowed_user_email

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn):
    user = authenticate(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not is_allowed_user_email(user.get("email")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not allowed")
    token = create_access_token(user["id"], user["username"])
    return TokenOut(access_token=token)


@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn):
    if not is_allowed_user_email(body.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This email is not allowed")
    ok = create_user(body.username.strip(), body.password, email=body.email)
    if not ok:
        raise HTTPException(status_code=400, detail="Username already taken")
    user = authenticate(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=500, detail="Registration failed")
    if not is_allowed_user_email(user.get("email")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not allowed")
    token = create_access_token(user["id"], user["username"])
    return TokenOut(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    return UserOut(**user)
