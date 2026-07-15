"""Approvals — the batch review screen. The gate every post passes through.

This is the safety property the whole system rests on: the machine drafts, a human
decides. Batch review does not weaken that — it makes deciding about *many* posts fast.
Nothing here publishes, and nothing here decides anything itself. Every button hands post
ids and on-screen text to `services.workflow`, which performs a legal transition and
audits it, or refuses.

The flow the page is built around:

    Generate a week  ->  scan and edit the drafts together  ->  approve the batch
                     ->  adjust the proposed schedule  ->  confirm

State lives in the database and is re-read on every rerun. `st.session_state` holds only
transient UI state: which batch is under review, which cards are ticked, and the
half-finished schedule the user is tweaking before they confirm it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from linkedos.core.errors import LinkedOSError
from linkedos.core.logging import configure_logging
from linkedos.services import content, scheduling, workflow
from linkedos.services.workflow import BatchOutcome, PostView
from linkedos.ui import data
from linkedos.ui.components import (
    approvable_card,
    audit_table,
    read_only_card,
    require_database,
    selection_key,
    status_badge,
)

configure_logging()
st.set_page_config(page_title="linkedos · approvals", page_icon="✅", layout="wide")

# Transient UI state keys. None of these is durable data — the database is.
REVIEW_BATCH = "review_batch_id"
REVIEW_CADENCE = "review_cadence"
SCHEDULE_BATCH = "schedule_batch_id"
DEFAULT_CADENCE = "1 per weekday"


# --------------------------------------------------------------------------- callbacks
# Each one calls a single service function and then refreshes. No business logic here:
# whether a transition is legal is the state machine's answer, not a button's.


def _report(outcome: BatchOutcome, verb: str) -> None:
    if outcome.applied:
        st.toast(f"{verb} {len(outcome.applied)} post(s)")
    if outcome.failed:
        lines = "\n".join(f"- post {f.post_id}: {f.reason}" for f in outcome.failed)
        st.warning(f"{len(outcome.failed)} post(s) could not be {verb.lower()}:\n{lines}")


def _approve_one(post_id: int, edited_content: str) -> None:
    try:
        workflow.approve(post_id, edited_content=edited_content)
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    _open_schedule_editor_for(post_id)
    st.toast(f"Approved post {post_id}")
    data.after_mutation()


def _reject_one(post_id: int, reason: str) -> None:
    try:
        workflow.reject(post_id, reason=reason)
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    st.toast(f"Rejected post {post_id}")
    data.after_mutation()


def _regenerate_one(post_id: int) -> None:
    try:
        with st.spinner("Drafting a fresh variant…"):
            content.regenerate(post_id, n=1, client=data.ai_client())
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    st.toast(f"Regenerated from post {post_id}")
    data.after_mutation()


def _revert_one(post_id: int) -> None:
    try:
        workflow.revert_to_draft(post_id)
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    st.toast(f"Post {post_id} back to draft")
    data.after_mutation()


def _approve_many(post_ids: list[int], edits: dict[int, str], batch_id: str | None) -> None:
    if not post_ids:
        st.warning("Nothing selected.")
        return
    outcome = workflow.batch_approve(post_ids, edits=edits)
    if batch_id is not None and outcome.applied:
        st.session_state[SCHEDULE_BATCH] = batch_id
    _report(outcome, "Approved")
    data.after_mutation()


def _reject_many(post_ids: list[int]) -> None:
    if not post_ids:
        st.warning("Nothing selected.")
        return
    outcome = workflow.batch_reject(post_ids)
    _report(outcome, "Rejected")
    data.after_mutation()


def _confirm_schedule(posts: list[PostView]) -> None:
    errors: list[str] = []
    scheduled = 0
    for post in posts:
        chosen_date = st.session_state.get(f"sched_date_{post.id}")
        chosen_time = st.session_state.get(f"sched_time_{post.id}")
        if chosen_date is None or chosen_time is None:
            continue
        at = datetime.combine(chosen_date, chosen_time, tzinfo=UTC)
        try:
            workflow.schedule(post.id, at)
            scheduled += 1
        except LinkedOSError as exc:
            errors.append(f"post {post.id}: {exc}")
    if errors:
        st.warning("Some posts could not be scheduled:\n" + "\n".join(f"- {e}" for e in errors))
    if scheduled:
        st.toast(f"Scheduled {scheduled} post(s)")
        st.session_state.pop(SCHEDULE_BATCH, None)
    data.after_mutation()


# ------------------------------------------------------------------------------ helpers


def _open_schedule_editor_for(post_id: int) -> None:
    """After a single approve, open the schedule editor on that post's batch, if any."""
    post = content.get_post(post_id)
    if post.batch_id is not None:
        st.session_state[SCHEDULE_BATCH] = post.batch_id


