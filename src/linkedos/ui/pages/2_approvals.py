"""Approvals page: the gate every post passes through before anything else can happen.

This is the safety property the whole system rests on — the machine drafts, a human
decides. Nothing here decides anything itself. Each button hands its post id and the
text currently on screen to `services.workflow`, which either performs a legal
transition and audits it, or raises.

State lives in the database. The queue is re-read on every rerun, so approving in one
tab makes the draft vanish from the other.
"""

from __future__ import annotations

import streamlit as st

from linkedos.core.errors import LinkedOSError
from linkedos.core.logging import configure_logging
from linkedos.services import content, workflow
from linkedos.ui import data
from linkedos.ui.components import approvable_card, audit_table, read_only_card

configure_logging()
st.set_page_config(page_title="linkedos · approvals", page_icon="✅", layout="wide")


def _approve(post_id: int, edited_content: str) -> None:
    try:
        workflow.approve(post_id, edited_content=edited_content)
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    st.toast(f"Approved post {post_id}")
    data.after_mutation()


def _reject(post_id: int, reason: str) -> None:
    try:
        workflow.reject(post_id, reason=reason)
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    st.toast(f"Rejected post {post_id}")
    data.after_mutation()


def _regenerate(post_id: int) -> None:
    try:
        with st.spinner("Drafting a fresh variant…"):
            content.regenerate(post_id, n=1, client=data.ai_client())
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    st.toast(f"Regenerated from post {post_id}")
    data.after_mutation()


def _revert(post_id: int) -> None:
    try:
        workflow.revert_to_draft(post_id)
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    st.toast(f"Post {post_id} back to draft")
    data.after_mutation()


st.title("Approvals")

queue = data.queue()

pending_tab, approved_tab = st.tabs(
    [f"Pending ({queue.pending_count})", f"Approved ({len(queue.approved)})"]
)

with pending_tab:
    if not queue.pending:
        st.success("Nothing to review. Draft something on the **Content** page.")
    else:
        st.caption(
            "Edit the text if you want; approving saves whatever is in the box. "
            "Rejection is final — write a new draft instead of undoing one."
        )
        for post in queue.pending:
            approvable_card(
                post,
                on_approve=_approve,
                on_reject=_reject,
                on_regenerate=_regenerate,
            )
            with st.expander(f"History of post {post.id}"):
                audit_table(data.audit_history(post.id), empty="Nothing recorded yet.")

with approved_tab:
    if not queue.approved:
        st.caption("Nothing approved yet.")
    else:
        st.caption("Approved and waiting. Scheduling and publishing arrive in Milestone 3.")
        for post in queue.approved:
            read_only_card(post)
            if st.button("Return to draft", key=f"revert_{post.id}"):
                _revert(post.id)
