"""Bounded balanced RS history required for guarded activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.group_rank_history_policy import (
    DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
)
from app.services.market_calendar_service import MarketCalendarService
from app.services.market_rs_rollout_contracts import normalize_rollout_market
from app.services.rrg_service import MIN_TAIL_WEEKS

RRG_MINIMUM_LOOKBACK_DAYS = MIN_TAIL_WEEKS * 7
MARKET_RS_ACTIVATION_LOOKBACK_DAYS = max(
    DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
    RRG_MINIMUM_LOOKBACK_DAYS,
)


def market_rs_activation_start_date(through_date: date) -> date:
    """Cover 6M Group changes, RRG tails, and the latest daily scan."""

    return through_date - timedelta(days=MARKET_RS_ACTIVATION_LOOKBACK_DAYS)


@dataclass(frozen=True)
class MarketRsActivationCoverage:
    market: str
    through_date: date
    required_dates: tuple[date, ...]

    def __post_init__(self) -> None:
        normalized = normalize_rollout_market(self.market)
        required_dates = tuple(self.required_dates)
        if not required_dates or required_dates[-1] != self.through_date:
            raise ValueError(
                "Guarded activation date must be the final required trading date."
            )
        if tuple(sorted(set(required_dates))) != required_dates:
            raise ValueError("Guarded activation dates must be unique and increasing.")
        object.__setattr__(self, "market", normalized)
        object.__setattr__(self, "required_dates", required_dates)

    @property
    def start_date(self) -> date:
        return self.required_dates[0]

    @classmethod
    def build(
        cls,
        *,
        calendar_service: MarketCalendarService,
        market: str,
        through_date: date,
    ) -> MarketRsActivationCoverage:
        normalized = normalize_rollout_market(market)
        required_dates = tuple(
            calendar_service.trading_days(
                normalized,
                market_rs_activation_start_date(through_date),
                through_date,
            )
        )
        if not required_dates or required_dates[-1] != through_date:
            raise ValueError(
                "Guarded activation date must be a completed market trading day."
            )
        return cls(
            market=normalized,
            through_date=through_date,
            required_dates=required_dates,
        )


__all__ = [
    "MARKET_RS_ACTIVATION_LOOKBACK_DAYS",
    "MarketRsActivationCoverage",
    "market_rs_activation_start_date",
]
