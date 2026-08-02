"""Canonical market calendar provider and session-override policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class CalendarProvider(Enum):
    """Supported third-party calendar engines."""

    EXCHANGE_CALENDARS = "exchange_calendars"
    PANDAS_MARKET_CALENDARS = "pandas_market_calendars"

    @property
    def package_name(self) -> str:
        return self.value.replace("_", "-")


@dataclass(frozen=True, slots=True)
class CalendarSessionOverride:
    """Authoritative override for a provider-reported market session date."""

    market: str
    day: date
    is_trading_day: bool

    def normalized_market(self) -> str:
        return self.market.strip().upper()


DEFAULT_CALENDAR_SESSION_OVERRIDES: tuple[CalendarSessionOverride, ...] = (
    CalendarSessionOverride("JP", date(2026, 3, 20), False),
    CalendarSessionOverride("JP", date(2026, 9, 22), False),
    CalendarSessionOverride("JP", date(2026, 9, 23), False),
)


def calendar_session_probe_expectations(
    overrides: tuple[CalendarSessionOverride, ...] = DEFAULT_CALENDAR_SESSION_OVERRIDES,
) -> dict[str, dict[date, bool]]:
    """Return market/date expectations used by health checks and gates."""
    expectations: dict[str, dict[date, bool]] = {}
    for override in overrides:
        expectations.setdefault(override.normalized_market(), {})[
            override.day
        ] = override.is_trading_day
    return expectations
