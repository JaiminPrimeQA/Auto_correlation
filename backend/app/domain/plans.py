"""The plan catalog: the free allowance and the three paid plans.

Prices, file limits and history retention come from settings so they can be
changed without code changes. Durations are calendar months.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime

from ..core.config import MIB, Settings

PLAN_IDS = ("monthly", "semiannual", "yearly")


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    months: int
    price_cents: int
    currency: str
    max_file_bytes: int
    retention_days: int

    @property
    def max_file_mb(self) -> int:
        return self.max_file_bytes // MIB


def plan_catalog(settings: Settings) -> list[Plan]:
    currency = settings.plan_currency.upper()
    return [
        Plan("monthly", "Monthly", 1, settings.plan_monthly_price_cents, currency,
             settings.plan_monthly_max_file_mb * MIB, settings.plan_monthly_retention_days),
        Plan("semiannual", "6 months", 6, settings.plan_semiannual_price_cents, currency,
             settings.plan_semiannual_max_file_mb * MIB, settings.plan_semiannual_retention_days),
        Plan("yearly", "Yearly", 12, settings.plan_yearly_price_cents, currency,
             settings.plan_yearly_max_file_mb * MIB, settings.plan_yearly_retention_days),
    ]


def get_plan(plan_id: str, settings: Settings) -> Plan | None:
    return next((p for p in plan_catalog(settings) if p.id == plan_id), None)


def free_max_file_bytes(settings: Settings) -> int:
    return settings.free_max_file_mb * MIB


def add_months(timestamp: float, months: int) -> float:
    """The same moment `months` calendar months later (UTC), clamping the day
    to the target month's length: 31 January + 1 month is 28/29 February."""
    start = datetime.fromtimestamp(timestamp, tz=UTC)
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day).timestamp()


def format_price(cents: int, currency: str) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(currency.upper())
    amount = f"{cents / 100:,.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency.upper()}"
