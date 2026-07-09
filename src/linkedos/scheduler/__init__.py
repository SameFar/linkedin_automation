"""The background daemon: APScheduler jobs that run without the UI open.

Layering rule: the scheduler calls `services/` only. Anything it can do, the UI can
do, because both drive the same service functions.
"""

from __future__ import annotations
