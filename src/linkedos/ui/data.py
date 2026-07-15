"""Cached read seam between Streamlit and the service layer.

Streamlit re-runs a page's script top to bottom on every widget interaction. Two rules
follow, and everything in this file exists to enforce them:

1. **The database is the only durable state.** Nothing here memoises a post, a status,
   or a queue beyond a short TTL, and `st.session_state` never holds one at all. A rerun
   re-reads. If two browser tabs are open, or the scheduler moves a post while you are
   looking at it, the next rerun tells the truth.

2. **Caches must be dropped the moment we write.** Every mutation goes through
   `after_mutation()`, which clears the read caches and reruns the script. A cached
   queue that survives an approval is a UI that lies to the person approving.

`st.cache_resource` is for handles that are expensive to build and safe to share across
reruns and sessions (the AI client, which owns an HTTP connection pool).
`st.cache_data` is for query results, which are copied per caller and expire fast.

The UI reaches `linkedos.services` and nothing below it. `AIClient` is named here only
as a type, re-exported through `services.content`, so `ui/` never imports `ai/`.
"""

from __future__ import annotations

import streamlit as st

from linkedos.services import audit, content, costs, logs, status

#: Short enough that the dashboard feels live; long enough to survive a widget click.
READ_TTL_S = 5

#: Prefix of the per-card text-area widget keys. Transient UI state, purged on write.
DRAFT_TEXT_PREFIX = "draft_text_"

#: Prefix of the per-card batch-select checkbox keys. Also transient, also purged.
SELECT_PREFIX = "select_"


@st.cache_resource
def ai_client() -> content.AIClient:
    """The process-wide AI client. Built once, shared across reruns and sessions.

    Cached because constructing it opens an HTTP client; rebuilding one per rerun would
    leak connections as fast as the user clicks.
    """
    return content.get_default_client()


@st.cache_data(ttl=READ_TTL_S)
def app_status() -> status.AppStatus:
    return status.get_app_status()


@st.cache_data(ttl=READ_TTL_S)
def queue() -> content.Queue:
    return content.get_queue()


@st.cache_data(ttl=READ_TTL_S)
def pending_drafts() -> list[content.PostView]:
    return content.get_pending_drafts()


@st.cache_data(ttl=READ_TTL_S)
def spend() -> costs.SpendReport:
    return costs.month_to_date()


@st.cache_data(ttl=READ_TTL_S)
def recent_batches(limit: int = 10) -> list[content.BatchSummary]:
    return content.recent_batches(limit)


@st.cache_data(ttl=READ_TTL_S)
def batch_drafts(batch_id: str) -> list[content.PostView]:
    return content.get_batch(batch_id, content.PostStatus.DRAFT)


@st.cache_data(ttl=READ_TTL_S)
def batch_approved(batch_id: str) -> list[content.PostView]:
    return content.get_batch(batch_id, content.PostStatus.APPROVED)


@st.cache_data(ttl=READ_TTL_S)
def recent_audit(limit: int = 5) -> list[audit.AuditEntry]:
    return audit.list_recent(limit)


@st.cache_data(ttl=READ_TTL_S)
def audit_history(post_id: int, limit: int = 20) -> list[audit.AuditEntry]:
    return audit.history_for_post(post_id, limit)


@st.cache_data(ttl=READ_TTL_S)
def log_tail(limit: int = 5, min_level: str | None = None) -> list[logs.LogLine]:
    return logs.tail(limit, min_level=min_level)


def after_mutation() -> None:
    """Drop every read cache and stale widget value, then rerun.

    Call immediately after any service write. Not `st.cache_data.clear()` scattered at
    call sites: forgetting one leaves a stale queue on screen, and a stale approval
    queue is how a post gets approved twice.

    Widget state is purged as well as the caches. A `text_area` keyed by post id keeps
    whatever the user last typed, and `value=` only seeds it on first render — so a card
    would go on displaying text the database no longer holds. Dropping the keys forces
    the next rerun to re-seed from the database, which is the only source of truth.
    """
    st.cache_data.clear()
    stale = (DRAFT_TEXT_PREFIX, SELECT_PREFIX)
    for key in [k for k in st.session_state if k.startswith(stale)]:
        del st.session_state[key]
    st.rerun()
