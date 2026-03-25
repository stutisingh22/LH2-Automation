"""Server-side SPOC tracking row builder (same rules as Streamlit apps.py)."""

from __future__ import annotations

from datetime import datetime
import re

from config import CONTACT_STATUS_LABELS, normalize_contact_status
from db_ops import (
    get_advisor_parents_with_children,
    get_advisor_company,
    get_all_companies,
    get_company,
    get_company_by_domain,
    get_contact,
    get_contacts,
    get_or_create_shadow_company_for_advisor,
    get_session_actions,
    get_sessions_for_company,
    get_spoc_contacts_by_company,
    get_spoc_contacts_by_spoc,
)

from integrations.analytics.tracker_sources import load_domain_to_platforms, get_presence_for_domain


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


def _infer_offering_labels_from_actions(actions: list[dict] | None) -> set[str]:
    """
    Infer offering labels (Accelerate/MediaOS/Acquisition) from planned action descriptions.
    Strategist action description format: "... — Accelerate|MediaOS|Acquisition".
    """
    labels: set[str] = set()
    if not actions:
        return labels

    # Accept either em-dash (—) or hyphen (-) separator.
    rx = re.compile(r"(?:—|-)\s*(Accelerate|MediaOS|Acquisition)\b", re.IGNORECASE)
    for a in actions:
        desc = (a or {}).get("description") or ""
        m = rx.search(desc)
        if not m:
            continue
        v = (m.group(1) or "").strip().lower()
        if v == "accelerate":
            labels.add("Accelerate")
        elif v == "mediaos":
            labels.add("MediaOS")
        elif v == "acquisition":
            labels.add("Acquisition")
    return labels


