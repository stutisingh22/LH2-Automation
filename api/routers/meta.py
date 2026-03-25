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
