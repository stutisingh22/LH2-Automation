"""Server-side SPOC tracking row builder (same rules as Streamlit apps.py)."""

from __future__ import annotations

from datetime import datetime
import json
import re

from config import (
    CONTACT_STATUSES,
    CONTACT_STATUS_LABELS,
    normalize_contact_status,
    normalize_domain,
)
from db_ops import (
    bulk_ensure_shadow_companies_for_advisor_ids,
    bulk_get_advisor_companies_by_ids,
    bulk_get_companies_by_ids,
    bulk_get_contacts_by_ids,
    bulk_get_monthly_visits_by_domains,
    get_advisor_parents_with_children,
    get_all_companies,
    get_contacts,
    get_contacts_for_company_ids,
    get_or_create_shadow_company_for_advisor,
    get_session_actions,
    get_session_actions_for_sessions,
    get_sessions_for_companies,
    get_sessions_for_company,
    get_spoc_contacts_by_spoc,
    get_spoc_contacts_by_company_and_leader,
    get_spoc_contacts_for_company_ids,
    lookup_existing_shadow_pairs_for_advisors,
)

from integrations.analytics.tracker_sources import load_domain_to_platforms, get_presence_for_domain


def _extra_has_research(extra) -> bool:
    """True if saved deep research exists (matches /api/intelligence/has-research)."""
    ed = extra
    if isinstance(ed, str) and ed.strip():
        try:
            ed = json.loads(ed)
        except Exception:
            ed = {}
    if not isinstance(ed, dict) or not ed:
        return False
    rf = (ed.get("research_full") or "").strip() if isinstance(ed.get("research_full"), str) else ""
    rdh = (ed.get("research_display_html") or "").strip() if isinstance(ed.get("research_display_html"), str) else ""
    rs = ed.get("research_sections") if isinstance(ed.get("research_sections"), list) else []
    return bool(rf or rdh or rs)


def _normalize_extra_dict(extra) -> dict:
    ed = extra
    if isinstance(ed, str) and ed.strip():
        try:
            ed = json.loads(ed)
        except Exception:
            ed = {}
    return ed if isinstance(ed, dict) else {}


def _platform_links_from_extra(extra: dict) -> dict[str, str]:
    details = _normalize_extra_dict(extra).get("platform_presence_details") or {}
    if not isinstance(details, dict):
        return {}
    out: dict[str, str] = {}
    for k in ("msn", "yahoo", "newsbreak", "smartnews"):
        d = details.get(k) or {}
        if not isinstance(d, dict):
            continue
        url = str(d.get("url") or "").strip()
        if url:
            out[k] = url
    return out