def _selected_ids(posts: list[PostView]) -> list[int]:
    return [post.id for post in posts if st.session_state.get(selection_key(post.id))]


def _edits_for(posts: list[PostView]) -> dict[int, str]:
    edits: dict[int, str] = {}
    for post in posts:
        key = f"{data.DRAFT_TEXT_PREFIX}{post.id}"
        if key in st.session_state:
            edits[post.id] = str(st.session_state[key])
    return edits


# -------------------------------------------------------------------------------- views


def _generate_week(expanded: bool) -> None:
    with st.expander("⚡ Generate a week", expanded=expanded):
        st.caption(
            "One click drafts a set of posts you review together. Nothing is published — "
            "this screen is still the approval gate."
        )
        with st.form("generate_week"):
            count = st.slider("How many posts", min_value=1, max_value=14, value=5)
            cadence_text = st.text_input(
                "Cadence",
                value=DEFAULT_CADENCE,
                help='e.g. "1 per weekday", "Mon/Wed/Fri 09:00", "2 per weekday".',
            )
            submitted = st.form_submit_button("Generate the batch", type="primary")

        if not submitted:
            return

        try:
            scheduling.parse_cadence(cadence_text)  # fail fast on a bad cadence
        except LinkedOSError as exc:
            st.error(str(exc))
            return
        try:
            with st.spinner(f"Drafting {count} post(s) in your voice…"):
                result = content.generate_batch(count, per_topic=1, client=data.ai_client())
        except LinkedOSError as exc:
            st.error(str(exc))
            return

        st.session_state[REVIEW_BATCH] = result.batch_id
        st.session_state[REVIEW_CADENCE] = cadence_text
        st.session_state.pop(SCHEDULE_BATCH, None)
        data.after_mutation()


def _pick_batch() -> str | None:
    batches = data.recent_batches()
    if not batches:
        return None

    ids = [summary.batch_id for summary in batches]
    labels = {
        summary.batch_id: (
            f"{summary.batch_id[:8]} · {summary.draft_count}/{summary.total_count} pending · "
            + ", ".join(summary.topics[:3])
        )
        for summary in batches
    }
    current = st.session_state.get(REVIEW_BATCH)
    index = ids.index(current) if current in ids else 0
    return str(st.selectbox("Batch", ids, index=index, format_func=lambda b: labels[b]))


def _batch_action_bar(drafts: list[PostView], batch_id: str) -> None:
    approve_all, approve_sel, reject_sel = st.columns(3)
    if approve_all.button("✅ Approve all", type="primary", use_container_width=True):
        ids = [post.id for post in drafts]
        _approve_many(ids, _edits_for(drafts), batch_id)
    if approve_sel.button("Approve selected", use_container_width=True):
        selected = _selected_ids(drafts)
        _approve_many(selected, _edits_for(drafts), batch_id)
    if reject_sel.button("Reject selected", use_container_width=True):
        _reject_many(_selected_ids(drafts))


