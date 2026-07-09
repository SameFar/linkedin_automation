"""The voice profile: load it, and seed it on first use from `data/seed_voice.md`.

The seed file is a markdown document with two `##` sections, `Guidelines` and
`Examples`. It is checked into the repository as a placeholder for the user to fill in
with their real past posts — which is why `.gitignore` re-includes it out of `data/`.

Seeding happens lazily, the first time a draft is requested, rather than in a migration.
Migrations should move schema, not content; and a user who edits the seed file before
their first run should get what they wrote, not what the migration captured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from linkedos.core.errors import WorkflowError
from linkedos.core.logging import get_logger
from linkedos.db.repo import VoiceRepo
from linkedos.db.session import get_session

logger = get_logger(__name__)

SEED_PATH = Path("data/seed_voice.md")
PLACEHOLDER_MARKER = "TODO"


@dataclass(frozen=True, slots=True)
class Voice:
    """A voice profile as the prompt layer wants it: two blocks of text."""

    name: str
    examples: str
    guidelines: str

    @property
    def is_placeholder(self) -> bool:
        """True while the seed file is still the shipped TODO stub."""
        return PLACEHOLDER_MARKER in self.examples or not self.examples.strip()


def parse_seed(text: str) -> tuple[str, str]:
    """Split the seed markdown into `(guidelines, examples)`.

    Sections are `## Guidelines` and `## Examples`, case-insensitive. Anything before
    the first heading (a title, a note to self) is ignored.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []

    for line in text.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = heading.group(1).strip().lower()
            lines = []
        elif current is not None:
            lines.append(line)

    if current is not None:
        sections[current] = "\n".join(lines).strip()

    missing = {"guidelines", "examples"} - set(sections)
    if missing:
        raise WorkflowError(f"{SEED_PATH} is missing section(s): {', '.join(sorted(missing))}")

    return sections["guidelines"], sections["examples"]


def get_or_seed(name: str = VoiceRepo.DEFAULT_NAME, seed_path: Path = SEED_PATH) -> Voice:
    """Return the stored voice profile, seeding it from disk if the table is empty.

    Raises:
        WorkflowError: if no profile exists and the seed file is absent or malformed.
    """
    with get_session() as session:
        repo = VoiceRepo(session)
        profile = repo.get_by_name(name)

        if profile is None:
            if not seed_path.is_file():
                raise WorkflowError(
                    f"no voice profile named {name!r} and no seed file at {seed_path}"
                )
            guidelines, examples = parse_seed(seed_path.read_text(encoding="utf-8"))
            profile = repo.upsert(name=name, examples=examples, guidelines=guidelines)
            logger.info("seeded voice profile name=%s from %s", name, seed_path)

        voice = Voice(name=profile.name, examples=profile.examples, guidelines=profile.guidelines)

    if voice.is_placeholder:
        logger.warning(
            "voice profile %r still holds the placeholder from %s; "
            "drafts will not sound like you until you fill it in",
            name,
            seed_path,
        )
    return voice
