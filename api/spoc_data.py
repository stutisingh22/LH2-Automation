"""Server-side SPOC tracking row builder (same rules as Streamlit apps.py)."""

from __future__ import annotations

from datetime import datetime

from config import CONTACT_STATUS_LABELS, normalize_contact_status
from db_ops import (
    get_all_companies,
    get_contacts,
    get_session_actions,
    get_sessions_for_company,
    get_spoc_contacts_by_spoc,
)


def build_spoc_tracking_rows() -> list[dict]:
    _today = datetime.utcnow().date()
    all_comp = get_all_companies(exclude_advisor_shadows=True)
    spoc_rows: list[dict] = []

    for cd in all_comp:
        for ctd in get_contacts(cd["id"]):
            sub = get_spoc_contacts_by_spoc(ctd["id"])
            sessions = get_sessions_for_company(cd["id"])
            sess = next((s for s in sessions if s.get("contact_id") == ctd["id"]), None)
            actions = get_session_actions(sess["id"]) if sess else []
            planned = [
                a
                for a in actions
                if (a.get("status") or "").strip().lower() == "planned" and a.get("scheduled_at")
            ]
            next_dates = []
            planned_past = []
            for a in planned:
                try:
                    dt = datetime.fromisoformat(a["scheduled_at"].replace("Z", "+00:00"))
                    if dt.date() >= _today:
                        next_dates.append(dt)
                    else:
                        planned_past.append((a, dt))
                except Exception:
                    pass
            planned_past.sort(key=lambda x: x[1])
            first_overdue_action = planned_past[0][0] if planned_past else None
            first_overdue_date_str = planned_past[0][1].strftime("%b %d, %Y") if planned_past else None
            next_step_date = min(next_dates).strftime("%b %d, %Y") if next_dates else "—"
            primary_spoc = sub[0] if sub else None

            if primary_spoc:
                _has_real = (
                    (primary_spoc.get("name") or "").strip()
                    or (primary_spoc.get("email") or "").strip()
                    or (primary_spoc.get("linkedin_url") or "").strip()
                )
                if not _has_real:
                    continue
            else:
                _ct_name = (ctd.get("name") or "").strip()
                _ct_email = (ctd.get("email") or "").strip()
                _ct_li = (ctd.get("linkedin") or "").strip()
                _ct_domain = (cd.get("domain") or "").strip().lower()
                _name_is_placeholder = (not _ct_name) or (_ct_name.lower() == _ct_domain) or (
                    _ct_name.lower().replace("www.", "") == _ct_domain.replace("www.", "")
                )
                if _name_is_placeholder and not _ct_email and not _ct_li:
                    continue

            company_display = (cd.get("name") or cd.get("domain") or "—").strip() or "—"
            if primary_spoc:
                _nm = (primary_spoc.get("name") or "—").strip() or "—"
                _role = (primary_spoc.get("role") or "—").strip() or "—"
                _em = (primary_spoc.get("email") or "—").strip() or "—"
                _ph = (primary_spoc.get("phone") or "—")
                _ph = _ph.strip() if isinstance(_ph, str) else "—"
            else:
                _nm = (ctd.get("name") or "—").strip() or "—"
                _role = (ctd.get("role") or "—").strip() or "—"
                _em = (ctd.get("email") or "—").strip() or "—"
                _ph = ((ctd.get("extra_data") or {}).get("phone") or "—")
                _ph = _ph.strip() if isinstance(_ph, str) else "—"
            _norm = normalize_contact_status(ctd.get("status", "not_contacted"))
            _status_lbl = CONTACT_STATUS_LABELS.get(_norm, _norm)
            _search_text = " ".join(
                [
                    company_display,
                    _nm,
                    _role,
                    _em,
                    _ph,
                    _status_lbl,
                    next_step_date,
                    first_overdue_date_str or "",
                ]
            ).lower()

            spoc_rows.append(
                {
                    "company": cd,
                    "contact": ctd,
                    "session": sess,
                    "actions": actions,
                    "planned": planned,
                    "next_step_date": next_step_date,
                    "sub_count": len(sub),
                    "primary_spoc": primary_spoc,
                    "subs": sub,
                    "first_overdue_action": first_overdue_action,
                    "first_overdue_date_str": first_overdue_date_str,
                    "search_text": _search_text,
                }
            )

    return spoc_rows
