"""Reading the rotating log file, so the UI never touches the filesystem itself.

`core.logging` writes lines shaped like:

    2026-07-10 03:14:15 INFO     linkedos.services.workflow post 3: draft -> approved

Anything that does not match — a traceback's continuation lines, a stray `print` — is
kept and returned with `level=""`, because the whole point of a log tail is to show what
is actually there rather than what the parser expected.

Only the tail is read into memory. The file rotates at 5 MB, and reading a whole one to
show fifty lines would be silly.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from linkedos.core.config import get_settings

LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_LINE = re.compile(
    r"^(?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<logger>\S+)\s+"
    r"(?P<message>.*)$"
)

#: Never read more than this many bytes from the end of the file.
_TAIL_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class LogLine:
    """One parsed log line. `level` is empty for lines the format does not match."""

    at: str
    level: str
    logger: str
    message: str
    raw: str


def log_path() -> Path:
    """Where `core.logging` writes. Read-only concern of this module."""
    return get_settings().log_dir / "linkedos.log"


def _parse(raw: str) -> LogLine:
    match = _LINE.match(raw)
    if match is None:
        return LogLine(at="", level="", logger="", message=raw, raw=raw)
    return LogLine(
        at=match["at"],
        level=match["level"],
        logger=match["logger"],
        message=match["message"],
        raw=raw,
    )


def _min_level_index(level: str) -> int:
    try:
        return LEVELS.index(level.upper())
    except ValueError:
        return 0


def tail(limit: int = 50, *, min_level: str | None = None) -> list[LogLine]:
    """The last `limit` log lines at or above `min_level`, oldest first.

    Returns an empty list when no log file exists yet — a fresh checkout has not logged
    anything, and that is not an error worth surfacing.

    Filtering happens before truncation, so asking for 5 ERROR lines returns the last 5
    errors rather than whatever errors happen to sit in the last 5 lines.
    """
    path = log_path()
    if not path.is_file():
        return []

    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - _TAIL_BYTES))
        # A mid-character seek can split a UTF-8 sequence; drop the partial first line.
        chunk = handle.read().decode("utf-8", errors="replace")
    lines = chunk.splitlines()
    if size > _TAIL_BYTES and lines:
        lines = lines[1:]

    threshold = _min_level_index(min_level) if min_level else 0
    kept: deque[LogLine] = deque(maxlen=limit)
    for raw in lines:
        if not raw.strip():
            continue
        parsed = _parse(raw)
        if min_level and parsed.level and _min_level_index(parsed.level) < threshold:
            continue
        if min_level and not parsed.level:
            # An unparseable line has no level to compare; keep it only when unfiltered.
            continue
        kept.append(parsed)

    return list(kept)
