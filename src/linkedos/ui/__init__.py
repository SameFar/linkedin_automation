"""Streamlit dashboard.

Layering rule: the UI calls `services/` only. It holds no business logic and never
imports `db/`, `ai/`, or `integrations/`. If a page needs something the service layer
does not expose, the fix is a new service function, not a shortcut.
"""

from __future__ import annotations
