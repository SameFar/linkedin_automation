"""What the model calls have cost. Reads the `ai_calls` ledger, writes nothing.

Month-to-date is measured against the calendar month in UTC, which is the same basis the
`monthly_budget_usd` setting is expressed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from linkedos.core.config import get_settings
from linkedos.db.repo import AiCallRepo, SpendRow
from linkedos.db.session import get_session


@dataclass(frozen=True, slots=True)
class SpendReport:
    """Spend over a window, broken down and totalled."""

    since: datetime
    rows: list[SpendRow]
    total_usd: float
    budget_usd: float

    @property
    def budget_remaining_usd(self) -> float:
        return self.budget_usd - self.total_usd

    @property
    def over_budget(self) -> bool:
        return self.total_usd > self.budget_usd


def start_of_month(now: datetime | None = None) -> datetime:
    """Midnight UTC on the first of the current month."""
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_to_date(now: datetime | None = None) -> SpendReport:
    """Spend since the first of this month, grouped by model and purpose."""
    since = start_of_month(now)

    with get_session() as session:
        repo = AiCallRepo(session)
        rows = list(repo.spend_since(since))
        total = repo.total_since(since)

    return SpendReport(
        since=since,
        rows=rows,
        total_usd=total,
        budget_usd=get_settings().monthly_budget_usd,
    )