def build_publisher_crm_rows(source: str, offer_tab: str | None = None) -> list[dict]:
    """
    Build CRM rows powering the React Dashboard (Publisher CRM).

    source:
      - independent: real companies + contacts (excludes Media Houses shadow companies)
      - media: Media Houses child companies represented as shadow companies (includes only shadows)

    offer_tab:
      - indication: keep rows with next planned offering = Accelerate
      - general_acq: keep rows with next planned offering = MediaOS or Acquisition
      - None/other: keep all
    """
    _today = datetime.utcnow().date()
    # Tracker CSV presence lookup for syndication display (fast; no external API).
    tracker_map = load_domain_to_platforms()

    src = (source or "").strip().lower()
    if src not in ("independent", "media"):
        src = "independent"

    # Build media rows from advisor tree directly (not only from existing shadow companies),
    # so all children are visible even before Strategist creates any session/contact.
    if src == "media":
        all_comp = []
    else:
        all_comp = [
            c
            for c in get_all_companies(exclude_advisor_shadows=True)
            if (c.get("extra_data") or {}).get("source_advisor_company_id") is None
        ]

    offer_key = (offer_tab or "").strip().lower()
    # Map UI tab to offering labels.
    if offer_key == "indication":
        want_labels = {"Accelerate"}
    elif offer_key in ("general_acq", "general-acq", "general aq", "general acq"):
        want_labels = {"MediaOS", "Acquisition"}
    else:
        want_labels = set()

    out_rows: list[dict] = []

    if src == "media":
        parents = get_advisor_parents_with_children() or []
        # Avoid repeating DB lookups for Similarweb monthly_visits per child domain.
        monthly_visits_cache: dict[str, int | None] = {}
        for g in parents:
            parent_name = (g.get("parent_name") or "").strip() or "(No parent)"
            for ch in g.get("children") or []:
                advisor_company_id = ch.get("id")
                if not advisor_company_id:
                    continue
                shadow_company_id, shadow_contact_id = get_or_create_shadow_company_for_advisor(advisor_company_id)
                shadow_company = get_company(shadow_company_id) if shadow_company_id else None
                ctd = get_contact(shadow_contact_id) if shadow_contact_id else None
                sessions = get_sessions_for_company(shadow_company_id) if shadow_company_id else []
                sess = next((s for s in sessions if ctd and s.get("contact_id") == ctd.get("id")), None)
                actions = get_session_actions(sess["id"]) if sess else []
                planned = [
                    a
                    for a in actions
                    if (a.get("status") or "").strip().lower() == "planned" and a.get("scheduled_at")
                ]

                next_dates: list[datetime] = []
                due_today = False
                planned_past: list[tuple[dict, datetime]] = []
                for a in planned:
                    try:
                        dt = datetime.fromisoformat((a.get("scheduled_at") or "").replace("Z", "+00:00"))
                        if dt.date() >= _today:
                            next_dates.append(dt)
                        else:
                            planned_past.append((a, dt))
                    except Exception:
                        continue
                planned_past.sort(key=lambda x: x[1])
                first_overdue_date_str = planned_past[0][1].strftime("%b %d, %Y") if planned_past else None
                min_next_dt = min(next_dates) if next_dates else None
                next_step_date = min_next_dt.strftime("%b %d, %Y") if min_next_dt else "—"
                # "Due today" must align with the first upcoming step that will be shown in the UI.
                due_today = bool(min_next_dt and min_next_dt.date() == _today)

                offering_labels = _infer_offering_labels_from_actions(planned)
                if want_labels and offering_labels and not (offering_labels & want_labels):
                    continue

                status_norm = normalize_contact_status((ch.get("extra_data") or {}).get("pipeline_status", "not_contacted"))
                status_label = CONTACT_STATUS_LABELS.get(status_norm, status_norm)

                extra = (shadow_company or {}).get("extra_data") or {}
                platform_presence = extra.get("platform_presence") or {}
                # Shadow companies may not have platform_presence saved yet.
                # Fall back to tracker_sources.csv using the *child* domain.
                if not platform_presence or not any(
                    bool(platform_presence.get(k))
                    for k in ("msn", "yahoo", "newsbreak", "smartnews")
                ):
                    child_domain = (ch.get("domain") or "").strip()
                    platform_presence = get_presence_for_domain(child_domain, tracker_map) if child_domain else platform_presence

                syndication = [
                    k
                    for k, v in (platform_presence or {}).items()
                    if k in ("msn", "yahoo", "newsbreak", "smartnews") and bool(v)
                ]
                # Traffic (Similarweb) must be sourced from the *real* child domain.
                # Media Houses use shadow companies (`__advisor_*`) for pipeline actions,
                # but Similarweb monthly_visits is stored on the real company domain.
                child_domain = (ch.get("domain") or "").strip()
                monthly_visits = (shadow_company or {}).get("monthly_visits")
                if monthly_visits is None and child_domain:
                    if child_domain not in monthly_visits_cache:
                        comp = get_company_by_domain(child_domain) or {}
                        monthly_visits_cache[child_domain] = comp.get("monthly_visits")
                    monthly_visits = monthly_visits_cache.get(child_domain)

                planned_sorted = sorted(planned, key=lambda a: a.get("scheduled_at") or "")
                planned_preview = [
                    {
                        "id": a.get("id"),
                        "sequence_step": a.get("sequence_step"),
                        "channel": a.get("channel"),
                        "status": a.get("status"),
                        "scheduled_at": a.get("scheduled_at"),
                        "description": a.get("description"),
                    }
                    for a in planned_sorted[:4]
                ]

                contacts_summary = []
                if ctd:
                    contacts_summary.append(
                        {
                            "kind": "contact",
                            "name": (ctd.get("name") or "").strip() or "Leadership",
                            "role": (ctd.get("role") or "").strip() or "Leadership",
                            "email": (ctd.get("email") or "").strip(),
                            "linkedin_url": (ctd.get("linkedin") or "").strip(),
                        }
                    )
                if shadow_company_id:
                    for srow in get_spoc_contacts_by_company(shadow_company_id) or []:
                        contacts_summary.append(
                            {
                                "kind": "spoc",
                                "name": (srow.get("name") or "").strip() or "—",
                                "role": (srow.get("role") or "").strip() or "—",
                                "email": (srow.get("email") or "").strip(),
                                "linkedin_url": (srow.get("linkedin_url") or "").strip(),
                            }
                        )

                out_rows.append(
                    {
                        "row_type": "publisher",
                        "company_id": shadow_company_id or 0,
                        "company_domain": (ch.get("domain") or ch.get("parent_name") or "—"),
                        "contact_id": (ctd or {}).get("id") or 0,
                        "advisor_company_id": advisor_company_id,
                        "parent_name": parent_name,
                        "source": src,
                        "spoc_name": (ctd or {}).get("name") or "Leadership",
                        "spoc_role": (ctd or {}).get("role") or "Leadership",
                        "spoc_email": (ctd or {}).get("email") or "",
                        "spoc_linkedin_url": (ctd or {}).get("linkedin") or "",
                        "spoc_phone": ((ctd or {}).get("extra_data") or {}).get("phone") or "",
                        "status": status_norm,
                        "status_label": status_label,
                        "next_step_date": next_step_date,
                        "due_today": due_today,
                        "first_overdue_date_str": first_overdue_date_str,
                        "platform_presence": platform_presence,
                        "syndication": syndication,
                        "monthly_visits": monthly_visits,
                        "planned_preview": planned_preview,
                        "contacts_summary": contacts_summary,
                        "offering_labels": sorted(offering_labels),
                    }
                )
        return out_rows

    for cd in all_comp:
        company_id = cd.get("id")
        if not company_id:
            continue

        company_contacts = get_contacts(company_id) or []
        # Ensure company still appears even if no contacts are created yet.
        if not company_contacts:
            company_contacts = [{"id": 0, "name": "—", "role": "—", "email": "", "linkedin": "", "status": "not_contacted", "extra_data": {}}]

        for ctd in company_contacts:
            sub = get_spoc_contacts_by_spoc(ctd["id"])
            sessions = get_sessions_for_company(company_id)
            sess = next((s for s in sessions if s.get("contact_id") == ctd["id"]), None)
            actions = get_session_actions(sess["id"]) if sess else []

            planned: list[dict] = [
                a
                for a in actions
                if (a.get("status") or "").strip().lower() == "planned" and a.get("scheduled_at")
            ]

            # Next step / due today / overdue
            next_dates: list[datetime] = []
            planned_past: list[tuple[dict, datetime]] = []
            due_today = False
            for a in planned:
                try:
                    dt = datetime.fromisoformat((a.get("scheduled_at") or "").replace("Z", "+00:00"))
                    if dt.date() >= _today:
                        next_dates.append(dt)
                    else:
                        planned_past.append((a, dt))
                except Exception:
                    continue

            planned_past.sort(key=lambda x: x[1])
            first_overdue_action = planned_past[0][0] if planned_past else None
            first_overdue_date_str = planned_past[0][1].strftime("%b %d, %Y") if planned_past else None

            min_next_dt = min(next_dates) if next_dates else None
            next_step_date = min_next_dt.strftime("%b %d, %Y") if min_next_dt else "—"
            due_today = bool(min_next_dt and min_next_dt.date() == _today)

            primary_spoc = sub[0] if sub else None

            # Prefer showing leadership/sub-SPOC when we have it.
            if primary_spoc:
                _nm = (primary_spoc.get("name") or "—").strip() or "—"
                _role = (primary_spoc.get("role") or "—").strip() or "—"
                _em = (primary_spoc.get("email") or "").strip()
                _li = (primary_spoc.get("linkedin_url") or "").strip()
                _ph = (primary_spoc.get("phone") or "").strip()
            else:
                _nm = (ctd.get("name") or "—").strip() or "—"
                _role = (ctd.get("role") or "—").strip() or "—"
                _em = (ctd.get("email") or "").strip()
                _li = (ctd.get("linkedin") or "").strip()
                # Leadership phone tends to live under extra_data
                _ph = ((ctd.get("extra_data") or {}).get("phone") or "").strip()

            # Offering filter (based on planned action descriptions)
            offering_labels = _infer_offering_labels_from_actions(planned)
            # Keep unlabeled rows visible so the dashboard doesn't look "empty" when
            # historical/planned actions don't carry an offering suffix yet.
            if want_labels and offering_labels:
                if not (offering_labels & want_labels):
                    continue

            status_norm = normalize_contact_status(ctd.get("status", "not_contacted"))
            status_label = CONTACT_STATUS_LABELS.get(status_norm, status_norm)

            # Traffic + syndication for the publisher row
            monthly_visits = cd.get("monthly_visits")
            extra = cd.get("extra_data") or {}
            platform_presence = extra.get("platform_presence") or {}
            if not platform_presence or not any(
                bool(platform_presence.get(k))
                for k in ("msn", "yahoo", "newsbreak", "smartnews")
            ):
                # Match Streamlit display logic: if platform presence isn't saved yet,
                # infer it from tracker_sources.csv.
                company_domain = (cd.get("domain") or cd.get("name") or "").strip()
                if company_domain:
                    platform_presence = get_presence_for_domain(company_domain, tracker_map)

            syndication = [
                k
                for k, v in (platform_presence or {}).items()
                if k in ("msn", "yahoo", "newsbreak", "smartnews") and bool(v)
            ]

            # Keep a small, UI-friendly slice of planned actions for the detail panel.
            planned_sorted = sorted(planned, key=lambda a: a.get("scheduled_at") or "")
            planned_preview = [
                {
                    "id": a.get("id"),
                    "sequence_step": a.get("sequence_step"),
                    "channel": a.get("channel"),
                    "status": a.get("status"),
                    "scheduled_at": a.get("scheduled_at"),
                    "description": a.get("description"),
                }
                for a in planned_sorted[:4]
            ]

            advisor_company_id = (cd.get("extra_data") or {}).get("source_advisor_company_id")
            parent_name = ""
            if advisor_company_id is not None:
                adv = get_advisor_company(advisor_company_id)
                parent_name = (adv or {}).get("parent_name") or ""

            spoc_rows = get_spoc_contacts_by_company(company_id) or []
            contacts_summary = []
            for b in get_contacts(company_id):
                contacts_summary.append(
                    {
                        "kind": "contact",
                        "name": (b.get("name") or "").strip() or "—",
                        "role": (b.get("role") or "").strip() or "—",
                        "email": (b.get("email") or "").strip(),
                        "linkedin_url": (b.get("linkedin") or "").strip(),
                    }
                )
            for srow in spoc_rows:
                contacts_summary.append(
                    {
                        "kind": "spoc",
                        "name": (srow.get("name") or "").strip() or "—",
                        "role": (srow.get("role") or "").strip() or "—",
                        "email": (srow.get("email") or "").strip(),
                        "linkedin_url": (srow.get("linkedin_url") or "").strip(),
                    }
                )

            out_rows.append(
                {
                    "row_type": "publisher",
                    "company_id": company_id,
                    "company_domain": cd.get("domain") or cd.get("name") or "—",
                    "contact_id": ctd.get("id"),
                    "advisor_company_id": advisor_company_id,
                    "parent_name": parent_name,
                    "source": src,
                    "spoc_name": _nm,
                    "spoc_role": _role,
                    "spoc_email": _em,
                    "spoc_linkedin_url": _li,
                    "spoc_phone": _ph,
                    "status": status_norm,
                    "status_label": status_label,
                    "next_step_date": next_step_date,
                    "due_today": due_today,
                    "first_overdue_date_str": first_overdue_date_str,
                    "platform_presence": platform_presence,
                    "syndication": syndication,
                    "monthly_visits": monthly_visits,
                    "planned_preview": planned_preview,
                    "contacts_summary": contacts_summary,
                    # Keep planned for UI filters/offer labels/debug (optional payload)
                    "offering_labels": sorted(offering_labels),
                }
            )

    return out_rows
