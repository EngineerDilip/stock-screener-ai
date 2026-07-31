"""Bounded balanced RS history required for guarded activation."""

from __future__ import annotations

from datetime import date, timedelta

from app.services.group_rank_history_policy import (
    DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
)
from app.services.rrg_service import MIN_TAIL_WEEKS


RRG_MINIMUM_LOOKBACK_DAYS = MIN_TAIL_WEEKS * 7
MARKET_RS_ACTIVATION_LOOKBACK_DAYS = max(
    DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
    RRG_MINIMUM_LOOKBACK_DAYS,
)


def market_rs_activation_start_date(through_date: date) -> date:
    """Cover 6M Group changes, RRG tails, and the latest daily scan."""

    return through_date - timedelta(days=MARKET_RS_ACTIVATION_LOOKBACK_DAYS)


__all__ = [
    "MARKET_RS_ACTIVATION_LOOKBACK_DAYS",
    "market_rs_activation_start_date",
]
