"""Parsing the voice seed file."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedos.core.errors import WorkflowError
from linkedos.services.voice import Voice, parse_seed

REPO_ROOT = Path(__file__).parents[2]

SEED = """\
# Voice profile — seed

Some preamble that is not part of any section.

## Guidelines

Write plainly. No hooks.

## Examples

Shipped the migration today.

---

It broke nothing.
"""


def test_parse_seed_returns_guidelines_then_examples() -> None:
    guidelines, examples = parse_seed(SEED)

    assert guidelines == "Write plainly. No hooks."
    assert examples.startswith("Shipped the migration today.")
    assert "It broke nothing." in examples


def test_parse_seed_ignores_the_preamble_before_the_first_heading() -> None:
    guidelines, examples = parse_seed(SEED)

    assert "preamble" not in guidelines
    assert "preamble" not in examples


def test_parse_seed_is_case_insensitive_about_headings() -> None:
    guidelines, examples = parse_seed("## GUIDELINES\ng\n\n## Examples\ne\n")

    assert guidelines == "g"
    assert examples == "e"


@pytest.mark.parametrize(
    "text",
    ["## Guidelines\nonly guidelines\n", "## Examples\nonly examples\n", "no headings at all"],
)
def test_parse_seed_rejects_a_file_missing_a_section(text: str) -> None:
    with pytest.raises(WorkflowError, match="missing section"):
        parse_seed(text)


def test_the_shipped_placeholder_is_detected_as_a_placeholder() -> None:
    voice = Voice(name="default", examples="TODO: replace with your posts", guidelines="g")

    assert voice.is_placeholder


def test_an_empty_examples_block_is_a_placeholder() -> None:
    assert Voice(name="default", examples="   ", guidelines="g").is_placeholder


def test_a_filled_in_profile_is_not_a_placeholder() -> None:
    voice = Voice(name="default", examples="Shipped the migration today.", guidelines="g")

    assert not voice.is_placeholder


def test_the_repo_seed_file_parses() -> None:
    # The file we actually ship must satisfy the parser it is written for.
    text = (REPO_ROOT / "data" / "seed_voice.md").read_text(encoding="utf-8")
    guidelines, examples = parse_seed(text)

    assert "TODO" in guidelines
    assert "TODO" in examples
