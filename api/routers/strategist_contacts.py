"""
Strategist contacts from research (SPOC extract + LinkedIn lookup) — parity with Streamlit.
External lookups are live (not cached).
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db_ops import (
    get_advisor_company,
    get_company,
    get_contact,
    get_contacts,
    get_enrichment_result,
    get_session_summary,
    get_spoc_contacts_by_company,
    merge_advisor_company_extra_data,
    merge_company_extra_data,
    update_contact,
    upsert_contact,
)
from integrations.ai.research import fetch_research_from_notion
from integrations.search.linkedin_lookup import (
    extract_spoc_table_contacts,
    get_linkedin_url_for_contact,
    run_linkedin_lookup_for_contacts,
)
from integrations.signalhire.enrichment import request_enrichment
from api.deps import get_current_user, user_has_perm
from api.routers.intelligence_routes import _load_intel_company, _research_notes_for_intel

router = APIRouter(prefix="/strategist", tags=["strategist-contacts"])


class ExtractContactsIn(BaseModel):
    company_id: Optional[int] = None
    advisor_company_id: Optional[int] = None
    session_id: Optional[int] = None


class AddContactLookupIn(BaseModel):
    company_id: int
    advisor_company_id: Optional[int] = None
    name: str = ""
    role: str = ""
    company_override: Optional[str] = None


class BulkRowsIn(BaseModel):
    company_id: int
    advisor_company_id: Optional[int] = None
    rows: list[dict] = []


class EnsureContactIn(BaseModel):
    company_id: int
    advisor_company_id: Optional[int] = None
    name: str = ""
    role: str = ""
    email: str = ""
    linkedin_url: str = ""


class UpdateContactEmailIn(BaseModel):
    company_id: int
    advisor_company_id: Optional[int] = None
    row_id: Optional[int] = None
    name: str = ""
    role: str = ""
    linkedin_url: str = ""
    email: str = ""


def _linkedin_result_matches_company(result_company: Optional[str], comp: dict) -> bool:
    if not comp:
        return True
    rc = (result_company or "").strip()
    rc_lower = rc.lower()
    domain = (comp.get("domain") or "").strip().lower()
    name = (comp.get("name") or "").strip().lower()
    if not domain and not name:
        return True
    if not rc or rc_lower == "this company":
        return True
    identifiers = [x for x in [domain, name] if x]
    for ident in identifiers:
        if ident and (
            rc_lower == ident
            or (len(ident) >= 3 and ident in rc_lower)
            or (len(rc_lower) >= 3 and rc_lower in ident)
        ):
            return True
    return False


def _saved_lookup_list(company_id: int, advisor_company_id: Optional[int]) -> list:
    if advisor_company_id is not None:
        adv = get_advisor_company(advisor_company_id)
        if not adv:
            return []
        ed = adv.get("extra_data") if isinstance(adv.get("extra_data"), dict) else {}
        raw = ed.get("linkedin_lookup_results")
        return raw if isinstance(raw, list) else []
    c = get_company(company_id)
    if not c:
        return []
    ed = c.get("extra_data") if isinstance(c.get("extra_data"), dict) else {}
    raw = ed.get("linkedin_lookup_results")
    return raw if isinstance(raw, list) else []


def _persist_lookup_list(company_id: int, advisor_company_id: Optional[int], lst: list) -> None:
    if advisor_company_id is not None:
        merge_advisor_company_extra_data(advisor_company_id, {"linkedin_lookup_results": lst})
    else:
        merge_company_extra_data(company_id, {"linkedin_lookup_results": lst})


def _first_contact_value(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return ""
    first = items[0]
    if isinstance(first, dict):
        return str(first.get("value") or "").strip()
    return str(first).strip()


def _board_rows(company_id: int, advisor_company_id: Optional[int]) -> tuple[list[dict[str, Any]], bool]:
    comp_like, _ = _load_intel_company(company_id, advisor_company_id)
    saved = _saved_lookup_list(company_id, advisor_company_id)
    filtered = [r for r in saved if _linkedin_result_matches_company(r.get("company"), comp_like)]
    saved_rows: list[dict[str, Any]] = []
    from_extraction = bool(filtered)
    if filtered:
        for r in filtered:
            saved_rows.append(
                {
                    "id": r.get("id"),
                    "name": (r.get("name") or "").strip() or "—",
                    "role": (r.get("role") or "").strip() or "—",
                    "company": (r.get("company") or "").strip() or "—",
                    "linkedin_url": (r.get("linkedin_url") or "").strip() or "—",
                    "email": (r.get("email") or "").strip() or "—",
                    "phone": (r.get("phone") or "").strip() or "—",
                    "status": (r.get("enrichment_status") or "").strip() or "—",
                }
            )

    c = get_company(company_id)
    if not c:
        return saved_rows, from_extraction
    company_name = (c.get("domain") or c.get("name") or "—").strip()
    contacts = get_contacts(company_id) or []
    spocs = get_spoc_contacts_by_company(company_id) or []
    spoc_by_contact_id: dict = {}
    for s in spocs:
        pid = s.get("spoc_contact_id")
        if pid and pid not in spoc_by_contact_id:
            spoc_by_contact_id[pid] = s
    rows = []
    for ct in contacts:
        s = spoc_by_contact_id.get(ct.get("id"))
        rows.append(
            {
                "id": ct.get("id"),
                "name": (ct.get("name") or "").strip() or "—",
                "role": (ct.get("role") or "").strip()
                or ((s.get("role") or "").strip() if s else "")
                or "—",
                "company": company_name or "—",
                "linkedin_url": (ct.get("linkedin") or "").strip()
                or ((s.get("linkedin_url") or "").strip() if s else "")
                or "—",
                "email": (ct.get("email") or "").strip()
                or ((s.get("email") or "").strip() if s else "")
                or "—",
                "phone": (s.get("phone") or "").strip() if s else "—",
                "status": "saved"
                if (ct.get("email") or (s and s.get("email")))
                else "—",
            }
        )

    # When extraction rows exist, we still want normal contacts to appear in the
    # Strategist "contacts-board" table (especially after adding SPOCs from the dashboard).
    # Merge + de-dupe by (name/email/linkedin).
    def _norm(s: Any) -> str:
        return str(s or "").strip().lower()

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for r in saved_rows + rows:
        key = f"{_norm(r.get('name'))}|{_norm(r.get('email'))}|{_norm(r.get('linkedin_url'))}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)

    return merged, from_extraction


@router.get("/contacts-board")
def get_contacts_board(
    company_id: int = Query(..., description="Workspace company id (shadow id for media)"),
    advisor_company_id: Optional[int] = Query(None),
    user: dict = Depends(get_current_user),
):
    del user
    if not get_company(company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    rows, from_extraction = _board_rows(company_id, advisor_company_id)
    return {"rows": rows, "from_extraction": from_extraction}


@router.post("/extract-contacts")
def post_extract_contacts(body: ExtractContactsIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if body.company_id is None:
        raise HTTPException(status_code=400, detail="company_id required")
    comp, _ = _load_intel_company(body.company_id, body.advisor_company_id)
    research_full = ""
    if body.advisor_company_id is not None:
        adv = get_advisor_company(body.advisor_company_id)
        if adv:
            ed = adv.get("extra_data") if isinstance(adv.get("extra_data"), dict) else {}
            research_full = (ed.get("research_full") or "").strip()
    else:
        c = get_company(body.company_id)
        if c:
            ed = c.get("extra_data") if isinstance(c.get("extra_data"), dict) else {}
            research_full = (ed.get("research_full") or "").strip()
    if not research_full and body.session_id:
        summ, _ = get_session_summary(body.session_id)
        research_full = (summ or "").strip()
    rn = _research_notes_for_intel(comp)
    research_notes_text = "\n\n".join(
        f"{(n.get('title') or 'Section')}\n{(n.get('content') or '').strip()}" for n in (rn or []) if isinstance(n, dict)
    ).strip()
    combined = (research_full or research_notes_text or "").strip()
    company_name = comp.get("domain") or comp.get("name") or "this company"
    contacts = extract_spoc_table_contacts("", combined, company_name)
    if not contacts:
        return {"ok": True, "results": [], "message": "No SPOC table found in research."}
    results = run_linkedin_lookup_for_contacts(contacts)
    for r in results:
        if "enrichment_status" not in r or not r.get("enrichment_status"):
            r["enrichment_status"] = "—"
        # Ensure extracted rows become real contacts so they appear in Strategist "To" dropdown.
        try:
            upsert_contact(
                body.company_id,
                r.get("name") or "",
                role=(r.get("role") or "").strip() or None,
                linkedin=(r.get("linkedin_url") or "").strip() or None,
            )
        except Exception:
            pass
    _persist_lookup_list(body.company_id, body.advisor_company_id, results)
    return {"ok": True, "results": results, "count": len(results)}


@router.post("/add-contact-lookup")
def post_add_contact_lookup(body: AddContactLookupIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    comp, _ = _load_intel_company(body.company_id, body.advisor_company_id)
    nm = (body.name or "").strip()
    if not nm:
        raise HTTPException(status_code=400, detail="name required")
    default_co = (body.company_override or comp.get("domain") or comp.get("name") or "").strip()
    url = get_linkedin_url_for_contact(nm, default_co)
    new_row = {
        "name": nm,
        "role": (body.role or "").strip(),
        "company": default_co or "this company",
        "linkedin_url": url or "",
        "email": "",
        "phone": "",
        "enrichment_status": "—",
    }
    existing = list(_saved_lookup_list(body.company_id, body.advisor_company_id))
    merged = existing + [new_row]
    _persist_lookup_list(body.company_id, body.advisor_company_id, merged)
    try:
        upsert_contact(
            body.company_id,
            nm,
            role=(body.role or "").strip() or None,
            linkedin=(url or "").strip() or None,
        )
    except Exception:
        pass
    return {"ok": True, "row": new_row, "count": len(merged)}


@router.post("/enrich-contacts")
def post_enrich_contacts(body: BulkRowsIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not body.company_id:
        raise HTTPException(status_code=400, detail="company_id required")
    saved = list(_saved_lookup_list(body.company_id, body.advisor_company_id))
    if not saved:
        return {"ok": True, "updated": 0, "rows": []}
    targets = {(str((r.get("name") or "")).strip().lower(), str((r.get("linkedin_url") or "")).strip()) for r in body.rows or []}
    updated = 0
    out_rows = []
    for row in saved:
        nm = str((row.get("name") or "")).strip()
        li = str((row.get("linkedin_url") or "")).strip()
        key = (nm.lower(), li)
        if targets and key not in targets:
            out_rows.append(row)
            continue
        if not li:
            out_rows.append(row)
            continue
        record_id, _request_id, status = request_enrichment(li, user.get("id"))
        if status == "completed" and record_id:
            rec = get_enrichment_result(record_id) or {}
            res = rec.get("result_json") or {}
            email = _first_contact_value(res.get("emails"))
            phone = _first_contact_value(res.get("phones"))
            if email:
                row["email"] = email
            if phone:
                row["phone"] = phone
            row["enrichment_status"] = "completed"
            try:
                upsert_contact(
                    body.company_id,
                    nm,
                    role=(row.get("role") or "").strip() or None,
                    email=(row.get("email") or "").strip() or None,
                    linkedin=(li or "").strip() or None,
                )
            except Exception:
                pass
            updated += 1
        elif status == "pending":
            row["enrichment_status"] = "pending"
            updated += 1
        out_rows.append(row)
    _persist_lookup_list(body.company_id, body.advisor_company_id, out_rows)
    return {"ok": True, "updated": updated, "rows": out_rows}


@router.post("/delete-contacts")
def post_delete_contacts(body: BulkRowsIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not body.company_id:
        raise HTTPException(status_code=400, detail="company_id required")
    saved = list(_saved_lookup_list(body.company_id, body.advisor_company_id))
    if not saved:
        return {"ok": True, "deleted": 0}
    targets = {(str((r.get("name") or "")).strip().lower(), str((r.get("linkedin_url") or "")).strip()) for r in body.rows or []}
    if not targets:
        return {"ok": True, "deleted": 0}
    kept = []
    deleted = 0
    for row in saved:
        nm = str((row.get("name") or "")).strip()
        li = str((row.get("linkedin_url") or "")).strip()
        if (nm.lower(), li) in targets:
            deleted += 1
            continue
        kept.append(row)
    _persist_lookup_list(body.company_id, body.advisor_company_id, kept)
    return {"ok": True, "deleted": deleted}


@router.post("/ensure-contact")
def post_ensure_contact(body: EnsureContactIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not body.company_id:
        raise HTTPException(status_code=400, detail="company_id required")
    nm = (body.name or "").strip()
    if not nm:
        raise HTTPException(status_code=400, detail="name required")
    cid = upsert_contact(
        body.company_id,
        nm,
        role=(body.role or "").strip() or None,
        email=(body.email or "").strip() or None,
        linkedin=(body.linkedin_url or "").strip() or None,
    )
    return {"ok": True, "contact_id": cid}


@router.patch("/update-contact-email")
def patch_update_contact_email(body: UpdateContactEmailIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not body.company_id:
        raise HTTPException(status_code=400, detail="company_id required")
    email_clean = (body.email or "").strip()
    if body.row_id:
        ct = get_contact(body.row_id)
        if ct and int(ct.get("company_id") or 0) == int(body.company_id):
            update_contact(body.row_id, email=(email_clean or None))
    else:
        nm = (body.name or "").strip()
        if nm:
            upsert_contact(
                body.company_id,
                nm,
                role=(body.role or "").strip() or None,
                email=(email_clean or None),
                linkedin=(body.linkedin_url or "").strip() or None,
            )

    saved = list(_saved_lookup_list(body.company_id, body.advisor_company_id))
    if saved:
        nm_l = (body.name or "").strip().lower()
        li = (body.linkedin_url or "").strip()
        updated = False
        for row in saved:
            r_id = row.get("id")
            r_nm_l = str(row.get("name") or "").strip().lower()
            r_li = str(row.get("linkedin_url") or "").strip()
            if (body.row_id and r_id and int(r_id) == int(body.row_id)) or ((not body.row_id) and nm_l and r_nm_l == nm_l and r_li == li):
                row["email"] = email_clean
                row["enrichment_status"] = "saved" if email_clean else (row.get("enrichment_status") or "—")
                updated = True
                break
        if updated:
            _persist_lookup_list(body.company_id, body.advisor_company_id, saved)
    return {"ok": True, "email": email_clean}

