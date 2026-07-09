"""Repository pattern — intentionally empty until there is a table worth abstracting.

Once real tables land (posts, drafts, ideas, metrics, cost ledger), each gets a
repository here: a small class or module of functions that owns every query against
that table and returns ORM objects or plain dataclasses — never a `Session`, never a
raw `Row`.

    class PostRepo:
        def __init__(self, session: Session) -> None: ...
        def get(self, post_id: int) -> Post | None: ...
        def list_scheduled(self, before: datetime) -> Sequence[Post]: ...
        def add(self, post: Post) -> Post: ...

Why bother:

* Services stay readable — they orchestrate `PostRepo` and `DraftRepo` instead of
  assembling `select()` statements inline.
* Query logic is tested in one place against a temp SQLite database, offline.
* Transaction boundaries stay with the caller. Repositories take a `Session` and
  never commit; only `db.session.get_session()` decides when a transaction ends.

Rules that follow from the layering in CLAUDE.md: repositories may import `db.models`
and `core`, nothing else. Nothing outside `services/` may import a repository.
"""

from __future__ import annotations
