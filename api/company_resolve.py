"""Merge Media Houses advisor research onto shadow company dicts for AI context."""

from db_ops import get_advisor_company


def enrich_company_for_strategist(comp: dict | None) -> dict | None:
    if not comp:
        return comp
    extra = comp.get("extra_data") if isinstance(comp.get("extra_data"), dict) else {}
    aid = extra.get("source_advisor_company_id")
    if not aid:
        return comp
    adv = get_advisor_company(int(aid))
    if not adv:
        return comp
    adv_ed = adv.get("extra_data") if isinstance(adv.get("extra_data"), dict) else {}
    merged = {**extra}
    for k, v in adv_ed.items():
        if v is not None:
            merged[k] = v
    return {**comp, "extra_data": merged}
