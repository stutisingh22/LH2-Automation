"""
Gmail (draft), draft refine, recipient research, strategist → actions.
External calls (AI, Gmail) are live — not cached.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_current_user, user_has_perm
from db_ops import get_action_by_id, get_session, get_sequence_actions, get_session_actions, update_action
from integrations.ai.ai_engine import adapt_following_sequence_drafts, refine_email_draft
from integrations.ai.recipient_research import research_recipient
from integrations.gmail.auth import gmail_compose_token_exists
from integrations.gmail.service import create_gmail_draft, parse_email_content
from services.deck_links import inject_deck_links_into_email_body
from services.strategist_add_to_action import (
    add_draft_as_new_sequence,
    add_sequence_to_timeline,
    add_step_to_existing_sequence,
    get_cleaned_draft_for_save,
    overwrite_sequence_step,
    parse_multi_email_sequence,
)

router = APIRouter(tags=["workflows"])


class RefineDraftIn(BaseModel):
    instruction: str = ""
    draft_type: str = "email"


class AdaptFollowingIn(BaseModel):
    """Optional focus text (e.g. emphasize rev share). anchor_body overrides DB when set (e.g. unsaved editor)."""
    instruction: str = ""
    anchor_body: Optional[str] = None


class GmailDraftIn(BaseModel):
    account_email: Optional[str] = None
    to_email: str = ""
    content: str = ""
    product_key: Optional[str] = None
    step_index: Optional[int] = None
    lead_brand: Optional[str] = None


class RecipientResearchIn(BaseModel):
    force_refresh: bool = False


class ChatToActionIn(BaseModel):
    sender_user_id: int
    draft_content: str = ""
    mode: Literal["new_sequence", "overwrite", "append_sequence", "multi_sequence"] = "new_sequence"
    channel: str = "Email"
    sequence_id: Optional[str] = None
    target_action_id: Optional[int] = None
    offering_label: Optional[str] = None
    product_key: Optional[str] = None
    parse_multi: bool = False


def _offering_from_product_key(pk: Optional[str]) -> Optional[str]:
    k = (pk or "").strip().lower()
    if k == "accelerate":
        return "Accelerate"
    if k == "mediaos":
        return "MediaOS"
    if k == "acquisition":
        return "Acquisition"
    return None


def _session_has_sequence(session_id: int, sequence_id: str) -> bool:
    for a in get_session_actions(session_id):
        if (a.get("sequence_id") or "") == sequence_id:
            return True
    return False


@router.post("/actions/{action_id}/refine-draft")
def refine_action_draft(
    action_id: int,
    body: RefineDraftIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    act = get_action_by_id(action_id)
    if not act:
        raise HTTPException(status_code=404, detail="Action not found")
    instr = (body.instruction or "").strip()
    if not instr:
        raise HTTPException(status_code=400, detail="instruction required")

    revisions = act.get("draft_revisions") or []
    if not isinstance(revisions, list):
        revisions = []
    content_sent_raw = (act.get("content_sent") or "").strip()
    orig = (act.get("draft_original") or "").strip()
    if not orig:
        orig = content_sent_raw
    current = revisions[-1] if revisions else (orig or content_sent_raw)
    if not (current or "").strip():
        raise HTTPException(status_code=400, detail="No draft text to refine")

    revised = refine_email_draft(current, instr, draft_type=body.draft_type or "email")
    if not revised:
        raise HTTPException(status_code=502, detail="Refine failed")

    revisions = list(revisions)
    revisions.append(revised.strip())
    is_first = len(revisions) == 1
    kwargs: dict = {"draft_revisions": revisions}
    if is_first and content_sent_raw and not (act.get("draft_original") or "").strip():
        kwargs["draft_original"] = content_sent_raw

    update_action(action_id, **kwargs)
    return {"revised": revised.strip(), "draft_revisions": revisions}


def _effective_action_draft_for_adapt(act: dict) -> str:
    revs = act.get("draft_revisions") or []
    if isinstance(revs, list) and revs:
        last = revs[-1]
        if last is not None and str(last).strip():
            return str(last).strip()
    return str(act.get("content_sent") or "").strip()


def _adapt_following_steps_for_anchor(
    act: dict,
    anchor_text: str,
    user_note: Optional[str] = None,
):
    """Align later sequence steps with anchor step updates; returns (updated_rows, count)."""
    seq_id = (act.get("sequence_id") or "").strip()
    sid = act.get("session_id")
    if not seq_id or not sid:
        return [], 0
    anchor_step = int(act.get("sequence_step") or 0)
    if anchor_step <= 0 or not (anchor_text or "").strip():
        return [], 0

    all_steps = get_sequence_actions(int(sid), seq_id)
    following_raw = [s for s in all_steps if int(s.get("sequence_step") or 0) > anchor_step]
    if not following_raw:
        return [], 0

    results = adapt_following_sequence_drafts(
        anchor_step,
        (anchor_text or "").strip(),
        following_raw,
        user_note=(user_note or "").strip() or None,
    )
    updated = []
    for row in results:
        aid = row.get("id")
        rev = (row.get("revised_content") or "").strip()
        if not aid or not rev:
            continue
        update_action(
            int(aid),
            content_sent=rev,
            draft_revisions=[],
            draft_original="",
        )
        updated.append({
            "id": int(aid),
            "change_reason": row.get("change_reason") or "",
        })
    return updated, len(updated)


@router.post("/actions/{action_id}/adapt-following-drafts")
def adapt_following_drafts_endpoint(
    action_id: int,
    body: AdaptFollowingIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    act = get_action_by_id(action_id)
    if not act:
        raise HTTPException(status_code=404, detail="Action not found")
    if not (act.get("sequence_id") or "").strip():
        raise HTTPException(status_code=400, detail="Action is not part of a sequence")
    anchor_text = (body.anchor_body or "").strip() if body.anchor_body is not None else ""
    if not anchor_text:
        anchor_text = _effective_action_draft_for_adapt(act)
    if not anchor_text:
        raise HTTPException(status_code=400, detail="No anchor draft text")
    updated, count = _adapt_following_steps_for_anchor(act, anchor_text, user_note=body.instruction or "")
    if count == 0:
        return {"ok": True, "updated": [], "count": 0, "message": "No later steps in this sequence"}
    return {"ok": True, "updated": updated, "count": count}


@router.get("/gmail/status")
def gmail_status(
    account_email: str = Query("", description="Sender account (token filename key)"),
    user: dict = Depends(get_current_user),
):
    del user
    acc = (account_email or "").strip()
    if not acc:
        raise HTTPException(status_code=400, detail="account_email required")
    return {"connected": gmail_compose_token_exists(acc)}


@router.post("/gmail/draft")
def post_gmail_draft(body: GmailDraftIn, user: dict = Depends(get_current_user)):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    acc = (body.account_email or user.get("email") or "").strip()
    if not acc:
        raise HTTPException(status_code=400, detail="account_email required")
    to_em = (body.to_email or "").strip()
    raw = (body.content or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="content required")

    subj, bod_plain = parse_email_content(raw)
    pk = body.product_key
    bod_html = inject_deck_links_into_email_body(
        bod_plain,
        product_key=pk,
        step_index=body.step_index,
        lead_brand=body.lead_brand,
    )
    out = create_gmail_draft(
        acc,
        to_em,
        subj or "(No subject)",
        bod_plain,
        body_html=bod_html or None,
        allow_interactive=False,
    )
    if not out.get("success"):
        raise HTTPException(
            status_code=400,
            detail=out.get("error") or "Gmail draft failed",
        )
    return {"draft_id": out.get("draft_id")}


@router.post("/contacts/{contact_id}/recipient-research")
def post_recipient_research(
    contact_id: int,
    body: RecipientResearchIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_use_strategist"):
        raise HTTPException(status_code=403, detail="Forbidden")
    data = research_recipient(contact_id, force_refresh=body.force_refresh)
    if not data:
        raise HTTPException(status_code=502, detail="Recipient research failed")
    return {"research": data}


@router.post("/sessions/{session_id}/strategist-draft-to-actions")
def strategist_draft_to_actions(
    session_id: int,
    body: ChatToActionIn,
    user: dict = Depends(get_current_user),
):
    if not user_has_perm(user, "can_log_outcomes"):
        raise HTTPException(status_code=403, detail="Forbidden")
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    draft = (body.draft_content or "").strip()
    if not draft:
        raise HTTPException(status_code=400, detail="draft_content required")

    offering = body.offering_label or _offering_from_product_key(body.product_key)

    multi_steps = parse_multi_email_sequence(draft)

    if body.mode in ("new_sequence", "multi_sequence") and len(multi_steps) >= 2:
        ids, seq_id = add_sequence_to_timeline(
            session_id, body.sender_user_id, multi_steps, offering_label=offering
        )
        return {"ok": True, "mode": "multi_sequence", "action_ids": ids, "sequence_id": seq_id}

    if body.parse_multi or body.mode == "multi_sequence":
        raise HTTPException(
            status_code=400,
            detail=(
                "Multi-email mode needs at least two sections. Start each email with its own line, e.g. "
                '"Email 1:", "Email 2:", or "Email 1 — …".'
            ),
        )

    if body.mode == "new_sequence":
        cleaned = get_cleaned_draft_for_save(draft) or draft
        ids, seq_id = add_draft_as_new_sequence(
            session_id,
            body.sender_user_id,
            cleaned,
            channel=body.channel or "Email",
            offering_label=offering,
        )
        return {"ok": True, "mode": "new_sequence", "action_ids": ids, "sequence_id": seq_id}

    if body.mode == "overwrite":
        if not body.target_action_id:
            raise HTTPException(status_code=400, detail="target_action_id required")
        act = get_action_by_id(body.target_action_id)
        if not act or act.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail="Action not found")
        cleaned = get_cleaned_draft_for_save(draft) or draft
        if not overwrite_sequence_step(body.target_action_id, cleaned):
            raise HTTPException(status_code=400, detail="Overwrite failed")
        updated, count = _adapt_following_steps_for_anchor(act, cleaned)
        return {
            "ok": True,
            "mode": "overwrite",
            "action_id": body.target_action_id,
            "adapted_following_count": count,
            "adapted_following": updated,
        }

    if body.mode == "append_sequence":
        seq_id = (body.sequence_id or "").strip()
        if not seq_id:
            raise HTTPException(status_code=400, detail="sequence_id required")
        if not _session_has_sequence(session_id, seq_id):
            raise HTTPException(status_code=404, detail="Sequence not found")
        aid = add_step_to_existing_sequence(
            session_id,
            body.sender_user_id,
            seq_id,
            draft,
            channel=body.channel or "Email",
            offering_label=offering,
        )
        if not aid:
            raise HTTPException(status_code=400, detail="Append failed")
        return {"ok": True, "mode": "append_sequence", "action_id": aid, "sequence_id": seq_id}

    raise HTTPException(status_code=400, detail="Invalid mode")
