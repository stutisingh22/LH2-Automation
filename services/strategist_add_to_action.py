"""
Strategist Add to Action — Use latest chat draft as new sequence or overwrite a step.

When the user is in the Strategist chat and the AI has generated a draft (latest
assistant message), this module provides an "Add to Action" flow similar to
"Add Full Plan to Timeline": create a new sequence with this draft, or overwrite
a specific email/step in an existing sequence.

All UI and logic for this feature lives here so apps.py stays manageable.
"""

import streamlit as st

from db_ops import (
    bulk_log_actions,
    get_active_sequences,
    get_sequence_actions,
    update_action,
)
from services.ui_data_loader import clear_ui_cache
from ui_helpers import get_add_to_action_labels


def get_latest_assistant_draft_content(chat_messages):
    """
    Return the content of the latest assistant message, if any.
    That message is treated as the "latest draft" for Add to Action.
    """
    if not chat_messages:
        return None
    last = chat_messages[-1]
    if last.get("role") != "assistant":
        return None
    content = (last.get("content") or "").strip()
    return content if content else None


def add_draft_as_new_sequence(session_id, sender_id, draft_content, channel="Email", description=None):
    """
    Create a new sequence with a single step containing the draft.
    Returns (list of action ids, sequence_id).
    """
    if not draft_content or not session_id or not sender_id:
        return [], None
    desc = description or f"Step 1: {channel} draft from strategist"
    actions_list = [
        {
            "action_type": "Outreach",
            "channel": channel,
            "description": desc,
            "status": "planned",
            "content_sent": draft_content,
            "context_note": None,
        }
    ]
    ids, seq_id = bulk_log_actions(session_id, sender_id, actions_list)
    return ids, seq_id


def overwrite_sequence_step(action_id, draft_content):
    """Overwrite the content_sent of an existing action (sequence step)."""
    if not action_id or not draft_content:
        return False
    update_action(
        action_id,
        content_sent=draft_content.strip(),
        draft_revisions=[],
        draft_original="",
    )
    return True


def render_add_to_action_ui(session_id, sender_id, chat_messages, key_suffix="last"):
    """
    Render "Add to Action" for the Strategist chat: show a button and flow only when
    the latest message is from the assistant (the latest draft). User can either
    create a new sequence with this draft or overwrite a step in an existing sequence.

    key_suffix: unique suffix for widget keys so the same UI can be rendered in multiple
    places (e.g. in the message loop vs after streaming) without duplicate key errors.
    """
    labels = get_add_to_action_labels()
    draft_content = get_latest_assistant_draft_content(chat_messages)
    if not draft_content:
        return

    k = key_suffix  # short alias for key suffix

    # Session state key for expander open state and choices (suffixed so each call site has its own)
    key_open = f"strategist_add_to_action_open_{k}"
    key_mode = f"strategist_add_to_action_mode_{k}"
    key_seq = f"strategist_add_to_action_seq_id_{k}"
    key_step = f"strategist_add_to_action_step_id_{k}"

    if st.button(labels["button"], key=f"btn_add_to_action_{k}", type="primary", use_container_width=True):
        st.session_state[key_open] = True
        st.session_state[key_mode] = None
        st.session_state[key_seq] = None
        st.session_state[key_step] = None
        st.rerun()

    if not st.session_state.get(key_open):
        return

    with st.expander(labels["expander_title"], expanded=True):
        if st.button("Close", key=f"add_to_action_close_{k}"):
            st.session_state[key_open] = False
            st.rerun()
        mode = st.radio(
            labels["mode_label"],
            options=["new", "overwrite"],
            format_func=lambda x: labels["new_sequence"] if x == "new" else labels["overwrite"],
            key=f"add_to_action_radio_{k}",
            horizontal=True,
        )
        st.session_state[key_mode] = mode

        if mode == "new":
            channel = st.selectbox(
                labels["channel_label"],
                options=["Email", "LinkedIn"],
                key=f"add_to_action_channel_{k}",
            )
            if st.button(labels["confirm_new"], key=f"add_to_action_confirm_new_{k}", type="primary"):
                ids, seq_id = add_draft_as_new_sequence(
                    session_id, sender_id, draft_content, channel=channel
                )
                if ids and seq_id:
                    clear_ui_cache()
                    st.session_state[key_open] = False
                    st.success(labels["success_new"].format(seq_id=seq_id))
                    st.rerun()
                else:
                    st.error(labels["error_generic"])

        else:
            sequences = get_active_sequences(session_id) if session_id else []
            if not sequences:
                st.info(labels["no_sequences"])
                return
            seq_options = {f"{s['id']} ({s['total']} steps)": s["id"] for s in sequences}
            chosen_seq_label = st.selectbox(
                labels["select_sequence"],
                options=list(seq_options.keys()),
                key=f"add_to_action_select_seq_{k}",
            )
            seq_id = seq_options.get(chosen_seq_label) if chosen_seq_label else None
            st.session_state[key_seq] = seq_id

            if not seq_id:
                return
            steps = get_sequence_actions(session_id, seq_id)
            # Only steps that can sensibly be overwritten (e.g. have a channel)
            step_options = []
            for s in steps:
                ch = (s.get("channel") or "").strip() or "Step"
                step_num = s.get("sequence_step") or "?"
                step_options.append((s["id"], f"Step {step_num}: {ch}"))
            if not step_options:
                st.warning(labels["no_steps"])
                return
            step_labels = [opt[1] for opt in step_options]
            step_ids = [opt[0] for opt in step_options]
            chosen_idx = st.selectbox(
                labels["select_step"],
                options=list(range(len(step_labels))),
                format_func=lambda i: step_labels[i],
                key=f"add_to_action_select_step_{k}",
            )
            action_id = step_ids[chosen_idx] if chosen_idx is not None else None
            st.session_state[key_step] = action_id

            if st.button(labels["confirm_overwrite"], key=f"add_to_action_confirm_overwrite_{k}", type="primary"):
                if action_id:
                    overwrite_sequence_step(action_id, draft_content)
                    # Clear per-step UI state so Actions tab shows the new content
                    st.session_state.pop(f"draft_revisions_list_{action_id}", None)
                    st.session_state[f"draft_original_{action_id}"] = draft_content.strip()
                    st.session_state.pop(f"refine_version_sel_{action_id}", None)
                    clear_ui_cache()
                    st.session_state[key_open] = False
                    st.success(labels["success_overwrite"])
                    st.rerun()
                else:
                    st.error(labels["error_generic"])
