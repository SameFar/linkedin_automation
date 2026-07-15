"""Reusable render functions shared by the pages.

These draw widgets and hand what the user typed back to a callback. They contain no
business logic, open no session, and decide nothing: whether an approval is legal is the
state machine's answer, not a button's.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import streamlit as st

from linkedos.services.audit import AuditEntry
from linkedos.services.logs import LogLine
from linkedos.services.status import AppStatus
from linkedos.services.workflow import PostView
from linkedos.ui.data import DRAFT_TEXT_PREFIX, SELECT_PREFIX

#: `on_approve(post_id, edited_content)` — the text on screen, not the text in the DB.
ApproveFn = Callable[[int, str], None]
#: `on_reject(post_id, reason)`
RejectFn = Callable[[int, str], None]
#: `on_regenerate(post_id)`
RegenerateFn = Callable[[int], None]

_STATUS_COLOUR = {
    "draft": "grey",
    "approved": "green",
    "scheduled": "blue",
    "published": "violet",
    "rejected": "red",
    "failed": "red",
}


def require_database(status: AppStatus) -> bool:
    """Render why the database is unusable and return `False`, or return `True`.

    Every page calls this before its first read. A page that skips it queries a database
    that may be missing or a migration behind, and Streamlit answers with a raw
    `OperationalError` traceback instead of the one sentence that fixes it.
    """
    if not status.db_exists:
        st.error(f"No database at `{status.db_path}`. Run `alembic upgrade head`, then reload.")
        return False

    if status.needs_migration:
        st.error(
            f"Database schema is out of date: `{status.db_path}` is at revision "
            f"`{status.db_revision or 'none'}`, the code expects `{status.head_revision}`. "
            "Run `alembic upgrade head`, then reload."
        )
        return False

    return True


def status_badge(status: str) -> str:
    """A coloured markdown badge for a post status."""
    return f":{_STATUS_COLOUR.get(status, 'grey')}-badge[{status}]"


def _age(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - aware).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86_400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86_400)}d ago"


def selection_key(post_id: int) -> str:
    """Session-state key holding whether a card's batch-select checkbox is ticked."""
    return f"{SELECT_PREFIX}{post_id}"


def approvable_card(
    post: PostView,
    *,
    on_approve: ApproveFn,
    on_reject: RejectFn,
    on_regenerate: RegenerateFn,
    selectable: bool = False,
    disabled: bool = False,
) -> None:
    """One draft, editable, with Approve / Reject / Regenerate.

    The text area is seeded from the database and keyed by post id. Whatever it holds at
    the moment Approve is pressed is what gets passed to `on_approve` — so an edit the
    reviewer made but never explicitly "saved" is still the text that gets approved. The
    alternative, silently approving the unedited original, is the dangerous one.

    With `selectable=True` the card grows a checkbox whose state lives under
    `selection_key(post.id)`, so a page can read which cards are ticked and hand their
    ids to a batch approve/reject. The checkbox is transient UI state, not durable data.
    """
    text_key = f"{DRAFT_TEXT_PREFIX}{post.id}"
    reason_key = f"reject_reason_{post.id}"

    with st.container(border=True):
        if selectable:
            select_col, header, meta = st.columns([0.5, 2.5, 1])
            select_col.checkbox(
                "Select",
                key=selection_key(post.id),
                label_visibility="collapsed",
                disabled=disabled,
            )
        else:
            header, meta = st.columns([3, 1])
        header.markdown(f"**{post.topic}**")
        meta.markdown(status_badge(post.status.value))
        st.caption(
            f"post {post.id} · group {post.variant_group_id[:8]} · "
            f"prompt {post.prompt_version} · created {_age(post.created_at)}"
        )

        edited = st.text_area(
            "Draft",
            value=post.content,
            key=text_key,
            height=220,
            label_visibility="collapsed",
            disabled=disabled,
        )
        if edited.strip() != post.content.strip():
            st.caption(":orange-badge[edited] — approving saves this text")

        st.text_input(
            "Rejection reason (optional)",
            key=reason_key,
            placeholder="why this one does not work",
            disabled=disabled,
        )

        approve_col, reject_col, regen_col = st.columns(3)
        if approve_col.button(
            "Approve",
            key=f"approve_{post.id}",
            type="primary",
            disabled=disabled,
            use_container_width=True,
        ):
            on_approve(post.id, edited)
        if reject_col.button(
            "Reject", key=f"reject_{post.id}", disabled=disabled, use_container_width=True
        ):
            on_reject(post.id, str(st.session_state.get(reason_key, "")))
        if regen_col.button(
            "Regenerate", key=f"regen_{post.id}", disabled=disabled, use_container_width=True
        ):
            on_regenerate(post.id)


def read_only_card(post: PostView) -> None:
    """A post nobody is being asked to decide about — approved, scheduled, published."""
    with st.container(border=True):
        header, meta = st.columns([3, 1])
        header.markdown(f"**{post.topic}**")
        meta.markdown(status_badge(post.status.value))
        st.caption(f"post {post.id} · updated {_age(post.updated_at)}")
        st.markdown(post.content)


def audit_table(entries: list[AuditEntry], *, empty: str = "Nothing yet.") -> None:
    """Render audit entries newest first, or a placeholder if there are none."""
    if not entries:
        st.caption(empty)
        return
    st.dataframe(
        [
            {
                "when": entry.at_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "actor": entry.actor.value,
                "action": entry.action.value,
                "post": entry.entity_id,
                "detail": entry.detail,
            }
            for entry in entries
        ],
        hide_index=True,
        use_container_width=True,
    )


def log_view(lines: list[LogLine], *, empty: str = "No log file yet.") -> None:
    """Render log lines oldest first, in a scrollable code block."""
    if not lines:
        st.caption(empty)
        return
    st.code("\n".join(line.raw for line in lines), language="log")
