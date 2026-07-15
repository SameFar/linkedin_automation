"""Populate the database with demo drafts using the fake provider — no API, no network.

Run once to fill the dashboard with something to look at, then launch the UI:

    uv run python scripts/seed_demo.py
    make run-ui

Everything the UI does except the Generate/Regenerate buttons reads the database, so the
whole app is browsable offline once this has run. Uses the same deterministic
`FakeProvider` the test suite uses, so it never opens a socket and costs nothing real.
"""

from __future__ import annotations

from linkedos.ai.client import AIClient
from linkedos.ai.providers.fake import FAKE_EMBED_MODEL, FakeProvider
from linkedos.core.logging import configure_logging
from linkedos.services import content

DEMO_TOPICS = [
    "why code review is a teaching tool",
    "what makes a database migration safe",
    "the real cost of being on-call",
    "reading code you did not write",
    "when to delete a test instead of fixing it",
]


def main() -> None:
    configure_logging()
    client = AIClient(FakeProvider(), embed_model=FAKE_EMBED_MODEL)
    batch = content.generate_batch(DEMO_TOPICS, per_topic=1, client=client)
    print(f"seeded batch {batch.batch_id} with {len(batch.posts)} draft(s)")
    print("now run:  make run-ui   → open the Approvals page → Batch review")


if __name__ == "__main__":
    main()
