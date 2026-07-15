"""Logs page: the log tail and the audit-log browser, side by side.

Two different records of the same events. The log file says what the process was doing;
the audit log says what was decided and by whom. When a post is in a state you did not
expect, the audit log tells you how it got there, and the log file tells you why.
"""

from __future__ import annotations

import streamlit as st

from linkedos.core.logging import configure_logging
from linkedos.services import audit, logs
from linkedos.ui import data
from linkedos.ui.components import audit_table, log_view, require_database

# `AuditAction` reaches the UI re-exported through `services.audit`; `ui/` must not
# import `db/` directly, even for an enum.
AuditAction = audit.AuditAction

configure_logging()
st.set_page_config(page_title="linkedos · logs", page_icon="🪵", layout="wide")

st.title("Logs")

log_tab, audit_tab = st.tabs(["Application log", "Audit log"])

with log_tab:
    controls, _ = st.columns([1, 2])
    with controls:
        level = st.selectbox("Minimum level", ("ALL", *logs.LEVELS), index=0)
        limit = st.slider("Lines", min_value=20, max_value=500, value=100, step=20)

    st.caption(f"Tail of `{logs.log_path()}`")
    log_view(
        data.log_tail(limit, min_level=None if level == "ALL" else level),
        empty="No log file yet. Run the CLI, the scheduler, or generate a draft.",
    )

with audit_tab:
    # Only this tab needs the database; the log tail is read from a file and is often the
    # only thing that can explain why the database is unusable.
    if require_database(data.app_status()):
        action_names = ("ALL", *(action.value for action in AuditAction))
        controls, _ = st.columns([1, 2])
        with controls:
            action = st.selectbox("Action", action_names, index=0)
            post_id = st.number_input("Post id (0 for all)", min_value=0, value=0, step=1)
            limit = st.slider("Entries", min_value=10, max_value=500, value=50, step=10)

        entries = audit.list_recent(
            limit,
            entity_id=int(post_id) or None,
            action=None if action == "ALL" else AuditAction(action),
        )
        st.caption(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, newest first")
        audit_table(entries, empty="Nothing audited yet.")
