from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, or_

from config import normalize_domain
from db_ops import SessionFactory
from models import AdvisorCompany, Company, Contact, Session, SpocContact

SourceType = Literal["independent", "media_house"]
TargetType = Literal["independent", "media_house"]


def _as_dict(extra) -> dict:
    return dict(extra) if isinstance(extra, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_company_by_normalized_domain(db, domain: str) -> Company | None:
    norm = normalize_domain(domain) or (domain or "").strip().lower().lstrip(".")
    if not norm:
        return None
    www = f"www.{norm}"
    row = (
        db.query(Company)
        .filter(
            or_(
                Company.domain == norm,
                Company.domain == www,
                func.lower(Company.domain) == norm,
                func.lower(Company.domain) == www,
            )
        )
        .first()
    )
    if row:
        return row
    for c in db.query(Company).all():
        if normalize_domain(c.domain or "") == norm:
            return c
    return None


def _ensure_shadow_company_for_advisor(db, advisor: AdvisorCompany) -> Company:
    shadow_domain = f"__advisor_{advisor.id}"
    row = db.query(Company).filter_by(domain=shadow_domain).first()
    if row:
        return row
    row = Company(
        domain=shadow_domain,
        name=(advisor.domain or advisor.parent_name or "Child").strip() or "Child",
        extra_data={"source_advisor_company_id": int(advisor.id)},
    )
    db.add(row)
    db.flush()
    leadership = Contact(company_id=row.id, name="Leadership", role="Leadership")
    db.add(leadership)
    db.flush()
    return row


def _move_workspace_rows(db, from_company_id: int, to_company_id: int) -> dict:
    moved_contacts = (
        db.query(Contact)
        .filter(Contact.company_id == from_company_id)
        .update({Contact.company_id: to_company_id}, synchronize_session=False)
    )
    moved_sessions = (
        db.query(Session)
        .filter(Session.company_id == from_company_id)
        .update({Session.company_id: to_company_id}, synchronize_session=False)
    )
    moved_spoc = (
        db.query(SpocContact)
        .filter(SpocContact.company_id == from_company_id)
        .update({SpocContact.company_id: to_company_id}, synchronize_session=False)
    )
    return {
        "moved_contacts": int(moved_contacts or 0),
        "moved_sessions": int(moved_sessions or 0),
        "moved_spoc_rows": int(moved_spoc or 0),
    }


def reclassify_company(
    *,
    source_type: SourceType,
    source_id: int,
    target_type: TargetType,
    parent_name: str | None,
    reason: str | None,
    actor_user_id: int | None,
) -> dict:
    if source_type == target_type:
        raise ValueError("source_type and target_type must be different")

    actor_id = int(actor_user_id) if actor_user_id else None
    parent = (parent_name or "").strip()
    reason_txt = (reason or "").strip() or None
    now_iso = _now_iso()

    db = SessionFactory()
    try:
        if source_type == "independent" and target_type == "media_house":
            source = db.get(Company, int(source_id))
            if not source:
                raise ValueError("Source company not found")
            source_extra = _as_dict(source.extra_data)
            if source_extra.get("source_advisor_company_id") is not None:
                raise ValueError("Source company is already a media shadow")
            if not parent:
                raise ValueError("parent_name is required for media_house target")

            original_domain = source.domain or ""
            target_domain = normalize_domain(original_domain) or original_domain.strip().lower().lstrip(".")
            if not target_domain or target_domain.startswith("__"):
                raise ValueError("Invalid independent domain for media reclassification")

            advisor = (
                db.query(AdvisorCompany)
                .filter_by(domain=target_domain, parent_name=parent)
                .first()
            )
            if not advisor:
                advisor = AdvisorCompany(domain=target_domain, parent_name=parent)
                db.add(advisor)
                db.flush()

            advisor.extra_data = {
                **_as_dict(advisor.extra_data),
                "reclassified_from": "independent",
                "reclassified_to": "media_house",
                "reclassified_at": now_iso,
                "reclassified_by": actor_id,
                "reclassified_reason": reason_txt,
            }

            shadow = _ensure_shadow_company_for_advisor(db, advisor)
            moved = {"moved_contacts": 0, "moved_sessions": 0, "moved_spoc_rows": 0}
            archived_source = False
            if shadow.id != source.id:
                moved = _move_workspace_rows(db, source.id, shadow.id)
                db.delete(source)
                archived_source = True
            else:
                source.domain = f"__advisor_{advisor.id}"
                source.extra_data = {
                    **source_extra,
                    "source_advisor_company_id": int(advisor.id),
                    "reclassification_target_type": "media_house",
                    "reclassification_target_id": int(advisor.id),
                    "reclassification_at": now_iso,
                    "reclassification_by": actor_id,
                    "reclassification_reason": reason_txt,
                    "original_domain": original_domain,
                    "original_company_id": int(source.id),
                }

            db.commit()
            return {
                "ok": True,
                "target_type": "media_house",
                "target_id": int(advisor.id),
                "archived_source": archived_source,
                "source_company_id": int(source_id),
                **moved,
            }

        if source_type == "media_house" and target_type == "independent":
            source = db.get(AdvisorCompany, int(source_id))
            if not source:
                raise ValueError("Source media-house company not found")
            source_domain = normalize_domain(source.domain or "") or (source.domain or "").strip().lower().lstrip(".")
            if not source_domain or source_domain.startswith("__"):
                raise ValueError("Cannot reclassify parent-only/synthetic media rows")

            target = _find_company_by_normalized_domain(db, source_domain)
            if target and _as_dict(target.extra_data).get("source_advisor_company_id") is not None:
                target = None
            if not target:
                target = Company(domain=source_domain, name=source_domain)
                db.add(target)
                db.flush()

            moved = {"moved_contacts": 0, "moved_sessions": 0, "moved_spoc_rows": 0}
            shadow = db.query(Company).filter_by(domain=f"__advisor_{source.id}").first()
            if shadow and shadow.id != target.id:
                moved = _move_workspace_rows(db, shadow.id, target.id)
                db.delete(shadow)

            target.extra_data = {
                **_as_dict(target.extra_data),
                "reclassified_from": "media_house",
                "reclassified_to": "independent",
                "reclassified_at": now_iso,
                "reclassified_by": actor_id,
                "reclassified_reason": reason_txt,
                "reclassified_source_advisor_id": int(source.id),
            }
            db.delete(source)
            db.commit()
            return {
                "ok": True,
                "target_type": "independent",
                "target_id": int(target.id),
                "archived_source": True,
                "source_advisor_company_id": int(source_id),
                **moved,
            }

        raise ValueError("Unsupported reclassification route")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

