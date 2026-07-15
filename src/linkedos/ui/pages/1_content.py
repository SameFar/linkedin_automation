"""Content page: type a topic, get drafts.

Thin by construction. This file collects two inputs, calls `content.create_drafts`, and
renders what came back. Every decision — how many variants are allowed, what the prompt
says, whether the topic is a repeat — belongs to the service.
"""

from __future__ import annotations

import streamlit as st

from linkedos.core.errors import LinkedOSError
from linkedos.core.logging import configure_logging
from linkedos.services import content
from linkedos.ui import data
from linkedos.ui.components import require_database, status_badge

configure_logging()
st.set_page_config(page_title="linkedos · content", page_icon="✍️", layout="wide")

st.title("Draft a post")
st.caption("Generates variants in your voice, saves them as drafts, and queues them for review.")

if not require_database(data.app_status()):
    st.stop()

with st.form("draft_form"):
    topic = st.text_input(
        "Topic",
        placeholder="why code review is a teaching tool",
        help="In your own words. The model writes about this, in your voice.",
    )
    variants = st.slider("Variants", min_value=1, max_value=content.MAX_VARIANTS, value=3)
    submitted = st.form_submit_button("Generate", type="primary")

if submitted:
    if not topic.strip():
        st.warning("Give it a topic first.")
        st.stop()

    try:
        with st.spinner(f"Drafting {variants} variant(s)…"):
            batch = content.create_drafts(topic, variants, client=data.ai_client())
    except LinkedOSError as exc:
        st.error(str(exc))
        st.stop()

    # Fresh drafts exist now; the Home and Approvals pages must not serve a stale queue.
    st.cache_data.clear()

    if (duplicate := batch.duplicate_warning) is not None:
        st.warning(
            f"You have written about this before — post {duplicate.post.id}, "
            f"similarity {duplicate.score:.2f}. Consider a different angle."
        )

    st.success(
        f"{len(batch.posts)} draft(s) · prompt `{batch.prompt_version}` · "
        f"this run cost ${batch.cost_usd:.4f}"
    )
    st.caption("Review and approve them on the **Approvals** page.")

    for index, post in enumerate(batch.posts, start=1):
        with st.container(border=True):
            header, meta = st.columns([3, 1])
            header.markdown(f"**Variant {index} of {len(batch.posts)}** · post {post.id}")
            meta.markdown(status_badge(post.status.value))
            st.markdown(post.content)
