"""Service layer — the only entry point for `ui/`, `scheduler/`, and `cli`.

Services orchestrate `ai/`, `integrations/`, and `db/`. They own transaction
boundaries and business rules. Everything the UI can do, the scheduler can do, because
both go through exactly these functions.
"""

from __future__ import annotations