def _parse_md_row(line: str) -> list[str]:
    s = (line or "").strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_md_separator_row(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells = [c.strip() for c in s.split("|")]
    if not cells:
        return False
    for c in cells:
        if not re.match(r"^:?-{3,}:?$", c):
            return False
    return True


def _extract_first_markdown_table(text: str) -> tuple[list[str], list[list[str]]] | tuple[None, None]:
    lines = (text or "").splitlines()
    n = len(lines)
    for i in range(n - 1):
        if "|" not in lines[i]:
            continue
        if not _is_md_separator_row(lines[i + 1]):
            continue
        header = _parse_md_row(lines[i])
        rows: list[list[str]] = []
        j = i + 2
        while j < n:
            ln = lines[j]
            if not ln.strip() or "|" not in ln:
                break
            rows.append(_parse_md_row(ln))
            j += 1
        if header and rows:
            return header, rows
    return None, None


def _relevance_reasons_from_first_table(text: str) -> tuple[list[str], list[str]]:
    header, rows = _extract_first_markdown_table(text)
    if not header or not rows:
        return [], []

    h = [str(x or "").strip().lower() for x in header]

    def _find_idx(candidates: tuple[str, ...]) -> int:
        for idx, col in enumerate(h):
            if any(c in col for c in candidates):
                return idx
        return -1

    status_idx = _find_idx(("status", "result", "verdict", "outcome", "pass/fail", "pass fail"))
    reason_idx = _find_idx(("reason", "rationale", "why", "evidence", "note", "explanation"))
    label_idx = _find_idx(("criterion", "criteria", "check", "factor", "signal", "dimension"))
    if label_idx < 0:
        label_idx = 0

    pass_reasons: list[str] = []
    fail_reasons: list[str] = []

    for row in rows:
        if not row:
            continue
        padded = row + [""] * (len(h) - len(row))
        status_text = ""
        if status_idx >= 0 and status_idx < len(padded):
            status_text = str(padded[status_idx] or "")
        if not status_text:
            status_text = " ".join(str(c or "") for c in padded)
        st = status_text.strip().lower()

        is_pass = bool(re.search(r"\bpass(ed)?\b", st))
        is_fail = bool(re.search(r"\bfail(ed)?\b", st))
        if not is_pass and not is_fail:
            continue

        label = str(padded[label_idx] or "").strip() if 0 <= label_idx < len(padded) else ""
        reason = str(padded[reason_idx] or "").strip() if 0 <= reason_idx < len(padded) else ""

        if not reason:
            for c in padded:
                cst = str(c or "").strip()
                if cst and cst != label and not re.search(r"\bpass(ed)?\b|\bfail(ed)?\b", cst, re.I):
                    reason = cst
                    break
        if not reason:
            reason = label

        # Prefer the explanation text itself for human-readable tooltips.
        # Fall back to the criterion label only when no explicit reason exists.
        line = reason or label
        line = re.sub(r"\s+", " ", line).strip(" -:;")
        if not line:
            continue
        if is_pass:
            pass_reasons.append(line)
        if is_fail:
            fail_reasons.append(line)

    return pass_reasons[:3], fail_reasons[:3]


def _derive_relevance_from_extra(extra) -> str:
    """
    Relevance rules:
    - Manual override wins: relevance_override in {relevant, irrelevant}
    - No saved research => "-"
    - Saved research + verdict-like "proceed" => relevant
    - Saved research + verdict-like "stop/no/do not proceed/not fit" => irrelevant
    - Else => "-"
    """
    ed = _normalize_extra_dict(extra)
    override = str(ed.get("relevance_override") or ed.get("relevance") or "").strip().lower()
    if override in ("relevant", "irrelevant"):
        return override
    auto = str(ed.get("relevance_auto") or "").strip().lower()
    # Backward compatibility: older saved rows may have "-" even when research text
    # now clearly implies pass/fail. Only treat explicit relevant/irrelevant as final.
    if auto in ("relevant", "irrelevant"):
        return auto
    if not _extra_has_research(ed):
        return "-"
    rf = (ed.get("research_full") or "") if isinstance(ed.get("research_full"), str) else ""
    rdh = (ed.get("research_display_html") or "") if isinstance(ed.get("research_display_html"), str) else ""
    combined = f"{rf}\n{rdh}".strip().lower()
    if re.search(r"\b(do not|don't|not)\s+proceed\b", combined):
        return "irrelevant"
    if re.search(r"\b(stop|no-go|not a fit|do not pursue|don't pursue)\b", combined):
        return "irrelevant"
    if re.search(r"\b(proceed|go ahead|good fit|recommended to proceed)\b", combined):
        return "relevant"
    # If research exists but no explicit keyword/table verdict is detected,
    # default to relevant so the CRM always shows a visible state.
    return "relevant"


def _derive_relevance_reason_from_extra(extra) -> str:
    ed = _normalize_extra_dict(extra)
    saved = str(ed.get("relevance_reason_auto") or "").strip()
    saved_l = saved.lower()
    _generic_saved = {
        "research verdict indicates do not proceed.",
        "research indicates outreach should stop.",
        "research indicates weak strategic fit.",
        "marked irrelevant from research verdict.",
        "do not proceed.",
        "outreach should stop.",
        "weak strategic fit.",
        "strong strategic fit.",
    }
    if saved and saved_l not in _generic_saved:
        return saved
    if not _extra_has_research(ed):
        return ""
    rf = (ed.get("research_full") or "") if isinstance(ed.get("research_full"), str) else ""
    rdh = (ed.get("research_display_html") or "") if isinstance(ed.get("research_display_html"), str) else ""
    combined = f"{rf}\n{rdh}".strip().lower()
    p, f = _relevance_reasons_from_first_table(f"{rf}\n{rdh}".strip())
    if f:
        return "; ".join(f[:3])
    if p:
        return "; ".join(p[:3])
    if re.search(r"\b(do not|don't|not)\s+proceed\b", combined):
        return "Do not proceed."
    if re.search(r"\b(stop|no-go|do not pursue|don't pursue)\b", combined):
        return "Outreach should stop."
    if re.search(r"\b(not a fit|poor fit|bad fit)\b", combined):
        return "Weak strategic fit."
    if re.search(r"\b(proceed|go ahead|good fit|recommended to proceed)\b", combined):
        return "Strong strategic fit."
    return "Research completed; no explicit pass/fail marker found."


def _merge_relevance(*values: str) -> str:
    vals = [str(v or "").strip().lower() for v in values if str(v or "").strip()]
    if "relevant" in vals:
        return "relevant"
    if "irrelevant" in vals:
        return "irrelevant"
    return "-"


def _merge_relevance_reason(relevance: str, *values: str) -> str:
    rel = str(relevance or "").strip().lower()
    if rel not in ("relevant", "irrelevant"):
        return ""
    for v in values:
        txt = str(v or "").strip()
        if txt:
            return txt
    return "Strong strategic fit." if rel == "relevant" else "Weak strategic fit."


def _is_meaningful_person_name(name: str, company_domain: str = "") -> bool:
    n = str(name or "").strip()
    if not n or n == "—":
        return False
    d = str(company_domain or "").strip().lower().replace("www.", "")
    n_norm = n.lower().replace("www.", "")
    if d and (n_norm == d or n_norm == d.split(".")[0]):
        return False
    if "@" in n_norm:
        return False
    return True


def _has_outreach_activity(actions: list[dict] | None) -> bool:
    """
    Treat active outreach work as an explicit "relevant" signal.
    This covers drafted/planned/sent workflows regardless of initial research verdict.
    """
    if not actions:
        return False
    for a in actions:
        if not isinstance(a, dict):
            continue
        status = str(a.get("status") or "").strip().lower()
        channel = str(a.get("channel") or "").strip().lower()
        action_type = str(a.get("action_type") or "").strip().lower()
        has_draft_content = bool(
            str(a.get("description") or "").strip()
            or str(a.get("content_sent") or "").strip()
            or str(a.get("draft_original") or "").strip()
            or (a.get("draft_revisions") if isinstance(a.get("draft_revisions"), list) else [])
        )
        if channel in ("email", "linkedin", "phone", "in-person"):
            return True
        if action_type in ("outreach", "follow-up", "meeting"):
            return True
        if status in (
            "planned",
            "sent",
            "completed",
            "no_response",
            "replied_positive",
            "replied_negative",
            "meeting_booked",
            "deal_closed",
            "deal_lost",
        ):
            return True
        if has_draft_content:
            return True
    return False


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


def build_publisher_crm_rows(
    source: str,
    offer_tab: str | None = None,
    *,
    lite: bool = False,
    filter_company_id: int | None = None,
    filter_advisor_company_id: int | None = None,
    media_parent_key: str | None = None,
) -> list[dict]:
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
        # Flatten (parent_name, child) in tree order, then one DB batch for all shadows/sessions/actions.
        worklist: list[tuple[str, dict]] = []
        for g in parents:
            parent_name = (g.get("parent_name") or "").strip() or "(No parent)"
            parent_key = f"parent:{parent_name}"
            if media_parent_key is not None and parent_key != media_parent_key:
                continue

            for ch in g.get("children") or []:
                aid = ch.get("id")
                if not aid:
                    continue
                if filter_advisor_company_id is not None and aid != filter_advisor_company_id:
                    continue
                worklist.append((parent_name, ch))

        if not worklist:
            return out_rows

        advisor_ids = [int(ch["id"]) for _, ch in worklist]
        # Keep list reads side-effect free: do not create missing shadow rows on lite list fetch.
        if lite and filter_advisor_company_id is None and media_parent_key is None:
            shadow_pair_by_aid = lookup_existing_shadow_pairs_for_advisors(advisor_ids)
        else:
            shadow_pair_by_aid = bulk_ensure_shadow_companies_for_advisor_ids(advisor_ids)

        shadow_company_ids = [sc for sc, ct in shadow_pair_by_aid.values() if sc]
        shadow_contact_ids = [ct for sc, ct in shadow_pair_by_aid.values() if ct]
        companies_map = bulk_get_companies_by_ids(shadow_company_ids)
        contacts_map = bulk_get_contacts_by_ids(shadow_contact_ids)
        sessions_by_company = get_sessions_for_companies(shadow_company_ids)

        session_id_by_aid: dict[int, int | None] = {}
        picked_session_ids: list[int] = []
        for aid in advisor_ids:
            scid, ctid = shadow_pair_by_aid.get(aid, (None, None))
            if not scid or not ctid:
                session_id_by_aid[aid] = None
                continue
            sessions = sessions_by_company.get(scid, [])
            session_by_contact_id: dict = {}
            for sess in sessions:
                cc = sess.get("contact_id")
                if cc is not None and cc not in session_by_contact_id:
                    session_by_contact_id[cc] = sess
            sess = session_by_contact_id.get(ctid)
            if sess and sess.get("id"):
                sid = int(sess["id"])
                session_id_by_aid[aid] = sid
                picked_session_ids.append(sid)
            else:
                session_id_by_aid[aid] = None

        actions_by_session = get_session_actions_for_sessions(picked_session_ids)
        spoc_by_company = get_spoc_contacts_for_company_ids(shadow_company_ids)
        all_contacts_by_shadow = get_contacts_for_company_ids(shadow_company_ids)

        domains_for_monthly: set[str] = set()
        for _, ch in worklist:
            aid = int(ch["id"])
            scid, _ = shadow_pair_by_aid.get(aid, (None, None))
            shadow_company = companies_map.get(scid) if scid else None
            if (shadow_company or {}).get("monthly_visits") is not None:
                continue
            cd = (ch.get("domain") or "").strip()
            if cd:
                nd = normalize_domain(cd)
                if nd:
                    domains_for_monthly.add(nd)
        visits_by_norm = bulk_get_monthly_visits_by_domains(domains_for_monthly)

        for parent_name, ch in worklist:
            advisor_company_id = ch.get("id")
            aid = int(advisor_company_id)
            shadow_company_id, shadow_contact_id = shadow_pair_by_aid.get(aid, (None, None))
            shadow_company = companies_map.get(shadow_company_id) if shadow_company_id else None
            ctd = contacts_map.get(shadow_contact_id) if shadow_contact_id else None
            sid = session_id_by_aid.get(aid)
            actions = actions_by_session.get(sid, []) if sid else []

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
            due_today = bool(min_next_dt and min_next_dt.date() == _today)

            offering_labels = _infer_offering_labels_from_actions(planned)
            if want_labels and offering_labels and not (offering_labels & want_labels):
                continue

            status_norm = normalize_contact_status((ch.get("extra_data") or {}).get("pipeline_status", "not_contacted"))
            status_label = CONTACT_STATUS_LABELS.get(status_norm, status_norm)

            extra = _normalize_extra_dict((shadow_company or {}).get("extra_data"))
            # Prefer shadow company analytics when present, but honor advisor-child extra_data
            # so newly bulk-imported media brands can surface syndication immediately.
            extra = {**_normalize_extra_dict(ch.get("extra_data")), **extra}
            platform_presence = extra.get("platform_presence") or {}
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
            syndication_links = _platform_links_from_extra(extra)
            child_domain = (ch.get("domain") or "").strip()
            monthly_visits = (shadow_company or {}).get("monthly_visits")
            if monthly_visits is None and child_domain:
                monthly_visits = visits_by_norm.get(normalize_domain(child_domain))

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

            spoc_rows_for_child = spoc_by_company.get(shadow_company_id, []) if shadow_company_id else []

            if lite:
                brand_spoc_names: list[str] = []
                if ctd:
                    nm = (ctd.get("name") or "").strip()
                    if nm and nm not in ("—",):
                        brand_spoc_names.append(nm)
                for srow in spoc_rows_for_child:
                    n = (srow.get("name") or "").strip()
                    if n and n != "—":
                        brand_spoc_names.append(n)
                # Extra rows on shadow company (e.g. Add Publisher SPOCs) are plain contacts, not spoc_contacts.
                child_domain = (ch.get("domain") or "").strip()
                for bc in all_contacts_by_shadow.get(shadow_company_id, []) or []:
                    n = (bc.get("name") or "").strip()
                    if not n or n == "—":
                        continue
                    has_email = bool((bc.get("email") or "").strip())
                    if n not in brand_spoc_names and (
                        _is_meaningful_person_name(n, child_domain) or has_email
                    ):
                        brand_spoc_names.append(n)
                contacts_for_row = []
                if ctd:
                    contacts_for_row = [
                        {
                            "kind": "contact",
                            "name": (ctd.get("name") or "").strip() or "Leadership",
                            "role": (ctd.get("role") or "").strip() or "Leadership",
                            "email": (ctd.get("email") or "").strip(),
                            "linkedin_url": (ctd.get("linkedin") or "").strip(),
                        }
                    ]
                planned_use: list[dict] = []
                contacts_use = contacts_for_row
                extra_media = {"brand_spoc_names": brand_spoc_names}
            else:
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
                for srow in spoc_rows_for_child:
                    contacts_summary.append(
                        {
                            "kind": "spoc",
                            "name": (srow.get("name") or "").strip() or "—",
                            "role": (srow.get("role") or "").strip() or "—",
                            "email": (srow.get("email") or "").strip(),
                            "linkedin_url": (srow.get("linkedin_url") or "").strip(),
                        }
                    )
                _seen_ct = {
                    ((c.get("name") or "").strip().lower(), (c.get("email") or "").strip().lower())
                    for c in contacts_summary
                }
                child_domain_full = (ch.get("domain") or "").strip()
                for bc in all_contacts_by_shadow.get(shadow_company_id, []) or []:
                    bid = bc.get("id")
                    if ctd and bid == ctd.get("id"):
                        continue
                    nm = (bc.get("name") or "").strip() or "—"
                    em = ((bc.get("email") or "").strip()).lower()
                    key = (nm.lower(), em)
                    if key in _seen_ct:
                        continue
                    contacts_summary.append(
                        {
                            "kind": "contact",
                            "name": nm,
                            "role": (bc.get("role") or "").strip() or "—",
                            "email": (bc.get("email") or "").strip(),
                            "linkedin_url": (bc.get("linkedin") or "").strip(),
                        }
                    )
                    _seen_ct.add(key)
                planned_use = planned_preview
                contacts_use = contacts_summary
                extra_media = {"platform_presence": platform_presence}

            ch_extra = _normalize_extra_dict(ch.get("extra_data"))
            shadow_extra = _normalize_extra_dict((shadow_company or {}).get("extra_data"))
            has_research_media = _extra_has_research(ch_extra) or _extra_has_research(shadow_extra)
            relevance_media = _merge_relevance(
                _derive_relevance_from_extra(ch_extra),
                _derive_relevance_from_extra(shadow_extra),
            )
            relevance_reason_media = _merge_relevance_reason(
                relevance_media,
                _derive_relevance_reason_from_extra(ch_extra),
                _derive_relevance_reason_from_extra(shadow_extra),
            )
            if _has_outreach_activity(actions):
                relevance_media = "relevant"
                relevance_reason_media = ""
            media_row = {
                "row_type": "publisher",
                "company_id": shadow_company_id or 0,
                "company_domain": (ch.get("domain") or ch.get("parent_name") or "—"),
                "contact_id": (ctd or {}).get("id") or 0,
                "advisor_company_id": advisor_company_id,
                "parent_name": parent_name,
                "source": src,
                "has_research": has_research_media,
                "relevance": relevance_media,
                "relevance_reason": relevance_reason_media,
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
                "syndication": syndication,
                "syndication_links": syndication_links,
                "monthly_visits": monthly_visits,
                "planned_preview": planned_use,
                "contacts_summary": contacts_use,
                "offering_labels": sorted(offering_labels),
            }
            media_row.update(extra_media)
            out_rows.append(media_row)
        return out_rows

    work_cd: list[dict] = []
    for cd in all_comp:
        company_id = cd.get("id")
        if not company_id:
            continue
        if filter_company_id is not None and company_id != filter_company_id:
            continue
        work_cd.append(cd)

    if not work_cd:
        return out_rows

    company_ids = [int(cd["id"]) for cd in work_cd]
    contacts_by_company = get_contacts_for_company_ids(company_ids)
    spoc_all_by_company = get_spoc_contacts_for_company_ids(company_ids)
    sub_nested_by_company = get_spoc_contacts_by_company_and_leader(company_ids)
    sessions_by_company = get_sessions_for_companies(company_ids)

    picked_session_ids: list[int] = []
    for cid in company_ids:
        sessions = sessions_by_company.get(cid, [])
        session_by_c: dict = {}
        for s in sessions:
            cc = s.get("contact_id")
            if cc is not None and cc not in session_by_c:
                session_by_c[cc] = s
        for s in session_by_c.values():
            if s.get("id"):
                picked_session_ids.append(int(s["id"]))
    actions_by_session = get_session_actions_for_sessions(picked_session_ids)

    advisor_ids_unique: list[int] = sorted(
        {
            int((cd.get("extra_data") or {}).get("source_advisor_company_id"))
            for cd in work_cd
            if (cd.get("extra_data") or {}).get("source_advisor_company_id") is not None
        }
    )
    advisor_parent_by_id = bulk_get_advisor_companies_by_ids(advisor_ids_unique)

    for cd in work_cd:
        company_id = cd.get("id")
        raw_contacts = contacts_by_company.get(company_id, []) or []
        company_contacts = raw_contacts
        if not company_contacts:
            company_contacts = [{"id": 0, "name": "—", "role": "—", "email": "", "linkedin": "", "status": "not_contacted", "extra_data": {}}]

        spoc_list_all = spoc_all_by_company.get(company_id, []) or []

        company_spoc_names: list[str] = []
        if lite:
            for srow in spoc_list_all:
                n = (srow.get("name") or "").strip()
                if n and n != "—":
                    company_spoc_names.append(n)
            # Always merge contacts table names (Add Publisher, etc.). SpocContact-only rows
            # previously blocked this fallback and hid real contact names in compact CRM.
            company_domain = (cd.get("domain") or cd.get("name") or "").strip()
            for b in raw_contacts:
                n = (b.get("name") or "").strip()
                if not n or n == "—":
                    continue
                has_email = bool((b.get("email") or "").strip())
                if n not in company_spoc_names and (
                    _is_meaningful_person_name(n, company_domain) or has_email
                ):
                    company_spoc_names.append(n)

        contacts_summary_full: list[dict] | None = None
        if not lite:
            contacts_summary_full = []
            for b in raw_contacts:
                contacts_summary_full.append(
                    {
                        "kind": "contact",
                        "name": (b.get("name") or "").strip() or "—",
                        "role": (b.get("role") or "").strip() or "—",
                        "email": (b.get("email") or "").strip(),
                        "linkedin_url": (b.get("linkedin") or "").strip(),
                    }
                )
            for srow in spoc_list_all:
                contacts_summary_full.append(
                    {
                        "kind": "spoc",
                        "name": (srow.get("name") or "").strip() or "—",
                        "role": (srow.get("role") or "").strip() or "—",
                        "email": (srow.get("email") or "").strip(),
                        "linkedin_url": (srow.get("linkedin_url") or "").strip(),
                    }
                )

        sessions = sessions_by_company.get(company_id, [])
        session_by_contact_id: dict = {}
        for s in sessions:
            scid = s.get("contact_id")
            if scid is None or scid in session_by_contact_id:
                continue
            session_by_contact_id[scid] = s

        leader_nested = sub_nested_by_company.get(company_id, {})
        advisor_company_id_company = (cd.get("extra_data") or {}).get("source_advisor_company_id")
        parent_name_company = ""
        if advisor_company_id_company is not None:
            adv_co = advisor_parent_by_id.get(int(advisor_company_id_company))
            parent_name_company = (adv_co or {}).get("parent_name") or ""

        for ctd in company_contacts:
            cid = ctd.get("id")
            sub = leader_nested.get(int(cid), []) if cid is not None and int(cid) != 0 else []
            sess = session_by_contact_id.get(cid) if cid is not None else None
            actions = actions_by_session.get(int(sess["id"]), []) if sess and sess.get("id") else []

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
            extra = _normalize_extra_dict(cd.get("extra_data"))
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
            syndication_links = _platform_links_from_extra(extra)

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

            advisor_company_id = advisor_company_id_company
            parent_name = parent_name_company

            if lite:
                planned_use = []
                contacts_use: list[dict] = []
                extra_ind: dict = {"company_spoc_names": company_spoc_names}
            else:
                planned_use = planned_preview
                contacts_use = contacts_summary_full or []
                extra_ind = {"platform_presence": platform_presence}

            relevance_ind = _derive_relevance_from_extra(extra)
            relevance_reason_ind = _merge_relevance_reason(
                relevance_ind,
                _derive_relevance_reason_from_extra(extra),
            )
            if _has_outreach_activity(actions):
                relevance_ind = "relevant"
                relevance_reason_ind = ""
            ind_row = {
                "row_type": "publisher",
                "company_id": company_id,
                "company_domain": cd.get("domain") or cd.get("name") or "—",
                "contact_id": ctd.get("id"),
                "advisor_company_id": advisor_company_id,
                "parent_name": parent_name,
                "source": src,
                "has_research": _extra_has_research(cd.get("extra_data")),
                "relevance": relevance_ind,
                "relevance_reason": relevance_reason_ind,
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
                "syndication": syndication,
                "syndication_links": syndication_links,
                "monthly_visits": monthly_visits,
                "planned_preview": planned_use,
                "contacts_summary": contacts_use,
                "offering_labels": sorted(offering_labels),
            }
            ind_row.update(extra_ind)
            out_rows.append(ind_row)

    return out_rows


def _utc_today_display() -> str:
    return datetime.utcnow().date().strftime("%b %d, %Y")


def _unique_by_key(items: list, key_fn) -> list:
    seen: set[str] = set()
    out: list = []
    for it in items:
        k = key_fn(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _parse_display_date_py(s: str | None) -> datetime | None:
    if not s:
        return None
    v = str(s).strip()
    if not v or v == "—":
        return None
    try:
        return datetime.strptime(v, "%b %d, %Y")
    except Exception:
        return None


def _crm_kpi_buckets() -> dict:
    opts = CONTACT_STATUSES
    pick = lambda *cands: next((c for c in cands if c in opts), cands[0])
    return {
        "not_contacted": pick("not_contacted", opts[0] if opts else "not_contacted"),
        "in_campaign": pick("outreach_sent", opts[1] if len(opts) > 1 else "outreach_sent"),
        "responded": [pick("replied_positive", "replied_positive"), pick("meeting_booked", "meeting_booked")],
        "won": pick("partnership_active", "partnership_active"),
    }


def rows_to_company_groups(rows: list[dict], crm_source: str) -> list[dict]:
    """Mirror React Dashboard grouping (one row per company or per media parent)."""

    def status_rank(st: str) -> int:
        try:
            return CONTACT_STATUSES.index(st)
        except ValueError:
            return -1

    group_map: dict[str, list[dict]] = {}
    for r in rows:
        if crm_source == "media":
            k = f"parent:{r.get('parent_name') or '(No parent)'}"
        else:
            k = f"company:{r.get('company_id')}"
        group_map.setdefault(k, []).append(r)

    groups: list[dict] = []
    for key, arr in group_map.items():
        display_title = (
            (arr[0].get("parent_name") or "(No parent)") if crm_source == "media" else (arr[0].get("company_domain") or "—")
        )

        flat_cs: list[dict] = []
        for r in arr:
            for x in r.get("contacts_summary") or []:
                flat_cs.append(x)
        deduped_contacts = _unique_by_key(
            flat_cs,
            lambda c: f"{c.get('kind')}|{c.get('email') or ''}|{c.get('linkedin_url') or ''}|{c.get('name') or ''}",
        )

        status_best = max(arr, key=lambda x: status_rank(str(x.get("status") or "")))
        due_today = any(bool(x.get("due_today")) for x in arr)

        dated: list[tuple[dict, datetime]] = []
        for x in arr:
            dt = _parse_display_date_py(x.get("next_step_date"))
            if dt:
                dated.append((x, dt))
        dated.sort(key=lambda t: t[1])
        next_step_date = (
            dated[0][0].get("next_step_date")
            if dated
            else (next((x.get("next_step_date") for x in arr if x.get("due_today")), None) or "—")
        )

        syndication = _unique_by_key([s for r in arr for s in (r.get("syndication") or [])], lambda s: str(s))
        syndication_links: dict[str, str] = {}
        for r in arr:
            for k, v in (r.get("syndication_links") or {}).items():
                ks = str(k or "").strip().lower()
                vs = str(v or "").strip()
                if ks in ("msn", "yahoo", "newsbreak", "smartnews") and vs and ks not in syndication_links:
                    syndication_links[ks] = vs

        contact_ids: list[int] = []
        _seen_c = set()
        for r in arr:
            cid = r.get("contact_id")
            if cid and int(cid) > 0 and cid not in _seen_c:
                _seen_c.add(cid)
                contact_ids.append(int(cid))

        advisor_company_ids: list[int] = []
        _seen_a = set()
        for r in arr:
            aid = r.get("advisor_company_id")
            if aid is not None and aid not in _seen_a:
                _seen_a.add(aid)
                advisor_company_ids.append(int(aid))

        planned_preview = _unique_by_key(
            [p for r in arr for p in (r.get("planned_preview") or [])],
            lambda p: str(p.get("id")),
        )

        if crm_source == "media":
            s_visits = sum(
                int(r["monthly_visits"])
                for r in arr
                if isinstance(r.get("monthly_visits"), (int, float)) and int(r["monthly_visits"] or 0) > 0
            )
            monthly_visits: int | None = s_visits if s_visits > 0 else None
        else:
            monthly_visits = arr[0].get("monthly_visits")

        primary_contact = next((c for c in deduped_contacts if c.get("kind") == "spoc"), None)
        if primary_contact is None and deduped_contacts:
            primary_contact = deduped_contacts[0]

        spoc_names: list[str] = []
        if crm_source != "media":
            for r in arr:
                for nm in r.get("company_spoc_names") or []:
                    if nm and nm not in spoc_names:
                        spoc_names.append(nm)
            if not spoc_names:
                spocs = [c for c in deduped_contacts if c.get("kind") == "spoc"]
                for s in spocs:
                    n = (s.get("name") or "").strip()
                    if n and n != "—" and n not in spoc_names:
                        spoc_names.append(n)

        brands = None
        if crm_source == "media":
            brand_map: dict[str, list[dict]] = {}
            for r in arr:
                bk = str(r.get("company_domain") or "")
                brand_map.setdefault(bk, []).append(r)
            brand_list: list[dict] = []
            for bk, br_arr in brand_map.items():
                best = max(br_arr, key=lambda x: status_rank(str(x.get("status") or "")))
                bd_flat: list[dict] = []
                for r in br_arr:
                    for x in r.get("contacts_summary") or []:
                        bd_flat.append(x)
                bd_contacts = _unique_by_key(
                    bd_flat,
                    lambda c: f"{c.get('kind')}|{c.get('email') or ''}|{c.get('linkedin_url') or ''}|{c.get('name') or ''}",
                )
                spoc_names_brand: list[str] = []
                for r in br_arr:
                    for nm in r.get("brand_spoc_names") or []:
                        if nm and nm not in spoc_names_brand:
                            spoc_names_brand.append(nm)
                if not spoc_names_brand:
                    for c in bd_contacts:
                        if c.get("kind") == "spoc":
                            n = (c.get("name") or "").strip()
                            if n and n != "—" and n not in spoc_names_brand:
                                spoc_names_brand.append(n)
                brand_rel = _merge_relevance(*[str(x.get("relevance") or "") for x in br_arr])
                brand_reason = _merge_relevance_reason(
                    brand_rel,
                    *[str(x.get("relevance_reason") or "") for x in br_arr],
                )
                brand_list.append(
                    {
                        "company_domain": bk,
                        "status": best.get("status"),
                        "status_label": best.get("status_label"),
                        "next_step_date": best.get("next_step_date"),
                        "due_today": any(bool(x.get("due_today")) for x in br_arr),
                        "monthly_visits": best.get("monthly_visits"),
                        "syndication": _unique_by_key(
                            [s for r in br_arr for s in (r.get("syndication") or [])],
                            lambda x: str(x),
                        ),
                        "syndication_links": {
                            ks: vs
                            for br in br_arr
                            for ks, vs in (br.get("syndication_links") or {}).items()
                            if str(ks).strip().lower() in ("msn", "yahoo", "newsbreak", "smartnews")
                            and str(vs or "").strip()
                        },
                        "spoc_name": best.get("spoc_name"),
                        "contacts_summary": bd_contacts,
                        "advisor_company_id": best.get("advisor_company_id"),
                        "spoc_names": spoc_names_brand,
                        "has_research": any(bool(x.get("has_research")) for x in br_arr),
                        "relevance": brand_rel,
                        "relevance_reason": brand_reason,
                    }
                )
            brand_list.sort(key=lambda b: (not bool(b.get("due_today")), str(b.get("company_domain") or "")))
            brands = brand_list

        spoc_name = status_best.get("spoc_name") or (primary_contact or {}).get("name") or display_title
        ceo_contact = next((c for c in deduped_contacts if c.get("kind") == "contact"), None)

        group_rel = _merge_relevance(*[str(x.get("relevance") or "") for x in arr])
        group_reason = _merge_relevance_reason(
            group_rel,
            *[str(x.get("relevance_reason") or "") for x in arr],
        )
        groups.append(
            {
                "key": key,
                "source": crm_source,
                "display_title": display_title,
                "parent_name": (arr[0].get("parent_name") or "") if crm_source == "media" else None,
                "company_id": int(arr[0]["company_id"]) if crm_source != "media" and arr[0].get("company_id") else None,
                "has_research": any(bool(x.get("has_research")) for x in arr),
                "relevance": group_rel,
                "relevance_reason": group_reason,
                "status": status_best.get("status"),
                "status_label": status_best.get("status_label"),
                "due_today": due_today,
                "next_step_date": next_step_date or "—",
                "monthly_visits": monthly_visits,
                "syndication": syndication,
                "syndication_links": syndication_links,
                "spoc_name": spoc_name,
                "spoc_names": spoc_names,
                "ceo_display_name": (ceo_contact or {}).get("name") if ceo_contact else None,
                "contacts_summary": deduped_contacts,
                "brands": brands,
                "contact_ids": contact_ids,
                "advisor_company_ids": advisor_company_ids,
                "planned_preview": planned_preview,
            }
        )

    far_future = datetime(9999, 12, 31, 23, 59, 59)
    groups.sort(
        key=lambda g: (
            not bool(g.get("due_today")),
            _parse_display_date_py(g.get("next_step_date")) or far_future,
            str(g.get("display_title") or ""),
        )
    )
    return groups


def _strip_media_brands_for_list_response(groups: list[dict]) -> list[dict]:
    """Omit heavy per-brand payloads from media list API; keep counts for parent rows."""
    nc = _crm_kpi_buckets()["not_contacted"]
    out: list[dict] = []
    for g in groups:
        brands = g.get("brands") or []
        contacted = sum(1 for b in brands if b.get("status") != nc)
        out.append({**g, "brand_count": len(brands), "contacted_brand_count": contacted, "brands": []})
    return out


def _slim_media_list_group(g: dict) -> dict:
    """Fields needed for Dashboard media-house parent rows only (counts, traffic, CEO label)."""
    return {
        "key": g.get("key"),
        "source": g.get("source"),
        "display_title": g.get("display_title"),
        "parent_name": g.get("parent_name"),
        "status": g.get("status"),
        "status_label": g.get("status_label"),
        "due_today": g.get("due_today"),
        "next_step_date": g.get("next_step_date"),
        "monthly_visits": g.get("monthly_visits"),
        "syndication": list(g.get("syndication") or []),
        "syndication_links": dict(g.get("syndication_links") or {}),
        "spoc_name": g.get("spoc_name"),
        "ceo_display_name": g.get("ceo_display_name"),
        "relevance": g.get("relevance") or "-",
        "relevance_reason": g.get("relevance_reason") or "",
        "brands": [],
        "brand_count": g.get("brand_count"),
        "contacted_brand_count": g.get("contacted_brand_count"),
    }


def _slim_independent_list_group(g: dict) -> dict:
    """Fields needed for Dashboard independent company rows; SPOC column uses spoc_names."""
    return {
        "key": g.get("key"),
        "source": g.get("source"),
        "display_title": g.get("display_title"),
        "parent_name": g.get("parent_name"),
        "company_id": g.get("company_id"),
        "has_research": bool(g.get("has_research")),
        "relevance": g.get("relevance") or "-",
        "relevance_reason": g.get("relevance_reason") or "",
        "status": g.get("status"),
        "status_label": g.get("status_label"),
        "due_today": g.get("due_today"),
        "next_step_date": g.get("next_step_date"),
        "monthly_visits": g.get("monthly_visits"),
        "syndication": list(g.get("syndication") or []),
        "syndication_links": dict(g.get("syndication_links") or {}),
        "spoc_name": g.get("spoc_name"),
        "ceo_display_name": g.get("ceo_display_name"),
        "spoc_names": list(g.get("spoc_names") or []),
        "contacts_summary": [],
        "brands": None,
    }


def compute_publisher_crm_kpis(groups: list[dict], due_dates: list[str] | None) -> dict:
    b = _crm_kpi_buckets()
    total = len(groups)
    in_campaign = sum(1 for g in groups if g.get("status") == b["in_campaign"])
    responded = sum(1 for g in groups if g.get("status") in b["responded"])
    no_response = sum(1 for g in groups if g.get("status") == b["not_contacted"])
    won = sum(1 for g in groups if g.get("status") == b["won"])
    due_filters = [x.strip() for x in (due_dates or []) if x.strip()] or [_utc_today_display()]
    due_n = sum(1 for g in groups if (g.get("next_step_date") or "") in due_filters)
    return {
        "total": total,
        "in_campaign": in_campaign,
        "responded": responded,
        "no_response": no_response,
        "won": won,
        "due_today": due_n,
    }


def filter_publisher_crm_groups_by_search(groups: list[dict], q: str, crm_source: str) -> list[dict]:
    q = (q or "").strip().lower()
    if not q:
        return groups
    out: list[dict] = []
    for g in groups:
        if crm_source != "media":
            if q in str(g.get("display_title") or "").lower():
                out.append(g)
                continue
            if any(q in str(s or "").lower() for s in (g.get("spoc_names") or [])):
                out.append(g)
                continue
            hit = False
            for c in g.get("contacts_summary") or []:
                if c.get("kind") != "spoc":
                    continue
                if q in str(c.get("name") or "").lower() or q in str(c.get("email") or "").lower():
                    hit = True
                    break
            if hit:
                out.append(g)
            continue

        # media houses
        if q in str(g.get("display_title") or "").lower():
            out.append(g)
            continue
        hit = False
        for b in g.get("brands") or []:
            if q in str(b.get("company_domain") or "").lower():
                hit = True
                break
            for nm in b.get("spoc_names") or []:
                if q in str(nm or "").lower():
                    hit = True
                    break
            if hit:
                break
            for c in b.get("contacts_summary") or []:
                if c.get("kind") != "spoc":
                    continue
                if q in str(c.get("name") or "").lower() or q in str(c.get("email") or "").lower():
                    hit = True
                    break
            if hit:
                break
        if hit:
            out.append(g)
    return out


def filter_publisher_crm_groups_by_kpi(
    groups: list[dict],
    kpi: str,
    due_dates: list[str] | None,
) -> list[dict]:
    k = (kpi or "total").strip().lower()
    if k == "total":
        return groups
    b = _crm_kpi_buckets()
    if k == "in_campaign":
        return [g for g in groups if g.get("status") == b["in_campaign"]]
    if k == "responded":
        return [g for g in groups if g.get("status") in b["responded"]]
    if k == "no_response":
        return [g for g in groups if g.get("status") == b["not_contacted"]]
    if k == "won":
        return [g for g in groups if g.get("status") == b["won"]]
    if k == "due":
        due_filters = [x.strip() for x in (due_dates or []) if x.strip()] or [_utc_today_display()]
        return [g for g in groups if (g.get("next_step_date") or "") in due_filters]
    return groups


def _parse_next_step_q_to_display(next_step_q: str | None) -> str | None:
    v = (next_step_q or "").strip()
    if not v or v == "—":
        return None
    # Accept date input from the UI (`YYYY-MM-DD`) and convert it to the dashboard display format.
    if len(v) == 10 and v[4] == "-" and v[7] == "-":
        try:
            dt = datetime.strptime(v, "%Y-%m-%d")
            return dt.strftime("%b %d, %Y")
        except Exception:
            return None
    return v


def filter_publisher_crm_groups_by_column_filters(
    groups: list[dict],
    crm_source: str,
    *,
    title_q: str | None = None,
    spoc_q: str | None = None,
    status_filter: str | None = None,
    next_step_q: str | None = None,
    relevance_filter: str | None = None,
    traffic_min: int | None = None,
    traffic_max: int | None = None,
    syndication_filter: str | None = None,
    brands_min: int | None = None,
    contacted_min: int | None = None,
    ceo_q: str | None = None,
) -> list[dict]:
    # Normalize strings once.
    title_need = (title_q or "").strip().lower()
    spoc_need = (spoc_q or "").strip().lower()
    status_need = (status_filter or "").strip()
    next_step_need = _parse_next_step_q_to_display(next_step_q)
    rel_need = (relevance_filter or "").strip().lower()
    synd_need = (syndication_filter or "").strip().lower()
    ceo_need = (ceo_q or "").strip().lower()

    if crm_source not in ("independent", "media"):
        crm_source = "independent"

    not_contacted = _crm_kpi_buckets().get("not_contacted")

    out: list[dict] = []
    for g in groups:
        if title_need:
            if title_need not in str(g.get("display_title") or "").lower():
                continue

        # Relevance is shared.
        if rel_need:
            if rel_need != str(g.get("relevance") or "").strip().lower():
                continue

        # Next step date is shared.
        if next_step_need:
            if next_step_need != str(g.get("next_step_date") or ""):
                continue

        # Traffic is shared (monthly_visits already computed for both sources).
        if traffic_min is not None or traffic_max is not None:
            mv = g.get("monthly_visits")
            if mv is None:
                continue
            mv_n = float(mv)
            if traffic_min is not None and mv_n < float(traffic_min):
                continue
            if traffic_max is not None and mv_n > float(traffic_max):
                continue

        # Syndication is shared (tags are saved as msn|yahoo|newsbreak|smartnews).
        if synd_need:
            syns = [str(s or "").strip().lower() for s in (g.get("syndication") or [])]
            if synd_need not in syns:
                continue

        if crm_source == "independent":
            if spoc_need:
                spoc_name = str(g.get("spoc_name") or "").lower()
                spoc_names = [str(s or "").lower() for s in (g.get("spoc_names") or [])]
                if spoc_need not in spoc_name and not any(spoc_need in n for n in spoc_names):
                    continue

            if status_need:
                if str(g.get("status") or "") != status_need:
                    continue

        else:  # media
            brands = g.get("brands") or []
            if brands_min is not None:
                if len(brands) < int(brands_min):
                    continue

            if contacted_min is not None:
                cnt_contacted = sum(1 for b in brands if str(b.get("status") or "") != str(not_contacted))
                if cnt_contacted < int(contacted_min):
                    continue

            if ceo_need:
                ceo = str(g.get("ceo_display_name") or "").lower()
                if ceo_need not in ceo:
                    continue

        out.append(g)

    return out


def paginate_publisher_crm_list(
    source: str,
    offer_tab: str | None,
    *,
    page: int,
    page_size: int,
    search: str,
    kpi: str,
    due_dates: list[str] | None,
    compact: bool = False,
    title_q: str | None = None,
    spoc_q: str | None = None,
    status_filter: str | None = None,
    next_step_q: str | None = None,
    relevance_filter: str | None = None,
    traffic_min: int | None = None,
    traffic_max: int | None = None,
    syndication_filter: str | None = None,
    brands_min: int | None = None,
    contacted_min: int | None = None,
    ceo_q: str | None = None,
) -> tuple[dict, list[dict], int]:
    rows = build_publisher_crm_rows(source, offer_tab, lite=True)
    all_groups = rows_to_company_groups(rows, source)

    searched = filter_publisher_crm_groups_by_search(all_groups, search, source)
    column_filtered = filter_publisher_crm_groups_by_column_filters(
        searched,
        source,
        title_q=title_q,
        spoc_q=spoc_q,
        status_filter=status_filter,
        next_step_q=next_step_q,
        relevance_filter=relevance_filter,
        traffic_min=traffic_min,
        traffic_max=traffic_max,
        syndication_filter=syndication_filter,
        brands_min=brands_min,
        contacted_min=contacted_min,
        ceo_q=ceo_q,
    )
    kpis = compute_publisher_crm_kpis(column_filtered, due_dates)
    filtered = filter_publisher_crm_groups_by_kpi(column_filtered, kpi, due_dates)
    total_filtered = len(filtered)
    p = max(1, page)
    ps = max(1, min(3000, page_size))
    start = (p - 1) * ps
    page_groups = filtered[start : start + ps]
    if source == "media":
        page_groups = _strip_media_brands_for_list_response(page_groups)
    if compact:
        if source == "media":
            page_groups = [_slim_media_list_group(g) for g in page_groups]
        else:
            page_groups = [_slim_independent_list_group(g) for g in page_groups]
    return kpis, page_groups, total_filtered


def build_publisher_crm_group_detail(
    source: str,
    offer_tab: str | None,
    *,
    company_id: int | None = None,
    advisor_company_id: int | None = None,
) -> dict | None:
    src = (source or "").strip().lower()
    if src not in ("independent", "media"):
        src = "independent"
    if src == "independent" and company_id:
        rows = build_publisher_crm_rows(src, offer_tab, lite=False, filter_company_id=int(company_id))
    elif src == "media" and advisor_company_id is not None:
        rows = build_publisher_crm_rows(src, offer_tab, lite=False, filter_advisor_company_id=int(advisor_company_id))
    else:
        return None
    if not rows:
        return None
    groups = rows_to_company_groups(rows, src)
    return groups[0] if groups else None


def build_publisher_crm_brand_detail_parent_group(
    source: str,
    offer_tab: str | None,
    *,
    parent_key: str,
) -> dict | None:
    """Load full CRM group for a media parent (for campaign tab at parent level). parent_key like parent:Acme."""
    src = (source or "").strip().lower()
    if src != "media":
        return None
    if not parent_key.startswith("parent:"):
        return None
    pk = parent_key.strip()
    rows = build_publisher_crm_rows(src, offer_tab, lite=False, media_parent_key=pk)
    if not rows:
        return None
    all_groups = rows_to_company_groups(rows, src)
    return all_groups[0] if all_groups else None
