from fastapi import APIRouter, Depends

from db_ops import get_all_users
from api.deps import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(user: dict = Depends(get_current_user)):
    del user
    return get_all_users()
