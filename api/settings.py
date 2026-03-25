import os
from pathlib import Path

from dotenv import load_dotenv

_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / "keys.env", override=True)
load_dotenv(_root / ".env")

JWT_SECRET = (os.getenv("JWT_SECRET") or os.getenv("LH2_JWT_SECRET") or "dev-change-me-in-production").strip()
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 days default

CORS_ORIGINS = [
    o.strip()
    for o in (os.getenv("CORS_ORIGINS") or "http://127.0.0.1:5173,http://localhost:5173").split(",")
    if o.strip()
]