def _draft_grid(drafts: list[PostView]) -> None:
    columns = st.columns(2)
    for index, post in enumerate(drafts):
        with columns[index % 2]:
            approvable_card(
                post,
                on_approve=_approve_one,
                on_reject=_reject_one,
                on_regenerate=_regenerate_one,
                selectable=True,
            )


def _schedule_editor(batch_id: str) -> None:
    posts = [post for post in data.batch_approved(batch_id) if post.scheduled_at is None]
    st.subheader("Schedule this batch")
    if not posts:
        st.success("Every approved post in this batch has a publish time. P3 will post them.")
        if st.button("Done scheduling"):
            st.session_state.pop(SCHEDULE_BATCH, None)
            st.rerun()
        return

    cadence_text = str(st.session_state.get(REVIEW_CADENCE, DEFAULT_CADENCE))
    st.caption(
        f"Proposed from cadence “{cadence_text}”, in UTC. Tweak any row, then confirm. "
        "Publishing happens in Milestone 3 — this only records the times."
    )
    try:
        cadence = scheduling.parse_cadence(cadence_text)
        slots = scheduling.propose_schedule(posts, cadence, now=datetime.now(UTC))
    except LinkedOSError as exc:
        st.error(str(exc))
        return
    proposed = {slot.post_id: slot.at for slot in slots}

    for post in posts:
        default = proposed.get(post.id, datetime.now(UTC))
        topic_col, date_col, time_col = st.columns([3, 1, 1])
        topic_col.markdown(f"**{post.topic}** · {status_badge(post.status.value)}")
        date_col.date_input("Date", value=default.date(), key=f"sched_date_{post.id}")
        time_col.time_input("Time (UTC)", value=default.time(), key=f"sched_time_{post.id}")

    if st.button("Confirm schedule", type="primary"):
        _confirm_schedule(posts)


# -------------------------------------------------------------------------------- render

st.title("Approvals")
st.caption("Bulk review, edit, approve, and schedule a batch. Nothing publishes without you.")

if not require_database(data.app_status()):
    st.stop()

review_tab, loose_tab, approved_tab = st.tabs(["Batch review", "Loose drafts", "Approved"])

with review_tab:
    active_batch = st.session_state.get(REVIEW_BATCH)
    _generate_week(expanded=active_batch is None)

    chosen = _pick_batch()
    if chosen is None:
        st.info("No batches yet. Use **Generate a week** above to create one.")
    else:
        st.session_state[REVIEW_BATCH] = chosen
        drafts = data.batch_drafts(chosen)
        if drafts:
            st.caption(
                "Tick cards to act on a subset, or use **Approve all**. Approving saves "
                "whatever text is in each box."
            )
            _batch_action_bar(drafts, chosen)
            _draft_grid(drafts)
        else:
            st.success("No drafts left to review in this batch.")

        if st.session_state.get(SCHEDULE_BATCH) == chosen:
            st.divider()
            _schedule_editor(chosen)

with loose_tab:
    # Drafts made one-off on the Content page belong to no batch; approve them here.
    loose = [post for post in data.queue().pending if post.batch_id is None]
    if not loose:
        st.caption("No un-batched drafts. Single drafts from the Content page appear here.")
    else:
        for post in loose:
            approvable_card(
                post,
                on_approve=_approve_one,
                on_reject=_reject_one,
                on_regenerate=_regenerate_one,
            )
            with st.expander(f"History of post {post.id}"):
                audit_table(data.audit_history(post.id), empty="Nothing recorded yet.")

with approved_tab:
    approved = data.queue().approved
    if not approved:
        st.caption("Nothing approved yet.")
    else:
        st.caption("Approved posts. Scheduled ones show their time; publishing lands in M3.")
        for post in approved:
            read_only_card(post)
            when = post.scheduled_at.strftime("%Y-%m-%d %H:%M UTC") if post.scheduled_at else None
            if when:
                st.caption(f"⏰ scheduled for {when}")
            if st.button("Return to draft", key=f"revert_{post.id}"):
                _revert_one(post.id)
