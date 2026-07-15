"""Turning a batch of approved posts into proposed publish times. Pure, no I/O.

Milestone 2 stops at *proposing* and storing `scheduled_at`; Milestone 3 wires the
scheduler daemon to act on those times. Keeping this a pure function of `(count,
cadence, now)` is what makes a week's schedule testable without a clock or a database:
every test pins `now` and asserts on the exact datetimes that come back.

A cadence is a small grammar, not a cron string. The two shapes the UI offers —
"1 per weekday" and "Mon/Wed/Fri 09:00" — both parse to the same `Cadence`: a set of
weekdays, a time of day, and how many posts to place per chosen day. Everything is
computed in whatever timezone `now` carries; the caller decides that, and the UI passes
an aware UTC `now`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from linkedos.core.errors import WorkflowError
from linkedos.services.workflow import PostView

#: Monday=0 … Sunday=6, matching `datetime.weekday()`.
_WEEKDAY_NAMES: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_WORKWEEK = frozenset({0, 1, 2, 3, 4})
_WEEKEND = frozenset({5, 6})
_EVERY_DAY = frozenset(range(7))
_DEFAULT_TIME = time(9, 0)
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

#: A batch that would spill past this many days ahead is almost certainly a mistake.
_MAX_HORIZON_DAYS = 366


@dataclass(frozen=True, slots=True)
class Cadence:
    """When to publish: which weekdays, at what time, how many per day."""

    weekdays: frozenset[int]
    at: time = _DEFAULT_TIME
    per_day: int = 1

    def __post_init__(self) -> None:
        if not self.weekdays:
            raise WorkflowError("a cadence must allow at least one weekday")
        if not self.weekdays <= _EVERY_DAY:
            raise WorkflowError("weekdays must be in 0..6 (Mon..Sun)")
        if self.per_day < 1:
            raise WorkflowError("per_day must be at least 1")


@dataclass(frozen=True, slots=True)
class ProposedSlot:
    """A post and the datetime it is proposed to publish at."""

    post_id: int
    at: datetime


def _parse_time(token: str) -> time | None:
    match = _TIME_RE.match(token)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise WorkflowError(f"invalid time {token!r} in cadence")
    return time(hour, minute)


def parse_cadence(text: str) -> Cadence:
    """Parse a human cadence like ``"Mon/Wed/Fri 09:00"`` or ``"1 per weekday"``.

    Understood tokens, in any order and case-insensitive:

    * weekday names — ``mon tue wed thu fri sat sun``
    * groups — ``weekday``/``weekdays`` (Mon-Fri), ``weekend``/``weekends``, ``daily``
    * a ``HH:MM`` time — the publish time; defaults to 09:00
    * a bare integer — posts per chosen day (``"2 per weekday"``)

    Raises:
        WorkflowError: if no weekday can be inferred, or a token is unrecognised.
    """
    raw = text.strip().lower()
    if not raw:
        raise WorkflowError("cadence must not be empty")

    weekdays: set[int] = set()
    at = _DEFAULT_TIME
    per_day = 1

    for token in re.split(r"[\s,/]+", raw):
        if not token:
            continue
        if token in _WEEKDAY_NAMES:
            weekdays.add(_WEEKDAY_NAMES[token])
        elif token in {"weekday", "weekdays"}:
            weekdays |= _WORKWEEK
        elif token in {"weekend", "weekends"}:
            weekdays |= _WEEKEND
        elif token in {"daily", "everyday", "day"}:
            weekdays |= _EVERY_DAY
        elif (parsed := _parse_time(token)) is not None:
            at = parsed
        elif token.isdigit():
            per_day = int(token)
        elif token in {"per", "a", "every", "at", "post", "posts", "on"}:
            continue  # connective words, ignored
        else:
            raise WorkflowError(f"unrecognised token {token!r} in cadence {text!r}")

    if not weekdays:
        raise WorkflowError(f"cadence {text!r} names no days to publish on")
    return Cadence(weekdays=frozenset(weekdays), at=at, per_day=per_day)


def schedule_times(count: int, cadence: Cadence, *, now: datetime) -> list[datetime]:
    """The first `count` publish datetimes under `cadence`, all strictly after `now`.

    Walks forward day by day from `now`, and on each day whose weekday the cadence allows
    emits up to `per_day` slots at `cadence.at` (and hourly after it when `per_day > 1`).
    A slot at or before `now` is skipped, so nothing is ever scheduled in the past —
    including earlier slots on today, if the clock is already past them.

    Raises:
        WorkflowError: if `now` is naive, or the batch cannot fit within a year.
    """
    if now.tzinfo is None:
        raise WorkflowError("now must be timezone-aware")
    if count <= 0:
        return []

    slots: list[datetime] = []
    day: date = now.date()
    horizon = day + timedelta(days=_MAX_HORIZON_DAYS)

    while len(slots) < count:
        if day > horizon:
            raise WorkflowError(
                f"cannot place {count} post(s) within {_MAX_HORIZON_DAYS} days at this cadence"
            )
        if day.weekday() in cadence.weekdays:
            for index in range(cadence.per_day):
                moment = datetime.combine(day, cadence.at, tzinfo=now.tzinfo) + timedelta(
                    hours=index
                )
                if moment > now:
                    slots.append(moment)
                    if len(slots) == count:
                        break
        day += timedelta(days=1)

    return slots


def propose_schedule(
    posts: Sequence[PostView], cadence: Cadence, *, now: datetime | None = None
) -> list[ProposedSlot]:
    """Pair each post with a proposed publish datetime, in the order given.

    `now` defaults to the current instant; tests pass a fixed one. The result is a
    suggestion — the UI lets a human tweak each time before it is written to the posts.
    """
    moment = now if now is not None else datetime.now(UTC)
    times = schedule_times(len(posts), cadence, now=moment)
    return [ProposedSlot(post_id=post.id, at=at) for post, at in zip(posts, times, strict=True)]
