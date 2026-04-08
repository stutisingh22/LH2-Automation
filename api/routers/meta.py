import os
import subprocess
from pathlib import Path

from fastapi import APIRouter

from config import (
    ACTION_CHANNELS,
    ACTION_STATUSES,
    ACTION_STATUS_COLORS,
    ACTION_TYPES,
    CONTACT_STATUSES,
    CONTACT_STATUS_LABELS,
    CONTACT_STATUS_LABELS_STRIP,
)

router = APIRouter(prefix="/meta", tags=["meta"])

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_git_revision() -> str | None:
    for key in (
        "DEPLOY_GIT_SHA",
        "GIT_COMMIT",
        "RAILWAY_GIT_COMMIT_SHA",
        "RENDER_GIT_COMMIT",
        "VERCEL_GIT_COMMIT_SHA",
    ):
        v = (os.getenv(key) or "").strip()
        if v:
            return v
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


@router.get("/build")
def build_info():
    """Deploy identity + bulk-import toggles (compare local curl vs server)."""
    from services.bulk_domain_import import bulk_import_runtime_settings

    db_url = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI") or ""
    db_backend = "postgres" if "postgres" in db_url.lower() else ("sqlite" if "sqlite" in db_url.lower() else ("set" if db_url else "unset"))

    similarweb_cookie_set = bool((os.getenv("SIMILARWEB_COOKIES") or os.getenv("SIMWEB_COOKIES") or "").strip())
    similarweb_cookie_file = (os.getenv("SIMILARWEB_COOKIES_FILE") or "").strip()
    if not similarweb_cookie_file:
        similarweb_cookie_file = str(_REPO_ROOT / "similarweb_cookies.txt")
    similarweb_cookie_file_exists = False
    try:
        similarweb_cookie_file_exists = Path(similarweb_cookie_file).exists()
    except Exception:
        similarweb_cookie_file_exists = False

    return {
        "git_revision": _resolve_git_revision(),
        "database_backend_hint": db_backend,
        "bulk_import": bulk_import_runtime_settings(),
        "similarweb_cookie_set": similarweb_cookie_set,
        "similarweb_cookie_file_exists": similarweb_cookie_file_exists,
    }


@router.get("/config")
def ui_config():
    try:
        from product_profiles import get_product_options

        products = [{"key": k, "label": lbl} for k, lbl in get_product_options()]
    except Exception:
        products = []
    return {
        "contact_statuses": CONTACT_STATUSES,
        "contact_status_labels": CONTACT_STATUS_LABELS,
        "contact_status_labels_strip": CONTACT_STATUS_LABELS_STRIP,
        "action_types": ACTION_TYPES,
        "action_channels": ACTION_CHANNELS,
        "action_statuses": ACTION_STATUSES,
        "action_status_colors": ACTION_STATUS_COLORS,
        "products": products,
    }
