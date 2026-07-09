"""Streamlit entry point.

    streamlit run src/linkedos/ui/app.py

Everything on this page comes from `linkedos.services`. The UI never opens a database
session of its own.
"""

from __future__ import annotations

import streamlit as st

from linkedos.core.logging import configure_logging
from linkedos.services.status import AppStatus, get_app_status


def _render(status: AppStatus) -> None:
    st.title("linkedos — running")
    st.caption(f"version {status.version}")

    left, right = st.columns(2)
    left.metric("Database", "present" if status.db_exists else "missing")
    right.metric("Heartbeats", status.heartbeat_count)

    if not status.db_exists:
        st.warning(f"No database at `{status.db_path}`. Run `alembic upgrade head`.")
    elif status.last_heartbeat is None:
        st.info("No heartbeat recorded yet. Start the daemon with `make run-scheduler`.")
    else:
        st.success(f"Last heartbeat: {status.last_heartbeat.isoformat()}")

    st.divider()
    st.subheader("Pages")
    st.info(
        "Placeholder. Pages land in `src/linkedos/ui/pages/` as `NN_Title.py` and read "
        "through `linkedos.services` — never the database directly."
    )


def main() -> None:
    """Configure the process, then draw the page. Streamlit re-runs this on every event."""
    configure_logging()
    st.set_page_config(page_title="linkedos", page_icon="🧭", layout="wide")
    _render(get_app_status())


if __name__ == "__main__":
    main()
