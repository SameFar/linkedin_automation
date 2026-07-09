"""Versioned prompt templates, loaded from files. No prompt is ever an inline f-string.

A prompt is data: it gets reviewed, diffed, and evaluated against `tests/evals/`. Keeping
it in a file means `git log post_v1.md` answers "why does it write like that", and the
version string recorded on every draft and every ledger row points at an exact revision.

**File format.** One markdown file per version, two sections split by HTML-comment
markers so the file still renders as readable prose:

    <!-- linkedos:system -->
    ...instructions, persona, constraints...
    <!-- linkedos:user -->
    ...the task, with {placeholders} for data...

**Versioning.** The version *is* the filename stem — `post_v1.md` yields `"post_v1"`.
Prompts are immutable once drafts reference them: edit the meaning, add `post_v2.md`.

**Placeholders.** `{name}` only. Rendering is strict in both directions: a placeholder
with no value raises, and a value with no placeholder raises. Both are bugs, and a
silently-dropped voice profile is the kind of bug you discover in production output.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from string import Formatter

from linkedos.core.errors import WorkflowError

PROMPT_DIR = Path(__file__).parent
SYSTEM_MARKER = "<!-- linkedos:system -->"
USER_MARKER = "<!-- linkedos:user -->"


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """A prompt with its data injected, ready to hand to `AIClient.complete`."""

    system: str
    user: str
    version: str


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """An unrendered template and the version string that identifies it."""

    version: str
    system: str
    user: str

    @property
    def placeholders(self) -> frozenset[str]:
        """Every `{name}` appearing in either section."""
        names = {
            field
            for section in (self.system, self.user)
            for _, field, _, _ in Formatter().parse(section)
            if field
        }
        return frozenset(names)

    def render(self, **values: str) -> RenderedPrompt:
        """Inject `values`. Raises `WorkflowError` on any mismatch in either direction."""
        expected = self.placeholders
        supplied = frozenset(values)

        if missing := expected - supplied:
            raise WorkflowError(
                f"prompt {self.version!r} needs value(s) for: {', '.join(sorted(missing))}"
            )
        if unused := supplied - expected:
            raise WorkflowError(
                f"prompt {self.version!r} has no placeholder(s) for: {', '.join(sorted(unused))}"
            )

        return RenderedPrompt(
            system=self.system.format(**values),
            user=self.user.format(**values),
            version=self.version,
        )


@cache
def load(version: str) -> PromptTemplate:
    """Load and parse `<version>.md` from the prompt directory.

    Cached: prompt files do not change while the process runs. Tests that write a
    temporary prompt file must call `load.cache_clear()`.

    Raises:
        WorkflowError: if the file is missing or lacks either section marker.
    """
    path = PROMPT_DIR / f"{version}.md"
    if not path.is_file():
        raise WorkflowError(f"no prompt template named {version!r} in {PROMPT_DIR}")

    text = path.read_text(encoding="utf-8")
    if SYSTEM_MARKER not in text or USER_MARKER not in text:
        raise WorkflowError(
            f"prompt {version!r} must contain both {SYSTEM_MARKER} and {USER_MARKER}"
        )

    _preamble, _, remainder = text.partition(SYSTEM_MARKER)
    system, _, user = remainder.partition(USER_MARKER)

    return PromptTemplate(version=version, system=system.strip(), user=user.strip())


def render(version: str, **values: str) -> RenderedPrompt:
    """Load `version` and inject `values`. The one function callers need."""
    return load(version).render(**values)
