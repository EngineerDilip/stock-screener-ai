"""Market-session freshness helpers for bootstrap date policies."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

SESSION_WINDOW_LOOKBACK_SAFETY_DAYS = 30


class MarketSessionCalendar(Protocol):
    def trading_days(self, market: str, start: date, end: date) -> list[date]: ...


def session_window_start(
    calendar_service: MarketSessionCalendar,
    *,
    market: str,
    through_date: date,
    max_lag_sessions: int,
    fallback_safety_days: int = 0,
) -> date:
    """Return a bounded start date that includes the allowed session lag."""

    normalized_lag = max(0, int(max_lag_sessions))
    calendar_fallback = through_date - timedelta(
        days=normalized_lag + max(0, int(fallback_safety_days))
    )
    if normalized_lag == 0:
        return through_date

    probe_days = max(
        normalized_lag * 3 + SESSION_WINDOW_LOOKBACK_SAFETY_DAYS,
        SESSION_WINDOW_LOOKBACK_SAFETY_DAYS,
    )
    probe_start = through_date - timedelta(days=probe_days)
    sessions = tuple(calendar_service.trading_days(market, probe_start, through_date))
    if len(sessions) > normalized_lag:
        return sessions[-(normalized_lag + 1)]
    return calendar_fallback


def market_session_lag(
    calendar_service: MarketSessionCalendar,
    *,
    market: str,
    start_date: date,
    end_date: date,
) -> int:
    """Count market sessions elapsed from ``start_date`` through ``end_date``."""

    if start_date == end_date:
        return 0
    if start_date > end_date:
        return -market_session_lag(
            calendar_service,
            market=market,
            start_date=end_date,
            end_date=start_date,
        )

    sessions = tuple(calendar_service.trading_days(market, start_date, end_date))
    if not sessions:
        return (end_date - start_date).days
    if sessions[0] == start_date:
        return max(0, len(sessions) - 1)
    return len(sessions)


__all__ = [
    "MarketSessionCalendar",
    "market_session_lag",
    "session_window_start",
]
