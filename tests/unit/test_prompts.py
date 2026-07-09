"""The prompt registry: loads a versioned file, injects data, reports its version."""

from __future__ import annotations

import pytest

from linkedos.ai.prompts import registry
from linkedos.core.errors import WorkflowError

VALUES = {
    "topic": "why code review is a teaching tool",
    "voice_guidelines": "Write plainly.",
    "voice_examples": "Shipped the migration today.",
    "similar_posts": "(nothing yet)",
    "variant_index": "2",
    "variant_count": "3",
}


def test_load_returns_the_version_it_was_asked_for() -> None:
    template = registry.load("post_v1")
    assert template.version == "post_v1"


def test_load_splits_the_file_into_system_and_user_sections() -> None:
    template = registry.load("post_v1")

    assert template.system
    assert template.user
    # The markers themselves must not survive into either section.
    assert registry.SYSTEM_MARKER not in template.system
    assert registry.USER_MARKER not in template.user


def test_post_v1_declares_exactly_the_placeholders_the_service_supplies() -> None:
    assert registry.load("post_v1").placeholders == frozenset(VALUES)


def test_render_injects_values_into_both_sections() -> None:
    rendered = registry.render("post_v1", **VALUES)

    assert "Write plainly." in rendered.system
    assert "Shipped the migration today." in rendered.system
    assert "why code review is a teaching tool" in rendered.user
    assert "variant 2 of 3" in rendered.user


def test_render_returns_the_version_used() -> None:
    assert registry.render("post_v1", **VALUES).version == "post_v1"


def test_render_leaves_no_unsubstituted_placeholders() -> None:
    rendered = registry.render("post_v1", **VALUES)

    for name in VALUES:
        assert f"{{{name}}}" not in rendered.system
        assert f"{{{name}}}" not in rendered.user


def test_missing_value_raises_rather_than_rendering_a_hole() -> None:
    incomplete = {key: value for key, value in VALUES.items() if key != "voice_examples"}

    with pytest.raises(WorkflowError, match="needs value"):
        registry.render("post_v1", **incomplete)


def test_extra_value_raises_rather_than_being_silently_dropped() -> None:
    with pytest.raises(WorkflowError, match="no placeholder"):
        registry.render("post_v1", **VALUES, tone="breezy")


def test_unknown_version_raises() -> None:
    with pytest.raises(WorkflowError, match="no prompt template"):
        registry.load("post_v99")


def test_post_v1_carries_its_anti_cliche_and_no_fabrication_constraints() -> None:
    # These are the two constraints the whole prompt exists to enforce. If someone
    # rewrites the file and drops them, that is a behaviour change, not a typo fix.
    system = registry.load("post_v1").system.lower()

    assert "do not fabricate" in system
    assert "never invent a statistic" in system
    assert "game-changer" in system  # the banned-phrase list survived


def test_post_v1_separates_instructions_from_data_with_xml_tags() -> None:
    template = registry.load("post_v1")

    assert "<voice_guidelines>" in template.system
    assert "<voice_examples>" in template.system
    assert "<topic>" in template.user
    assert "<previously_written>" in template.user
