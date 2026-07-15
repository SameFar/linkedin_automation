"""Streamlit entry point — Home / Health.

    make run-ui        (streamlit run src/linkedos/ui/app.py)

Everything on this page comes from `linkedos.services` through `linkedos.ui.data`. The
UI never opens a database session, never imports `db/` or `ai/`, and holds no durable
state: Streamlit re-runs this script on every interaction, and it re-reads each time.
"""

from __future__ import annotations

import streamlit as st

from linkedos.core.logging import configure_logging
from linkedos.services.costs import SpendReport
from linkedos.services.status import HEARTBEAT_STALE_AFTER_S, AppStatus
from linkedos.ui import data
from linkedos.ui.components import audit_table, log_view, require_database


def _scheduler_panel(status: AppStatus) -> None:
    st.subheader("Scheduler")

    age = status.heartbeat_age_s()
    if age is None:
        st.warning("Never seen. Start the daemon with `make run-scheduler`.")
        return

    last = status.last_heartbeat_utc
    stamp = last.strftime("%Y-%m-%d %H:%M:%S UTC") if last else "unknown"
    if status.scheduler_is_alive():
        st.success(f"Alive — last beat {int(age)}s ago ({stamp})")
    else:
        st.error(
            f"Stale — last beat {int(age)}s ago ({stamp}), "
            f"threshold {int(HEARTBEAT_STALE_AFTER_S)}s. Is the daemon running?"
        )


def _spend_panel(report: SpendReport) -> None:
    st.subheader("AI spend, month to date")

    spent, remaining = st.columns(2)
    spent.metric("Spent", f"${report.total_usd:.4f}")
    remaining.metric(
        "Remaining",
        f"${report.budget_remaining_usd:.4f}",
        help=f"Budget ${report.budget_usd:.2f}",
    )

    fraction = report.total_usd / report.budget_usd if report.budget_usd > 0 else 0.0
    st.progress(min(fraction, 1.0))
    if report.over_budget:
        st.error("Month-to-date spend is over the configured budget.")


def _render() -> None:
    st.title("linkedos")

    status = data.app_status()
    st.caption(f"version {status.version} · database `{status.db_path}`")

    if not require_database(status):
        return

    queue = data.queue()

    left, middle, right = st.columns(3)
    left.metric("Pending approvals", queue.pending_count)
    middle.metric("Approved", len(queue.approved))
    right.metric("Scheduled", len(queue.scheduled))

    if queue.pending_count:
        st.info(f"{queue.pending_count} draft(s) waiting on you — see **Approvals**.")

    st.divider()
    scheduler_col, spend_col = st.columns(2)
    with scheduler_col:
        _scheduler_panel(status)
    with spend_col:
        _spend_panel(data.spend())

    st.divider()
    st.subheader("Recent activity")
    audit_table(data.recent_audit(5), empty="No audited actions yet.")

    st.subheader("Recent log lines")
    log_view(data.log_tail(5))


def main() -> None:
    """Configure the process, then draw the page. Streamlit re-runs this on every event."""
    configure_logging()
    st.set_page_config(page_title="linkedos", page_icon="🧭", layout="wide")
    _render()


if __name__ == "__main__":
    main()
