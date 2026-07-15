"""The scheduling maths, in isolation. No database, no clock — every `now` is pinned.

`schedule_times` is a pure function of `(count, cadence, now)`, so a week's proposed
publish times can be asserted exactly. The properties that matter: the right days, the
right count, spread into following weeks when a batch outgrows one, and never a slot in
the past.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from linkedos.core.errors import WorkflowError
from linkedos.db.models import PostStatus
from linkedos.services.scheduling import (
    Cadence,
    ProposedSlot,
    parse_cadence,
    propose_schedule,
    schedule_times,
)
from linkedos.services.workflow import PostView

# 2026-07-13 is a Monday, 08:00 UTC — before a 09:00 publish time.
MON_8AM = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)


def _weekdays(slots: list[datetime]) -> list[int]:
    return [slot.weekday() for slot in slots]


class TestParseCadence:
    def test_one_per_weekday(self) -> None:
        cadence = parse_cadence("1 per weekday")
        assert cadence.weekdays == frozenset({0, 1, 2, 3, 4})
        assert cadence.per_day == 1
        assert cadence.at == time(9, 0)

    def test_named_days_with_a_time(self) -> None:
        cadence = parse_cadence("Mon/Wed/Fri 09:00")
        assert cadence.weekdays == frozenset({0, 2, 4})
        assert cadence.at == time(9, 0)

    def test_named_days_with_a_custom_time(self) -> None:
        assert parse_cadence("Tue Thu 17:30").at == time(17, 30)

    def test_per_day_count(self) -> None:
        assert parse_cadence("2 per weekday").per_day == 2

    def test_weekends_and_daily(self) -> None:
        assert parse_cadence("weekend").weekdays == frozenset({5, 6})
        assert parse_cadence("daily").weekdays == frozenset(range(7))

    def test_case_and_separators_do_not_matter(self) -> None:
        assert parse_cadence("MON,wed,FRI").weekdays == frozenset({0, 2, 4})

    def test_empty_cadence_is_rejected(self) -> None:
        with pytest.raises(WorkflowError, match="must not be empty"):
            parse_cadence("   ")

    def test_a_cadence_with_no_days_is_rejected(self) -> None:
        with pytest.raises(WorkflowError, match="no days"):
            parse_cadence("09:00")

    def test_an_unknown_token_is_rejected(self) -> None:
        with pytest.raises(WorkflowError, match="unrecognised token"):
            parse_cadence("Mon fortnightly")

    def test_an_impossible_time_is_rejected(self) -> None:
        with pytest.raises(WorkflowError, match="invalid time"):
            parse_cadence("Mon 25:00")


class TestScheduleTimes:
    def test_weekday_only_skips_the_weekend(self) -> None:
        # Friday 08:00: Fri, then Mon, Tue — Sat and Sun are skipped.
        friday = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
        slots = schedule_times(3, parse_cadence("1 per weekday"), now=friday)

        assert _weekdays(slots) == [4, 0, 1]
        assert all(slot.weekday() < 5 for slot in slots)

    def test_mwf_lands_only_on_mon_wed_fri_and_spills_into_next_week(self) -> None:
        slots = schedule_times(5, parse_cadence("Mon/Wed/Fri 09:00"), now=MON_8AM)

        assert _weekdays(slots) == [0, 2, 4, 0, 2]  # Mon Wed Fri, then next Mon Wed
        assert {slot.weekday() for slot in slots} == {0, 2, 4}  # never Tue/Thu — the gaps

    def test_all_slots_are_at_the_cadence_time(self) -> None:
        slots = schedule_times(3, parse_cadence("Mon/Wed/Fri 09:00"), now=MON_8AM)
        assert all(slot.timetz() == time(9, 0, tzinfo=UTC) for slot in slots)

    def test_first_slot_is_today_when_the_time_has_not_passed(self) -> None:
        slots = schedule_times(1, parse_cadence("1 per weekday"), now=MON_8AM)
        assert slots[0] == datetime(2026, 7, 13, 9, 0, tzinfo=UTC)  # same Monday, 09:00

    def test_never_schedules_in_the_past(self) -> None:
        # 10:00 on Monday is already past the 09:00 slot, so the first Monday is skipped.
        monday_late = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
        slots = schedule_times(1, parse_cadence("Mon 09:00"), now=monday_late)

        assert slots[0] == datetime(2026, 7, 20, 9, 0, tzinfo=UTC)  # the following Monday
        assert all(slot > monday_late for slot in slots)

    def test_every_slot_is_strictly_after_now(self) -> None:
        slots = schedule_times(10, parse_cadence("1 per weekday"), now=MON_8AM)
        assert all(slot > MON_8AM for slot in slots)

    def test_slots_are_strictly_increasing(self) -> None:
        slots = schedule_times(8, parse_cadence("Mon/Wed/Fri"), now=MON_8AM)
        assert slots == sorted(slots)
        assert len(set(slots)) == len(slots)

    def test_per_day_places_multiple_and_spaces_them(self) -> None:
        slots = schedule_times(2, Cadence(frozenset({0}), at=time(9, 0), per_day=2), now=MON_8AM)
        assert slots == [
            datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
        ]

    def test_zero_count_is_empty(self) -> None:
        assert schedule_times(0, parse_cadence("1 per weekday"), now=MON_8AM) == []

    def test_a_naive_now_is_rejected(self) -> None:
        with pytest.raises(WorkflowError, match="timezone-aware"):
            schedule_times(1, parse_cadence("daily"), now=datetime(2026, 7, 13, 8, 0))


def _view(post_id: int) -> PostView:
    return PostView(
        id=post_id,
        content="body",
        status=PostStatus.APPROVED,
        topic=f"topic {post_id}",
        variant_group_id="g",
        batch_id="b",
        prompt_version="post_v1",
        created_at=MON_8AM,
        updated_at=MON_8AM,
        scheduled_at=None,
        published_at=None,
        linkedin_urn=None,
    )


class TestProposeSchedule:
    def test_pairs_each_post_with_a_time_in_order(self) -> None:
        posts = [_view(1), _view(2), _view(3)]
        slots = propose_schedule(posts, parse_cadence("1 per weekday"), now=MON_8AM)

        assert [slot.post_id for slot in slots] == [1, 2, 3]
        assert all(isinstance(slot, ProposedSlot) for slot in slots)
        expected = schedule_times(3, parse_cadence("1 per weekday"), now=MON_8AM)
        assert [slot.at for slot in slots] == expected

    def test_empty_posts_yield_no_slots(self) -> None:
        assert propose_schedule([], parse_cadence("daily"), now=MON_8AM) == []
